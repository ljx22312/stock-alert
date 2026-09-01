#!/usr/bin/env python3
"""下载申万一级行业指数日线全历史（31 个）→ data/downloads/sw_industry/*.csv
数据源：乐咕乐股（akshare index_hist_sw）。断点续传（已存在的跳过）。
用法：python3 dl_sw_industry.py
"""
import sys
import time
from pathlib import Path

import akshare as ak

OUT = Path(__file__).resolve().parent / "data" / "downloads" / "sw_industry"
OUT.mkdir(parents=True, exist_ok=True)

LEVEL1 = {  # 申万一级行业（2021 版 31 个）
    "801010": "农林牧渔", "801030": "基础化工", "801040": "钢铁", "801050": "有色金属",
    "801080": "电子", "801110": "家用电器", "801120": "食品饮料", "801130": "纺织服饰",
    "801140": "轻工制造", "801150": "医药生物", "801160": "公用事业", "801170": "交通运输",
    "801180": "房地产", "801200": "商贸零售", "801210": "社会服务", "801230": "综合",
    "801710": "建筑材料", "801720": "建筑装饰", "801730": "电力设备", "801740": "国防军工",
    "801750": "计算机", "801760": "传媒", "801770": "通信", "801780": "银行",
    "801790": "非银金融", "801880": "汽车", "801890": "机械设备", "801950": "煤炭",
    "801960": "石油石化", "801970": "环保", "801980": "美容护理",
}

def main():
    ok, fail = 0, []
    for code, name in LEVEL1.items():
        f = OUT / f"{code}_{name}.csv"
        if f.exists() and f.stat().st_size > 1000:
            print(f"跳过 {code} {name}（已存在）")
            ok += 1
            continue
        for attempt in range(4):
            try:
                df = ak.index_hist_sw(symbol=code, period="day")
                if df is None or df.empty:
                    raise RuntimeError("空数据")
                df.to_csv(f, index=False, encoding="utf-8-sig")
                print(f"✅ {code} {name}: {len(df)} 根 ({df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]})")
                ok += 1
                break
            except Exception as e:
                print(f"  ⚠ {code} 第{attempt+1}次失败: {str(e)[:60]}")
                time.sleep(5 * (attempt + 1))
        else:
            fail.append(code)
        time.sleep(1.0)  # 限流保护
    print(f"\n完成: {ok}/31 成功, 失败 {fail}")

if __name__ == "__main__":
    main()
