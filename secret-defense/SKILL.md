---
name: secret-defense
description: 为代码仓库落地与审计「密钥泄漏三层防御」——GitHub Secret Scanning、Push Protection、gitleaks 本地与 CI 拦截。当用户提到以下任何一种时使用：防止密钥/凭证/token 泄漏、开启 secret scanning 或 push protection、配置 gitleaks 或 pre-commit 密钥扫描、给 CI 加密钥门禁、审计仓库的防泄漏覆盖度、密钥已经提交上去了怎么办、要不要轮换、怎么清理 git 历史。不用于常规代码审查、依赖漏洞扫描或基础设施密钥管理（KMS/Vault）。
---

# 密钥泄漏三层防御

三层机制、四个检查点。三层是**拦截位置**的划分，gitleaks 在本地和 CI 各占一个检查点，所以落地时要盯 4 个开关。

## 输出什么

先判断用户要哪一种：

- **审计模式**（"我们仓库防泄漏做得怎么样""帮我看看"）→ 给覆盖度报告：4 个检查点各自状态 + 缺口 + 按性价比排序的补漏动作。
- **落地模式**（"开启 push protection""加 gitleaks"）→ 直接产出配置文件与可执行命令，说明每步的影响与代价。
- **应急模式**（"密钥泄漏了""token 提交上去了"）→ 先走 [references/incident-response.md](references/incident-response.md)，顺序是**先轮换、再清理**。

三种模式都要交付「改了什么 + 会带来什么新约束」，不能只给配置。

## 三层模型

| 层 | 组件 | 生效点 | 拦得住 | 拦不住 |
|---|---|---|---|---|
| 1 | gitleaks pre-commit | 开发者本机，commit 前 | secret 不进历史；无需轮换、无需重写历史 | `--no-verify` 绕过、未装 hook 的机器 |
| 2 | gitleaks CI 门禁 | 服务端 CI | 绕过本地的一切提交；全历史扫描 | 已经合并进主干的 secret |
| 3 | GitHub Push Protection | GitHub 服务端，push 时 | 高置信度 secret 不进远端；产生审计记录 | 自研格式、泛化密码；本地已有 commit |
| 4 | GitHub Secret Scanning | GitHub 服务端，持续 | 全历史 / PR / issue / wiki 告警；通知厂商吊销 | 不阻断任何操作（纯事后告警） |

**为什么四个都要**：本地层可被绕过；Push Protection 只认高置信度 provider patterns；Secret Scanning 只告警不阻断。任何单层都有明确漏口。

## 铁律

1. **已泄漏的密钥必须轮换，删文件不算修复。** 一旦进了 git 历史（哪怕只 push 过一次），就该认定已泄漏——历史能被克隆、缓存、fork。轮换完再谈清理历史，顺序反了等于没修。
2. **别把 gitleaks pre-commit 当唯一防线。** 它可被 `--no-verify`、`SKIP=gitleaks` 绕过，新 clone 的机器默认没装 hook。它是体验层，不是门禁层。
3. **不要靠收紧规则来压误报。** 误报阻塞 commit 会逼团队习惯性绕过，防线整体失效。正确做法是 allowlist（路径/regex/stopwords）+ `# gitleaks:allow` 注释，把例外写进仓库而非写进人情。
4. **先查仓库可见性和套餐再承诺方案。** 公开仓库免费且默认已开；私有仓库要 GitHub Secret Protection 付费附加组件。先确认再给建议，否则方案跑不起来。
5. **存量改造要先做基线。** 已有历史的老仓库直接上 CI 门禁会被几十条历史 finding 淹没，先出 baseline 再开门禁，否则第一次就被禁用了。

## 流程

### 第 0 步：侦察（必做）

```bash
python scripts/check_coverage.py <repo_path>
```

脚本会查明：仓库可见性、GitHub 侧两个开关、pre-commit 配置与 hook 安装状态、CI 里有没有 gitleaks、gitleaks 版本是否落后。拿到缺口表再往下走，不要凭感觉假设用户"应该已经开了"。

可见性决定后面的分叉：

