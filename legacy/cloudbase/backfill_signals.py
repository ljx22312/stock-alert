#!/usr/bin/env python3
"""历史信号回放：用云端日线/小时线，对历史数据重跑规则，把过去本会触发的信号算出来。

分两类：
  日线级精确回放（gap / ma_cross / limit_event）：
    用云端日线全历史（2015 起），回放最近 DAILY_DAYS 天。与实时 run_daily 结果一致。
  小时级近似回放（fast_drop / slow_drop）：
    用云端 hour_bars（60分钟K线，滚动窗口约 4 个月）做滚动窗口近似。
    粒度比实时 3 秒粗——实时可能在一小时内触发多次，回放只抓跨小时的那几次。
  5分钟级规则（zscore_dev / vol_ratio / index_move）不回放（小时线太粗，会失真）。

回放信号推送到云端 signals 集合，带 hist=1 标志、rule 加 "_hist" 后缀，与实时信号区分。

用法:
  python3 legacy/cloudbase/backfill_signals.py            # 回放并推送
  python3 legacy/cloudbase/backfill_signals.py --dry      # 只算不推，打印统计
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "jobs"))

from monitor.quotes import to_market_code  # noqa: E402
from sync_data import web_stocks  # noqa: E402

API = "https://ljx-d1gjpcu23fa094e67.service.tcloudbase.com/api"
DAILY_DAYS = 365          # 日线级回放最近 1 年
HOUR_LIMIT = 320          # 小时级回放用全部可用小时线（~4 个月）
CST = timezone(timedelta(hours=8))


def load_cfg() -> dict:
    cfg = json.loads((HERE / "cloud_sync.json").read_text())
    return cfg


def load_watchlist() -> list[dict]:
    conf = json.loads((HERE / "config.json").read_text())
    return [{"symbol": w["symbol"], "name": w["name"], "type": "stock"}
            for w in conf["watchlist"]]


def api_get(path: str):
    r = requests.get(API + path, timeout=30)
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["data"]


def fetch_daily(symbol: str, limit: int = 400) -> list[dict]:
    return api_get(f"/daily?symbol={symbol}&limit={limit}")


def fetch_hour(symbol: str, limit: int = HOUR_LIMIT) -> list[dict]:
    return api_get(f"/hour?symbol={symbol}&limit={limit}")


# ----------------------------------------------------------------------
# 日线级规则（精确回放）
# ----------------------------------------------------------------------

def replay_gap(bars: list[dict], sigma_th: float = 0.5) -> list[dict]:
    """开盘缺口：|今开/昨收-1| >= sigma_th 倍日波动率。每天最多 1 次。"""
    out = []
    closes = [b["close"] for b in bars]
    for i in range(21, len(bars)):
        b = bars[i]
        prev_close, open_ = bars[i - 1]["close"], b["open"]
        if not prev_close or not open_:
            continue
        rets = [closes[j] / closes[j - 1] - 1 for j in range(i - 20, i)]
        mu = sum(rets) / len(rets)
        dsig = math.sqrt(sum((x - mu) ** 2 for x in rets) / len(rets))
        if dsig <= 0:
            continue
        g = open_ / prev_close - 1
        z = g / dsig
        if abs(z) >= sigma_th:
            direction = "高开" if g > 0 else "低开"
            hint = "缺口历史多回补，持仓可考虑兑现" if g > 0 else "缺口历史多回补，关注接回机会"
            msg = (f"【{direction}】{b.get('name', b['symbol'])}({b['symbol']}) 今开 {open_:.2f} "
                   f"缺口 {g:+.2%}（{abs(z):.1f}σ日波动）。{hint}")
            out.append({"symbol": b["symbol"], "date": b["date"], "ts_hour": "09:30",
                        "rule": "gap", "message": msg})
    return out


def replay_ma_cross(bars: list[dict], ma_window: int = 20) -> list[dict]:
    """均线交叉：收盘上穿/下穿 MA20。每天最多 1 次。"""
    out = []
    closes = [b["close"] for b in bars]
    n = ma_window
    for i in range(n, len(bars)):
        ma_prev = sum(closes[i - n:i]) / n
        ma_now = sum(closes[i - n + 1:i + 1]) / n
        prev_close, now_close = closes[i - 1], closes[i]
        b = bars[i]
        d = b["date"]
        if prev_close <= ma_prev and now_close > ma_now:
            msg = (f"【日线上穿MA{n}】{b.get('name', b['symbol'])}({b['symbol']}) {d} "
                   f"收盘 {now_close:.2f} 站上 MA{n}={ma_now:.2f}")
            out.append({"symbol": b["symbol"], "date": d, "ts_hour": "15:00",
                        "rule": "daily:ma_cross", "message": msg})
        elif prev_close >= ma_prev and now_close < ma_now:
            msg = (f"【日线下穿MA{n}】{b.get('name', b['symbol'])}({b['symbol']}) {d} "
                   f"收盘 {now_close:.2f} 跌破 MA{n}={ma_now:.2f}")
            out.append({"symbol": b["symbol"], "date": d, "ts_hour": "15:00",
                        "rule": "daily:ma_cross", "message": msg})
    return out


def replay_limit_event(bars: list[dict]) -> list[dict]:
    """封板/炸板/跌停/撬板。每天最多 1 次（取最强事件）。"""
    out = []
    for i in range(1, len(bars)):
        b = bars[i]
        prev_close = bars[i - 1]["close"]
        if not prev_close:
            continue
        sym = b["symbol"]
        ratio = 1.20 if sym.startswith(("300", "301", "688")) else 1.10
        up = round(prev_close * ratio, 2)
        down = round(prev_close * (2 - ratio), 2)
        price, high, low = b["close"], b["high"], b["low"]
        fb = 0.005
        msg = None
        name = b.get("name", sym)
        if price >= up - 1e-9:
            msg = f"【封涨停】{name}({sym}) 封住涨停 {up:.2f}"
        elif high >= up - 1e-9 and price < up * (1 - fb):
            msg = f"【炸板】{name}({sym}) 触及涨停 {up:.2f} 后回落，收盘 {price:.2f}"
        elif price <= down + 1e-9:
            msg = f"【封跌停】{name}({sym}) 封住跌停 {down:.2f}"
        elif low <= down + 1e-9 and price > down * (1 + fb):
            msg = f"【撬板】{name}({sym}) 触及跌停 {down:.2f} 后撬开，收盘 {price:.2f}"
        if msg:
            out.append({"symbol": sym, "date": b["date"], "ts_hour": "15:00",
                        "rule": "limit_event", "message": msg})
    return out


# ----------------------------------------------------------------------
# 小时级规则（近似回放）
# ----------------------------------------------------------------------

def _hour_ts(date_str: str) -> int:
    """hour bar 时间戳 YYYYMMDDHHmm -> unix 秒（CST）。"""
    dt = datetime.strptime(date_str, "%Y%m%d%H%M").replace(tzinfo=CST)
    return int(dt.timestamp())


def replay_window_drop(bars: list[dict], window_bars: int, abs_th: float,
                       pct_th: float, abs_switch: float, rule: str,
                       evt_up: str, evt_down: str, directions=("down",)) -> list[dict]:
    """滚动窗口涨跌近似：当前K线收盘 vs window_bars 根前收盘。"""
    out = []
    # 跨天：window 可能跨日，用绝对 bar 索引近似（忽略午间/隔夜），足够近似
    for i in range(window_bars, len(bars)):
        cur = bars[i]
        base = bars[i - window_bars]
        if not base["close"] or not cur["close"]:
            continue
        # 只在同一天内比较（避免隔夜跳空干扰窗口涨跌）
        if cur["date"][:8] != base["date"][:8]:
            continue
        delta_abs = cur["close"] - base["close"]
        delta_pct = delta_abs / base["close"] * 100
        use_abs = max(base["close"], cur["close"]) >= abs_switch
        th, delta = (abs_th, delta_abs) if use_abs else (pct_th, delta_pct)
        if abs(delta) < th:
            continue
        up = delta > 0
        if up and "up" not in directions:
            continue
        if not up and "down" not in directions:
            continue
        # 当日涨跌幅（用该日第一根K线开盘近似昨收基准不可得，跳过精确 pct）
        win_min = window_bars * 60
        evt = evt_up if up else evt_down
        verb = "涨" if up else "跌"
        d = f"{verb} {abs(delta_abs):.2f}元({delta_pct:+.2f}%)" if use_abs else f"{verb} {abs(delta_pct):.2f}%"
        name = cur.get("name", cur["symbol"])
        msg = (f"【{evt}】{name}({cur['symbol']}) {win_min}分钟{d}，现价 {cur['close']:.2f}")
        out.append({"symbol": cur["symbol"], "date": cur["date"][:8],
                    "date_full": cur["date"], "ts_hour": cur["date"][8:12],
                    "rule": rule, "message": msg, "_ts": _hour_ts(cur["date"])})
    # 冷却去重：同标的同规则同一天只保留最强的一次（最大 |delta|）
    by_day = {}
    for s in out:
        k = (s["symbol"], s["rule"], s["date"])
        if k not in by_day or abs(s.get("_delta", 0)) > abs(by_day[k].get("_delta", 0)):
            by_day[k] = s
    return list(by_day.values())


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------

def to_ts(date_str: str, hm: str) -> int:
    """YYYY-MM-DD + HH:MM -> unix 秒（CST）。"""
    dt = datetime.strptime(f"{date_str} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=CST)
    return int(dt.timestamp())


def push_signals(docs: list[dict], cfg: dict) -> int:
    if not docs:
        return 0
    total = 0
    BATCH = 200
    for i in range(0, len(docs), BATCH):
        chunk = docs[i:i + BATCH]
        r = requests.post(cfg["url"], json={"collection": "signals", "docs": chunk},
                          headers={"x-sync-token": cfg["token"]}, timeout=90)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"signals 推送失败: {data}")
        total += data.get("upserted", 0)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只算不推")
    args = ap.parse_args()
    cfg = None if args.dry else load_cfg()

    names = {w["symbol"]: w["name"] for w in web_stocks()}
    cutoff = (datetime.now() - timedelta(days=DAILY_DAYS)).strftime("%Y-%m-%d")

    all_docs = []
    stats = {}
    for sym, name in names.items():
        # 日线级
        daily = fetch_daily(sym, 400)
        for b in daily:
            b["name"] = name
        daily = [b for b in daily if b["date"] >= cutoff]
        sigs = []
        sigs += replay_gap(daily)
        sigs += replay_ma_cross(daily)
        sigs += replay_limit_event(daily)
        for s in sigs:
            s["ts"] = to_ts(s["date"], s["ts_hour"])
            s["rule"] = s["rule"] + "_hist" if not s["rule"].startswith("daily:") else s["rule"] + "_hist"
            s["hist"] = 1
        # 小时级
        hour = fetch_hour(sym)
        for b in hour:
            b["name"] = name
        hs = []
        hs += replay_window_drop(hour, 1, 0.4, 1.5, 15.0, "fast_drop", "快速拉升", "快速下挫", ("down", "up"))
        hs += replay_window_drop(hour, 2, 0.6, 2.5, 15.0, "slow_drop", "持续走强", "持续走弱", ("down",))
        for s in hs:
            s["ts"] = s["_ts"]
            s["rule"] = s["rule"] + "_hist"
            s["hist"] = 1
            s.pop("_ts", None)
            s.pop("_delta", None)
            s.pop("date_full", None)
        combined = sigs + hs
        stats[sym] = len(combined)
        all_docs += combined
        print(f"{sym} {name}: 日线级 {len(sigs)} 条 + 小时级 {len(hs)} 条 = {len(combined)}")

    # 加 _id（幂等 upsert）：symbol_rule_ts
    for d in all_docs:
        d["_id"] = f"{d['symbol']}_{d['rule']}_{d['ts']}"

    print(f"\n合计 {len(all_docs)} 条回放信号，覆盖 {len(stats)} 个标的")
    if args.dry:
        print("(dry 模式，未推送)")
        # 抽样打印
        for d in all_docs[:5]:
            print("  样例:", d["rule"], d["message"][:50])
        return
    n = push_signals(all_docs, cfg)
    print(f"已推送 {n} 条到云端 signals")


if __name__ == "__main__":
    main()
