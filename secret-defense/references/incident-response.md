# 泄漏应急响应

核心原则：**先轮换，再清理。** 顺序反了等于没修。

## 为什么顺序不能反

密钥一旦被 push 过（哪怕随后 force push 覆盖），就要按已泄漏处理：

- 历史可以被任何人克隆、缓存、fork；GitHub 的 dangling object 也可能在一段时间内仍可访问
- 有自动化爬虫持续扫公开仓库的实时提交，窗口期以秒计
- 重写历史不能让已复制的密钥失效，只有轮换可以

所以优先级：**吊销 / 轮换 → 确认清理 → 复盘加防线**。重写历史只是"别再泄漏一遍"，不是修复动作。

## 处置清单（按顺序）

### 1. 定位与评估

```bash
# 全历史扫一遍，确认还有没有别的
gitleaks git --redact --report-path gitleaks-report.json -v
```

确认三件事：这个密钥**是什么服务**的、**权限范围**多大、**有没有被用过**（服务商后台的审计日志 / 使用量）。这一步决定紧急性。

### 2. 立即吊销或轮换

- 优先在服务商侧**吊销**旧凭证，再签发新的。只改配置里的值不吊销旧 key，等于旧 key 永久有效。
- 如果该 provider 在 GitHub partner 计划里（AWS / Azure / GCP 等），Secret Scanning 的 provider notification 可能已经让厂商**自动吊销**了 → 生产会突然中断，这时要立刻确认服务状态并同步用户。
- 轮换范围：CI secrets（GitHub Actions secrets、Vault）、部署配置、本地开发者的 `.env`、任何其他地方出现过同一密钥的地方。

### 3. 清理仓库

```bash
# 先备份
git clone --mirror <url> repo-backup.git

# 方案 A：git-filter-repo（推荐，需单独安装）
pip install git-filter-repo
git filter-repo --replace-text replacements.txt   # 每行 "旧密钥==>***REMOVED***"
git filter-repo --path path/to/leaky.env --invert-paths

# 方案 B：BFG
bfg --replace-text secrets.txt repo.git
```

必要动作：

- 通知所有协作者：`git fetch && git reset --hard origin/main`，否则他们本地的旧历史会把密钥重新 push 回来
- 关闭并删除 dangling 状态：重写后联系 GitHub Support 清理缓存/GC（重写过的提交在 GitHub 上可能仍可通过 commit SHA 访问一段时间）
- 如果仓库有 fork，**fork 不会被同步重写**，需要单独处理或直接联系 fork 拥有者
- PR / issue 评论里贴过的密钥**不会被历史重写覆盖**，要逐条编辑删除

### 4. 复盘并补防线

按 `check_coverage.py` 的四个检查点对齐，回答"这次哪一层应该拦住它、为什么没拦住"：

- 本地层拦得住却没拦 → hook 没装 / 被 `--no-verify` 绕过
- CI 层拦得住却没拦 → 没有 CI 门禁，或没开全历史扫描
- Push Protection 该拦却没拦 → 属于 generic pattern，免费档不覆盖
- 三层都拦不住 → 该密钥格式需要自定义规则（`[rules]` 加正则）

补防线的同时补规则：如果这次泄漏的是自研格式 token，就往 `.gitleaks.toml` 里加一条自定义规则，否则下次照样漏。

## 汇报口径

给用户的结论里必须包含：

1. **轮换状态**：已吊销 / 待用户操作（明确说明"我没有权限，需要你去服务商后台"）
2. **仍未覆盖的范围**：fork、其他仓库、其他协作者本地、issue/PR 正文
3. **不确定项**：无法验证的（如"是否已被利用"）要标"未验证"，不要替用户下结论
4. 不要把密钥明文复述进任何报告、日志或回复——引用位置即可