- **公开仓库** → Secret Scanning、Push Protection 免费，URL 可猜的 API 调用即可开启；注意 GitHub.com 的用户级 push protection 默认就是开的。
- **私有仓库** → 需要 GitHub Secret Protection（$19 / active committer / 月）。免费档拿不到 validity checks、generic patterns、Copilot secret scanning、bypass 控制、Security overview 洞察。用户问"能不能免费"时，坦诚说明并给替代路径（本地+CI 层做厚）。

### 第 1 步：GitHub 侧两个开关

细节、API 调用、免费档差异见 [references/github.md](references/github.md)。

要点：`PATCH /repos/{owner}/{repo}` 的 `security_and_analysis` 里改 `secret_scanning` 与 `secret_scanning_push_protection`。开完要提醒：绕过后会产生 alert + audit log + 邮件，默认**任何 write 权限的人都能绕过**。

### 第 2 步：本地层（gitleaks pre-commit）

细节见 [references/gitleaks.md](references/gitleaks.md)，直接取模板：

- [assets/pre-commit-config.yaml](assets/pre-commit-config.yaml) — 合并进项目的 `.pre-commit-config.yaml`
- [assets/gitleaks.toml](assets/gitleaks.toml) — allowlist 起步模板，放仓库根目录

三个 hook 变体按环境选，这一步最容易踩坑：

| hook id | 需要 | 适用 |
|---|---|---|
| `gitleaks` | **Go 工具链**（首次会编译） | 有 Go 的机器；CI 里最干净 |
| `gitleaks-docker` | Docker | 不想装 Go，团队已有 Docker |
| `gitleaks-system` | 自己保证 `gitleaks` 在 PATH | 已通过包管理器统一装好 |

推广时记得：`pre-commit install` 只对当前 clone 生效，新 clone 的人没有 hook。要让全团队默认带上，用 `pre-commit init-templatedir ~/.git-template` 然后告知大家设置 `git config --global init.templateDir`。

### 第 3 步：CI 门禁

模板：[assets/gitleaks-ci.yml](assets/gitleaks-ci.yml)。

两条路，按组织情况选：

- **`gitleaks/gitleaks-action@v3`** —— 省事，但**组织仓库必须配 `GITLEAKS_LICENSE`**（个人账号不需要）。用 v2 会失败，Node 20 已被 GitHub 移除。
- **直接跑二进制** —— 免 license，下载 release 后 `gitleaks git --redact --exit-code 1`。组织里不想买 license 就走这条。

放 baseline 的场景：老仓库先 `gitleaks git --report-path baseline.json`，再 `--baseline-path baseline.json`，让门禁只拦新增。

### 第 4 步：交付

按输出模板给报告，把"新引入的约束"显式写出来（例如：误报会阻塞 commit、直接 push 可能被拒、bot 提交会被拦）。

## 输出模板（审计模式）

```markdown
## 密钥泄漏防御覆盖度：<repo>

| 检查点 | 状态 | 说明 |
|---|---|---|
| Secret Scanning | ✅ / ❌ / ？ | |
| Push Protection | | |
| gitleaks pre-commit | | rev 版本、hook 是否已 install |
| gitleaks CI 门禁 | | |

**主要缺口（按性价比排序）**
1. … —— 影响：… ；动作：… ；代价：…

**新引入的约束**
- …

**遗留风险**
- …（说不清的宁可写"未验证"）
```

## 坑清单

- push 被拒后只删文件、不重写本地历史 → 反复被同一个 commit 拦。必须 reset/rebase/filter-repo。
- 本地规则版本与 CI 不一致 → "本地过了 CI 挂了"。CI 里钉死版本。
- pinned `rev` 长期不 bump → 漏掉新厂商模式。定期 `pre-commit autoupdate`，脚本会报落后多少。
- 假 token 形状的测试 fixture 被拦 → 用 `[[allowlists]]` 的 `paths` 覆盖 `tests/`，别用 `--no-verify`。
- 厂商收到通知后**直接吊销** key → 生产会突然中断，方案里要给预案（提前轮换、灰度）。
- 把 Secret Scanning 的告警数量当 KPI → 会诱发 dismiss，失去意义。

## 已知不适用的场景

- GitLab / Bitbucket：三层模型和 gitleaks 部分仍成立，GitHub 侧开关不适用（当前只覆盖 GitHub）。
- 运行时密钥（KMS/Vault/环境变量注入）不在范围内，本技能只管"硬编码进仓库的密钥"。
