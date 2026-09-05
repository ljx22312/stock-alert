# web-cloud/ — CloudBase 托管站专用前端变体

本目录存放 **CloudBase 静态托管站**（`ljx-d1gjpcu23fa094e67-1471691831.tcloudbaseapp.com`）
在线使用的 `app.js` / `ai.js`，与仓库 `web/` 的本机同源版**只有接线差异、功能同代**：

| 文件 | 差异 |
|---|---|
| `app.js` | 第 2 行 `const API` 指向云端函数 `https://<env>.service.tcloudbase.com/api`（本机版为同源 `'/api'`） |
| `ai.js` | AI 请求走 CloudBase NoSQL HTTP API（`AI_ENV` + 匿名 publishable key `AI_PKEY`，Bearer 鉴权；本机版为同源 `/collections/*` 免鉴权） |

其余前端文件（index.html / style.css / ai.css / echarts.min.js）与 `web/` 完全一致，无需归档。

## ⚠️ 部署注意

- **部署到 CloudBase 托管**：用本目录的 `app.js` / `ai.js` + `web/` 的其余文件。
- **部署到本机 nginx**（root 指向仓库 `web/`）：直接用 `web/`，**不要**把 `web/` 原样传到 CloudBase
  （相对路径 `/api`、`/collections` 在云端没有反代目标，数据和 AI 都会挂）。
- CloudBase 端数据链路依赖云端云函数 `api` / `ingest`（源码在 `legacy/cloudbase/functions/`，
  仍部署在 CloudBase 上运行）与云端 NoSQL 集合 `ai_requests` / `ai_replies`（由
  stock-ai 机的 `worker.js` CloudBase 实例出站轮询处理）。
