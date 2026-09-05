#!/usr/bin/env python3
"""宏观/海外/情绪指标 → 数据服务 macro_indicators 集合。

读 data/downloads/{macro,vix,fred}/*.csv，每个指标提取最新值 + 近 N 年序列，
推送 doc {id,name,unit,latest,prev,series:[[date,value]...]}，id=指标名。
用法：python3 jobs/sync_macro.py
"""
import csv
import math
import re
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from sync_data import push, load_cfg  # noqa: E402

D = ROOT / "data" / "downloads"
if not (D / "macro").is_dir():  # 仓库内无数据时用本机资产布局（/home/ubuntu/data/downloads）
    D = Path("/home/ubuntu/data/downloads")
HIST_YEARS = 10   # 每个指标保留近 10 年序列
MAX_POINTS = 500  # 单序列最大点数（超出均匀抽稀，控制 ingest 请求体在 413 限制内）


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def series(rows, date_col, val_col, skip_zero=False, max_points=MAX_POINTS):
    """rows -> [[date, value]...] 按日期升序，取近 HIST_YEARS 年；超 max_points 均匀抽稀且保留最新点。"""
    out = []
    for r in rows:
        try:
            v = float(r[val_col])
        except (ValueError, TypeError):
            continue
        if skip_zero and v == 0:
            continue
        out.append([r[date_col], v])
    out.sort(key=lambda x: x[0])
    cutoff = out[-1][0][:4] if out else ""
    if len(cutoff) == 4:
        out = [x for x in out if x[0][:4] >= str(int(cutoff) - HIST_YEARS)]
    if len(out) > max_points:
        stride = math.ceil(len(out) / max_points)
        sampled = out[::stride]
        if sampled[-1] != out[-1]:
            sampled.append(out[-1])
        out = sampled
    return out


def em_month_rows(rows, col="month"):
    """东财经济报表月份列归一为 'YYYY-MM'（兼容 '2026-07-01 00:00:00' 与 '2026年07月份'）。"""
    out = []
    for r in rows:
        m = re.search(r"(\d{4})[-年](\d{1,2})", str(r.get(col) or ""))
        if m:
            r = dict(r, **{col: f"{m.group(1)}-{int(m.group(2)):02d}"})
        out.append(r)
    return out


def build_indicators():
    inds = {}

    def add(iid, name, unit, rows, date_col, val_col, skip_zero=False, transform=None):
        s = series(rows, date_col, val_col, skip_zero)
        if not s:
            return
        if transform:
            s = [[d, transform(v)] for d, v in s]
        latest = s[-1][1]
        prev = s[-2][1] if len(s) > 1 else None
        inds[iid] = {"id": iid, "name": name, "unit": unit,
                     "latest": latest, "prev": prev, "series": s}

    # ---- 货币 ----
    ms = read_csv(D / "macro" / "money_supply.csv")
    add("m2_yoy", "M2 同比", "%", ms, "月份", "货币和准货币(M2)-同比增长")
    add("m1_yoy", "M1 同比", "%", ms, "月份", "货币(M1)-同比增长")

    # ---- 通胀（东财官方口径 cpi_em/ppi_em，月更；金十源 cpi_monthly 停更勿用）----
    cpi = em_month_rows(read_csv(D / "macro" / "cpi_em.csv"))
    add("cpi_yoy", "CPI 同比", "%", cpi, "month", "national_yoy")
    ppi = em_month_rows(read_csv(D / "macro" / "ppi_em.csv"))
    add("ppi_yoy", "PPI 同比", "%", ppi, "month", "yoy")

    # ---- 增长 ----
    pmi = read_csv(D / "macro" / "pmi.csv")
    add("pmi_mfg", "制造业 PMI", "", pmi, "月份", "制造业-指数")
    ip = em_month_rows(read_csv(D / "macro" / "ip_em.csv"))
    add("ip_yoy", "工业增加值同比", "%", ip, "month", "yoy")

    # ---- 信用 ----
    sf = read_csv(D / "macro" / "social_financing.csv")
    add("social_financing", "社融增量", "亿元", sf, "月份", "社会融资规模增量", skip_zero=True)

    # ---- 利率 ----
    lpr = read_csv(D / "macro" / "lpr.csv")
    add("lpr_1y", "1年 LPR", "%", lpr, "TRADE_DATE", "LPR1Y", skip_zero=True)
    sb = read_csv(D / "macro" / "shibor.csv")
    add("shibor_3m", "Shibor 3个月", "%", sb, "日期", "3M-定价", skip_zero=True)
    ty = read_csv(D / "macro" / "treasury_yield.csv")
    add("cn_gov10y", "中国国债10年", "%", ty, "日期", "中国国债收益率10年")
    add("cn_ts10y2y", "中债期限利差(10-2)", "%", ty, "日期", "中国国债收益率10年-2年")
    add("us_gov10y", "美国国债10年", "%", ty, "日期", "美国国债收益率10年")
    add("us_ts10y2y", "美债期限利差(10-2)", "%", ty, "日期", "美国国债收益率10年-2年")

    # ---- 资金（两融，akshare 原始单位为元 → 亿元）----
    mg = read_csv(D / "macro" / "margin_sh.csv")
    add("margin_sh", "沪市两融余额", "亿元", mg, "日期", "融资融券余额", skip_zero=True, transform=lambda v: v / 1e8)
    mg2 = read_csv(D / "macro" / "margin_sz.csv")
    add("margin_sz", "深市两融余额", "亿元", mg2, "日期", "融资融券余额", skip_zero=True, transform=lambda v: v / 1e8)

    # ---- 海外（FRED，MOVE 无源已剔除）----
    FRED = ROOT / "data" / "fred"
    if not FRED.is_dir():  # 本机资产布局在 /home/ubuntu/data/fred
        FRED = Path("/home/ubuntu/data/fred")
    for sid, name, unit, transform in [
        ("VIXCLS", "VIX 恐慌指数", "", None),
        ("WALCL", "美联储总资产", "万亿美元", lambda v: v / 1e6),   # 原始百万美元 → 万亿美元
        ("RRPONTSYD", "隔夜逆回购用量", "百万美元", None),
        ("DTWEXBGS", "美元广义指数", "", None),
        ("DFF", "联邦基金利率", "%", None),
    ]:
        f = FRED / f"{sid}.csv"
        if f.exists():
            rows = read_csv(f)
            # fredgraph.csv: 列1=日期, 列2=序列
            cols = list(rows[0].keys()) if rows else []
            vcol = cols[1] if len(cols) > 1 else sid
            add(f"fred_{sid}", name, unit, rows, cols[0], vcol, skip_zero=True, transform=transform)

    # ---- 情绪（QVIX，尾部空行自动跳过，剔除 0 值）----
    q1 = read_csv(D / "vix" / "qvix_300.csv")
    add("qvix_300", "QVIX 沪深300", "", q1, "date", "close", skip_zero=True)
    q2 = read_csv(D / "vix" / "qvix_1000.csv")
    add("qvix_1000", "QVIX 中证1000", "", q2, "date", "close", skip_zero=True)

    return inds


def main():
    cfg = load_cfg()
    inds = build_indicators()
    docs = list(inds.values())
    print(f"构建 {len(docs)} 个指标")
    for d in docs:
        print(f"  {d['id']:18} {d['name']:12} 最新 {d['latest']} {d['unit']} ({len(d['series'])} 点)")
    n = push("macro_indicators", docs, cfg)
    print(f"推送 {n} 条到 macro_indicators")


if __name__ == "__main__":
    main()
