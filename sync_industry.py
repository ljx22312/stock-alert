#!/usr/bin/env python3
"""申万一级行业指数 → 云端（复用 daily_bars / snapshots / stocks 集合）。

- 日线：读 data/downloads/sw_industry/*.csv（31 个，全历史）→ daily_bars
- 实时：akshare index_realtime_sw → snapshots（覆盖式）
- 目录：stocks 集合加 31 条 type=industry

用法：
  python3 sync_industry.py            # 全量推送
  python3 sync_industry.py --daily-only   # 只推日线（收盘后 cron 用）
"""
import argparse
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sync_cloud import push, load_cfg, load_meta, save_meta, cloud_rows  # noqa: E402

SW_DIR = HERE / "data" / "downloads" / "sw_industry"

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


def sync_daily(cfg) -> int:
    """读 31 个 CSV → daily_bars，增量推送（id=symbol_date，幂等 upsert）。

    行业 CSV 为全历史（6400+ 根），此前每天全量重推 ≈ 20 万次写。
    现在按水位只推新增日期：首次从云端读回最新日期建水位，之后每天仅 ~31 行。
    """
    meta = load_meta("industry_meta.json")
    total = 0
    for code, name in LEVEL1.items():
        f = SW_DIR / f"{code}_{name}.csv"
        if not f.exists():
            print(f"  ! {code} {name} CSV 缺失，跳过")
            continue
        import csv
        docs = []
        with open(f, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                try:
                    docs.append({
                        "symbol": code, "date": row["日期"],
                        "open": float(row["开盘"]), "close": float(row["收盘"]),
                        "high": float(row["最高"]), "low": float(row["最低"]),
                        "volume": float(row["成交量"]), "amount": float(row["成交额"]),
                    })
                except (ValueError, KeyError):
                    continue
        if not docs:
            continue
        if code not in meta:
            cloud = cloud_rows("daily", code)
            meta[code] = cloud[-1]["date"] if cloud else ""
        last = meta[code]
        new = [d for d in docs if d["date"] > last]
        if new:
            n = push("daily_bars", new, cfg)
            total += n
            print(f"daily: {code} {name} -> {n} 根增量（截至 {new[-1]['date']}）")
        else:
            print(f"daily: {code} {name} 无新 bar（水位 {last}），跳过")
        meta[code] = docs[-1]["date"]
        save_meta("industry_meta.json", meta)
    return total


def sync_snapshot(cfg) -> int:
    """由 CSV 最新两日收盘推导快照（行业指数无免费实时一级数据，收盘后更新即可）。"""
    import csv
    docs = []
    for code, name in LEVEL1.items():
        f = SW_DIR / f"{code}_{name}.csv"
        if not f.exists():
            continue
        with open(f, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))[-2:]
        if len(rows) < 2:
            continue
        last, prev = rows[-1], rows[-2]
        try:
            price = float(last["收盘"])
            prev_close = float(prev["收盘"])
            docs.append({
                "symbol": code, "name": name,
                "price": price, "prev_close": prev_close,
                "open": float(last["开盘"]), "high": float(last["最高"]),
                "low": float(last["最低"]),
                "volume_hand": float(last["成交量"]), "amount_wan": float(last["成交额"]),
                "pct_chg": (price / prev_close - 1) * 100 if prev_close else 0.0,
                "turnover_rate": 0.0, "quote_time": last["日期"],
            })
        except (ValueError, KeyError):
            continue
    n = push("snapshots", docs, cfg)
    print(f"snapshot: 推送 {n} 条行业快照（收盘价）")
    return n


def sync_stocks(cfg) -> int:
    """目录 → stocks 集合（type=industry）。"""
    docs = [{"symbol": code, "name": name, "type": "industry"} for code, name in LEVEL1.items()]
    n = push("stocks", docs, cfg)
    print(f"stocks: 推送 {n} 条行业目录")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-only", action="store_true")
    args = ap.parse_args()
    cfg = load_cfg()
    sync_daily(cfg)
    if not args.daily_only:
        time.sleep(1)
        sync_snapshot(cfg)
        sync_stocks(cfg)
    print("完成")


if __name__ == "__main__":
    main()
