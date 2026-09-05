"""验证 fast_drop / slow_drop 两条新规则：全股池 16 个交易日模拟触发 + 可视化。

与生产路径完全一致的评估: 逐根 5 分钟 bar 构造 RuleContext(开盘基准回退今开),
应用与 run_monitor 相同的 30 分钟冷却; 触发点标注到价格图上。

用法: /usr/bin/python3 validate_drop_rules.py [symbol ...]
  不带参数: 输出全池触发表, 并对最典型的 3 只(默认 600916/600801/002155)画图
  带参数:   对指定股票画图并输出其触发明细
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

from backtest_week import fetch_m5
from monitor.daily import _market_code
from monitor.store import Store
from monitor.strategies import RuleContext, fast_drop, slow_drop

TEST_DAYS = 16
COOLDOWN = 30 * 60  # 秒; 与 config 全局一致
DEFAULT_PLOT = ["600916", "600801", "002155"]


def simulate(symbol: str, name: str, bars, daily_all, params_cfg: dict) -> list[tuple]:
    """逐 bar 模拟两条新规则(30 分钟冷却), 返回 [(ts, rule, msg), ...]。"""
    by_date: dict[str, list[dict]] = {}
    for b in bars:
        by_date.setdefault(b["date"], []).append(b)
    daily_by_date = {b["date"]: b for b in daily_all}
    dates_hist = sorted(daily_by_date)
    day_open = {}   # date -> 今开
    prev_close = {}  # date -> 昨收
    for d in sorted(by_date):
        day_open[d] = daily_by_date[d]["open"] if d in daily_by_date else by_date[d][0]["close"]
        idx = [x for x in dates_hist if x < d]
        prev_close[d] = daily_by_date[idx[-1]]["close"] if idx else by_date[d][0]["close"]

    last_fire = {"fast_drop": 0, "slow_drop": 0}
    out = []
    for d in sorted(by_date):
        bars_d = by_date[d]
        for i, b in enumerate(bars_d):
            intraday = [(x["ts"], x["close"], 0.0) for x in bars_d[:i + 1]]
            quote = {"price": b["close"], "open": day_open[d],
                     "prev_close": prev_close[d],
                     "pct_chg": (b["close"] - prev_close[d]) / prev_close[d] * 100}
            ctx = RuleContext(symbol=symbol, name=name, quote=quote, intraday=intraday)
            for rule, fn in (("fast_drop", fast_drop), ("slow_drop", slow_drop)):
                try:
                    ctx.params = params_cfg.get(rule, {})  # 与生产一致: rules 参数来自 config
                    msg = fn(ctx)
                except Exception:
                    continue
                if msg and b["ts"] - last_fire[rule] >= COOLDOWN:
                    last_fire[rule] = b["ts"]
                    out.append((b["ts"], rule, msg))
    return out


def short_msg(rule: str, msg: str) -> str:
    m = re.search(r"\d+分钟(涨|跌) ([^，]+)", msg)
    if not m:
        return "快" if rule == "fast_drop" else "走"
    val = m.group(2).replace("元", "")  # '0.70元(-2.55%)' / '2.57%'
    tag = "快" if rule == "fast_drop" else "走"
    return f"{tag}{m.group(1)} {val}"


def plot_symbol(symbol: str, name: str, bars, sigs, out_png: str):
    dates = sorted({b["date"] for b in bars})
    x = range(len(bars))
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    sig_by_bar: dict[int, list[tuple]] = {}
    for ts, rule, msg in sigs:
        i = min(range(len(bars)), key=lambda j: abs(bars[j]["ts"] - ts))
        sig_by_bar.setdefault(i, []).append((rule, msg))

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(16, 8.5), dpi=150, sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1], "hspace": 0.06})
    bounds = []
    for d in dates:
        idx = [i for i, b in enumerate(bars) if b["date"] == d]
        if idx:
            bounds.append((idx[0], idx[-1], d))
    for k, (i0, i1, _) in enumerate(bounds):
        if k % 2 == 1:
            ax.axvspan(i0 - 0.5, i1 + 0.5, color="#000000", alpha=0.035, lw=0)
        ax.axvline(i0 - 0.5, color="#bbbbbb", lw=0.8, ls=":", zorder=1)

    ax.plot(x, closes, color="#3a6ea5", lw=1.3, zorder=3, label="5分钟收盘价")
    ma20 = [sum(closes[i - 3:i + 1]) / 4 if i >= 3 else None for i in x]
    ax.plot(x, ma20, color="#e8a04c", lw=0.9, alpha=0.85, zorder=2, label="20分钟均价")

    n_same = {}
    vol_colors = {}
    for i, infos in sig_by_bar.items():
        for k, (rule, msg) in enumerate(infos):
            up = "分钟涨" in msg
            if rule == "fast_drop":
                marker, color = ("^", "#d94f4f") if up else ("v", "#d2691e")
            else:
                marker, color = ("X", "#d94f4f") if up else ("X", "#7b3294")
            vol_colors[i] = color
            dx = 14 * (k - 0.5)
            ax.scatter(i, closes[i], marker=marker, s=110, color=color,
                       edgecolors="white", linewidths=0.7, zorder=6)
            ax.annotate(short_msg(rule, msg), (i + dx / 20, closes[i]),
                        xytext=(dx, 13 + 15 * (k // 2)), textcoords="offset points",
                        ha="center", va="bottom",
                        fontsize=8, color=color, zorder=7,
                        bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.7))

    y_min, y_max = min(closes), max(closes)
    span = (y_max - y_min) or 1.0
    ax.set_ylim(y_min - span * 0.24, y_max + span * 0.24)
    ax.set_ylabel("价格(元)", fontsize=10)
    ax.set_title(f"{name}({symbol})  快速下挫/持续走弱 验证  {dates[0]} ~ {dates[-1]}  ·  "
                 f"带冷却后触发 {len(sigs)} 条", fontsize=13, pad=10)
    ax.grid(axis="y", color="#dddddd", lw=0.6, zorder=0)
    handles = [
        Line2D([], [], color="#3a6ea5", lw=1.3, label="5分钟收盘价"),
        Line2D([], [], color="#e8a04c", lw=0.9, label="20分钟均价"),
        Line2D([], [], color="#d2691e", marker="v", ls="none", ms=9, label="快速下挫  30分跌0.4元/1.5%"),
        Line2D([], [], color="#d94f4f", marker="^", ls="none", ms=9, label="快速拉升  30分涨0.4元/1.5%"),
        Line2D([], [], color="#7b3294", marker="X", ls="none", ms=8, label="持续走弱  2h跌0.6元/2.5%"),
        Line2D([], [], color="#d94f4f", marker="X", ls="none", ms=8, label="持续走强  2h涨0.6元/2.5%"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8.5, ncol=3,
              framealpha=0.9)

    axv.bar(x, vols, width=0.85, color="#b8c4d0", zorder=2)
    for i, color in vol_colors.items():
        axv.bar(i, vols[i], width=0.85, color=color, zorder=3)
    axv.set_ylabel("5分钟量(手)", fontsize=9)
    axv.grid(axis="y", color="#dddddd", lw=0.6, zorder=0)
    axv.set_xticks([(i0 + i1) / 2 for i0, i1, _ in bounds])
    axv.set_xticklabels([d[5:] for _, _, d in bounds], fontsize=10)
    axv.set_xlim(-1, len(bars))
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存 {out_png}  ({len(sigs)} 条触发)")


def main():
    cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))
    store = Store(cfg["db_path"])
    names = {w["symbol"]: w["name"] for w in cfg["watchlist"]}
    plot_list = sys.argv[1:] or DEFAULT_PLOT

    print("===== 全池 16 个交易日验证: 触发统计(已含30分钟冷却, 双向) =====")
    print(f"{'股票':<10}{'快动(30min)':>15}{'缓动(2h)':>12}{'合计/周(约)':>12}")
    stats = []
    all_sigs = {}
    for item in cfg["watchlist"]:
        s, name = item["symbol"], item["name"]
        bars = fetch_m5(_market_code(s), count=720)
        daily_all = store.get_daily(s)
        sigs = simulate(s, name, bars, daily_all, cfg["params"])
        all_sigs[s] = sigs
        fast = sum(1 for _, r, _ in sigs if r == "fast_drop")
        slow = sum(1 for _, r, _ in sigs if r == "slow_drop")
        ndays = len({b["date"] for b in bars})
        stats.append((s, name, fast, slow, ndays))
        print(f"{name:<10}{fast:>16}{slow:>14}{(fast+slow)*5/ndays:>12.1f}")

    for s in plot_list:
        if s not in all_sigs:
            continue
        name = names.get(s, s)
        bars = fetch_m5(_market_code(s), count=720)
        print(f"\n--- {name}({s}) 触发明细 ---")
        for ts, rule, msg in sorted(all_sigs[s]):
            print(f"  {datetime.fromtimestamp(ts):%m-%d %H:%M}  {msg}")
        plot_symbol(s, name, bars, all_sigs[s],
                    str(ROOT / "charts" / f"drop_{s}.png"))


if __name__ == "__main__":
    main()
