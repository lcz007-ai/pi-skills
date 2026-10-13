#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计一个仓库的「密钥泄漏三层防御」覆盖度。

三层机制、四个检查点：
  1. GitHub Secret Scanning         （检测，事后告警）
  2. GitHub Push Protection         （服务端阻断）
  3. gitleaks pre-commit            （本地阻断，可被绕过）
  4. gitleaks CI 门禁               （服务端阻断，不可绕过）

用法：
    python check_coverage.py [repo_path] [--json] [--no-network] [--strict]

只读：不修改任何文件，不改 GitHub 设置。
GitHub 侧的检查依赖 `gh` CLI 且需要仓库 admin 权限；拿不到时状态为「未知」，
不要把「未知」当成「未开启」。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from urllib import request as urlrequest

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except Exception:
        pass

GITLEAKS_REPO = "gitleaks/gitleaks"
URL_TIMEOUT = 8
CMD_TIMEOUT = 20


# ── 基础设施 ──────────────────────────────────────────────────────────────────

def run(cmd, cwd=None):
    """返回 (ok, stdout)。找不到命令或非零退出都返回 (False, "")。"""
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=CMD_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, ""
    if p.returncode != 0:
        return False, (p.stdout or "").strip()
    return True, (p.stdout or "").strip()


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def http_json(url):
    try:
        req = urlrequest.Request(url, headers={
            "User-Agent": "secret-defense-audit",
            "Accept": "application/vnd.github+json",
        })
        with urlrequest.urlopen(req, timeout=URL_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


# ── 各检查点 ──────────────────────────────────────────────────────────────────

def find_repo_root(path):
    ok, out = run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    return out if ok else None


def parse_remote(url):
    """从 remote url 解析 (host, owner/repo)。"""
    if not url:
        return None, None
    u = url.strip()
    m = re.match(r"^(?:git@|ssh://git@|https?://)([^/:]+)[/:](.+?)(?:\.git)?/?$", u)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def check_github(owner_repo, use_network):
    """读 security_and_analysis。返回 dict 或 None（拿不到）。

    注意：读不到该字段不等于功能没开 —— 它需要仓库 admin 权限或组织的
    security manager 角色，无权限时 API 静默不返回（而不是报错）。
    """
    if not use_network or not owner_repo or not shutil.which("gh"):
        return None
    ok, out = run(["gh", "api", "repos/" + owner_repo])
    if not ok or not out.strip().startswith("{"):
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    sa = data.get("security_and_analysis")
    res = {
        "visibility": data.get("visibility"),
        "archived": data.get("archived"),
        "sa_available": isinstance(sa, dict) and bool(sa),
    }
    sa = sa if isinstance(sa, dict) else {}
    res["secret_scanning"] = (sa.get("secret_scanning") or {}).get("status")
    res["push_protection"] = (sa.get("secret_scanning_push_protection") or {}).get("status")
    res["non_provider_patterns"] = (sa.get("secret_scanning_non_provider_patterns") or {}).get("status")
    return res


def scan_precommit(repo_root):
    """返回 (found, rev, path)。"""
    for name in (".pre-commit-config.yaml", ".pre-commit-config.yml"):
        path = os.path.join(repo_root, name)
        if not os.path.isfile(path):
            continue
        lines = read_text(path).splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("- repo:") and "gitleaks" in s:
                rev = None
                for j in range(i + 1, min(i + 20, len(lines))):
                    t = lines[j].strip()
                    if t.startswith("rev:"):
                        rev = t.split(":", 1)[1].strip().strip("\"'")
                        break
                return True, rev, name
        return False, None, name
    return False, None, None


def hook_installed(repo_root):
    """pre-commit 框架是否已在本 clone 装好 hook。"""
    ok, gitdir = run(["git", "-C", repo_root, "rev-parse", "--absolute-git-dir"])
    if not ok:
        return None
    ok_cfg, hooks_path = run(["git", "-C", repo_root, "config", "core.hooksPath"])
    if ok_cfg and hooks_path:
        hooks_dir = hooks_path if os.path.isabs(hooks_path) else os.path.join(repo_root, hooks_path)
    else:
        hooks_dir = os.path.join(gitdir, "hooks")
    hook = os.path.join(hooks_dir, "pre-commit")
    if not os.path.isfile(hook):
        return False
    body = read_text(hook)
    if not body:
        return False
    # pre-commit 框架生成的 hook 里含 "pre-commit" 或 INSTALL_PYTHON；
    # 自定义 hook 只要提到 gitleaks 也算已覆盖。
    low = body.lower()
    return ("pre-commit" in low) or ("install_python" in low) or ("gitleaks" in low)


def scan_workflows(repo_root):
    """CI 里有没有 gitleaks。返回命中的文件名列表。"""
    wf_dir = os.path.join(repo_root, ".github", "workflows")
    hits = []
    if not os.path.isdir(wf_dir):
        return hits
    for name in sorted(os.listdir(wf_dir)):
        if not name.lower().endswith((".yml", ".yaml")):
            continue
        if "gitleaks" in read_text(os.path.join(wf_dir, name)).lower():
            hits.append(name)
    return hits


def local_gitleaks_version():
    if not shutil.which("gitleaks"):
        return None
    ok, out = run(["gitleaks", "version"])
    if not ok:
        return None
    m = re.search(r"(\d+\.\d+\.\d+)", out)
    return m.group(1) if m else out.strip() or None


def latest_gitleaks_version(use_network):
    if not use_network:
        return None
    data = http_json("https://api.github.com/repos/%s/releases/latest" % GITLEAKS_REPO)
    if not data:
        return None
    return (data.get("tag_name") or "").lstrip("v") or None


def version_tuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])
    except Exception:
        return None


