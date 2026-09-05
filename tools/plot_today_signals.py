"""今日分时走势 + 信号标注图：每只触发信号的股票一格，看信号在走势上的位置。

数据: 腾讯 m5 今日 48 根 bar; 信号: signal_log 当日记录; 昨收: 日线库。
用法: /usr/bin/python3 plot_today_signals.py
输出: charts/today_signals_YYYYMMDD.png
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

for fp in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",):
    if Path(fp).exists():
        font_manager.fontManager.addfont(fp)
_cjk = [f.name for f in font_manager.fontManager.ttflist if "CJK" in f.name]
if _cjk:
    plt.rcParams["font.sans-serif"] = [_cjk[0]]
plt.rcParams["axes.unicode_minus"] = False

from monitor.daily import _market_code
from backtest_week import fetch_m5

UP, DOWN = "#d94f4f", "#2e9e5b"
C_PRICE, C_MA, C_PREV = "#3a6ea5", "#e8a04c", "#9a9a9a"

STYLE = {
    "gap": ("D", lambda m: "up" if "高开" in m else "down"),
    "vol_ratio": ("^", lambda m: "up" if "拉升" in m else "down"),
    "zscore_dev": ("s", lambda m: "up" if "急拉" in m else "down"),
    "fast_drop": ("o", lambda m: "up" if "快速拉升" in m else "down"),
    "slow_drop": ("p", lambda m: "up" if "持续走强" in m else "down"),
    "limit_event": ("*", lambda m: "up"),
}


def short_label(rule: str, msg: str) -> str:
    if rule == "gap":
        return "高开" if "高开" in msg else "低开"
    if rule == "vol_ratio":
        m = re.search(r"均值 ([\d.]+) 倍", msg)
        return ("放量拉升" if "拉升" in msg else "放量下挫") + (f" {m.group(1)}×" if m else "")
    if rule == "zscore_dev":
        m = re.search(r"均价 ([\d.]+)σ", msg)
        return ("急拉" if "急拉" in msg else "急跌") + (f" {m.group(1)}σ" if m else "")
    if rule == "fast_drop" or rule == "slow_drop":
        return "快速拉升" if "快速拉升" in msg else ("快速下挫" if "快速下挫" in msg else "持续")
    m = re.search(r"【(.+?)】", msg)
    return m.group(1) if m else rule


def bar_idx_from_ts(ts: int, bars: list[dict]) -> int:
    """取信号 ts 所在/之后的最近一根 bar。"""
    hm = datetime.fromtimestamp(ts).strftime("%H:%M")
    m = int(hm[:2]) * 60 + int(hm[3:])
    for i, b in enumerate(bars):
        bm = int(b["hm"][:2]) * 60 + int(b["hm"][3:])
        if bm >= m:
            return i
    return len(bars) - 1


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))

    # 日线库拿昨收
    daily_conn = sqlite3.connect(cfg["db_path"])
    rows = daily_conn.execute(
        "SELECT ts, symbol, rule, message FROM signal_log WHERE ts >= ? AND ts < ? ORDER BY ts",
        (int(datetime.strptime(today, "%Y-%m-%d").timestamp()),
         int(datetime.strptime(today, "%Y-%m-%d").timestamp()) + 86400),
    ).fetchall()
    by_sym: dict[str, list[tuple]] = {}
    for ts, sym, rule, msg in rows:
        by_sym.setdefault(sym, []).append((ts, rule, msg))
    print(f"{today} 共 {len(rows)} 条信号，涉及 {len(by_sym)} 只股票")

    daily = daily_conn.execute(
        "SELECT symbol, date, close FROM daily_bars WHERE date < ? ORDER BY date DESC",
        (today,),
    ).fetchall()
    prev_by_sym: dict[str, float] = {}
    for sym, d, c in daily:
        if sym not in prev_by_sym:
            prev_by_sym[sym] = c

    n = len(by_sym)
    cols = min(3, n)
    rows_n = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_n, cols, figsize=(16, 4.6 * rows_n), dpi=150)
    axl = axes.reshape(-1) if n > 1 else [axes]

    xticks, xticklabels = None, None
    for k, (symbol, sigs) in enumerate(sorted(by_sym.items())):
        ax = axl[k]
        name = next((w["name"] for w in cfg["watchlist"] if w["symbol"] == symbol), symbol)
        bars = [b for b in fetch_m5(_market_code(symbol)) if b["date"] == today]
        x = list(range(len(bars)))
        closes = [b["close"] for b in bars]
        vols = [b["volume"] for b in bars]
        ax.plot(x, closes, color=C_PRICE, lw=1.3, zorder=3)
        ma4 = [sum(closes[i - 3:i + 1]) / 4 if i >= 3 else None for i in x]
        ax.plot(x, ma4, color=C_MA, lw=0.8, alpha=0.85, zorder=2)

        prev = prev_by_sym.get(symbol)
        if prev:
            ax.hlines(prev, -1, len(bars), colors=C_PREV, ls="--", lw=0.9, zorder=1)
            pct = (closes[-1] - prev) / prev * 100

        # 成交量子图(同格)
        axv = ax.twinx()
        axv.bar(x, vols, width=0.8, color="#b8c4d0", alpha=0.55, zorder=0)
        axv.set_ylim(0, max(vols) * 2.6 if vols else 1)
        axv.set_yticks([])

        # 信号标注
        y_min, y_max = min(closes), max(closes)
        span = y_max - y_min or 1.0
        used: dict[tuple[int, str], int] = {}
        for ts, rule, msg in sigs:
            i = bar_idx_from_ts(ts, bars)
            marker, orient = STYLE.get(rule, ("o", lambda m: "up"))
            side = orient(msg)
            k2 = used.get((i, side), 0)
            used[(i, side)] = k2 + 1
            color = UP if side == "up" else DOWN
            ax.scatter(i, closes[i], marker=marker, s=85, color=color,
                       edgecolors="white", linewidths=0.6, zorder=5)
            dy = 13 + k2 * 15
            ax.annotate(short_label(rule, msg), (i, closes[i]),
                        xytext=(0, dy if side == "up" else -dy), textcoords="offset points",
                        ha="center", va="bottom" if side == "up" else "top",
                        fontsize=8, color=color, zorder=6,
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.65))
        ax.set_ylim(y_min - span * 0.2, y_max + span * 0.2)
        ax.set_title(f"{name}({symbol})  今收 {closes[-1]:.2f}  当日 {pct:+.2f}%  ·  {len(sigs)} 条信号",
                     fontsize=10)
        ax.grid(axis="y", color="#dddddd", lw=0.5, zorder=0)

        # x 轴刻度: 09:35 / 10:30 / 11:30 / 13:30 / 14:30 / 15:00
        want = {"09:35", "10:30", "11:30", "13:30", "14:30", "15:00"}
        ticks = [(i, b["hm"]) for i, b in enumerate(bars) if b["hm"] in want]
        ax.set_xticks([t[0] for t in ticks])
        ax.set_xticklabels([t[1] for t in ticks], fontsize=8)
        if k == 0:
            xticks, xticklabels = [t[0] for t in ticks], [t[1] for t in ticks]

    for j in range(n, len(axl)):
        axl[j].axis("off")

    fig.suptitle(f"A股今日({today}) 报信标注: 信号触发点 vs 分时走势（腾讯5分钟K线）",
                 fontsize=14)
    fig.savefig(ROOT / "charts" / f"today_signals_{today.replace('-', '')}.png",
                bbox_inches="tight")
    plt.close(fig)
    print("已保存 charts/today_signals_" + today.replace("-", "") + ".png")


if __name__ == "__main__":
    main()
