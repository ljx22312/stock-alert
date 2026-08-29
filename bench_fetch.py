#!/usr/bin/env python3
"""数据获取能力基准测试 — 直接打东方财富接口（akshare 背后的真实数据源）。

测量项:
  1. 全市场快照: 一次请求拿全部 A 股最新行情, 测延迟和数据量
  2. 单股 60 分钟线: 10 只观察名单, 测单请求延迟与数据新鲜度
  3. 频率压力: 逐级提高请求频率, 找到被限流(HTTP000/空响应/rc!=0)的拐点
  4. 结论输出: 建议的安全工作频率
"""
import json
import sys
import time

import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})

SNAPSHOT_URL = "https://push2.eastmoney.com/api/qt/clist/get"
SNAPSHOT_PARAMS = {
    "pn": 1, "pz": 6000, "po": 1, "np": 1,
    "fltt": 2, "invt": 2, "fid": "f3",
    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 深A+沪A
    "fields": "f12,f14,f2,f3,f5,f6",
}
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

WATCHLIST = [  # (secid, 名称)
    ("1.600519", "贵州茅台"), ("0.000001", "平安银行"), ("1.601318", "中国平安"),
    ("0.300750", "宁德时代"), ("1.600036", "招商银行"), ("0.002594", "比亚迪"),
    ("1.601899", "紫金矿业"), ("0.000858", "五粮液"), ("1.600900", "长江电力"),
    ("0.300059", "东方财富"),
]


def fetch_snapshot():
    t0 = time.monotonic()
    r = S.get(SNAPSHOT_URL, params=SNAPSHOT_PARAMS, timeout=15)
    dt = time.monotonic() - t0
    d = r.json().get("data") or {}
    return dt, len(d.get("diff") or [])


def fetch_minute_bar(secid, klt=60):
    t0 = time.monotonic()
    r = S.get(KLINE_URL, params={
        "secid": secid, "klt": klt, "fqt": 0,
        "beg": 0, "end": 20500101, "lmt": 1000000,
        "fields1": "f1", "fields2": "f51,f53",
    }, timeout=15)
    dt = time.monotonic() - t0
    ks = (r.json().get("data") or {}).get("klines") or []
    latest = ks[-1].split(",")[0] if ks else None
    return dt, len(ks), latest


def probe_rpm(interval_s, n_requests):
    """以固定间隔发 n_requests 次快照请求, 返回失败数"""
    fails = 0
    for i in range(n_requests):
        t0 = time.monotonic()
        try:
            r = S.get(SNAPSHOT_URL, params=SNAPSHOT_PARAMS, timeout=15)
            ok = r.status_code == 200 and r.json().get("data")
        except Exception:
            ok = False
        if not ok:
            fails += 1
        elapsed = time.monotonic() - t0
        if elapsed < interval_s:
            time.sleep(interval_s - elapsed)
    return fails


def main():
    print("=" * 56)
    print("1) 全市场快照 x3")
    for i in range(3):
        try:
            dt, n = fetch_snapshot()
            print(f"   第{i+1}次: {dt*1000:.0f}ms, 拿到 {n} 只股票")
        except Exception as e:
            print(f"   第{i+1}次: 失败 {e}")
        time.sleep(2)

    print("=" * 56)
    print("2) 观察名单 10 只 x 60分钟线 (间隔 1.5s)")
    total = 0.0
    for secid, name in WATCHLIST:
        try:
            dt, n, latest = fetch_minute_bar(secid)
            total += dt
            print(f"   {name}({secid}): {dt*1000:.0f}ms, {n} 根K线, 最新: {latest}")
        except Exception as e:
            print(f"   {name}({secid}): 失败 {e}")
        time.sleep(1.5)
    print(f"   平均单只延迟: {total/len(WATCHLIST)*1000:.0f}ms")

    print("=" * 56)
    print("3) 频率压力测试 (快照请求, 每级 20 次)")
    for interval in [5.0, 2.0, 1.0]:
        fails = probe_rpm(interval, 20)
        rpm = 60.0 / interval
        status = "OK" if fails == 0 else f"失败 {fails}/20"
        print(f"   目标 {rpm:.0f} RPM (间隔{interval}s): {status}")
        if fails:
            print(f"   >>> 拐点: {rpm:.0f} RPM 开始被限流, 停止加压")
            break
        time.sleep(10)  # 级间冷却
    print("=" * 56)


if __name__ == "__main__":
    main()
