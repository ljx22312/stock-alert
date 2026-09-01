#!/usr/bin/env python3
"""下载宏观指标（统计局/人行/中国货币网）→ data/downloads/macro/*.csv
覆盖：M1/M2 货币供应、CPI、PPI、PMI、社融、LPR、工业增加值、国债收益率、两融余额。
用法：python3 dl_macro.py
"""
import time
from pathlib import Path

import akshare as ak

OUT = Path(__file__).resolve().parent / "data" / "downloads" / "macro"
OUT.mkdir(parents=True, exist_ok=True)

JOBS = [
    ("money_supply", lambda: ak.macro_china_money_supply()),
    ("cpi_monthly", lambda: ak.macro_china_cpi_monthly()),
    ("ppi_yoy", lambda: ak.macro_china_ppi()),
    ("pmi", lambda: ak.macro_china_pmi()),
    ("social_financing", lambda: ak.macro_china_shrzgm()),
    ("lpr", lambda: ak.macro_china_lpr()),
    ("industrial_production_yoy", lambda: ak.macro_china_industrial_production_yoy()),
    ("treasury_yield", lambda: ak.bond_zh_us_rate(start_date="20100101")),
    ("margin_sh", lambda: ak.macro_china_market_margin_sh()),
    ("margin_sz", lambda: ak.macro_china_market_margin_sz()),
    ("shibor", lambda: ak.macro_china_shibor_all()),
]

def main():
    ok, fail = 0, []
    for name, fn in JOBS:
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
    print(f"\n完成: {ok}/{len(JOBS)} 成功, 失败 {fail}")

if __name__ == "__main__":
    main()
