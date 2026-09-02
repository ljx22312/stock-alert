#!/usr/bin/env python3
"""本地 stock-alert → CloudBase 云端数据库同步（方案 B 推送端）。

用法:
  python3 sync_cloud.py stocks        # 推送股池 + 指数目录到 stocks 集合
  python3 sync_cloud.py backfill      # 一次性全历史日线回填（股池 + 指数）
  python3 sync_cloud.py daily         # 增量日线同步（拉最近 150 根 upsert，幂等）
  python3 sync_cloud.py hours         # 60分钟线同步（腾讯 mkline，滚动窗口约 4 个月）
  python3 sync_cloud.py snapshot      # 盘中最新快照推送（覆盖式）+ 分时历史帧 ticks
  python3 sync_cloud.py signals       # 增量信号日志推送
  python3 sync_cloud.py profile       # 日内量能分布（同时段5分钟均量基线）
  python3 sync_cloud.py all           # stocks + daily + hours + signals + snapshot + profile

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

from quotes import fetch_quotes, to_market_code  # noqa: E402
from daily import TENCENT_URL, TENCENT_MKLINE_URL, _fetch_tencent, _fetch_eastmoney  # noqa: E402

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
    {"symbol": "sh000010", "name": "上证180", "type": "index"},
    {"symbol": "sh000906", "name": "中证800", "type": "index"},
    {"symbol": "sz399330", "name": "深证100", "type": "index"},
    {"symbol": "sh000698", "name": "科创100", "type": "index"},
    {"symbol": "sh000922", "name": "中证红利", "type": "index"},
]

# 只上网页、不参与本地监控的股票（本地有完整数据，云端补全历史后仅供网页展示）
WEB_ONLY = [
    {"symbol": "000001", "name": "平安银行", "type": "stock"},
    {"symbol": "000858", "name": "五粮液", "type": "stock"},
    {"symbol": "002594", "name": "比亚迪", "type": "stock"},
    {"symbol": "300059", "name": "东方财富", "type": "stock"},
    {"symbol": "300750", "name": "宁德时代", "type": "stock"},
    {"symbol": "600036", "name": "招商银行", "type": "stock"},
    {"symbol": "600519", "name": "贵州茅台", "type": "stock"},
    {"symbol": "601318", "name": "中国平安", "type": "stock"},
    {"symbol": "601899", "name": "紫金矿业", "type": "stock"},
    {"symbol": "600798", "name": "宁波海运", "type": "stock"},
    {"symbol": "000039", "name": "中集集团", "type": "stock"},
    {"symbol": "600863", "name": "华能蒙电", "type": "stock"},
    {"symbol": "002782", "name": "可立克", "type": "stock"},
    {"symbol": "600585", "name": "海螺水泥", "type": "stock"},
]

BATCH = 200  # 单次推送最大文档数
MAX_BYTES = 80 * 1024  # 单次推送请求体上限（CloudBase HTTP 访问服务 ~100KB）
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


def web_stocks() -> list[dict]:
    """网页展示股池 = 本地监控自选 + 只上网页的股票（与本地提醒解耦）。"""
    return load_watchlist() + WEB_ONLY


def all_targets() -> list[dict]:
    return web_stocks() + INDICES


def req_code(symbol: str) -> str:
    """个股补市场前缀（sh/sz）用于行情请求；指数等完整代码原样返回。"""
    if symbol[:2] in ("sh", "sz"):
        return symbol
    return to_market_code(symbol)


# 增量同步水位（记录各标的云端已推到的位置，避免每天全量覆盖重写）
STATE_DIR = HERE / "data" / "sync_state"
API_BASE = "https://ljx-d1gjpcu23fa094e67.service.tcloudbase.com/api"


def load_meta(name: str) -> dict:
    p = STATE_DIR / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_meta(name: str, data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = STATE_DIR / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def cloud_rows(kind: str, symbol: str) -> list[dict]:
    """读云端当前已入库的行情（用于初始化增量水位；读比写便宜 10 倍）。
    kind: 'daily' | 'hour'。返回升序文档数组。"""
    r = requests.get(f"{API_BASE}/{kind}", params={"symbol": symbol, "limit": 300}, timeout=60)
    r.raise_for_status()
    return (r.json().get("data") or [])


def push(collection: str, docs: list[dict], cfg: dict) -> int:
    """分批发往 ingest（按文档数 ≤ BATCH 且单请求体 ≤ MAX_BYTES 双上限），
    任何一批失败即抛错（由调用方决定是否重试）。"""
    if not docs:
        return 0
    total = 0
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_size = 0
    for d in docs:
        # 单条序列化体积（含集合名的头部开销按全量估算，偏保守）
        sz = len(json.dumps({"collection": collection, "docs": [d]}, ensure_ascii=False).encode("utf-8"))
        if cur and (len(cur) >= BATCH or cur_size + sz > MAX_BYTES):
            chunks.append(cur)
            cur, cur_size = [], 0
        cur.append(d)
        cur_size += sz
    if cur:
        chunks.append(cur)
    for i, chunk in enumerate(chunks):
        r = requests.post(
            cfg["url"], json={"collection": collection, "docs": chunk},
            headers={"x-sync-token": cfg["token"]}, timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"{collection} 推送失败: {data}")
        if data.get("failed"):
            print(f"  ! {collection} 批次 {i + 1} 有 {len(data['failed'])} 条失败: {data['failed'][:3]}")
        total += data.get("upserted", 0)
    return total


def cmd_stocks(cfg: dict) -> int:
    docs = web_stocks() + INDICES
    n = push("stocks", docs, cfg)
    print(f"stocks: 推送 {n} 条目录")
    return n


def fetch_window(code: str, begin, end) -> list[list]:
    """拉一个日期窗口（code 须为带市场前缀的完整代码，如 sh601169）。
    腾讯 qfq 对突发请求会限流数分钟（返回空 data），故对空结果做耐心退避重试。"""
    attempts = [("qfq", 2.0), ("qfq", 5.0), ("qfq", 10.0), ("qfq", 20.0),
                ("raw", 20.0), ("qfq", 30.0), ("raw", 30.0), ("qfq", 60.0), ("raw", 60.0)]
    for i, (mode, wait) in enumerate(attempts):
        if i:
            time.sleep(wait)
        qfq = ",qfq" if mode == "qfq" else ""
        try:
            r = requests.get(TENCENT_URL, params={
                "param": f"{code},day,{begin.isoformat()},{end.isoformat()},800{qfq}",
            }, timeout=20)
            node = r.json()["data"][code]
            rows = node.get("qfqday") or node.get("day") or []
            if rows:
                return rows
            if i % 2 == 0:
                print(f"    {code} 窗口 {begin}~{end} 第{i+1}次尝试为空，等待限流解除…")
        except Exception:
            pass
    return []


def fetch_full_history(symbol: str) -> list[dict]:
    """用日期窗口翻页拉全历史日线（腾讯单次上限约 800 根）。"""
    code = req_code(symbol)
    seen: dict[str, dict] = {}
    window = timedelta(days=1200)  # 约 3.3 年 ≈ 800 交易日
    end = date.today()
    start = datetime.strptime(HISTORY_START, "%Y-%m-%d").date()
    guard = 0
    while end > start and guard < 12:
        guard += 1
        begin = max(start, end - window)
        rows = fetch_window(code, begin, end)
        if not rows:
            print(f"  ! {symbol} 窗口 {begin}~{end} 重试后仍无数据，中断该标的")
            break
        for f in rows:
            seen[f[0]] = {"date": f[0], "open": float(f[1]), "close": float(f[2]),
                          "high": float(f[3]), "low": float(f[4]),
                          "volume": float(f[5]) if len(f) > 5 else 0.0,
                          "amount": 0.0}  # 腾讯日线无成交额字段
        if len(rows) < 700:  # 已到上市起点
            break
        end = datetime.strptime(rows[0][0], "%Y-%m-%d").date() - timedelta(days=1)
        time.sleep(1.0)
    return [{"symbol": symbol, **v} for v in sorted(seen.values(), key=lambda x: x["date"])]


def cmd_backfill(cfg: dict, symbols: list[str] | None = None) -> int:
    total = 0
    targets = all_targets()
    if symbols:
        targets = [t for t in targets if t["symbol"] in symbols]
    for i, t in enumerate(targets):
        if i:
            time.sleep(1.5)  # 错开 qfq 请求，避免触发限流
        bars = fetch_full_history(t["symbol"])
        if not bars:
            print(f"backfill: {t['symbol']} {t['name']} 无数据，跳过")
            continue
        n = push("daily_bars", bars, cfg)
        total += n
        print(f"backfill: {t['symbol']} {t['name']} -> {n} 根日线 ({bars[0]['date']} ~ {bars[-1]['date']})")
    return total


def fetch_recent_bars(symbol: str, days: int = 150) -> list[dict]:
    """拉最近 days 根前复权日线，腾讯主用（带市场前缀直接请求，勿套 _fetch_tencent 二次加前缀）、东财兜底。"""
    code = req_code(symbol)
    bars = []
    try:
        r = requests.get(TENCENT_URL, params={
            "param": f"{code},day,,,{days},qfq",
        }, timeout=20)
        node = r.json()["data"][code]
        rows = node.get("qfqday") or node.get("day") or []
        bars = [{"date": f[0], "open": float(f[1]), "close": float(f[2]),
                 "high": float(f[3]), "low": float(f[4]),
                 "volume": float(f[5]) if len(f) > 5 else 0.0, "amount": 0.0}
                for f in rows]
    except Exception:
        pass
    if not bars and symbol[:2] not in ("sh", "sz"):
        # 东财兜底（仅 6 位个股代码）
        east_symbol = f"1.{symbol}" if symbol.startswith(("6", "9")) else f"0.{symbol}"
        try:
            bars = _fetch_eastmoney(east_symbol, days)
        except Exception:
            pass
    time.sleep(1.2)  # 限流保护
    return bars


def cmd_daily(cfg: dict) -> int:
    """增量日线同步：拉最近 150 根前复权，与云端水位对比，只推新增/复权调整过的根。

    前复权价在除权除息日会整体调整，故水位按 {date: close} 记录：
    非除权日每天仅新增 1 根（~36 次写），除权日一次性补推调整段。
    首次运行（无水位）从云端读回当前库建水位，不做全量覆盖。
    """
    meta = load_meta("daily_meta.json")
    total = 0
    for t in all_targets():
        bars = fetch_recent_bars(t["symbol"], days=150)
        if not bars:
            print(f"daily: {t['symbol']} 无数据，跳过")
            continue
        if t["symbol"] not in meta:
            # 首次：读云端已入库的历史建水位（读 300 根 < 写 150 根 x 36 标的）
            cloud = cloud_rows("daily", t["symbol"])
            meta[t["symbol"]] = {b["date"]: b.get("close") for b in cloud}
        old = meta[t["symbol"]]
        changed = [b for b in bars if old.get(b["date"]) != b["close"]]
        if changed:
            docs = [{"symbol": t["symbol"], **b} for b in changed]
            n = push("daily_bars", docs, cfg)
            total += n
            print(f"daily: {t['symbol']} -> {n} 根增量/复权调整（150 根中）")
        else:
            print(f"daily: {t['symbol']} 无变化，跳过")
        meta[t["symbol"]] = {b["date"]: b["close"] for b in bars}
        save_meta("daily_meta.json", meta)
    return total


def cmd_snapshot(cfg: dict) -> int:
    symbols = [w["symbol"] for w in web_stocks()]
    codes = tuple(i["symbol"] for i in INDICES)
    quotes = fetch_quotes(symbols, extra_codes=codes)
    now = datetime.now()
    docs = [{"symbol": sym, "ts": int(now.timestamp()), **{k: v for k, v in q.items() if k != "symbol"}}
            for sym, q in quotes.items() if q]
    n = push("snapshots", docs, cfg)
    # 盘中历史帧（ticks）：快照每 5 分钟一帧，网页"今日分时"用
    tdocs = [{"symbol": sym, "date": _tick_dt(q, now),
              "price": q.get("price"), "pct_chg": q.get("pct_chg"),
              "volume_hand": q.get("volume_hand"), "amount_wan": q.get("amount_wan")}
             for sym, q in quotes.items() if q]
    m = push("ticks", tdocs, cfg)
    print(f"snapshot: 推送 {n} 条最新快照 + {m} 条分时帧")
    return n


def _tick_dt(q: dict, now: datetime) -> str:
    """从行情时间戳提取 YYYYMMDDHHmm；解析失败用当前时间。"""
    digits = "".join(ch for ch in str(q.get("quote_time") or "") if ch.isdigit())
    if len(digits) >= 12:
        y, mo, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        if 2000 < y < 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return digits[:12]
    return now.strftime("%Y%m%d%H%M")


def fetch_hour_bars(symbol: str, count: int = 320) -> list[dict]:
    """拉最近 count 根 60 分钟K线（腾讯 mkline，滚动窗口约 4 个月，不复权）。
    行格式: [YYYYMMDDHHmm, open, close, high, low, volume, info, turnover]"""
    code = req_code(symbol)
    r = requests.get(TENCENT_MKLINE_URL,
                     params={"param": f"{code},m60,,{count}"}, timeout=20)
    rows = (r.json().get("data") or {}).get(code, {}).get("m60") or []
    return [{
        "date": x[0], "open": float(x[1]), "close": float(x[2]),
        "high": float(x[3]), "low": float(x[4]), "volume": float(x[5]),
    } for x in rows]


def cmd_hours(cfg: dict) -> int:
    """增量 60 分钟线同步：腾讯 mkline 不复权、历史不变，
    只推超过水位的新 bar（每标的每天约 4-8 根）。首次运行读云端建水位。"""
    meta = load_meta("hour_meta.json")
    total = 0
    for i, t in enumerate(all_targets()):
        if i:
            time.sleep(1.0)  # 限流保护
        bars = fetch_hour_bars(t["symbol"])
        if not bars:
            print(f"hours: {t['symbol']} 无小时线，跳过")
            continue
        if t["symbol"] not in meta:
            cloud = cloud_rows("hour", t["symbol"])
            meta[t["symbol"]] = cloud[-1]["date"] if cloud else ""
        last = meta[t["symbol"]]
        new = [b for b in bars if b["date"] > last]
        if new:
            docs = [{"symbol": t["symbol"], **b} for b in new]
            n = push("hour_bars", docs, cfg)
            total += n
            print(f"hours: {t['symbol']} -> {n} 根增量（截至 {new[-1]['date']}）")
        else:
            print(f"hours: {t['symbol']} 无新 bar，跳过")
        meta[t["symbol"]] = bars[-1]["date"]
        save_meta("hour_meta.json", meta)
    return total


def cmd_profile(cfg: dict) -> int:
    import sqlite3
    db = sqlite3.connect(str(HERE / "data" / "stock_alert.db"))
    rows = db.execute("SELECT symbol, hm, avg_vol FROM vol_profile").fetchall()
    docs = [{"symbol": s, "hm": h, "avg_vol": v} for s, h, v in rows]
    n = push("vol_profile", docs, cfg)
    symbols = len({d["symbol"] for d in docs})
    print(f"profile: 推送 {n} 条量基线（{symbols} 个标的 × 每标的 {n // max(symbols, 1)} 时段）")
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
    docs = [{"symbol": r[2], "rule": r[3], "message": r[4], "ts": r[1], "id": r[0],
             "_id": f"{r[2]}_{r[3]}_{r[1]}"} for r in rows]
    n = push("signals", docs, cfg) if docs else 0
    if rows:
        state["last_signal_id"] = rows[-1][0]
        (HERE / "cloud_sync.state.json").write_text(json.dumps(state))
    print(f"signals: 推送 {n} 条（水位 {state['last_signal_id']}）")
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="stock-alert -> CloudBase 同步")
    ap.add_argument("cmd", choices=["stocks", "backfill", "daily", "hours", "snapshot", "signals", "profile", "all"])
    ap.add_argument("--symbols", help="backfill 限定标的，逗号分隔（如 601169,600900）")
    args = ap.parse_args()
    cfg = load_cfg()

    t0 = time.time()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    if args.cmd == "stocks":
        cmd_stocks(cfg)
    elif args.cmd == "backfill":
        cmd_backfill(cfg, symbols)
    elif args.cmd == "daily":
        cmd_daily(cfg)
    elif args.cmd == "hours":
        cmd_hours(cfg)
    elif args.cmd == "snapshot":
        cmd_snapshot(cfg)
    elif args.cmd == "signals":
        cmd_signals(cfg)
    elif args.cmd == "profile":
        cmd_profile(cfg)
    else:  # all
        cmd_stocks(cfg)
        cmd_daily(cfg)
        cmd_hours(cfg)
        cmd_signals(cfg)
        cmd_snapshot(cfg)
        cmd_profile(cfg)
    print(f"完成，耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