# ── 组装报告 ──────────────────────────────────────────────────────────────────

OK, MISSING, PARTIAL, UNKNOWN, NA = "OK", "缺失", "部分", "未知", "不适用"


def audit(repo_path, use_network=True):
    repo = os.path.abspath(repo_path)
    root = find_repo_root(repo)
    result = {"repo_path": repo, "is_git_repo": bool(root), "checks": {}, "gaps": []}

    if not root:
        result["notes"] = ["不是 git 仓库，只能做文件层面的检查。"]
        root = repo
    result["repo_root"] = root

    ok, remote = run(["git", "-C", root, "remote", "get-url", "origin"])
    host, owner_repo = parse_remote(remote if ok else "")
    result["remote"] = remote if ok else None
    result["host"] = host
    result["owner_repo"] = owner_repo

    gh = check_github(owner_repo, use_network) if host == "github.com" else None
    result["github"] = gh

    is_github = host == "github.com"
    private = (gh or {}).get("visibility") == "private"

    checks = result["checks"]

    # 1. Secret Scanning
    if not is_github:
        checks["secret_scanning"] = {
            "status": NA, "detail": "远端不是 GitHub（host=%s）" % (host or "无")}
    elif gh is None:
        checks["secret_scanning"] = {
            "status": UNKNOWN,
            "detail": "需要 gh CLI（`gh auth login`）才能读，不等于未开启"}
    elif not gh.get("sa_available"):
        checks["secret_scanning"] = {
            "status": UNKNOWN,
            "detail": "读不到 security_and_analysis，需仓库 admin 或组织 security manager 权限"}
    elif (gh.get("secret_scanning") or "").lower() == "enabled":
        checks["secret_scanning"] = {"status": OK, "detail": "provider patterns 已启用"}
    else:
        checks["secret_scanning"] = {"status": MISSING, "detail": "未启用"}

    # 2. Push Protection
    if not is_github:
        checks["push_protection"] = {"status": NA, "detail": "远端不是 GitHub"}
    elif gh is None or not gh.get("sa_available"):
        checks["push_protection"] = {
            "status": UNKNOWN, "detail": "读不到状态，需 gh CLI + admin 权限"}
    elif (gh.get("push_protection") or "").lower() == "enabled":
        detail = "已启用"
        if private and not (gh.get("non_provider_patterns") or "").lower() == "enabled":
            detail += "；私有仓库的 generic patterns 需付费档，自研格式仍会漏"
        checks["push_protection"] = {"status": OK, "detail": detail}
    else:
        detail = "未启用"
        if private:
            detail += "（私有仓库需 GitHub Secret Protection 付费附加组件）"
        else:
            detail += "（公开仓库免费，建议开启）"
        checks["push_protection"] = {"status": MISSING, "detail": detail}

    # 3. gitleaks pre-commit
    found, rev, cfg_name = scan_precommit(root)
    installed = hook_installed(root) if root else None
    if not found:
        checks["precommit"] = {
            "status": MISSING,
            "detail": "没有 gitleaks 的 pre-commit 配置",
            "config_file": cfg_name,
        }
    elif installed is False:
        checks["precommit"] = {
            "status": PARTIAL,
            "detail": "%s 已配置，但本 clone 未安装 hook（跑 pre-commit install）" % cfg_name,
            "config_file": cfg_name, "rev": rev,
        }
    elif installed is None:
        checks["precommit"] = {
            "status": PARTIAL,
            "detail": "%s 已配置，hook 安装状态无法确认" % cfg_name,
            "config_file": cfg_name, "rev": rev,
        }
    else:
        checks["precommit"] = {
            "status": OK, "detail": "%s，hook 已安装" % cfg_name,
            "config_file": cfg_name, "rev": rev,
        }

    # gitleaks 版本漂移
    binary_ver = local_gitleaks_version()
    latest_ver = latest_gitleaks_version(use_network)
    if binary_ver or latest_ver:
        checks["gitleaks_version"] = {
            "status": OK, "detail": "本地 %s / pinned %s / 最新 %s" % (
                binary_ver or "无", rev or "无", latest_ver or "未知"),
            "local": binary_ver, "pinned": rev, "latest": latest_ver,
        }
        tv = version_tuple(rev)
        lv = version_tuple(latest_ver)
        if tv and lv and tv < lv:
            result["gaps"].append({
                "priority": "中",
                "item": "gitleaks 版本落后",
                "detail": "pinned %s，最新 %s（pre-commit autoupdate）" % (rev, latest_ver),
            })

    # 4. CI 门禁
    wf_hits = scan_workflows(root)
    if wf_hits:
        checks["ci"] = {"status": OK, "detail": "命中 " + ", ".join(wf_hits)}
    else:
        checks["ci"] = {"status": MISSING, "detail": "没有带 gitleaks 的 workflow"}

    # 其他配置文件
    result["files"] = {
        "gitleaks_toml": os.path.isfile(os.path.join(root, ".gitleaks.toml")),
        "gitleaksignore": os.path.isfile(os.path.join(root, ".gitleaksignore")),
    }

    # 缺口汇总（按性价比排序）
    g = result["gaps"]
    if checks["ci"]["status"] == MISSING:
        g.append({"priority": "高", "item": "缺少 gitleaks CI 门禁",
                  "detail": "本地 hook 可被 --no-verify 绕过，必须有不可绕过的一层"})
    if checks["precommit"]["status"] == MISSING:
        g.append({"priority": "高", "item": "没有 gitleaks pre-commit",
                  "detail": "最早、最廉价的一层；加入后 secret 不进历史，省掉轮换和历史重写"})
    elif checks["precommit"]["status"] == PARTIAL:
        g.append({"priority": "中", "item": "pre-commit hook 未在本 clone 安装",
                  "detail": "跑 pre-commit install；全团队用 pre-commit init-templatedir"})
    if checks["push_protection"]["status"] == MISSING:
        g.append({"priority": "高", "item": "Push Protection 未开启",
                  "detail": checks["push_protection"]["detail"]})
    if checks["secret_scanning"]["status"] == MISSING:
        g.append({"priority": "中", "item": "Secret Scanning 未开启",
                  "detail": checks["secret_scanning"]["detail"]})
    if checks["secret_scanning"]["status"] == UNKNOWN or checks["push_protection"]["status"] == UNKNOWN:
        g.append({"priority": "低", "item": "GitHub 侧状态未知",
                  "detail": "装 gh CLI 并以 admin 身份 `gh auth login` 后重跑"})
    if not result["files"]["gitleaks_toml"]:
        g.append({"priority": "低", "item": "没有 .gitleaks.toml",
                  "detail": "误报迟早会出现；提前备好 allowlist，避免团队用 --no-verify 绕过"})

    order = {"高": 0, "中": 1, "低": 2}
    g.sort(key=lambda x: order.get(x["priority"], 9))
    return result


