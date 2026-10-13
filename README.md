# pi-skills

Pi Coding Agent 自定义技能集（agent skills）。每个目录是一个独立技能，含 `SKILL.md`（按需加载的指令 + 触发描述）及配套脚本/参考资料。

## 技能列表

### safe-search-curl
联网获取外部信息的统一流程与安全边界。核心三件事：

- **成本分工**：`web_search`（credit）与 `curl`（token）的适用场景划分，模糊情况优先走 web_search
- **降噪过滤**：`scripts/fetch_clean.sh` 抓取前先过滤，防整页 HTML 爆上下文
- **安全边界**：prompt injection 防御、SSRF 拦截（原始 URL + 每次重定向目标复查，`SafeRedirectHandler`）、凭证卫生（搜索词与 URL 不带凭证）

`references/audit-20261012.md` 记录一轮外部安全审核的处置决策与验证方法（302 重定向穿透复现与修复、已知边界说明）。

### skill-search
在多个技能/插件目录（skills.sh、mcp.so、pi.dev/packages）跨站并行检索 skill、MCP server 与 Pi 包，跨源去重、按相关性 + 流行度排序，秒级返回统一结果。只负责"找现成的"。

### secret-defense
代码仓库硬编码密钥的审计与防护：GitHub Secret Scanning / Push Protection、gitleaks 本地与 CI 门禁，以及密钥泄漏应急响应（先轮换，再清理历史）。

### skill-creator
创建、修改与优化 agent skill 的方法论：从零新建技能、改进正文或触发描述、写测试用例，或把对话中已跑通的流程固化成可复用技能。

## 安装

将技能目录（或其符号链接）放入 agent 的技能目录，例如 pi：

```bash
ln -s /path/to/pi-skills/safe-search-curl ~/.pi/agent/skills/safe-search-curl
```

## 结构约定

```
<skill-name>/
├── SKILL.md          # 必需：frontmatter（name/description）+ 指令正文
├── scripts/          # 可选：配套脚本
└── references/       # 可选：参考资料 / 决策记录
```
