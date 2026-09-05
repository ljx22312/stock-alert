"""收盘后日线任务：更新日线库 + 同时段量基线 + 评估日线规则 + 推送汇总。

建议 crontab（交易日 15:35 执行）:
  35 15 * * 1-5 cd /home/admin/stock-alert && /usr/bin/python3 monitor/run_daily.py >> logs/daily.log 2>&1
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monitor.daily import compute_vol_profile, fetch_daily_bars, fetch_m5_bars
from monitor.notify import Notifier
from monitor.store import Store
from monitor.strategies import DAILY_RULES, RuleContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(),
                              logging.FileHandler(ROOT / "logs" / "daily.log", encoding="utf-8")])
log = logging.getLogger("daily")


def main():
    cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))
    store = Store(cfg["db_path"])
    notifier = None
    if cfg.get("push_enabled", True):
        notifier = Notifier(cfg["wxpusher_config"], cfg["recipient"])
    rules = [(n, DAILY_RULES[n]) for n in cfg["daily_rules_enabled"] if n in DAILY_RULES]

    messages = []
    for item in cfg["watchlist"]:
        symbol, name = item["symbol"], item["name"]
        try:
            bars = fetch_daily_bars(symbol)
            if not bars:
                log.warning("%s 日线拉取为空", symbol)
                continue
            store.upsert_daily(symbol, bars)
            log.info("%s(%s) 日线入库 %d 根, 最新 %s 收 %.2f",
                     name, symbol, len(bars), bars[-1]["date"], bars[-1]["close"])
        except Exception as e:
            log.error("%s(%s) 日线更新失败: %s", name, symbol, e)
            continue

        # 同时段量基线（vol_ratio 规则用）
        try:
            m5 = fetch_m5_bars(symbol)
            if m5:
                profile = compute_vol_profile(m5)
                store.save_vol_profile(symbol, profile)
                log.info("%s(%s) 量基线更新 %d 个时段", name, symbol, len(profile))
        except Exception as e:
            log.warning("%s(%s) 量基线更新失败: %s", name, symbol, e)

        daily = store.get_daily(symbol)
        for rule_name, rule_fn in rules:
            ctx = RuleContext(
                symbol=symbol, name=name, quote={}, daily=daily,
                params=cfg["params"].get(rule_name, {}),
            )
            try:
                msg = rule_fn(ctx)
            except Exception as e:
                log.warning("规则 %s 在 %s 上异常: %s", rule_name, symbol, e)
                continue
            if msg and not store.in_cooldown(symbol, f"daily:{rule_name}", 60 * 20):
                # 日线信号同一天只推一次：用 20 小时冷却实现
                store.log_signal(symbol, f"daily:{rule_name}", msg)
                messages.append(msg)

    if messages:
        summary = "\n\n".join(messages)
        if notifier:
            ok = notifier.send(summary, summary=f"日线信号 {len(messages)} 条")
            log.info("推送 %d 条日线信号: %s", len(messages), "成功" if ok else "失败")
        else:
            log.info("push_enabled=false, 记录但不推送 %d 条日线信号", len(messages))
            for m in messages:
                print(f"[DRY-RUN 推送] {m}")
    else:
        log.info("今日无日线信号")


if __name__ == "__main__":
    main()
