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

---

## 云端数据同步（CloudBase 方案 B）

本地数据链路不变；新增 `sync_cloud.py` 把 SQLite 数据增量推送到 CloudBase
云端数据库，供网页可视化与手机端浏览调用（提醒仍由本地监控负责，实时性不依赖云端）。

### 命令

| 命令 | 作用 |
|---|---|
| `python3 sync_cloud.py stocks` | 推送股池 + 指数目录（改 `config.json` 后手动跑一次） |
| `python3 sync_cloud.py backfill [--symbols 601169,...]` | 一次性全历史日线回填（2015 起，腾讯窗口翻页） |
| `python3 sync_cloud.py daily` | 收盘后增量日线同步（幂等 upsert） |
| `python3 sync_cloud.py hours` | 60分钟线同步（腾讯 mkline，滚动窗口约 4 个月） |
| `python3 sync_cloud.py snapshot` | 盘中最新快照推送（覆盖式）+ 分时历史帧 ticks |
| `python3 sync_cloud.py signals` | 信号日志增量推送（水位在 `cloud_sync.state.json`） |
| `python3 sync_cloud.py profile` | 日内量能分布（同时段 5 分钟均量基线） |
| `python3 sync_cloud.py all` | stocks + daily + hours + signals + snapshot + profile 一次跑完 |

### 配置

- `cloud_sync.json`（不入库）：`{"url": "https://<env>.service.tcloudbase.com/ingest", "token": "..."}`
- 云端写入入口 `ingest`（令牌校验）、只读接口 `api`：
  - `GET /api/quote?symbols=601169,sh000300` 最新快照
  - `GET /api/daily?symbol=601169&limit=250` 历史日线
  - `GET /api/hour?symbol=601169&limit=320` 60分钟线（升序，最新在后）
  - `GET /api/tick?symbol=601169` 当日分时帧（无则回退最近交易日）
  - `GET /api/profile?symbol=601169` 日内量能分布（48 个 5 分钟时段的均量）
  - `GET /api/stocks?type=stock|index` 股池/指数目录
  - `GET /api/signals?limit=50` 最近信号
  - `GET /api/stats` 各集合文档数
- 函数代码在 `cloud/functions/`，部署：`cd cloud && tcb fn deploy ingest --path /ingest` / `tcb fn deploy api --path /api`
- 指数目录在 `sync_cloud.py` 的 `INDICES` 常量里维护。

### crontab（已配置）

- 15:45 交易日收盘后：`all`（日线 + 小时线 + 量基线 + 信号 + 快照/分时帧）
- 9-11/13-15 点每 5 分钟：快照 + 分时帧推送
- 每小时：信号增量

## AI Agent 系统（网页 AI 助手 + 本机 pi coding agent）

网页「AI 助手」入口（⚡快速 / 🤖Agent 两模式）的推理发生在本机，不在云端：

```
前端(匿名) --写--> 云端 ai_requests {mode, question, skill, status}
本机 worker.js(API Key, 出站轮询) --取走--> 调模型 / spawn pi --写--> 云端 ai_replies
前端 --轮询 ai_replies--> 流式拼字显示（含 thinking 思考中间推送，可随时停止）
```

- **⚡快速**：直接调模型 API（kimi/k3、oczen/deepseek-v4-flash…），附带 stock-data 工具查真实行情，便宜快。
- **🤖Agent**：spawn `pi coding agent`（RPC 模式），按用户选择的专家 skill 载入 `skill/<skill-name>/SKILL.md`，以该领域专家身份工作；能自己写 Python 跑计算/回测。Agent 会话落在 `~/.pi/agent/sessions/<session_id>/`，本地终端 `pi --resume` 可找回网页产生的对话继续。

### 相关文件

| 文件 | 作用 |
|---|---|
| `worker.js` | 出站轮询 worker：读 ai_requests → 处理 → 写 ai_replies（SSE 流式解析、thinking 推送、stop 中止、agent 会话复用/回收） |
| `server.js` | 可选：本地 HTTP shell（Bearer 鉴权 + SSE 透传），无需它也可由 worker.js 独立轮询工作 |
| `extensions/providers.js` | pi 模型 provider 注册（kimi / oczen）+ `before_agent_start` 注入专家身份 |
| `skill/` | 17 个 skill：15 个专家领域（估值、回测、因子挖掘、财报…）+ `stock-data`（行情工具）+ `web-search`（联网搜索） |
| `.env.example` | 复制为 `.env` 填真实密钥（`.env` 不入库） |

### 部署要点

1. 安装 pi coding agent（`@mariozechner/pi-coding-agent`），模型密钥写入 `.env`：`KIMI_API_KEY` / `OPENCODE_API_KEY`，以及 CloudBase 管理 `CB_API_KEY`（worker 写入云端用）。
2. 前端 `web/ai.js` 里的 `AI_ENV` / `AI_PKEY` 是 CloudBase publishable key（匿名 scope，设计上公开，仅用于前端初始化 SDK）。
3. 云端函数只需 `api` + `ingest`（已有），AI 会话数据走 `ai_requests` / `ai_replies` 两个集合，无需额外部署。
4. 可选 `SKILL_BASE` 环境变量指向自定义 skill 目录（默认仓库内 `./skill`）。
