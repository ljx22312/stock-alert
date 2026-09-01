#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在【你的桌面电脑】上运行：下载全A股票日线历史（东方财富 push2his 接口）。

背景：云端机器连不上东方财富行情接口，你的桌面能连（已实测 HTTP 200）。
这个脚本用 requests 直连东财（不依赖 akshare），下载全A 5000+ 只股票的前复权日线。

运行方式（Windows）：
  cd D:\Desktop
  pip install requests          # 若没有
  python dl_eastmoney.py        # 全量下载（约 40-90 分钟，可中断续传）
  python dl_eastmoney.py --limit 200   # 先下载 200 只试试

下载完成后把整个 eastmoney_data 文件夹传到云端服务器：
  scp -r D:\Desktop\eastmoney_data ubuntu@82.156.15.12:~/data/
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime

import requests

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eastmoney_data")
START = "20150101"   # 起始日期
END = "20990101"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
import random

def sleep_jitter():
    """0.6~1.0s 随机间隔，降低被风控概率。"""
    time.sleep(random.uniform(0.6, 1.0))

def get_stock_list(limit=0):
    """全A股票列表（按总市值降序，limit=0 表示全部）→ [(代码, 名称)]。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    stocks = []
    page = 1
    while True:
        params = {
            "pn": page, "pz": 500, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f20",  # 按总市值排序
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f12,f14",  # 代码、名称
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        data = (r.json().get("data") or {}).get("diff") or []
        if not data:
            break
        for d in data:
            stocks.append((d["f12"], d["f14"]))
        total = (r.json().get("data") or {}).get("total", 0)
        if page * 500 >= total or (limit and len(stocks) >= limit):
            break
        page += 1
        sleep_jitter()
    return stocks[:limit] if limit else stocks

def secid(code):
    return f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"

def download_stock(code, name):
    fpath = os.path.join(OUT_DIR, f"{code}_{name}.csv")
    if os.path.exists(fpath) and os.path.getsize(fpath) > 500:
        return "skip"
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    klines = []
    # 失败退避重试：遇空响应/风控，等待后重试（最长约 4 分钟）
    for attempt in range(6):
        try:
            params = {
                "secid": secid(code), "klt": 101, "fqt": 1,
                "beg": START, "end": END, "lmt": 1000000,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            }
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            rows = (r.json().get("data") or {}).get("klines") or []
            if rows:
                klines = rows
                break
            print(f"    ⚠ {code} 第{attempt+1}次空响应（可能风控），等待 {30*(attempt+1)}s 重试")
            time.sleep(30 * (attempt + 1))
        except Exception as e:
            print(f"    ⚠ {code} 第{attempt+1}次异常: {str(e)[:50]}，等待 {30*(attempt+1)}s")
            time.sleep(30 * (attempt + 1))
    if not klines:
        return "empty"
    with open(fpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "close", "high", "low", "volume", "amount"])
        for line in klines:
            w.writerow(line.split(","))
    return "ok"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="只下载市值最大的前 N 只（建议先 1800，覆盖主要指数权重股）")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"拉取全A股票列表（按总市值排序）…")
    stocks = get_stock_list(args.limit)
    print(f"共 {len(stocks)} 只股票（{'全部' if not args.limit else '市值Top' + str(args.limit)}）")

    ok = skip = fail = empty = 0
    t0 = time.time()
    for i, (code, name) in enumerate(stocks, 1):
        try:
            res = download_stock(code, name)
            if res == "skip":
                skip += 1
            elif res == "ok":
                ok += 1
            elif res == "empty":
                empty += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
        if i % 50 == 0 or i == len(stocks):
            el = time.time() - t0
            eta = el / i * (len(stocks) - i) / 60
            print(f"[{i}/{len(stocks)}] 新增{ok} 跳过{skip} 失败{fail} 空{empty} 已用{el/60:.1f}分 预计剩余{eta:.0f}分")
        sleep_jitter()
    print(f"\n完成: 新增 {ok}, 跳过 {skip}, 失败 {fail}, 空响应 {empty}")
    print(f"数据目录: {OUT_DIR}")
    print("上传到云端服务器: scp -r " + OUT_DIR + r" ubuntu@82.156.15.12:~/data/")

if __name__ == "__main__":
    main()
