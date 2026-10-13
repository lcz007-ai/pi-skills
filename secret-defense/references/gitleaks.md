# gitleaks 使用与配置

核实日期 2026-10-10，版本 v8.30.1（2026-03-21）。命令与 TOML 字段随版本变动较快，写死版本号前先核对 release。

## 命令模型（v8.19 起）

`detect` / `protect` 已废弃（仍可用但已从 `--help` 隐藏）。三种扫描模式：

| 命令 | 用途 |
|---|---|
| `gitleaks git` | 扫 git 历史（底层是 `git log -p`） |
| `gitleaks dir` | 扫目录/文件（别名 `files`、`directory`） |
| `gitleaks stdin` | 从标准输入扫，如 `cat f \| gitleaks stdin` |

```bash
gitleaks git -v                                   # 扫当前目录的 git 历史
gitleaks git -v --log-opts="--all a..b" <path>    # 扫范围
gitleaks dir -v <path>                            # 扫工作目录
gitleaks git --redact --exit-code 1               # CI 门禁常用
```

`--exit-code`：默认 0=无泄漏、1=有泄漏或出错、126=未知参数。CI 里显式写 `--exit-code 1` 更清楚。

## pre-commit 集成

官方 `.pre-commit-hooks.yaml` 定义（三个变体的 entry 完全一样）：

```yaml
- id: gitleaks
  entry: gitleaks git --pre-commit --redact --staged --verbose
  language: golang          # 需要 Go 工具链，首次会下载编译
- id: gitleaks-docker
  entry: zricethezav/gitleaks git --pre-commit --redact --staged --verbose
  language: docker_image    # 需要 Docker
- id: gitleaks-system
  entry: gitleaks git --pre-commit --redact --staged --verbose
  language: system          # 自己保证 gitleaks 在 PATH
```

选型：CI 或已有 Go 环境用 `gitleaks`；不想装 Go 但有 Docker 用 `gitleaks-docker`；团队用包管理器统一装了 gitleaks（brew / scoop / apt）用 `gitleaks-system`。

`--staged` 只扫暂存区，所以速度快；`--redact` 让输出脱敏，避免密钥被打印进终端和 CI 日志——这两个参数别去掉。

跳过机制（要提前告知用户，否则会被当成"失效"）：

```bash
SKIP=gitleaks git commit -m "..."   # 官方支持的跳过方式
git commit --no-verify -m "..."     # 跳过所有 hook
```

推广时：`pre-commit install` 只对当前 clone 生效。全团队默认带上：

```bash
pre-commit init-templatedir ~/.git-template
git config --global init.templateDir ~/.git-template
```

## 配置 TOML

三种用法：不写配置（内置默认规则）、`[extend] useDefault = true` 在默认规则上加料（**推荐**）、完全自定义。

```toml
[extend]
useDefault = true   # 与 path 不能同时用

[[allowlists]]      # 全局 allowlist，优先级高于规则级
description = "测试 fixture 与示例文档"
paths = [
  '''tests/fixtures/.*''',
  '''docs/examples/.*''',
  '''.*\.md$''',
]

[[allowlists]]
description = "明确的假值"
# 用 shape/stopwords 豁免，不要往这里粘形状像真 key 的字符串（会被下游扫描器误拦）
stopwords = ['''example''', '''dummy''', '''placeholder''']
```

字段语义与版本变更（都向后兼容，但新写法是唯一推荐）：

| 字段 | 说明 |
|---|---|
| `[rules.allowlist]` → `[[rules.allowlists]]` | v8.21.0 起改复数 |
| `[allowlist]` → `[[allowlists]]` | v8.25.0 起改复数 |
| `targetRules` | v8.25.0 新增，让一条 allowlist 只作用于指定规则 |
| `condition` | `OR`（默认）/ `AND`；多个字段间的组合方式 |
| `regexTarget` | `secret`（默认）/ `match` / `line`；注意看的是"提取出的密钥"还是整行 |
| `stopwords` | 作用于**提取出的 secret**，不是整个 regex 匹配（v8.8.0+） |
| `paths` / `commits` / `regexes` | 按路径、commit、正则豁免 |

三条等价的手动豁免途径，按粒度从细到粗：

1. 行内注释：`discord_client_secret = '...'  #gitleaks:allow`
2. 仓库根 `.gitleaksignore`：写 finding 的 `Fingerprint`（需要报告里带该字段，v8.10.0+，实验性）
3. `.gitleaks.toml` 的 `[[allowlists]]`：路径/regex 级别，团队共享，**首选**

## 基线（老仓库必用）

```bash
gitleaks git --report-path baseline.json                        # 1. 出基线
gitleaks git --baseline-path baseline.json --report-path f.json # 2. 之后只报新增
```

不先出基线就给老仓库开 CI 门禁，会被历史 finding 淹没，结果通常是门禁被关掉或加一堆 `|| true`。

## CI 集成

### gitleaks-action

```yaml
- uses: actions/checkout@v6
  with:
    fetch-depth: 0          # 必须，否则扫不到历史
- uses: gitleaks/gitleaks-action@v3
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}   # 组织需要，个人账号不需要
```

- 用 **@v3**。@v2 基于 Node 20，GitHub 已于 2026-09-16 从托管 runner 移除 Node 20，v2 会直接失败。
- @v3 需要 runner ≥ v2.327.1（自托管 runner 要先升级）。
- **组织仓库需要 GITLEAKS_LICENSE**（付费），个人账号不需要。这是给组织出方案时的关键成本点。

### 免 license 的替代：直接跑二进制

```yaml
- run: |
    curl -sSL -o gl.tar.gz https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz
    tar -xzf gl.tar.gz gitleaks
    ./gitleaks git --redact --verbose --exit-code 1
```

组织里不想买 license 就走这条。代价是要自己维护版本号（把版本钉在脚本里）。

## 版本漂移

`pre-commit-config.yaml` 里的 `rev` 不更新会漏掉新厂商模式。定期：

```bash
pre-commit autoupdate
```

技能自带的 `scripts/check_coverage.py` 会比对当前 `rev` 与最新 release 并报落后几个版本。**本地 rev 与 CI 用的版本要一致**，否则会出现"本地过了 CI 挂了"。
