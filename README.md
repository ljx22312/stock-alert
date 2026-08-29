# stock-alert — A股微信信号提醒器

盘中实时监控观察名单，触发规则时通过 WxPusher 推送微信消息。非交易程序，只做提醒。

## 你需要碰的只有两个文件

| 文件 | 用途 |
|---|---|
| `config.json` | 股池（watchlist）、轮询间隔、冷却时间、规则开关与参数 |
| `src/strategies.py` | 因子与信号规则（文件头有详细写法说明） |

## 结构

```
stock-alert/
├── config.json          # 股池 + 参数（你维护）
├── run_monitor.py       # 盘中监控入口（常驻）
├── run_daily.py         # 收盘后日线更新 + 日线信号（cron 每天一次）
├── src/
│   ├── quotes.py        # 实时快照：腾讯主用，新浪降级（批量，3秒级）
│   ├── daily.py         # 日线：腾讯主用，东财兜底（前复权）
│   ├── strategies.py    # 信号规则（你维护）
│   ├── store.py         # SQLite：日线库 + 信号日志（冷却去重）
│   └── notify.py        # WxPusher 推送（复用 ~/wechat-notify/config.json）
├── data/stock_alert.db  # SQLite 数据库（自动创建）
└── logs/                # 运行日志
```

## 数据链路（已实测）

- **盘中**：腾讯 `qt.gtimg.cn` 批量快照，一个请求带全部股池（≤60只），3 秒一轮，
  1Hz 持续压测零失败、延迟约 40ms。快照数据本身约 3 秒刷新，再快没有意义。
- **日线**：腾讯 `web.ifzq.gtimg.cn` 前复权日线，每次取最近 150 根 upsert 入库。
  东财接口限流激进（触发后封 IP 半小时以上），只作兜底。
- **运行时依赖**：仅 `requests`（系统 Python 自带），akshare 为可选增强，未接入主链路。

## 使用

```bash
# 测试推送通道
python3 run_monitor.py --push-test

# 单周期冒烟测试（拉一轮行情、评估规则、打印但不推送）
python3 run_monitor.py --once

# 盘中常驻监控（非交易时段自动休眠，连续失败自动降级并告警）
nohup python3 run_monitor.py > logs/monitor.out 2>&1 &

# 手动跑一次日线任务
python3 run_daily.py
```

## 部署（crontab 示例）

```
# 盘中监控：每个交易日 9:25 启动，15:05 停止（或用进程守护常驻）
25 9 * * 1-5  cd /home/admin/stock-alert && nohup python3 run_monitor.py >> logs/monitor.out 2>&1 &
5 15 * * 1-5  pkill -f run_monitor.py
# 收盘后日线任务
35 15 * * 1-5 cd /home/admin/stock-alert && python3 run_daily.py >> logs/daily.out 2>&1
```

## 内置规则（在 config.json 中开关）

盘中：
- `rapid_move`：5 分钟内涨跌幅超 ±2%（参数 `window_min` / `threshold_pct`）
- `day_extreme_break`：突破/跌破日内高低点

日线（收盘后评估）：
- `ma_cross`：收盘价上穿/下穿 MA20（参数 `ma_window`）

所有规则按 (股票, 规则) 冷却去重，默认 30 分钟（`cooldown_minutes`）。
日线信号当天只推一次。

## 已知边界

- 交易时段判断只看"周一至五 + 时间"，法定节假日当天会空跑（拉到的行情日期
  不是今天，规则不会误触发，但进程不退出）。如需精确交易日历可后续加。
- 推送依赖 WxPusher 免费额度，规则触发频率高时请加大冷却时间。
