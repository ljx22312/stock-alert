#!/usr/bin/env python3
"""下载中国波指 QVIX（沪深300/中证1000 期权隐含波动率）→ data/downloads/vix/*.csv
用法：python3 dl_vix.py
"""
import time
from pathlib import Path

import akshare as ak

OUT = Path(__file__).resolve().parents[1] / "data" / "downloads" / "vix"
OUT.mkdir(parents=True, exist_ok=True)

def main():
    jobs = [
        ("qvix_300", lambda: ak.index_option_300index_qvix()),
        ("qvix_1000", lambda: ak.index_option_1000index_qvix()),
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
