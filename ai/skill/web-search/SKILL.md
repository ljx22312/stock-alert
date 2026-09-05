---
name: web-search
description: 联网搜索与网页抓取：获取股票数据之外的实时信息（新闻、公告、宏观事件、公司基本面背景）。当用户询问最近发生的事、新闻、或模型知识可能过时的信息时使用。后端支持博查(Bocha)与 Tavily，读环境变量 BOCHA_API_KEY / TAVILY_API_KEY。
---

# Web Search

脚本（纯标准库）：

```bash
scripts/search.py "查询词"            # 搜索，返回标题/链接/摘要
scripts/search.py "查询词" --n 10    # 指定条数
scripts/fetch_page.py <url>          # 抓取网页正文（转纯文本，限长）
```

## 后端选择

按环境变量自动选择，都未配置时明确告知用户"搜索功能未配置 API key"（不要编造搜索结果）：

1. `BOCHA_API_KEY`（博查，国内推荐）
2. `TAVILY_API_KEY`

## 使用准则

- 新闻/公告类问题：先搜后答，引用来源链接
- 股价/行情数字**不要**用搜索获取，用 `stock-data` skill 的实时接口
- 搜索结果只是摘要，深入阅读某条结果时用 fetch_page.py 抓正文
