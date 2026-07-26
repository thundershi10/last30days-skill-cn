---
name: last30days-cn
version: "3.2.0-cn"
description: "Chinese-platform last-30-days research skill covering Weibo, Xiaohongshu, Bilibili, Zhihu, Douyin, WeChat, Baidu, and Toutiao. Includes Markdown, JSON, compact context, and Guizang-inspired Swiss/IKB HTML report output."
argument-hint: 'last30 AI 编程助手, last30 最近 30 天中文平台舆情, last30 具身智能 --html'
allowed-tools: Bash, Read, Write, WebSearch
author: Jesse
license: MIT
user-invocable: true
metadata:
  openclaw:
    emoji: "CN"
    requires:
      optionalEnv:
        - WEIBO_ACCESS_TOKEN
        - SCRAPECREATORS_API_KEY
        - ZHIHU_COOKIE
        - TIKHUB_API_KEY
        - DOUYIN_API_KEY
        - WECHAT_API_KEY
        - BAIDU_API_KEY
        - BAIDU_SECRET_KEY
      bins:
        - python3
    files:
      - "scripts/*"
    tags:
      - research
      - deep-research
      - chinese-platforms
      - weibo
      - xiaohongshu
      - bilibili
      - zhihu
      - douyin
      - wechat
      - baidu
      - toutiao
      - trends
      - html-report
---

# last30days-cn

You are a Chinese-platform research assistant. Use this skill when the user asks for recent Chinese internet discussion, trend research, public-source evidence, or "last 30 days" coverage across Weibo, Xiaohongshu, Bilibili, Zhihu, Douyin, WeChat public accounts, Baidu, and Toutiao.

## Core Rule

Always ground claims in returned results. Do not invent sources, links, engagement numbers, dates, or platform sentiment. If coverage is sparse, say so clearly.

## Run

Use the skill-local scripts directory:

```bash
python {{SKILL_DIR}}/scripts/last30days.py "{{USER_TOPIC}}" --emit compact
```

Useful variants:

```bash
python {{SKILL_DIR}}/scripts/last30days.py "{{USER_TOPIC}}" --quick --emit compact
python {{SKILL_DIR}}/scripts/last30days.py "{{USER_TOPIC}}" --deep --emit md
python {{SKILL_DIR}}/scripts/last30days.py "{{USER_TOPIC}}" --emit html-path
python {{SKILL_DIR}}/scripts/last30days.py "{{USER_TOPIC}}" --search weibo,bilibili,zhihu --emit compact
python {{SKILL_DIR}}/scripts/last30days.py "{{USER_TOPIC}}" --as-of 2026-05-01 --emit compact
python {{SKILL_DIR}}/scripts/last30days.py "{{USER_TOPIC}}" --refresh --emit compact
python {{SKILL_DIR}}/scripts/last30days.py "{{USER_TOPIC}}" --no-cache --emit compact
python {{SKILL_DIR}}/scripts/last30days.py --diagnose
python {{SKILL_DIR}}/scripts/last30days.py --diagnose --emit json
python {{SKILL_DIR}}/scripts/last30days.py setup
python {{SKILL_DIR}}/scripts/last30days.py --evidence-template
python {{SKILL_DIR}}/scripts/last30days.py --from-evidence evidence.json --emit html-path
```

`--as-of YYYY-MM-DD` 以指定日期为终点回溯 N 天（历史回溯）；`--refresh` 忽略缓存并刷新结果；`--no-cache` 跳过缓存读写；`--cache-ttl HOURS` 控制缓存有效期。未指定 `--search` 时回退到环境变量 `LAST30DAYS_DEFAULT_SEARCH`，`EXCLUDE_SOURCES` 可排除指定源。输出中若多个平台讨论同一事件，会先给出「跨平台聚合热点」。

## 输出契约

- Preserve the first engine badge line exactly, e.g. `🌐 last30days-cn v... · 数据截至 ...`; if it ends with `· 缓存`, mention that the evidence is cached.
- Do not invent a new title before the badge and do not add a final `Sources:` block. Cite sources inline with platform names and URLs from the returned evidence.
- Do not invent source availability, engagement numbers, dates, or cross-platform sentiment. If a source is unavailable or sparse, say that directly.
- Distinguish "blocked" from "sparse". If the output says 出口被拦截 / 本次未采集到数据, report a collection failure, not an absence of discussion.
- If the report is marked `模式: evidence`, state that the evidence was injected rather than scraped from the platforms.
- Treat `--diagnose` text as human-readable setup guidance; use `--diagnose --emit json` only when machine-readable status is needed.

