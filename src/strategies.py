"""策略层 —— 你主要改这个文件。

== 如何添加自己的信号规则 ==

1. 盘中规则：写一个函数 `def my_rule(ctx) -> str | None`，
   返回消息文本表示触发，返回 None 表示无信号。然后加入 INTRADAY_RULES。
   作用于指数本身的规则（如大盘急动）加入 INDEX_RULES。
2. 日线规则（收盘后跑）：同理，加入 DAILY_RULES。

ctx 是 RuleContext，可用字段：
  ctx.quote     最新快照 dict: price/open/high/low/prev_close/pct_chg/
                volume_hand(累计成交量,手)/amount_wan/turnover_rate/quote_time
  ctx.intraday  今日盘中轨迹 [(unix_ts, price, cum_volume_hand), ...]，按时间升序
  ctx.daily     最近日线 [{'date','open','high','low','close','volume','amount'}...]，
                按日期升序，最新在最后（需要先用 run_daily.py 入库）
  ctx.params    config.json 里 params 下以规则同名的参数字典
                （params 里可放 "cooldown_minutes" 覆盖全局冷却，如每天只报一次设 1200）
  ctx.vol_profile  同时段量比基线 {"HH:MM": 该时段5分钟均量(手)}，
                由 run_daily.py 每天收盘后更新
  ctx.index_intraday / ctx.index_quote  指数盘中轨迹与快照（配置了 index_code 才有）

注意事项：
  - 函数名就是规则名，冷却去重按 (标的, 规则名) 生效
  - 规则抛异常会被引擎捕获并跳过，不会中断其他规则
  - 返回值尽量带现价和关键数字，直接进微信

== 阈值标定依据（2026-05-29 ~ 08-28，65 交易日 x 10 只蓝筹，5分钟K线回测）==
  zscore_dev   @2.0 -> 8.8 次/周；@2.5 -> 2.5 次/周
  vol_ratio    @4.0 -> 7.6 次/周；@6.0 -> 1.7 次/周
  gap          @0.5σ -> 2.2 次/周（缺口后方向调整 EOD 收益 -0.61%，回归倾向明显）
  index_move   @0.5% -> 1.0 次/周；@0.8% -> 0.1 次/周
  limit_event  三个月 0 次（蓝筹特性，规则零成本保留，换高波动股池才有用）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class RuleContext:
    symbol: str
    name: str
    quote: dict
    intraday: list[tuple] = field(default_factory=list)   # (ts, price, cum_vol)
    daily: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    vol_profile: dict = field(default_factory=dict)       # "HH:MM" -> 均量(手)
    index_intraday: list[tuple] = field(default_factory=list)  # (ts, price)
    index_quote: dict = field(default_factory=dict)


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------

def _five_min_grid(intraday: list[tuple]) -> list[tuple[int, float, float]]:
    """把 3 秒轨迹抽稀到 5 分钟网格（每格取最后一个样本）。

    返回 [(grid_ts, price, cum_vol), ...] 按时间升序。
    """
    grid: dict[int, tuple[int, float, float]] = {}
    for row in intraday:
        ts = row[0]
        g = ts - ts % 300
        grid[g] = (row[0], row[1], row[2] if len(row) > 2 else 0.0)
    return [grid[g] for g in sorted(grid)]


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100 if b else 0.0


# ----------------------------------------------------------------------
# 盘中规则（每 3 秒评估一次）
# ----------------------------------------------------------------------

def zscore_dev(ctx: RuleContext) -> str | None:
    """波动率归一偏离：现价偏离 20 分钟均值达到个股自身噪声的 z_th 倍。

    z = (现价 - 近4根5分钟均价) / (近20根5分钟收益std * 现价 * sqrt((n^2-1)/(3n)))
    启动约 1.5 小时后才有足够数据，属正常。
    """
    p = {"z_th": 2.0, **ctx.params}
    grid = _five_min_grid(ctx.intraday)
    if len(grid) < 21:
        return None
    closes = [g[1] for g in grid]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    mu = sum(rets) / len(rets)
    sigma = math.sqrt(sum((x - mu) ** 2 for x in rets) / len(rets))
    if sigma <= 0:
        return None
    n = 4
    ma = sum(closes[-n:]) / n
    scale = sigma * closes[-1] * math.sqrt((n * n - 1) / (3.0 * n))
    z = (closes[-1] - ma) / scale
    if abs(z) >= p["z_th"]:
        direction = "急拉" if z > 0 else "急跌"
        return (f"【{direction}异动】{ctx.name}({ctx.symbol}) 偏离20分钟均价 {abs(z):.1f}σ，"
                f"现价 {closes[-1]:.2f}（{ctx.quote.get('pct_chg', 0):+.2f}%）")
    return None


def vol_ratio(ctx: RuleContext) -> str | None:
    """同时段量比异动：最近 5 分钟成交量 >= 同时段 10 日均量的 ratio_th 倍。

    基线由 run_daily.py 每日更新；基线缺失时自动静默。
    """
    p = {"ratio_th": 4.0, **ctx.params}
    if not ctx.vol_profile:
        return None
    grid = _five_min_grid(ctx.intraday)
    if len(grid) < 3:
        return None
    ts, price, cum_vol = grid[-1]
    prev_vol = grid[-2][2]
    bucket_vol = cum_vol - prev_vol
    if bucket_vol <= 0:
        return None
    import time as _time
    hm = _time.strftime("%H:%M", _time.localtime(ts))
    base = ctx.vol_profile.get(hm)
    if not base or base <= 0:
        return None
    ratio = bucket_vol / base
    if ratio < p["ratio_th"]:
        return None
    chg = _pct(price, grid[-2][1])
    direction = "放量拉升" if chg >= 0 else "放量下挫"
    return (f"【{direction}】{ctx.name}({ctx.symbol}) 最近5分钟量为同时段均值 {ratio:.1f} 倍，"
            f"5分钟{chg:+.2f}%，现价 {price:.2f}")


def gap(ctx: RuleContext) -> str | None:
    """开盘缺口：|今开/昨收-1| >= sigma_th 倍日波动率。建议配 cooldown_minutes=1200（每天一次）。"""
    p = {"sigma_th": 0.5, **ctx.params}
    if len(ctx.daily) < 21:
        return None
    q = ctx.quote
    prev_close, open_ = q.get("prev_close", 0), q.get("open", 0)
    if not prev_close or not open_:
        return None
    closes = [b["close"] for b in ctx.daily]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    mu = sum(rets) / len(rets)
    dsig = math.sqrt(sum((x - mu) ** 2 for x in rets) / len(rets))
    if dsig <= 0:
        return None
    g = open_ / prev_close - 1
    z = g / dsig
    if abs(z) >= p["sigma_th"]:
        direction = "高开" if g > 0 else "低开"
        hint = "缺口历史多回补，持仓可考虑兑现" if g > 0 else "缺口历史多回补，关注接回机会"
        return (f"【{direction}】{ctx.name}({ctx.symbol}) 今开 {open_:.2f} "
                f"缺口 {g:+.2%}（{abs(z):.1f}σ日波动）。{hint}")
    return None


def limit_event(ctx: RuleContext) -> str | None:
    """封板/炸板/跌停/撬板。建议配 cooldown_minutes=1200（每天每向一次）。"""
    p = {"fallback_pct": 0.5, **ctx.params}
    q = ctx.quote
    prev_close = q.get("prev_close", 0)
    if not prev_close:
        return None
    ratio = 1.20 if ctx.symbol.startswith(("300", "301", "688")) else 1.10
    up = round(prev_close * ratio, 2)
    down = round(prev_close * (2 - ratio), 2)
    price, high, low = q.get("price", 0), q.get("high", 0), q.get("low", 0)
    if not price:
        return None
    fb = p["fallback_pct"] / 100
    if price >= up - 1e-9:
        return f"【封涨停】{ctx.name}({ctx.symbol}) 封住涨停 {up:.2f}"
    if high >= up - 1e-9 and price < up * (1 - fb):
        return f"【炸板】{ctx.name}({ctx.symbol}) 触及涨停 {up:.2f} 后回落，现价 {price:.2f}"
    if price <= down + 1e-9:
        return f"【封跌停】{ctx.name}({ctx.symbol}) 封住跌停 {down:.2f}"
    if low <= down + 1e-9 and price > down * (1 + fb):
        return f"【撬板】{ctx.name}({ctx.symbol}) 触及跌停 {down:.2f} 后撬开，现价 {price:.2f}"
    return None


# ----------------------------------------------------------------------
# 指数级规则（作用于 index_code 本身，每周期评估一次）
# ----------------------------------------------------------------------

def index_move(ctx: RuleContext) -> str | None:
    """大盘急动：指数 5 分钟涨跌幅超 threshold_pct%%（系统性事件）。"""
    p = {"threshold_pct": 0.5, **ctx.params}
    grid = _five_min_grid(ctx.intraday)
    if len(grid) < 2:
        return None
    chg = _pct(grid[-1][1], grid[-2][1])
    if abs(chg) >= p["threshold_pct"]:
        direction = "急拉" if chg > 0 else "急跌"
        return (f"【大盘{direction}】上证指数 5分钟 {chg:+.2f}%，"
                f"现价 {grid[-1][1]:.2f}。检查持仓是否受波及")
    return None


# ----------------------------------------------------------------------
# 日线规则（收盘后 run_daily.py 评估一次）
# ----------------------------------------------------------------------

def ma_cross(ctx: RuleContext) -> str | None:
    """均线交叉：收盘价上穿/下穿 MA(window)，默认 MA20。"""
    p = {"ma_window": 20, **ctx.params}
    n = p["ma_window"]
    if len(ctx.daily) < n + 1:
        return None
    closes = [b["close"] for b in ctx.daily]
    ma_prev = sum(closes[-n - 1:-1]) / n
    ma_now = sum(closes[-n:]) / n
    prev_close, now_close = closes[-2], closes[-1]
    d = ctx.daily[-1]["date"]
    if prev_close <= ma_prev and now_close > ma_now:
        return (f"【日线上穿MA{n}】{ctx.name}({ctx.symbol}) {d} 收盘 {now_close:.2f} "
                f"站上 MA{n}={ma_now:.2f}")
    if prev_close >= ma_prev and now_close < ma_now:
        return (f"【日线下穿MA{n}】{ctx.name}({ctx.symbol}) {d} 收盘 {now_close:.2f} "
                f"跌破 MA{n}={ma_now:.2f}")
    return None


# ----------------------------------------------------------------------
# 注册表：把你写的规则加到这里（名字需与 config.json 里 enabled 对应）
# ----------------------------------------------------------------------

INTRADAY_RULES = {
    "zscore_dev": zscore_dev,
    "vol_ratio": vol_ratio,
    "gap": gap,
    "limit_event": limit_event,
}

INDEX_RULES = {
    "index_move": index_move,
}

DAILY_RULES = {
    "ma_cross": ma_cross,
}
