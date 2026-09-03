#!/usr/bin/env python3
"""一次性本地主库回填（bootstrap）：从数据源把全量种子写入本机 sqlite。

默认数据源 = CloudBase api（过渡期唯一一次读取；CloudBase 停用后如重建本机库，
可改用 115 推送重放 / 本机 CSV 重新入库，本脚本的 --base 可指向任意同形状源）。

用法：
  python3 server/bootstrap_local.py            # 全量回填（幂等 upsert，可重跑）
  python3 server/bootstrap_local.py --base http://127.0.0.1:8791/api   # 从本地服务回填（自举）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data_service as ds  # noqa: E402  复用建表/写入映射

DEFAULT_BASE = "https://ljx-d1gjpcu23fa094e67.service.tcloudbase.com/api"
FETCH_TTL = 60  # 单接口超时秒


def get(base, path):
    req = urllib.request.Request(base + path, headers={"User-Agent": "stockdesk-bootstrap/1.0"})
    with urllib.request.urlopen(req, timeout=FETCH_TTL) as r:
        return json.loads(r.read().decode("utf-8"))


def get_data(base, path):
    j = get(base, path)
    if j.get("error"):
        raise RuntimeError(f"{path}: {j['error']}")
    return j.get("data") or []


def fetch_many(base, paths, workers=8):
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(get_data, base, p): p for p in paths}
        for fut in futs:
            try:
                out[futs[fut]] = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"  ! 拉取失败 {futs[fut]}: {str(e)[:120]}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    t0 = time.time()
    ds.init_db()
    print(f"回填开始 base={base} db={ds.DB_PATH}")

    # 1. 股池目录
    stocks = get_data(base, "/stocks")
    n = ds.ingest_docs("stocks", stocks)[0]
    print(f"stocks: {len(stocks)} 条 -> {n}")
    syms = [s["symbol"] for s in stocks]
    by_type = {}
    for s in stocks:
        by_type.setdefault(s.get("type"), []).append(s["symbol"])

    # 2. 最新快照
    quote = get_data(base, "/quote")
    n = ds.ingest_docs("snapshots", quote)[0]
    print(f"snapshots: {len(quote)} 条 -> {n}")

    # 3. 最近信号
    sigs = get_data(base, "/signals?limit=500")
    n = ds.ingest_docs("signals", sigs)[0]
    print(f"signals: {len(sigs)} 条 -> {n}")

    # 4. 宏观指标（列表只带截尾序列，需逐 id 拉全量）
    macro_list = get_data(base, "/macro")
    ids = [m.get("id") for m in macro_list if m.get("id")]
    full = fetch_many(base, [f"/macro?id={i}" for i in ids])
    macro_docs = [d for i in ids for d in full.get(f"/macro?id={i}", [])]
    n = ds.ingest_docs("macro_indicators", macro_docs)[0]
    print(f"macro: {len(ids)} 个指标 -> {n}")

    # 5. 各标的日线（全目录，最多 2000 根/标的）
    daily_paths = [f"/daily?symbol={s}&limit=2000" for s in syms]
    daily = fetch_many(base, daily_paths)
    all_bars = [b for p in daily_paths for b in daily.get(p, [])]
    n = ds.ingest_docs("daily_bars", all_bars)[0]
    print(f"daily_bars: {len(syms)} 标的 {len(all_bars)} 根 -> {n}")

    # 6. 小时线/分时/量能分布（股票+指数；行业只有日频快照）
    ex = by_type.get("stock", []) + by_type.get("index", [])
    if ex:
        hour_paths = [f"/hour?symbol={s}&limit=2000" for s in ex]
        hour = fetch_many(base, hour_paths)
        bars = [b for p in hour_paths for b in hour.get(p, [])]
        n = ds.ingest_docs("hour_bars", bars)[0]
        print(f"hour_bars: {len(ex)} 标的 {len(bars)} 根 -> {n}")
        tick_paths = [f"/tick?symbol={s}" for s in ex]
        tick = fetch_many(base, tick_paths)
        frames = [b for p in tick_paths for b in tick.get(p, [])]
        n = ds.ingest_docs("ticks", frames)[0]
        print(f"ticks: {len(frames)} 帧 -> {n}")
        prof_paths = [f"/profile?symbol={s}" for s in ex]
        prof = fetch_many(base, prof_paths)
        rows = [b for p in prof_paths for b in prof.get(p, [])]
        n = ds.ingest_docs("vol_profile", rows)[0]
        print(f"vol_profile: {len(rows)} 条 -> {n}")

    stats = ds.api_stats()
    print(f"回填完成 耗时 {time.time() - t0:.0f}s 库内计数: {stats}")


if __name__ == "__main__":
    main()
