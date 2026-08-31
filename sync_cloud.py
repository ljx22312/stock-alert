#!/usr/bin/env python3
"""本地 stock-alert → CloudBase 云端数据库同步（方案 B 推送端）。

用法:
  python3 sync_cloud.py stocks        # 推送股池 + 指数目录到 stocks 集合
  python3 sync_cloud.py backfill      # 一次性全历史日线回填（股池 + 指数）
  python3 sync_cloud.py daily         # 增量日线同步（拉最近 150 根 upsert，幂等）
  python3 sync_cloud.py snapshot      # 盘中最新快照推送（覆盖式）
  python3 sync_cloud.py signals       # 增量信号日志推送
  python3 sync_cloud.py all           # stocks + daily + signals + snapshot 一次跑完

配置: 同目录 cloud_sync.json {"url": "...", "token": "..."}（不入库）
状态: 同目录 cloud_sync.state.json（信号增量水位，自动维护）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

from quotes import fetch_quotes  # noqa: E402
from daily import fetch_daily_bars, TENCENT_URL  # noqa: E402

# 指数目录（腾讯行情代码）：宽基 / 规模 / 基准
INDICES = [
    {"symbol": "sh000001", "name": "上证指数", "type": "index"},
    {"symbol": "sh000016", "name": "上证50", "type": "index"},
    {"symbol": "sh000300", "name": "沪深300", "type": "index"},
    {"symbol": "sh000905", "name": "中证500", "type": "index"},
    {"symbol": "sh000852", "name": "中证1000", "type": "index"},
    {"symbol": "sh000688", "name": "科创50", "type": "index"},
    {"symbol": "sh000985", "name": "中证全指", "type": "index"},
    {"symbol": "sh000510", "name": "中证A500", "type": "index"},
    {"symbol": "sz399001", "name": "深证成指", "type": "index"},
    {"symbol": "sz399006", "name": "创业板指", "type": "index"},
    {"symbol": "sz399303", "name": "国证2000", "type": "index"},
]

BATCH = 200  # 单次推送最大文档数
HISTORY_START = "2015-01-01"  # 回填起点


def load_cfg() -> dict:
    cfg = json.loads((HERE / "cloud_sync.json").read_text())
    if not cfg.get("url") or not cfg.get("token"):
        raise SystemExit("cloud_sync.json 缺少 url 或 token")
    return cfg


def load_watchlist() -> list[dict]:
    conf = json.loads((HERE / "config.json").read_text())
    return [{"symbol": w["symbol"], "name": w["name"], "type": "stock"}
            for w in conf["watchlist"]]


def all_targets() -> list[dict]:
    return load_watchlist() + INDICES


def push(collection: str, docs: list[dict], cfg: dict) -> int:
    """分批发往 ingest，任何一批失败即抛错（由调用方决定是否重试）。"""
    if not docs:
        return 0
    total = 0
    for i in range(0, len(docs), BATCH):
        chunk = docs[i:i + BATCH]
        r = requests.post(
            cfg["url"], json={"collection": collection, "docs": chunk},
            headers={"x-sync-token": cfg["token"]}, timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"{collection} 推送失败: {data}")
        if data.get("failed"):
            print(f"  ! {collection} 批次 {i // BATCH + 1} 有 {len(data['failed'])} 条失败: {data['failed'][:3]}")
        total += data.get("upserted", 0)
    return total


def cmd_stocks(cfg: dict) -> int:
    docs = load_watchlist() + INDICES
    n = push("stocks", docs, cfg)
    print(f"stocks: 推送 {n} 条目录")
    return n


def fetch_full_history(symbol: str) -> list[dict]:
    """用日期窗口翻页拉全历史日线（腾讯单次上限约 800 根）。"""
    seen: dict[str, dict] = {}
    window = timedelta(days=1200)  # 约 3.3 年 ≈ 800 交易日
    end = date.today()
    start = datetime.strptime(HISTORY_START, "%Y-%m-%d").date()
    guard = 0
    while end > start and guard < 12:
        guard += 1
        begin = max(start, end - window)
        rows = None
        for attempt in range(3):  # 腾讯偶发返回空 data，重试
            try:
                r = requests.get(TENCENT_URL, params={
                    "param": f"{symbol},day,{begin.isoformat()},{end.isoformat()},800,qfq",
                }, timeout=20)
                node = r.json()["data"][symbol]
                rows = node.get("qfqday") or node.get("day") or []
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  ! {symbol} 窗口 {begin}~{end} 拉取失败: {e}")
                    return sorted(({"symbol": symbol, **v} for v in seen.values()),
                                  key=lambda x: x["date"])
                time.sleep(1)
        for f in rows:
            seen[f[0]] = {"date": f[0], "open": float(f[1]), "close": float(f[2]),
                          "high": float(f[3]), "low": float(f[4]),
                          "volume": float(f[5]) if len(f) > 5 else 0.0,
                          "amount": 0.0}  # 腾讯日线无成交额字段
        if len(rows) < 700:  # 已到上市起点
            break
        end = datetime.strptime(rows[0][0], "%Y-%m-%d").date() - timedelta(days=1)
        time.sleep(0.5)
    return [{"symbol": symbol, **v} for v in sorted(seen.values(), key=lambda x: x["date"])]


def cmd_backfill(cfg: dict) -> int:
    total = 0
    for t in all_targets():
        bars = fetch_full_history(t["symbol"])
        if not bars:
            print(f"backfill: {t['symbol']} {t['name']} 无数据，跳过")
            continue
        n = push("daily_bars", bars, cfg)
        total += n
        print(f"backfill: {t['symbol']} {t['name']} -> {n} 根日线 ({bars[0]['date']} ~ {bars[-1]['date']})")
    return total


def cmd_daily(cfg: dict) -> int:
    total = 0
    for t in all_targets():
        bars = fetch_daily_bars(t["symbol"], days=150)
        docs = [{"symbol": t["symbol"], **b} for b in bars]
        n = push("daily_bars", docs, cfg)
        total += n
        print(f"daily: {t['symbol']} -> {n} 根")
    return total


def cmd_snapshot(cfg: dict) -> int:
    symbols = [w["symbol"] for w in load_watchlist()]
    codes = tuple(i["symbol"] for i in INDICES)
    quotes = fetch_quotes(symbols, extra_codes=codes)
    now = int(time.time())
    docs = [{"symbol": sym, "ts": now, **{k: v for k, v in q.items() if k != "symbol"}}
            for sym, q in quotes.items() if q]
    n = push("snapshots", docs, cfg)
    print(f"snapshot: 推送 {n} 条最新快照")
    return n


def _state() -> dict:
    f = HERE / "cloud_sync.state.json"
    if f.exists():
        return json.loads(f.read_text())
    return {"last_signal_id": 0}


def cmd_signals(cfg: dict) -> int:
    import sqlite3
    state = _state()
    db = sqlite3.connect(str(HERE / "data" / "stock_alert.db"))
    rows = db.execute(
        "SELECT id, ts, symbol, rule, message FROM signal_log WHERE id > ? ORDER BY id",
        (state.get("last_signal_id", 0),),
    ).fetchall()
    docs = [{"symbol": r[2], "rule": r[3], "message": r[4], "ts": r[1],
             "_id": f"{r[2]}_{r[3]}_{r[1]}"} for r in rows]
    n = push("signals", docs, cfg) if docs else 0
    if rows:
        state["last_signal_id"] = rows[-1][0]
        (HERE / "cloud_sync.state.json").write_text(json.dumps(state))
    print(f"signals: 推送 {n} 条（水位 {state['last_signal_id']}）")
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="stock-alert -> CloudBase 同步")
    ap.add_argument("cmd", choices=["stocks", "backfill", "daily", "snapshot", "signals", "all"])
    args = ap.parse_args()
    cfg = load_cfg()

    t0 = time.time()
    if args.cmd == "stocks":
        cmd_stocks(cfg)
    elif args.cmd == "backfill":
        cmd_backfill(cfg)
    elif args.cmd == "daily":
        cmd_daily(cfg)
    elif args.cmd == "snapshot":
        cmd_snapshot(cfg)
    elif args.cmd == "signals":
        cmd_signals(cfg)
    else:  # all
        cmd_stocks(cfg)
        cmd_daily(cfg)
        cmd_signals(cfg)
        cmd_snapshot(cfg)
    print(f"完成，耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
