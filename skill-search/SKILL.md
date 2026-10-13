---
name: skill-search
description: 在多个技能/插件目录（skills.sh、mcp.so、pi.dev/packages）中跨站搜索并去重 skill、MCP server 与 Pi 包，几秒内返回统一结果。当用户想找现成的 skill/技能/插件/扩展、问"有没有做 X 的 skill / 插件"、想按关键词检索技能库或 Pi 包，或提到 skills.sh、skill.sh、mcp.so、pi.dev、Pi package/extension 时使用。与 skill-creator 区分：本技能只负责"找现成的"，不创建或修改技能。
---

# 技能搜索

跨三个目录做并行检索、跨源去重、按相关性 + 流行度排序，一次给出统一结果。
目标是**快 + 全 + 不重复**：三源并发抓取，本地合并。

覆盖的目录：

| 来源 | 内容 |
| --- | --- |
| skills.sh | Vercel 的开放 Agent Skill 目录 |
| mcp.so | MCP 服务器 + Skills 目录 |
| pi.dev/packages | Pi 的包画廊（扩展 / 技能 / 提示 / 主题，即 npm 上带 `pi-package` 的包） |

## 用法

在技能目录下运行脚本（脚本仅依赖 Python 3 标准库）：

```bash
python3 scripts/search_skills.py "<关键词>"
```

带选项：

```bash
python3 scripts/search_skills.py "code review" --limit 8            # 结果条数
python3 scripts/search_skills.py "pdf" --json                       # 机器可读 JSON
python3 scripts/search_skills.py "react" --sources skills.sh,mcp.so # 限定来源
python3 scripts/search_skills.py "react" --skills-only              # 只看纯 skill，不含 MCP server / Pi 包
```

参数：

| 选项 | 说明 |
| --- | --- |
| `--limit N` | 最多返回 N 条（默认 10） |
| `--json` | 输出 JSON（含 `raw_hits` / `unique_hits` / `errors` / `results`） |
| `--sources a,b` | 来源子集：`skills.sh`、`mcp.so`、`pi.dev/packages` |
| `--skills-only` | 跳过 mcp.so 的 MCP server 索引与 pi.dev 包，只要 skill 目录 |

## 输出怎么读

每条结果包含：名称、流行度（skills.sh 是 `installs`，Pi 包是 `dl/mo` 月下载）、
仓库/作者、**出现在哪些目录**（`sources:`，即跨源去重后该技能被几站收录）、分类、
简介、链接；Pi 包还会给出 `pi install npm:<name>` 安装命令。

- 同一技能被多站收录会合并成一条，`sources` 列出全部来源——这就是"去重"的体现。
- 排序默认按相关性，其次流行度。
- 返回空 = 三个目录都没有词面匹配；换同义词/更短的关键词，或放宽 `--sources`。

## 给 agent 的建议

1. 直接跑脚本拿候选；用户口语化描述（"帮我找个能改图表的技能/插件"）时，抽 1–3 个关键词再查。
2. 结果是 skill 还是 MCP server / Pi 包，看行尾的类型标注（`MCP server` / `Pi extension` / `Pi package`）。
3. 结果里若某技能有多源背书（`sources` ≥ 2）或流行度高，质量通常更稳。
4. 需要更细的评估（star、更新时间、安全性）时，再对候选链接用 `web_search` 或直接打开补料。
5. 安装：skill 用 skills.sh 链接或 `npx skills add <owner>/<skill>`；Pi 包用结果里的
   `pi install npm:<name>`。**安装任何第三方技能/包前先审查其 SKILL.md 与脚本**（见全局规则）。

## 注意

- `skill.sh` 是停放域名（无内容）；用户多半指 `skills.sh`，脚本按 `skills.sh` 处理。
- mcp.so 的 `?q=` 未命中时会回落到推荐列表，脚本已用本地相关性打分过滤，陌生词返回空属正常。
- pi.dev/packages 的数据取自 npm（画廊即 npm 上带 `pi-package` 的包）；接口细节与失效排查见 `references/sources.md`，改版时改那里对应的抓取/正则。
