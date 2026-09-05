"""最近一周（5 个交易日）盘中规则回测验证。

复用 src/strategies.py 的生产规则代码，用腾讯 m5 K线（720 根 ≈ 15 个交易日）
逐 bar 模拟 run_monitor 的评估流程：
  - 每根 5 分钟 bar 视为一次网格点（等价于 3 秒轨迹抽稀后的最后一个样本）
  - vol_ratio 基线：只用测试日**之前**的 10 个交易日计算，避免前视
  - gap 的 open/prev_close 用 SQLite 日线库的真实值
  - 冷却去重按 (标的, 规则) 跨天持续，与线上 signal_log 行为一致

已知近似：limit_event 的日内 high/low 用累计 bar 收盘价代替真实最高/最低，
可能漏掉 bar 内触板瞬间（蓝筹池三个月 0 次，影响可忽略）。

用法: /usr/bin/python3 backtest_week.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests

from monitor.daily import compute_vol_profile, _market_code
from monitor.store import Store
from monitor.strategies import (DAILY_RULES, INDEX_RULES, INTRADAY_RULES,
                            RuleContext)

MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
M5_COUNT = 720          # 15 个交易日：10 天基线 + 5 天测试
BASELINE_DAYS = 10
TEST_DAYS = 5           # 最近 5 个交易日


def fetch_m5(code: str, count: int = M5_COUNT) -> list[dict]:
    """code 为带市场前缀代码（sh600519 / sh000001），返回含 ts 的 bar 列表。"""
    r = requests.get(MKLINE_URL, params={"param": f"{code},m5,,{count}"}, timeout=15)
    rows = r.json()["data"][code].get("m5") or []
    bars = []
    for x in rows:
        dt = datetime.strptime(x[0], "%Y%m%d%H%M")
        bars.append({
            "ts": int(dt.timestamp()),
            "date": f"{x[0][:4]}-{x[0][4:6]}-{x[0][6:8]}",
            "hm": f"{x[0][8:10]}:{x[0][10:12]}",
            "close": float(x[2]),
            "volume": float(x[5]),
        })
    return bars


def simulate_symbol(symbol, name, bars, daily_all, rules, params_cfg, cooldown_default):
    """逐 bar 回放一只股票的盘中规则，返回 [(ts, rule, msg), ...]。"""
    by_date: dict[str, list[dict]] = {}
    for b in bars:
        by_date.setdefault(b["date"], []).append(b)
    dates = sorted(by_date)
    test_dates = dates[-TEST_DAYS:]

    signals = []
    last_fire: dict[str, int] = {}   # rule -> 上次触发 ts（跨天持续）
    for d in test_dates:
        daily_hist = [b for b in daily_all if b["date"] < d]
        day_bar = next((b for b in daily_all if b["date"] == d), None)
        if len(daily_hist) < 21 or day_bar is None:
            continue
        prev_close = daily_hist[-1]["close"]
        # 基线：测试日之前的 BASELINE_DAYS 个交易日的 m5
        prior_dates = [x for x in dates if x < d][-BASELINE_DAYS:]
        profile = compute_vol_profile(
            [b for x in prior_dates for b in by_date[x]], days=BASELINE_DAYS)

        intraday = []
        cum_vol = 0.0
        run_high, run_low = None, None
        for bar in by_date[d]:
            cum_vol += bar["volume"]
            intraday.append((bar["ts"], bar["close"], cum_vol))
            run_high = bar["close"] if run_high is None else max(run_high, bar["close"])
            run_low = bar["close"] if run_low is None else min(run_low, bar["close"])
            quote = {
                "price": bar["close"], "open": day_bar["open"],
                "high": run_high, "low": run_low, "prev_close": prev_close,
                "pct_chg": (bar["close"] - prev_close) / prev_close * 100,
            }
            for rule_name, rule_fn in rules:
                params = params_cfg.get(rule_name, {})
                ctx = RuleContext(
                    symbol=symbol, name=name, quote=quote,
                    intraday=list(intraday), daily=daily_hist,
                    params=params, vol_profile=profile,
                )
                try:
                    msg = rule_fn(ctx)
                except Exception as e:
                    print(f"  [异常] {rule_name} @ {symbol} {d} {bar['hm']}: {e}")
                    continue
                if msg:
                    cd = params.get("cooldown_minutes", cooldown_default)
                    last = last_fire.get(rule_name)
                    if last is not None and bar["ts"] - last < cd * 60:
                        continue
                    last_fire[rule_name] = bar["ts"]
                    signals.append((bar["ts"], rule_name, msg))
    return signals


def main():
    cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))
    store = Store(cfg["db_path"])
    params_cfg = cfg["params"]
    cd_default = cfg["cooldown_minutes"]

    intraday_rules = [(n, INTRADAY_RULES[n]) for n in cfg["intraday_rules_enabled"]]
    index_rules = [(n, INDEX_RULES[n]) for n in cfg.get("index_rules_enabled", [])]
    daily_rules = [(n, DAILY_RULES[n]) for n in cfg["daily_rules_enabled"]]

    all_signals: list[tuple] = []   # (ts, symbol, rule, msg)
    test_dates: set[str] = set()

    for item in cfg["watchlist"]:
        symbol, name = item["symbol"], item["name"]
        bars = fetch_m5(_market_code(symbol))
        test_dates.update(sorted({b["date"] for b in bars})[-TEST_DAYS:])
        daily_all = store.get_daily(symbol)
        sigs = simulate_symbol(symbol, name, bars, daily_all,
                               intraday_rules, params_cfg, cd_default)
        all_signals.extend((ts, symbol, rule, msg) for ts, rule, msg in sigs)
        print(f"{name}({symbol}): {len(sigs)} 条盘中信号")
        time.sleep(0.3)

    # 指数规则
    if cfg.get("index_code") and index_rules:
        icode = cfg["index_code"]
        ibars = fetch_m5(icode)
        by_date: dict[str, list[dict]] = {}
        for b in ibars:
            by_date.setdefault(b["date"], []).append(b)
        last_fire: dict[str, int] = {}
        for d in sorted(by_date)[-TEST_DAYS:]:
            traj = []
            for bar in by_date[d]:
                traj.append((bar["ts"], bar["close"], 0.0))
                for rule_name, rule_fn in index_rules:
                    params = params_cfg.get(rule_name, {})
                    ctx = RuleContext(symbol=icode, name="上证指数", quote={},
                                      intraday=list(traj), params=params)
                    msg = rule_fn(ctx)
                    if msg:
                        cd = params.get("cooldown_minutes", cd_default)
                        last = last_fire.get(rule_name)
                        if last is not None and bar["ts"] - last < cd * 60:
                            continue
                        last_fire[rule_name] = bar["ts"]
                        all_signals.append((bar["ts"], icode, rule_name, msg))

    # 日线规则（收盘后视角，逐日回放）
    for item in cfg["watchlist"]:
        symbol, name = item["symbol"], item["name"]
        daily_all = store.get_daily(symbol)
        for d in sorted(test_dates):
            hist = [b for b in daily_all if b["date"] <= d]
            for rule_name, rule_fn in daily_rules:
                ctx = RuleContext(symbol=symbol, name=name, quote={}, daily=hist,
                                  params=params_cfg.get(rule_name, {}))
                msg = rule_fn(ctx)
                if msg:
                    ts = int(datetime.strptime(d, "%Y-%m-%d").timestamp()) + 15 * 3600
                    all_signals.append((ts, symbol, f"daily:{rule_name}", msg))

    # 汇总
    all_signals.sort()
    days = sorted(test_dates)
    print(f"\n===== 回测区间: {days[0]} ~ {days[-1]} ({len(days)} 个交易日) =====")
    counts: dict[str, int] = {}
    for _, _, rule, _ in all_signals:
        counts[rule] = counts.get(rule, 0) + 1
    expect = {"zscore_dev": 8.8, "vol_ratio": 7.6, "gap": 2.2,
              "index_move": 1.0, "limit_event": 0}
    print(f"{'规则':<16}{'本周触发':>8}{'标定(次/周)':>14}")
    for rule in sorted(set(counts) | set(expect)):
        print(f"{rule:<16}{counts.get(rule, 0):>8}{expect.get(rule, '-'):>14}")
    print("\n----- 信号明细（按时间） -----")
    for ts, symbol, rule, msg in all_signals:
        print(f"{datetime.fromtimestamp(ts):%m-%d %H:%M}  {msg}")


if __name__ == "__main__":
    main()
