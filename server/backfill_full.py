#!/usr/bin/env python3
"""历史补齐：从 CloudBase NoSQL 网关把"目录内标的全历史"镜像到本机主库。

背景：CloudBase 云函数 api 单次读取有 1000 条上限，本机 bootstrap 只拿到每标的
最新 ~1000 根日线；而云端集合里目录内标的自 2015 起就有全历史。本脚本用 DB HTTP
网关（Bearer CB_API_KEY）按游标分页把以下三类数据补全到本地（幂等，可重跑）：
  1) daily_bars 目录内 72 标的全历史（2015→今）
  2) signals 全量历史（不只最新 500）
  3) ticks 全部分时帧（含迁移日之前的交易日）
目录外的历史残留（早期测试/旧标的）不在本脚本范围，网页从不查询，无需镜像。

用法：python3 server/backfill_full.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import data_service as ds  # noqa: E402

GW = ("https://ljx-d1gjpcu23fa094e67.api.tcloudbasegateway.com/v1/database/"
      "instances/(default)/databases/(default)")


def load_key():
    # 优先 server/.env，其次 stock-ai/.env
    for p in (HERE / ".env", REPO.parent / "stock-ai" / ".env"):
        try:
            for line in p.read_text().splitlines():
                if line.startswith("CB_API_KEY="):
                    return line.split("=", 1)[1].strip()
        except FileNotFoundError:
            pass
    return os.environ.get("CB_API_KEY", "")


KEY = load_key()


def unwrap(v):
    """解 CloudBase 网关包装：{$numberInt/$numberLong/$numberDouble/$date: x} → 数值/毫秒。"""
    if isinstance(v, dict):
        if len(v) == 1 and next(iter(v)) in (
                "$numberInt", "$numberLong", "$numberDouble", "$numberDecimal"):
            val = next(iter(v.values()))
            return int(val) if next(iter(v)) != "$numberDouble" else float(val)
        if len(v) == 1 and "$date" in v:
            return unwrap(v["$date"])
        return {k: unwrap(x) for k, x in v.items()}
    if isinstance(v, list):
        return [unwrap(x) for x in v]
    return v


def gw_get(collection: str, query: dict, order: list, limit: int = 1000):
    q = urllib.parse.quote(json.dumps(query, ensure_ascii=False))
    o = urllib.parse.quote(json.dumps(order, ensure_ascii=False))
    url = f"{GW}/collections/{collection}/documents?query={q}&order={o}&limit={limit}"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8")).get("list") or []


def page_all(collection: str, base_query: dict, order: list, cursor_field: str):
    """按 order 方向以 cursor 严格小于/大于分页拉全量（依赖字段无并列或并列可接受）。"""
    cur = None
    while True:
        query = dict(base_query)
        if cur is not None:
            query[cursor_field] = {"$lt": cur} if order[0]["direction"] == "desc" else {"$gt": cur}
        rows = gw_get(collection, query, order)
        yield rows
        if len(rows) < 1000:
            return
        last = unwrap(rows[-1].get(cursor_field))
        if last == cur:
            return  # 防死循环
        cur = last


def fetch(collection, base_query, order, cursor_field):
    out = []
    for rows in page_all(collection, base_query, order, cursor_field):
        out.extend(unwrap(x) for x in rows)
    return out


def fetch_tiesafe(collection, order, cursor_field):
    """并列字段可能跨页边界：用 $lte + 按 _id 去重翻页，避免丢行。"""
    seen, out, cur = set(), [], None
    while True:
        query = {} if cur is None else {cursor_field: {"$lte": cur}}
        rows = gw_get(collection, query, order)
        fresh = []
        for x in rows:
            x = unwrap(x)
            if x.get("_id") not in seen:
                seen.add(x.get("_id"))
                fresh.append(x)
        out.extend(fresh)
        if len(rows) < 1000 or not fresh:
            break
        cur = min(unwrap(r)[cursor_field] for r in rows)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("sections", nargs="*", default=["daily", "signals", "ticks"])
    args = ap.parse_args()
    todo = args.sections or ["daily", "signals", "ticks"]
    if not KEY:
        raise SystemExit("缺少 CB_API_KEY（在 stock-ai/.env 或 server/.env）")
    t0 = time.time()
    ds.init_db()
    conn = ds.connect()
    dirs = [r[0] for r in conn.execute("SELECT symbol FROM stocks")]
    conn.close()
    print(f"补齐开始 目录标的 {len(dirs)} 个 任务 {todo}", flush=True)

    # 1) daily_bars 目录内全历史
    if "daily" in todo:
        added = 0
        for i, s in enumerate(dirs):
            rows = fetch("daily_bars", {"symbol": s},
                         [{"field": "date", "direction": "desc"}], "date")
            if not rows:
                continue
            n = ds.ingest_docs("daily_bars", rows)[0]
            added += n
            if i % 10 == 0 or i == len(dirs) - 1:
                print(f"  daily[{i + 1}/{len(dirs)}] {s}: 库内 {n} 根（含旧历史）", flush=True)
        print(f"daily_bars 补齐完成：{len(dirs)} 标的，本次写入 {added}", flush=True)

    # 2) signals 全量
    if "signals" in todo:
        rows = fetch_tiesafe("signals", [{"field": "ts", "direction": "desc"}], "ts")
        n = ds.ingest_docs("signals", rows)[0]
        print(f"signals 补齐完成：拉取 {len(rows)} 条，写入 {n}", flush=True)

    # 3) ticks 全部分时帧
    if "ticks" in todo:
        rows = fetch_tiesafe("ticks", [{"field": "date", "direction": "desc"}], "date")
        n = ds.ingest_docs("ticks", rows)[0]
        print(f"ticks 补齐完成：拉取 {len(rows)} 帧，写入 {n}", flush=True)

    # 汇总
    conn = ds.connect()
    local = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("daily_bars", "signals", "ticks", "hour_bars",
                       "vol_profile", "snapshots", "stocks", "macro_indicators")}
    conn.close()
    print(f"补齐结束 耗时 {time.time() - t0:.0f}s")
    print("本机计数:", json.dumps(local, ensure_ascii=False))


if __name__ == "__main__":
    main()