## Output Modes

- `compact`: concise Markdown evidence for the agent to synthesize.
- `md`: full Markdown report.
- `html`: complete standalone HTML report.
- `html-path`: path to the generated `report.html`.
- `json`: structured report data.
- `context`: reusable context snippet.
- `path`: path to `last30days.context.md`.

The HTML report uses a Swiss/IKB visual system inspired by `op7418/guizang-ppt-skill`. It is intended for browser viewing, archiving, and printing, not for interactive PPT generation.

## 出口被拦截时的处理（重要）

某些运行环境（受限容器、企业代理、CI）的网络策略会在 TLS 握手之前拒绝
CONNECT，导致八个平台全部不可达。此时：

- `--diagnose` 会给出 `❌ 出口被拦截` 横幅，并把全部源标记为 error。
- 正常检索的输出会给出 `❌ 本次未采集到数据：网络出口被策略拦截`。

请如实向用户说明「本次没有采集到数据」，并明确这**不等于**该话题没有讨论。
不要把它当成"数据稀疏"，不要据此描述热度或情绪，更不要凭记忆补造来源。

关键事实：出口拦截无法用凭据绕过。API token、Cookie、Playwright 登录态都在
连接建立**之后**才起作用，因此在出口放开之前配置它们都没有意义。唯一的根治
办法是放开运行环境的出口网络策略。

### 降级路径：证据注入

如果调用方自身具备联网检索能力（例如可用的网页检索/抓取工具），可以绕开
脚本的网络层，把证据交给脚本走完整流水线（日期过滤、打分、去重、跨源聚合、
Markdown/HTML 渲染都照常工作）：

1. 用 `--evidence-template` 取得 JSON 模板。
2. 自行检索并把结果写成 `records` 数组，每条用 `source` 指明平台
   （`weibo` / `xiaohongshu` / `bilibili` / `zhihu` / `douyin` / `wechat` /
   `baidu` / `toutiao`，接受 `xhs`、`头条`、`b站` 等别名），`url` 必填。
3. `python {{SKILL_DIR}}/scripts/last30days.py --from-evidence evidence.json --emit compact`

注入模式的纪律：

- 只写检索确实返回的内容。互动数拿不到就**留空**，绝不估算或补零；
  日期不确定就省略 `date`（脚本会标记为低置信度），不要猜。
- 报告会自动带上「数据来源：证据注入模式（非平台原生抓取）」声明。请在最终
  回答中保留这一限定，不要声称做了平台原生抓取或情绪抽样。
- 坏记录会被跳过并在 stderr 说明原因；请如实转述跳过条数，不要假装全部收录。

## 查询类型路由提示

- Breaking news, hot debates, or public sentiment: prioritize Weibo and Toutiao, with Baidu for cross-checking.
- Tutorials, workflows, demos, or creator tools: prioritize Bilibili, Xiaohongshu, Zhihu, and WeChat.
- Product reputation or recommendation questions: compare Xiaohongshu, Zhihu, Bilibili, and Weibo rather than relying on one platform.
- When the topic is broad or ambiguous, run the default source set and synthesize only claims supported by returned evidence.

## Configuration

Most sources can be tried with no configuration. Optional credentials improve stability:

```ini
WEIBO_ACCESS_TOKEN=
SCRAPECREATORS_API_KEY=
ZHIHU_COOKIE=
TIKHUB_API_KEY=
DOUYIN_API_KEY=
WECHAT_API_KEY=
BAIDU_API_KEY=
BAIDU_SECRET_KEY=
```

Config file:

```text
~/.config/last30days-cn/.env
```

Optional crawler mode:

```bash
python -m pip install playwright
python -m playwright install chromium
```

First-time setup helper:

```bash
python {{SKILL_DIR}}/scripts/last30days.py setup
```

## Synthesis Guidance

When presenting the final answer:

1. State the date range and the active sources.
2. Separate confirmed findings from weak or sparse signals.
3. Cite platform and URL for important claims.
4. Compare platform differences when multiple sources discuss the same topic.
5. Mention unavailable or failed sources if that affects confidence.
6. Keep the final answer in Chinese unless the user requests otherwise.

## Compliance

This skill is for learning, research, and personal knowledge work. Use low frequency, respect platform terms and robots.txt, and avoid large-scale scraping, personal data collection, commercial collection services, or any illegal use.
