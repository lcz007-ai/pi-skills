# 数据来源与实现细节

脚本 `scripts/search_skills.py` 从三个来源并行取数，本地合并去重。下面记录每个来源的
接口、解析方式与坑，便于日后维护（接口失效时改这里）。

## 1. skills.sh（Vercel 的开放 Skill 目录）

- 站点：https://www.skills.sh（注意：常被写成 `skill.sh`，但那是 Park.io 上的**停放
  域名**，无内容；真实站点是 `skills.sh`）。
- 搜索接口（前端自己在用，**公开无需鉴权**）：

  ```text
  GET https://www.skills.sh/api/search?q=<query>&limit=<n>
  ```

  返回 JSON：`{query, count, skills:[{id, source, skillId, name, installs}]}`。
  `id` 形如 `owner/repo/skillId`；详情页 `https://www.skills.sh/<id>`。
- 官方另有 `/api/v1/*`（如 `/api/v1/skills/search`），但**要求 Vercel OIDC 令牌**，
  非 Vercel 部署场景不可用，故不用它。文档：https://www.skills.sh/docs/api 。
- **坑**：接口是模糊检索，`limit` 就是"前 N 条"，会掺入不相关结果（`react` 会返回
  `tdd`）。脚本请求比目标更多的条数，再用本地相关性打分过滤。

## 2. mcp.so（MCP 服务器 + Skills 目录）

- 站点：https://mcp.so 。服务端渲染，无需鉴权、无反爬拦截。
- 检索入口（查询参数即服务端过滤）：
  - `https://mcp.so/skills?q=<query>` —— 技能
  - `https://mcp.so/servers?q=<query>` —— MCP 服务器（`--skills-only` 可跳过）
- 解析方式：抓 HTML，匹配卡片锚点
  `<a href="/skills/<slug>" class="group border-border bg-card ...">`，再取 `<h3>`（名称）、
  `<p>`（作者行 / 描述）、`<span class="bg-secondary">`（分类）。
- **坑**：查询命中不了时页面不会返回空，而是回落到推荐/全量列表（一次可几百条）。
  脚本用本地相关性打分把"零命中"记录过滤掉，所以陌生词返回空是正常的。

## 3. pi.dev/packages（Pi 包画廊）

- 站点：https://pi.dev/packages 。这是 Pi 的扩展 / 技能 / 提示 / 主题包画廊。
- **数据本质**：画廊收录的是 npm 上带 **`pi-package` keyword** 的包（见 Pi 文档
  `docs/packages.md`：`The pi-package keyword makes an npm package eligible for discovery
  in the Pi package gallery`）。
- pi.dev 页面本身**没有服务端文本搜索**（`?q=` 被忽略；仅支持 `?type=skill` 与
  `?page=N`，共约 108 页）；前端搜索只作用于当前已加载的 50 条。因此**不抓 pi.dev**，
  改用 npm registry 搜索 API（同一批包，服务端文本检索，快）：

  ```text
  GET https://registry.npmjs.org/-/v1/search?text=keywords:pi-package%20<query>&size=<n>
  ```

  返回对象里的 `package` 字段：`name / description / keywords / publisher / links.npm /
  version / date`。脚本再**二次过滤**（keyword 必须含 `pi-package`，防止 npm 的模糊
  召回混入）。
- 月下载量（画廊里的 `x/mo`）：

  ```text
  GET https://api.npmjs.org/downloads/point/last-month/<name>
  ```

  脚本对命中的 Pi 包并发补取，作为排序/展示的流行度信号（`installs` 字段，单位 `dl/mo`）。
- 详情页与安装：`https://pi.dev/packages/<name>`，安装 `pi install npm:<name>`。
- **坑**：npm 搜索的 `total` 会明显大于画廊实际收录数，别拿它当画廊规模；以返回对象为
  准。`keywords:pi-package` 是严格过滤，但免费文本召回偏模糊，靠本地相关性再收敛。

## 去重与排序

- 合并键 = 归一化名称（`norm()`）。**Pi 包单独命名空间**（键加 `|pkg` 后缀），避免
  一个叫 `pdf` 的 Pi 包被并进同名的 skill。
- 相关性打分（`relevance()`）：词元/前缀匹配 名称 > 作者/仓库 > 描述；未命中任何词元的
  记录直接丢弃（`_base == 0`）。命中多源只小幅加分（`+2/源`），且**绝不**把零命中
  记录变成结果。
- 最终排序：`(相关性, installs/dl)` 降序。

## 失效排查

- skills.sh：404/改版 → 看页面 JS chunk 里 `/api/search` 是否改名。
- mcp.so：卡片 class 或结构变了 → 更新 `_CARD_RE` 等正则。
- pi.dev/packages：npm keyword 约定若变更（例如不再用 `pi-package`）→ 改
  `fetch_pi_packages` 里的 `text=keywords:...`；下载 API 去掉后排序会自动退化为相关性。
