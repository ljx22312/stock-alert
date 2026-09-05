#!/usr/bin/env python3
"""下载 FRED 序列（fredgraph.csv 通道，远程机实测可用）→ data/downloads/fred/*.csv
覆盖美元流动性指数输入：美联储总资产、隔夜逆回购、美元指数、美债、VIX、联邦基金利率。
用法：python3 jobs/dl_fred.py [--start 2015-01-01]
"""
import argparse
import time
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parents[1] / "data" / "downloads" / "fred"
OUT.mkdir(parents=True, exist_ok=True)

# 序列ID → 说明
SERIES = {
    "WALCL": "美联储总资产(百万美元)",
    "RRPONTSYD": "隔夜逆回购用量(百万美元)",
    "DTWEXBGS": "美元广义指数",
    "DGS10": "美债10年期收益率(%)",
    "DGS2": "美债2年期收益率(%)",
    "T10Y2Y": "美债10-2年期限利差(%)",
    "DFF": "联邦基金利率(%)",
    "VIXCLS": "VIX恐慌指数",
    "MOVE": "MOVE债券波动率指数",
}

def fetch(sid: str, start: str) -> list[str]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
    for attempt in range(4):
        try:
            r = requests.get(url, params={"cosd": start}, timeout=30)
            r.raise_for_status()
            lines = r.text.strip().splitlines()
            if len(lines) < 2:
                raise RuntimeError("空响应")
            return lines
        except Exception as e:
            print(f"  ⚠ {sid} 第{attempt+1}次失败: {str(e)[:60]}")
            time.sleep(4 * (attempt + 1))
    return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2010-01-01")
    args = ap.parse_args()
    ok, fail = 0, []
    for sid, desc in SERIES.items():
        f = OUT / f"{sid}.csv"
        lines = fetch(sid, args.start)
        if lines:
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"✅ {sid} {desc}: {len(lines)-1} 行 ({lines[1].split(',')[0]} ~ {lines[-1].split(',')[0]})")
            ok += 1
        else:
            fail.append(sid)
        time.sleep(0.5)
    print(f"\n完成: {ok}/{len(SERIES)} 成功, 失败 {fail}")

if __name__ == "__main__":
    main()
