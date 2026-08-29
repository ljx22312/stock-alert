"""实时行情抓取：腾讯主用 + 新浪备用，批量快照。

实测结论（2026-08-28，阿里云上海）:
  - 腾讯 qt.gtimg.cn: 单请求可带几十只股票, 1Hz 持续零失败, 延迟 ~40ms
  - 新浪 hq.sinajs.cn: 可用作降级备用, 需带 Referer 头
  - 两者返回均为 GBK 编码
  - 行情快照本身约 3 秒刷新一次, 轮询间隔建议 >= 3s
"""
from __future__ import annotations

import time

import requests

_session = requests.Session()


def to_market_code(symbol: str) -> str:
    """'600519' -> 'sh600519'；'000001'/'300750' -> 'sz...'"""
    if symbol.startswith(("6", "9")):
        return f"sh{symbol}"
    return f"sz{symbol}"


def _parse_tencent(text: str) -> dict[str, dict]:
    """解析腾讯批量行情，按响应里的完整市场代码(sh600519)做键，避免与个股代码撞车。
    字段位置（~分隔）:
    1=名称 2=代码 3=现价 4=昨收 5=今开 6=成交量(手) 30=时间戳 31=涨跌 32=涨跌% 33=最高 34=最低 37=成交额(万) 38=换手率
    """
    quotes = {}
    for seg in text.strip().split(";"):
        if "=" not in seg:
            continue
        key, _, payload = seg.partition("=")
        code = key.strip().removeprefix("v_")  # 'v_sh600519' -> 'sh600519'
        parts = payload.strip().strip('"').split("~")
        if len(parts) < 39 or not parts[3]:
            continue
        try:
            quotes[code] = {
                "symbol": parts[2],
                "name": parts[1],
                "price": float(parts[3]),
                "prev_close": float(parts[4]),
                "open": float(parts[5]),
                "volume_hand": float(parts[6]),
                "high": float(parts[33]),
                "low": float(parts[34]),
                "pct_chg": float(parts[32]),
                "amount_wan": float(parts[37]) if parts[37] else 0.0,
                "turnover_rate": float(parts[38]) if parts[38] else 0.0,
                "quote_time": parts[30],  # '20260828161500'
            }
        except (ValueError, IndexError):
            continue
    return quotes


def _parse_sina(text: str) -> dict[str, dict]:
    """解析新浪批量行情（GBK 已解码），按完整市场代码做键。"""
    quotes = {}
    for line in text.strip().splitlines():
        if "=" not in line:
            continue
        key, _, payload = line.partition("=")
        code = key.replace("var hq_str_", "")  # 'sh600519'
        fields = payload.strip().strip('";').split(",")
        if len(fields) < 32 or not fields[3]:
            continue
        try:
            price = float(fields[3])
            prev_close = float(fields[2])
            quotes[code] = {
                "symbol": fields[0] and code[2:] or code[2:],
                "name": fields[0],
                "price": price,
                "prev_close": prev_close,
                "open": float(fields[1]),
                "volume_hand": float(fields[8]) / 100.0,  # 股 -> 手
                "high": float(fields[4]),
                "low": float(fields[5]),
                "pct_chg": (price - prev_close) / prev_close * 100 if prev_close else 0.0,
                "amount_wan": float(fields[9]) / 10000.0,
                "turnover_rate": 0.0,  # 新浪快照无换手率
                "quote_time": (fields[30] + fields[31]).replace("-", "").replace(":", ""),
            }
        except (ValueError, IndexError):
            continue
    return quotes


def fetch_quotes_raw(codes: list[str]) -> dict[str, dict]:
    """按完整市场代码批量拉取，返回 {market_code: quote}。腾讯失败自动降级新浪。"""
    joined = ",".join(codes)
    try:
        r = _session.get(f"https://qt.gtimg.cn/q={joined}", timeout=10)
        if r.status_code == 200:
            quotes = _parse_tencent(r.content.decode("gbk", "ignore"))
            if quotes:
                return quotes
    except requests.RequestException:
        pass

    r = _session.get(
        f"https://hq.sinajs.cn/list={joined}",
        headers={"Referer": "https://finance.sina.com.cn"},
        timeout=10,
    )
    r.raise_for_status()
    return _parse_sina(r.content.decode("gbk", "ignore"))


def fetch_quotes(symbols: list[str], extra_codes: tuple[str, ...] = ()) -> dict[str, dict]:
    """批量拉取实时快照，返回 {symbol: quote_dict}；extra_codes 里的完整市场代码
    （如指数 sh000001）原样作为键返回，不与 6 位个股代码混淆。"""
    codes = [to_market_code(s) for s in symbols] + list(extra_codes)
    raw = fetch_quotes_raw(codes)
    out = {}
    for code, q in raw.items():
        if code in extra_codes:
            out[code] = q
        else:
            out[q["symbol"]] = q
    return out


def is_trading_now(now: time.struct_time | None = None) -> bool:
    """简单的交易时段判断（周一至五 9:30-11:30 / 13:00-15:00）。"""
    t = time.localtime(now and time.mktime(now) or time.time())
    if t.tm_wday >= 5:
        return False
    hm = t.tm_hour * 100 + t.tm_min
    return (930 <= hm < 1130) or (1300 <= hm < 1500)
