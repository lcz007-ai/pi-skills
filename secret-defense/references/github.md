# GitHub 侧：Secret Scanning 与 Push Protection

核实日期 2026-10-10。定价与免费范围会变，给用户结论前若距此日期已久，先核对 https://github.com/security/plans 。

## 免费 / 付费边界

GitHub 把两件事拆成了独立附加组件：

| 附加组件 | 价格 |
|---|---|
| GitHub Secret Protection | $19 USD / active committer / 月 |
| GitHub Code Security | $30 USD / active committer / 月 |

**公开仓库免费的子集**（默认启用，无需付费）：

- Secret scanning、Push protection
- Provider patterns（与 AWS / Azure / GCP 等厂商协作的高置信度模式）
- Provider notification

**只有付费档才有**（公开仓库也拿不到）：

- Validity checks（判断该密钥当前是否仍然有效）
- Copilot secret scanning（AI 检测非结构化密码）
- Generic patterns（HTTP auth header、connection string、私钥等自研格式）
- Push protection bypass controls / delegated bypass
- Security overview 洞察、Scan history API

私有仓库要这两个功能，必须买 Secret Protection。给用户的替代路径：私有仓库若不想付费，就把 gitleaks 的本地层和 CI 层做厚（gitleaks 的规则集比 GitHub 免费档宽，能补上 generic patterns 的缺口）。

## 开启方式

### API（推荐，可脚本化）

`security_and_analysis` 是 `PATCH /repos/{owner}/{repo}` 的一个字段：

```bash
gh api -X PATCH repos/{owner}/{repo} \
  -f security_and_analysis[secret_scanning][status]=enabled \
  -f security_and_analysis[secret_scanning_push_protection][status]=enabled
```

用 `gh api repos/{owner}/{repo}` 可以读回当前状态。可读写的相关开关：

| 字段 | 含义 |
|---|---|
| `secret_scanning` | 检测开关 |
| `secret_scanning_push_protection` | 阻断开关 |
| `secret_scanning_non_provider_patterns` | generic patterns（付费） |
| `secret_scanning_delegated_bypass` | 委托绕过（付费） |
| `advanced_security` | 旧字段，独立产品化后语义有变化，一般不用动 |

注意：读 `security_and_analysis` 需要仓库 admin 权限，或组织的 owner / security manager。没权限时 API 会**不返回该字段**（不是报错），别把"没看到"当成"没开"。

### 组织级批量开启

组织 Settings → Code security 里可以配"新仓库默认开启"和批量应用到现有仓库。组织 owner 也可用 security manager 角色委派。给用户建议时优先用组织级默认设置，比逐个仓库 PATCH 稳。

### UI 路径

仓库 Settings → Security and quality → 分别开 Secret scanning 与 Push protection。

## Push Protection 的行为细节

- **公开仓库免费**（2023-05 GA）。
- **用户级 push protection 在 GitHub.com 默认开启**，且是账号级设置：任何人往公开仓库推 secret 都会被拦，**即使该仓库没开这项**。这是容易漏掉的一点——用户可能"没开却已经被拦过"。
- 用户级拦截不产生 alert；仓库级才产生。

### Bypass

- 默认**任何有 write 权限的人**都能绕过，只需填一个理由。
- 免费档**无法限制谁能绕过**；付费档有 bypass controls（可限定角色）和 delegated bypass（走审批流）。
- 每次绕过后 GitHub 必定：
  1. 在仓库/组织/企业的 Security and quality 页签生成 alert
  2. 写入 audit log
  3. 给 watching 仓库的 owner / security manager / admin 发邮件

所以 bypass 不是"静默放行"，而是"留痕放行"——这点在说服团队接受 push protection 时很有用。

## 限制（要主动告知）

- 只覆盖**可识别的高置信度格式**：provider patterns 命中的才拦，自研 token 格式、泛化密码、内网地址一律漏。
- **本地已有 commit 清不掉**：push 被拒后 secret 仍在本地历史里，必须 reset/rebase/filter-repo 重写，否则每次 push 都被拦。这是最高频的踩坑。
- **自建 runner / 定时任务 / bot 提交同样会被拦**，需要 allow list 或排除路径。
- 合法内容会被误伤：测试用假 token 形状、示例文档、fixture、示例 config。
- Secret Scanning 只告警不阻断：发现时通常已在历史里 → 必须轮换（见 incident-response.md）。
- Provider notification 可能导致厂商**自动吊销**密钥 → 生产突然中断，需要预案。

## 告警出口

- 仓库 / 组织 / 企业 Security and quality 页签
- 邮件
- Webhook、REST API（`/repos/{owner}/{repo}/secret-scanning/alerts`），可接 SIEM
- 组织在 Team / Enterprise 套餐下可免费跑"泄漏密钥报告"，并附带"有多少可被 push protection 拦下"的统计——这是说服管理层开 push protection 的好材料
