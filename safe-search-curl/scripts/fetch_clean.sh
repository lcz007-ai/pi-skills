#!/usr/bin/env bash
# fetch_clean.sh — 安全抓取网页并输出降噪纯文本
# 用法: fetch_clean.sh <URL> [max_chars=4000]
# 防护: 仅 http/https；SSRF 拦截覆盖原始 URL 与每一次重定向目标；UA；超时；按字符截断
# 边界: fake-ip 代理环境下 DNS 层检查不生效（输出 [warn]，兜底依赖代理转发策略）；DNS rebinding 为已知残余风险（见 SKILL.md）
set -euo pipefail

URL="${1:?用法: fetch_clean.sh <URL> [max_chars=4000]}"
MAX="${2:-4000}"

python3 - "$URL" "$MAX" <<'PY'
import html, ipaddress, re, socket, sys, urllib.parse, urllib.request

FAKE_IP = ipaddress.ip_network("198.18.0.0/15")  # 本机代理 fake-ip 段：仅豁免 DNS 解析结果（字面 IP 不豁免）；默认豁免保证本机可用，代价是该环境下 DNS 层检查退化为 [warn]


def is_bad_raw(addr) -> bool:
    """字面 IP 用：不豁免（fake-ip 段也按内网处理）。"""
    return addr in FAKE_IP or addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


def is_bad(addr) -> bool:
    """仅用于 DNS 解析结果：fake-ip 段豁免。"""
    if addr in FAKE_IP:
        return False
    return is_bad_raw(addr)


def check_host(u: str):
    """校验 URL 的协议与 host（仅 http/https；字面 IP 直查；域名含 DNS 解析后检查）。返回 None=放行，否则为拦截原因。"""
    parsed = urllib.parse.urlparse(u)
    if parsed.scheme not in ("http", "https"):
        return f"协议不允许: {parsed.scheme or '(无)'}（仅 http/https）"
    host = parsed.hostname or ""
    if host.lower() == "localhost" or host.lower().endswith(".local"):
        return "本机/本地域名"
    try:
        addr = ipaddress.ip_address(host)
        return None if not is_bad_raw(addr) else f"内网/保留地址 {host}"
    except ValueError:
        pass
    try:
        addrs = {ipaddress.ip_address(i[4][0]) for i in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return f"域名无法解析: {host}"
    for a in addrs:
        if a not in FAKE_IP and is_bad(a):
            return f"域名 {host} 解析到内网/保留地址 {a}"
    if addrs and all(a in FAKE_IP for a in addrs):
        print(f"[warn] {host} 解析全部落在 fake-ip 段 {FAKE_IP}（本机代理）：DNS 层 SSRF 检查不生效，依赖代理转发策略兜底", file=sys.stderr)
    return None


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向目标必须重新过同一套 SSRF 校验，否则 302→内网可穿透首层检查。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = check_host(newurl)
        if reason:
            sys.exit(f"[blocked] SSRF 防护：拒绝跟随重定向到 {newurl}（{reason}）")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


url, max_chars = sys.argv[1], int(sys.argv[2])

if not urllib.parse.urlparse(url).scheme:
    url = "https://" + url.lstrip("/")
    print(f"[info] URL 无 scheme，已自动补 https://：{url}", file=sys.stderr)

reason = check_host(url)
if reason:
    sys.exit(f"[blocked] SSRF 防护：拒绝抓取（{reason}）")

req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) safe-search-curl"})
opener = urllib.request.build_opener(SafeRedirectHandler)
try:
    with opener.open(req, timeout=20) as r:
        raw = r.read(2_000_000).decode("utf-8", "replace")
        final_url = r.geturl()
except Exception as e:
    sys.exit(f"[error] 抓取失败: {e}")

raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.S | re.I)
raw = re.sub(r"<[^>]+>", " ", raw)
text = html.unescape(re.sub(r"\s+", " ", raw))
print(f"[source] {final_url}")
print(text[:max_chars])
PY
