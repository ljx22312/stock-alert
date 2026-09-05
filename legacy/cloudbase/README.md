# legacy/cloudbase — 历史云端网关方案存档

当前系统**不依赖**本目录的任何代码。这里保存的是早期「云端数据库 + 云函数网关」方案的实现，
作为备份保留：当本机数据丢失、需要从历史云端找回全量数据，或想参考网关接口语义时使用。

## 内容

| 文件 | 说明 |
|---|---|
| `functions/ingest/` | 写入网关云函数：唯一写入口，x-sync-token 鉴权，按集合规则 upsert |
| `functions/api/` | 只读行情云函数：quote/daily/hour/tick/profile/stocks/signals/macro/stats |
| `bootstrap_local.py` | 一次性回填工具：从历史云端 api 拉全量种子写本机 sqlite |
| `backfill_full.py` | 历史补齐工具：分页镜像全历史日线/信号/分时到本机（需当时的管理密钥） |
| `backfill_signals.py` | 历史信号回放：用历史云端日线/小时线重跑规则生成历史信号 |
| `cloudbaserc.json` | 当时环境的 CLI 配置（函数定义与运行时） |

## 说明

- 上述脚本中的云端地址/密钥均属历史环境，云端服务下线后这些脚本即失效，仅作代码存档。
- 当前数据写入走 `server/data_service.py` 的 `/ingest`，只读走 `/api/*`，推送端在 `jobs/sync_data.py`。
