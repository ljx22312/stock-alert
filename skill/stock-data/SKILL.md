---
name: stock-data
description: 查询 StockDesk 股票数据：自选股池、实时行情（东财现抓）、历史日线、最近信号。凡涉及股票/指数的数字问题（价格、涨跌、K线、信号）都应先用本 skill 取真实数据，禁止凭记忆估算。
---

# StockDesk 股票数据

统一入口脚本（**调用本 skill 下的 `scripts/stock_api.py`，与本文件同目录，不要用环境变量替换路径**）：

```
scripts/stock_api.py
```

纯标准库，无需安装依赖。当前运行目录是 skill 根目录时可直接用上述相对路径；若不确定当前工作目录，先用绝对路径（由宿主注入，或在运行时用 `find` 定位 SKILL.md 所在目录）。

## 数据源说明

- 云端库（由上游同步，可能有分钟级延迟）：股池、历史日线、信号
- 实时行情（现抓）：东财优先、**东财断连自动切腾讯**，输出字段 `source` 标明实际来源

## 用法

```bash
# 股池目录（自选股+指数，含 symbol/name/type）
python3 scripts/stock_api.py stocks

# 实时行情（东财现抓）。symbols 用逗号分隔的6位代码；省略则取全部自选股
python3 scripts/stock_api.py realtime 601169,sh000001

# 历史日线（升序，最新在后）。limit 默认 120，最大 1000
python3 scripts/stock_api.py daily 601169 --limit 250

# 云端最新快照（上游程序写入的缓存价）
python3 scripts/stock_api.py quote 601169

# 最近信号
python3 scripts/stock_api.py signals --limit 20
```

## 约定

- 6位代码不带交易所前缀（601169）；指数用 sh000001 / sz399001 形式
- daily 输出 JSON 列表（date, open, high, low, close, volume），可直接喂给 pandas 算指标
- 需要 MA/MACD/回测等计算时：先 daily 取数，再用 python3 + pandas 现场算，不要心算
