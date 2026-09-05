"""把回测信号画到 5 分钟走势图上，直观展示"什么情况下报信"。

复用 backtest_week.py 的逐 bar 回放逻辑提取信号（与回测输出一致），
默认取最近 5 个交易日，画出价格线 + 昨收参考线 + 20 分钟均价 + 信号标注 + 分时量。

用法:
  /usr/bin/python3 plot_backtest_signals.py               # 默认 002155 000552
  /usr/bin/python3 plot_backtest_signals.py 600916 000902 # 指定股票

输出: charts/backtest_<symbol>.png
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D

for fp in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",):
    if Path(fp).exists():
        font_manager.fontManager.addfont(fp)
_cjk = [f.name for f in font_manager.fontManager.ttflist if "CJK" in f.name]
if _cjk:
    plt.rcParams["font.sans-serif"] = [_cjk[0]]
plt.rcParams["axes.unicode_minus"] = False

from monitor.store import Store
from monitor.strategies import INTRADAY_RULES
from backtest_week import fetch_m5, simulate_symbol
from monitor.daily import _market_code

UP, DOWN = "#d94f4f", "#2e9e5b"          # A股习惯: 涨红跌绿
C_PRICE, C_MA, C_PREV = "#3a6ea5", "#e8a04c", "#9a9a9a"

STYLE = {   # 规则 -> (marker, 取向函数(msg)->方向)
    "gap":         ("D", lambda m: "up" if "高开" in m else "down"),
    "vol_ratio":   ("^", lambda m: "up" if "拉升" in m else "down"),
    "zscore_dev":  ("s", lambda m: "up" if "急拉" in m else "down"),
    "limit_event": ("*", lambda m: "up" if "涨停" in m else "down"),
}


def short_label(rule: str, msg: str) -> str:
    """从完整信号消息里截取短标注。"""
    if rule == "gap":
        m = re.search(r"缺口 ([+-]?[\d.]+)%", msg)
        return ("高开" if "高开" in msg else "低开") + (f"{m.group(1)}%" if m else "")
    if rule == "vol_ratio":
        m = re.search(r"均值 ([\d.]+) 倍", msg)
        return ("放量拉升 " if "拉升" in msg else "放量下挫 ") + (f"{m.group(1)}×" if m else "")
    if rule == "zscore_dev":
        m = re.search(r"均价 ([\d.]+)σ", msg)
        return ("急拉 " if "急拉" in msg else "急跌 ") + (f"{m.group(1)}σ" if m else "")
    m = re.search(r"【(.+?)】", msg)
    return m.group(1) if m else rule


def plot_symbol(symbol: str, name: str, bars, daily_all, sigs, out_png: str):
    dates = sorted({b["date"] for b in bars})
    test = dates[-5:]
    test_bars = [b for b in bars if b["date"] in test]
    sigs = [s for s in sigs if datetime.fromtimestamp(s[0]).strftime("%Y-%m-%d") in test]
    x = range(len(test_bars))
    closes = [b["close"] for b in test_bars]
    vols = [b["volume"] for b in test_bars]
    sig_by_bar = {}
    for ts, rule, msg in sigs:
        sig_by_bar.setdefault(ts, []).append((rule, msg))

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(16, 8), dpi=150, sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1], "hspace": 0.06})

    # 逐日底色 / 分隔线 / 昨收参考线
    bounds = []   # (start_idx, end_idx, date)
    for d in test:
        idx = [i for i, b in enumerate(test_bars) if b["date"] == d]
        bounds.append((idx[0], idx[-1], d))
    for k, (i0, i1, d) in enumerate(bounds):
        if k % 2 == 1:
            ax.axvspan(i0 - 0.5, i1 + 0.5, color="#000000", alpha=0.035, lw=0)
        ax.axvline(i0 - 0.5, color="#bbbbbb", lw=0.8, ls=":", zorder=1)
        prev_close = next((b["close"] for b in reversed(daily_all) if b["date"] < d), None)
        if prev_close:
            ax.hlines(prev_close, i0 - 0.4, i1 + 0.4, colors=C_PREV, ls="--", lw=1.0, zorder=2)

    # 价格线 + 20分钟均价(4根5min)
    ax.plot(x, closes, color=C_PRICE, lw=1.4, zorder=3)
    ma4 = [sum(closes[i - 3:i + 1]) / 4 if i >= 3 else None for i in x]
    ax.plot(x, ma4, color=C_MA, lw=0.9, alpha=0.85, zorder=2)

    # 信号标注
    y_min, y_max = min(closes), max(closes)
    span = y_max - y_min or 1.0
    n_same_side = {}   # (bar_idx, side) 同 bar 同向叠放计数
    for ts, rule, msg in sigs:
        i = min(range(len(test_bars)), key=lambda j: abs(test_bars[j]["ts"] - ts))
        marker, orient_fn = STYLE[rule]
        side = orient_fn(msg)
        k = n_same_side.get((i, side), 0)
        n_same_side[(i, side)] = k + 1
        color = UP if side == "up" else DOWN
        ax.scatter(i, closes[i], marker=marker, s=70 if rule != "limit_event" else 260,
                   color=color, edgecolors="white", linewidths=0.6, zorder=5)
        dy = 14 + k * 16
        label = short_label(rule, msg)
        ax.annotate(label, (i, closes[i]),
                    xytext=(0, dy if side == "up" else -dy), textcoords="offset points",
                    ha="center", va="bottom" if side == "up" else "top",
                    fontsize=8.5, color=color, zorder=6,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.65))
    ax.set_ylim(y_min - span * 0.22, y_max + span * 0.22)

    handles = [
        Line2D([], [], color=C_PRICE, lw=1.4, label="5分钟收盘价"),
        Line2D([], [], color=C_MA, lw=1.0, label="20分钟均价"),
        Line2D([], [], color=C_PREV, lw=1.0, ls="--", label="昨收(缺口基准)"),
        Line2D([], [], color=UP, marker="D", ls="none", ms=7, label="缺口 高开/低开"),
        Line2D([], [], color=UP, marker="^", ls="none", ms=8, label="量比 放量拉升"),
        Line2D([], [], color=DOWN, marker="v", ls="none", ms=8, label="量比 放量下挫"),
        Line2D([], [], color=UP, marker="s", ls="none", ms=6, label="σ偏离 急拉/急跌"),
        Line2D([], [], color=UP, marker="*", ls="none", ms=13, label="封涨停/炸板"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8.5, ncol=4,
              framealpha=0.9, columnspacing=1.1, handletextpad=0.5)
    ax.set_ylabel("价格(元)", fontsize=10)
    ax.set_title(f"{name}({symbol})  盘中规则回测 {test[0]} ~ {test[-1]}  ·  触发 {len(sigs)} 条信号",
                 fontsize=13, pad=10)
    ax.grid(axis="y", color="#dddddd", lw=0.6, zorder=0)

    # 分时量
    axv.bar(x, vols, width=0.85, color="#b8c4d0", zorder=2)
    for ts, rules in sig_by_bar.items():
        i = min(range(len(test_bars)), key=lambda j: abs(test_bars[j]["ts"] - ts))
        rule = rules[0][0]
        side = STYLE[rule][1](rules[0][1])
        axv.bar(i, vols[i], width=0.85, color=UP if side == "up" else DOWN, zorder=3)
    axv.set_ylabel("5分钟量(手)", fontsize=9)
    axv.grid(axis="y", color="#dddddd", lw=0.6, zorder=0)

    # x 轴: 每天一个刻度(居中)
    axv.set_xticks([(i0 + i1) / 2 for i0, i1, _ in bounds])
    axv.set_xticklabels([d[5:] for _, _, d in bounds], fontsize=10)
    axv.set_xlim(-1, len(test_bars))

    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存 {out_png}  ({len(sigs)} 条信号)")


def main():
    symbols = sys.argv[1:] or ["002155", "000552"]
    cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))
    store = Store(cfg["db_path"])
    names = {w["symbol"]: w["name"] for w in cfg["watchlist"]}
    rules = [(n, INTRADAY_RULES[n]) for n in cfg["intraday_rules_enabled"] if n in INTRADAY_RULES]

    out_dir = ROOT / "charts"
    out_dir.mkdir(exist_ok=True)
    for symbol in symbols:
        name = names.get(symbol, symbol)
        bars = fetch_m5(_market_code(symbol))
        daily_all = store.get_daily(symbol)
        sigs = simulate_symbol(symbol, name, bars, daily_all, rules,
                               cfg["params"], cfg["cooldown_minutes"])
        for ts, rule, msg in sorted(sigs):
            print(f"  {datetime.fromtimestamp(ts):%m-%d %H:%M}  {msg}")
        plot_symbol(symbol, name, bars, daily_all, sigs,
                    str(out_dir / f"backtest_{symbol}.png"))


if __name__ == "__main__":
    main()
