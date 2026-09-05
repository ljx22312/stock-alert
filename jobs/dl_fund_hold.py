#!/usr/bin/env python3
"""下载主动偏股基金定期报告持仓（基金行为分析用）→ data/downloads/fund/hold/{code}.csv
数据源：天天基金（fund_portfolio_hold_em）。断点续传。
范围：fund_rank_all.csv 中 股票型/混合型 且非指数/债/货币/QDII/FOF/REIT 的基金。
用法：python3 dl_fund_hold.py [--limit 50] [--start 4600] [--end 0]
  --limit  只跑前 N 只（调试）
  --start/--end  分片范围（多机并行时各跑一段；end=0 表示到末尾）
"""
import argparse
import sys
import time
from pathlib import Path

import akshare as ak

HERE = Path(__file__).resolve().parents[1]
RANK = HERE / "data" / "downloads" / "fund" / "fund_rank_all.csv"
OUT = HERE / "data" / "downloads" / "fund" / "hold"
OUT.mkdir(parents=True, exist_ok=True)

def candidates():
    import pandas as pd
    df = ak.fund_name_em()  # 27718 只，含基金类型
    df = df[df["基金代码"].str.len() == 6]
    # 主动偏股：偏股混合 + 灵活配置 + 股票型（去掉指数/债/货币/QDII/FOF/REIT）
    mask = df["基金类型"].isin(["混合型-偏股", "混合型-灵活", "股票型"])
    return df[mask]["基金代码"].tolist(), df[mask]["基金简称"].tolist()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=0)
    args = ap.parse_args()
    codes, names = candidates()
    total = len(codes)
    end = args.end or total
    codes, names = codes[args.start:end], names[args.start:end]
    print(f"主动偏股基金候选: {total} 只，本片 {args.start}~{end} = {len(codes)} 只")
    if args.limit:
        codes, names = codes[:args.limit], names[:args.limit]

    ok = skip = fail = 0
    t0 = time.time()
    for i, (code, name) in enumerate(zip(codes, names), 1):
        f = OUT / f"{code}.csv"
        if f.exists() and f.stat().st_size > 500:
            skip += 1
        else:
            try:
                df = ak.fund_portfolio_hold_em(symbol=code, date="")  # 空 = 全部季度
                if df is None or df.empty:
                    df = ak.fund_portfolio_hold_em(symbol=code, date="2026")
                if df is not None and not df.empty:
                    df.to_csv(f, index=False, encoding="utf-8-sig")
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
        if i % 50 == 0 or i == len(codes):
            el = time.time() - t0
            eta = el / i * (len(codes) - i) / 60 if i else 0
            print(f"[{i}/{len(codes)}] 新增{ok} 跳过{skip} 失败{fail} 已用{el/60:.1f}分 预计剩余{eta:.0f}分")
        time.sleep(0.3)
    print(f"\n完成: 新增 {ok}, 跳过 {skip}, 失败 {fail}")

if __name__ == "__main__":
    main()