# ── 呈现 ──────────────────────────────────────────────────────────────────────

LABEL = {
    "secret_scanning": "1. Secret Scanning（检测）",
    "push_protection": "2. Push Protection（服务端阻断）",
    "precommit": "3. gitleaks pre-commit（本地阻断）",
    "ci": "4. gitleaks CI 门禁（服务端阻断）",
}

MARK = {OK: "[ OK ]", MISSING: "[缺失]", PARTIAL: "[部分]", UNKNOWN: "[未知]", NA: "[ -- ]"}


def dwidth(s):
    """终端显示宽度：宽字符（CJK）算 2 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s, width):
    """按显示宽度左对齐补齐，保证中英混排的表格不歪。"""
    s = s or ""
    return s + " " * max(0, width - dwidth(s))


LBL_W, MRK_W = 31, 6


def render(r):
    # 标签列宽度按实际内容算，避免中英混排时把说明列顶歪
    labels = list(LABEL.values()) + ["gitleaks 版本"]
    lbl_w = max(LBL_W, max(dwidth(x) for x in labels) + 2)

    out = []
    out.append("=== 密钥泄漏防御覆盖度审计 ===")
    out.append("仓库     : %s" % r["repo_path"])
    out.append("远端     : %s" % (r.get("remote") or "（无 origin）"))
    if r.get("owner_repo"):
        vis = (r.get("github") or {}).get("visibility")
        out.append("平台     : %s / %s%s" % (
            r["host"], r["owner_repo"], "  (%s)" % vis if vis else ""))
    out.append("")
    out.append(pad("检查点", lbl_w) + "  " + pad("状态", MRK_W) + "  说明")
    out.append("-" * lbl_w + "  " + "-" * MRK_W + "  " + "----")
    for key, label in LABEL.items():
        c = r["checks"].get(key)
        if not c:
            continue
        out.append("%s  %s  %s" % (pad(label, lbl_w),
                                    pad(MARK.get(c["status"], c["status"]), MRK_W),
                                    c["detail"]))
        if key == "precommit" and c.get("rev"):
            out.append("%s  %s  pinned rev: %s" % (pad("", lbl_w), " " * MRK_W, c["rev"]))
    if r["checks"].get("gitleaks_version"):
        out.append("%s  %s  %s" % (pad("gitleaks 版本", lbl_w), " " * MRK_W,
                                    r["checks"]["gitleaks_version"]["detail"]))
    out.append("")
    out.append("配置文件: .gitleaks.toml %s   .gitleaksignore %s" % (
        "有" if r["files"]["gitleaks_toml"] else "无",
        "有" if r["files"]["gitleaksignore"] else "无"))
    out.append("")
    if r["gaps"]:
        out.append("缺口与建议（按性价比排序）")
        for i, gap in enumerate(r["gaps"], 1):
            out.append("%d. [%s] %s —— %s" % (i, gap["priority"], gap["item"], gap["detail"]))
    else:
        out.append("四个检查点均已覆盖。建议定期 pre-commit autoupdate 并复核 allowlist。")
    if r["checks"].get("secret_scanning", {}).get("status") == UNKNOWN:
        out.append("")
        out.append("注：「未知」表示没有权限读取 GitHub 设置，不代表未开启 —— 请到 "
                   "Settings > Security and quality 手工核对。")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="审计仓库的密钥泄漏三层防御覆盖度（只读）")
    ap.add_argument("repo_path", nargs="?", default=".", help="仓库路径，默认当前目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-network", action="store_true", help="不做任何网络请求")
    ap.add_argument("--strict", action="store_true", help="存在缺口时以退出码 1 结束")
    args = ap.parse_args()

    r = audit(args.repo_path, use_network=not args.no_network)

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render(r))

    if args.strict and r["gaps"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
