#!/usr/bin/env python3
"""下载基金数据（天天基金）→ data/downloads/fund/*.csv
覆盖：新成立基金发行、公募指数增强基金名单（沪深300/中证500/中证1000 指增基金识别后拉净值）。
用法：python3 dl_fund.py
"""
import time
from pathlib import Path

import akshare as ak

OUT = Path(__file__).resolve().parent / "data" / "downloads" / "fund"
OUT.mkdir(parents=True, exist_ok=True)

def main():
    jobs = [
        ("new_fund_issuance", lambda: ak.fund_new_found_em()),
        ("fund_rank_all", lambda: ak.fund_open_fund_rank_em(symbol="全部")),
    ]
    ok, fail = 0, []
    for name, fn in jobs:
        f = OUT / f"{name}.csv"
        try:
            df = fn()
            if df is None or df.empty:
                raise RuntimeError("空数据")
            df.to_csv(f, index=False, encoding="utf-8-sig")
            print(f"✅ {name}: {df.shape}")
            ok += 1
        except Exception as e:
            print(f"❌ {name}: {str(e)[:80]}")
            fail.append(name)
        time.sleep(1.0)
    print(f"\n完成: {ok}/{len(jobs)} 成功, 失败 {fail}")

if __name__ == "__main__":
    main()
