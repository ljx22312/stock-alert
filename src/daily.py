"""日线数据更新：腾讯主用 + 东财兜底（均为前复权）。
另含盘中 5 分钟K线拉取（用于 vol_ratio 规则的同时段量基线）。

- 腾讯 web.ifzq.gtimg.cn: 与盘中快照同源，限流宽松，优先使用
  注意: mkline 接口必须用 ifzq.gtimg.cn（不带 web 前缀），
  web.ifzq.gtimg.cn 会 302 到 web3.ifzq.gtimg.cn，部分网络无法解析
- 东财 push2his: 限流激进（实测触发后 IP 封禁半小时以上），仅作兜底
信号计算用前复权(qfq)保证价格连续；每天收盘后跑一次即可。
"""
from __future__ import annotations

import time

import requests

TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
EM_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _market_code(symbol: str) -> str:
    return f"{'sh' if symbol.startswith(('6', '9')) else 'sz'}{symbol}"


def _fetch_tencent(symbol: str, days: int) -> list[dict]:
    r = requests.get(TENCENT_URL, params={
        "param": f"{_market_code(symbol)},day,,,{days},qfq",
    }, timeout=15)
    node = r.json()["data"][_market_code(symbol)]
    rows = node.get("qfqday") or node.get("day") or []
    # 行格式: [date, open, close, high, low, volume, ...]
    return [{
        "date": f[0], "open": float(f[1]), "close": float(f[2]),
        "high": float(f[3]), "low": float(f[4]),
        "volume": float(f[5]), "amount": 0.0,
    } for f in rows]


def _fetch_eastmoney(symbol: str, days: int) -> list[dict]:
    secid = f"{'1' if symbol.startswith(('6', '9')) else '0'}.{symbol}"
    r = requests.get(EM_URL, params={
        "secid": secid, "klt": 101, "fqt": 1,
        "beg": 0, "end": 20500101, "lmt": days,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }, timeout=15)
    klines = (r.json().get("data") or {}).get("klines") or []
    return [{
        "date": f[0], "open": float(f[1]), "close": float(f[2]),
        "high": float(f[3]), "low": float(f[4]),
        "volume": float(f[5]), "amount": float(f[6]),
    } for f in (line.split(",") for line in klines)]


def fetch_daily_bars(symbol: str, days: int = 150, sleep_sec: float = 1.2) -> list[dict]:
    """拉最近 days 天的前复权日线，按日期升序。腾讯失败自动降级东财。"""
    bars = []
    try:
        bars = _fetch_tencent(symbol, days)
    except Exception:
        pass
    if not bars:
        bars = _fetch_eastmoney(symbol, days)
    time.sleep(sleep_sec)  # 限流保护
    return bars


def fetch_m5_bars(symbol: str, count: int = 320) -> list[dict]:
    """拉最近 count 根 5 分钟K线（约 6.6 个交易日）。
    返回 [{'date', 'hm', 'volume', 'close'}, ...] 按时间升序。"""
    r = requests.get(TENCENT_MKLINE_URL,
                     params={"param": f"{_market_code(symbol)},m5,,{count}"},
                     timeout=15)
    rows = r.json()["data"][_market_code(symbol)].get("m5") or []
    return [{
        "date": f"{x[0][:4]}-{x[0][4:6]}-{x[0][6:8]}",
        "hm": f"{x[0][8:10]}:{x[0][10:12]}",
        "close": float(x[2]),
        "volume": float(x[5]),
    } for x in rows]


def compute_vol_profile(bars: list[dict], days: int = 10) -> dict[str, float]:
    """由 5 分钟K线计算同时段量基线: {hm: 最近 days 天该时段均量(手)}。"""
    day_order = sorted({b["date"] for b in bars})[-days:]
    recent = [b for b in bars if b["date"] in set(day_order)]
    by_hm: dict[str, list[float]] = {}
    for b in recent:
        by_hm.setdefault(b["hm"], []).append(b["volume"])
    return {hm: sum(vols) / len(vols) for hm, vols in by_hm.items()}
