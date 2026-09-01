#!/usr/bin/env python3
"""下载全A季度业绩报表（归母净利润等，A股景气 Nowcasting 输入）→ data/downloads/financial/*.csv
数据源：东财数据中心（本地可达）。覆盖 2019Q4 ~ 最新季度，每季度一份。
用法：python3 dl_financial.py
"""
import time
from pathlib import Path

import akshare as ak

OUT = Path(__file__).resolve().parent / "data" / "downloads" / "financial"
OUT.mkdir(parents=True, exist_ok=True)

# 季度报告截止日（2019Q4 ~ 2026Q2）
DATES = ["20191231", "20200331", "20200630", "20200930", "20201231",
         "20210331", "20210630", "20210930", "20211231",
         "20220331", "20220630", "20220930", "20221231",
         "20230331", "20230630", "20230930", "20231231",
         "20240331", "20240630", "20240930", "20241231",
         "20250331", "20250630", "20250930", "20251231",
         "20260331", "20260630"]

def main():
    ok, fail = 0, []
    for d in DATES:
        f = OUT / f"yjbb_{d}.csv"
        if f.exists() and f.stat().st_size > 1000:
            print(f"跳过 {d}")
            ok += 1
            continue
        for attempt in range(4):
            try:
                df = ak.stock_yjbb_em(date=d)
                if df is None or df.empty:
                    raise RuntimeError("空数据")
                df.to_csv(f, index=False, encoding="utf-8-sig")
                print(f"✅ {d}: {df.shape}")
                ok += 1
                break
            except Exception as e:
                print(f"  ⚠ {d} 第{attempt+1}次: {str(e)[:60]}")
                time.sleep(5 * (attempt + 1))
        else:
            fail.append(d)
        time.sleep(1.0)
    print(f"\n完成: {ok}/{len(DATES)} 成功, 失败 {fail}")

if __name__ == "__main__":
    main()
