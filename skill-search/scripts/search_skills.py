#!/usr/bin/env python3
"""Search agent-skill / Pi-package directories (skills.sh, mcp.so, pi.dev/packages)
in parallel, dedupe across sources, rank by relevance + popularity, and print a
unified result.

Usage:
  python3 search_skills.py "pdf"                 # human table
  python3 search_skills.py "code review" --json  # machine JSON
  python3 search_skills.py "react" --limit 15
  python3 search_skills.py "react" --sources skills.sh,mcp.so
  python3 search_skills.py "react" --skills-only

Only the Python standard library is required.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SOURCES = ["skills.sh", "mcp.so", "pi.dev/packages"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def http_get(url: str, timeout: int = 25, retries: int = 2, ua: str | None = UA) -> str:
    headers = {
        "Accept": "text/html,application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if ua:
        headers["User-Agent"] = ua
    req = urllib.request.Request(url, headers=headers)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001 - retry transient 403/429/timeout
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def clean_text(s: str | None) -> str:
    """Strip tags, unescape entities, collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def tokens_of(query: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t]


# --------------------------------------------------------------------------- #
# source: skills.sh  (public JSON API used by its own frontend)
# --------------------------------------------------------------------------- #
def fetch_skills_sh(query: str, limit: int) -> list[dict]:
    url = (
        "https://www.skills.sh/api/search?"
        + urllib.parse.urlencode({"q": query, "limit": max(limit, 20)})
    )
    data = json.loads(http_get(url))
    out = []
    for s in data.get("skills", []):
        sid = s.get("id", "")  # owner/repo/skill
        out.append(
            {
                "name": s.get("skillId") or s.get("name") or sid,
                "repo": s.get("source", ""),
                "desc": "",
                "cat": "",
                "installs": s.get("installs") or 0,
                "url": "https://www.skills.sh/" + urllib.parse.quote(sid) if sid else "",
                "type": "skill",
                "unit": "installs",
                "source": "skills.sh",
            }
        )
    return out


# --------------------------------------------------------------------------- #
# source: mcp.so  (server-rendered cards; /skills and /servers)
# --------------------------------------------------------------------------- #
_CARD_RE = r'<a href="(/{base}/[^"]+)" class="group border-border bg-card[^"]*">(.*?)(?=<a href="/{base}/|</div></section>|$)'
_NAME_RE = r"<h3[^>]*>(.*?)</h3>"
_P_RE = r"<p[^>]*>(.*?)</p>"
_CAT_RE = r'<span class="bg-secondary[^>]*>(.*?)</span>'


def _parse_mcp_cards(page: str, base: str, kind: str) -> list[dict]:
    out = []
    for m in re.finditer(_CARD_RE.format(base=base), page, re.S):
        href, body = m.group(1), m.group(2)
        h3 = re.search(_NAME_RE, body, re.S)
        ps = [clean_text(p) for p in re.findall(_P_RE, body, re.S)]
        cat = re.search(_CAT_RE, body, re.S)
        # ps[0] is the author/owner line, ps[1] the description (skills may add a badge line)
        author = next((p for p in ps if " / " in p or (p and len(p) < 80 and p != clean_text(h3.group(1) if h3 else ""))), "")
        desc = max(ps, key=len) if ps else ""
        out.append(
            {
                "name": clean_text(h3.group(1)) if h3 else href.rsplit("/", 1)[-1],
                "repo": author,
                "desc": desc,
                "cat": clean_text(cat.group(1)) if cat else "",
                "installs": 0,
                "url": "https://mcp.so" + href,
                "type": "server" if kind == "servers" else "skill",
                "unit": "",
                "source": "mcp.so",
            }
        )
    return out


def fetch_mcp_so(query: str, limit: int, include_servers: bool) -> list[dict]:
    q = urllib.parse.quote(query)
    urls = [(f"https://mcp.so/skills?q={q}", "skills")]
    if include_servers:
        urls.append((f"https://mcp.so/servers?q={q}", "servers"))
    out, errors = [], []
    for url, kind in urls:
        try:
            page = http_get(url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"mcp.so/{kind}: {e}")
            continue
        out.extend(_parse_mcp_cards(page, kind, kind))
    if not out and errors:
        raise RuntimeError("; ".join(errors))
    return out


# --------------------------------------------------------------------------- #
# source: pi.dev/packages  (the Pi package gallery)
#
# The gallery is an index over npm packages that carry the `pi-package` keyword
# (see Pi docs/packages.md). There is no server-side text search on pi.dev, but
# the npm registry search API supports `keywords:pi-package <text>`, returns the
# same package universe, and resolves fast — so use it and link back to pi.dev.
# --------------------------------------------------------------------------- #
def _pi_downloads(name: str) -> int:
    try:
        q = urllib.parse.quote(name, safe="@/")
        data = json.loads(http_get(f"https://api.npmjs.org/downloads/point/last-month/{q}", retries=1))
        return int(data.get("downloads") or 0)
    except Exception:  # noqa: BLE001 - downloads are a nice-to-have
        return 0


def fetch_pi_packages(query: str, limit: int) -> list[dict]:
    size = max(limit, 20)
    url = (
        "https://registry.npmjs.org/-/v1/search?"
        + urllib.parse.urlencode({"text": f"keywords:pi-package {query}", "size": size})
    )
    data = json.loads(http_get(url))
    out = []
    for obj in data.get("objects", []):
        pkg = obj.get("package", {})
        kws = [k.lower() for k in pkg.get("keywords", []) if isinstance(k, str)]
        if "pi-package" not in kws:
            continue
        name = pkg.get("name", "")
        if not name:
            continue
        if "skill" in kws:
            ptype = "skill"
        elif "theme" in kws:
            ptype = "theme"
        elif "extension" in kws or "pi-extension" in kws:
            ptype = "extension"
        else:
            ptype = "package"
        out.append(
            {
                "name": name,
                "repo": (pkg.get("publisher") or {}).get("username", ""),
                "desc": pkg.get("description", "") or "",
                "cat": ", ".join(pkg.get("keywords", [])[:3]),
                "installs": 0,
                "url": f"https://pi.dev/packages/{urllib.parse.quote(name, safe='@/')}",
                "npm": (pkg.get("links") or {}).get("npm", ""),
                "type": ptype,
                "unit": "dl/mo",
                "source": "pi.dev/packages",
            }
        )
    # monthly downloads are the gallery's popularity signal; fetch concurrently
    if out:
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            for row, dl in zip(out, pool.map(lambda r: _pi_downloads(r["name"]), out)):
                row["installs"] = dl
    return out


# --------------------------------------------------------------------------- #
# relevance / dedupe / ranking
# --------------------------------------------------------------------------- #
def _words(s: str | None) -> set[str]:
    """Split a string into lowercase word tokens (camelCase aware)."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s or "")
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+", s) if w}


def relevance(row: dict, toks: list[str], joined: str) -> tuple[int, int]:
    """Return (token_score, total_score). token_score==0 means no lexical match."""
    name = norm(row.get("name"))
    nwords = _words(row.get("name"))
    rwords = _words(row.get("repo"))
    dwords = _words(row.get("desc"))
    base = 0
    for t in toks:
        if t in nwords:
            base += 4
        elif any(w.startswith(t) for w in nwords):
            base += 2
        elif t in name:
            base += 1
        if t in rwords:
            base += 2
        elif any(w.startswith(t) for w in rwords):
            base += 1
        if t in dwords:
            base += 1
    if name and name == joined:
        base += 8
    elif joined and joined in name:
        base += 4
    # corroboration across independent directories is a mild positive signal,
    # but it must never turn a non-match into a result
    total = base
    if base > 0:
        total += 2 * (len(row.get("sources", [])) - 1)
    return base, total


def dedupe(rows: list[dict]) -> list[dict]:
    """Merge records that refer to the same skill across sources.

    Pi packages get their own namespace so a Pi npm package named e.g. "pdf"
    does not get merged into an unrelated skill also called "pdf".
    """
    merged: dict[str, dict] = {}
    for r in rows:
        group = "|pkg" if r.get("type") in ("package", "extension", "theme") else ""
        key = norm(r["name"]) + group
        if not key.strip("|"):
            continue
        if key not in merged:
            r = dict(r)
            r["sources"] = [r.pop("source")]
            merged[key] = r
            continue
        base = merged[key]
        base["sources"].append(r["source"])
        # fill gaps
        for f in ("repo", "desc", "cat"):
            if not base.get(f) and r.get(f):
                base[f] = r[f]
        if r.get("installs", 0) > base.get("installs", 0):
            base["installs"] = r["installs"]
            base["unit"] = r.get("unit", "")
        base.setdefault("unit", r.get("unit", ""))
        # prefer a skill listing over a server listing when the same name collides
        if base.get("type") == "server" and r.get("type") == "skill":
            base["type"] = "skill"
            base["url"] = r["url"]
    for r in merged.values():
        r["sources"] = sorted(set(r["sources"]))
    return list(merged.values())


def search(
    query: str,
    limit: int,
    sources: list[str],
    include_servers: bool,
) -> dict:
    toks = tokens_of(query)
    joined = norm(query)
    errors: list[str] = []
    collected: list[dict] = []

    jobs = {}
    with cf.ThreadPoolExecutor(max_workers=len(sources) or 1) as pool:
        if "skills.sh" in sources:
            jobs["skills.sh"] = pool.submit(fetch_skills_sh, query, limit)
        if "mcp.so" in sources:
            jobs["mcp.so"] = pool.submit(fetch_mcp_so, query, limit, include_servers)
        if "pi.dev/packages" in sources:
            jobs["pi.dev/packages"] = pool.submit(fetch_pi_packages, query, limit)
        for name, fut in jobs.items():
            try:
                collected.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")

    deduped = dedupe(collected)
    for r in deduped:
        base, total = relevance(r, toks, joined)
        r["score"] = total
        r["_base"] = base
    # Keep only genuine lexical matches: some directories return a full/featured
    # list for unknown queries, so a zero-score hit is noise, not a result.
    deduped = [r for r in deduped if r["_base"] > 0]
    for r in deduped:
        r.pop("_base", None)
    deduped.sort(key=lambda r: (r["score"], r.get("installs", 0)), reverse=True)
    results = deduped[:limit]

    return {
        "query": query,
        "sources": sources,
        "raw_hits": len(collected),
        "unique_hits": len(deduped),
        "errors": errors,
        "results": results,
    }


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def fmt_installs(n: int) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}k"
    return str(n)


def render_text(payload: dict) -> str:
    lines = []
    q, errs, rows = payload["query"], payload["errors"], payload["results"]
    lines.append(f'Search: "{q}"  |  {payload["raw_hits"]} raw -> {payload["unique_hits"]} unique')
    if errs:
        lines.append("  ! " + " | ".join(errs))
    if not rows:
        lines.append("No matches for these keywords. Try a shorter synonym, or widen --sources.")
        return "\n".join(lines)
    for i, r in enumerate(rows, 1):
        if r.get("installs"):
            unit = r.get("unit", "") or "installs"
            stars = f"  [{fmt_installs(r['installs'])} {unit}]"
        else:
            stars = ""
        lines.append(f"{i}. {r['name']}{stars}")
        meta = []
        if r.get("repo"):
            meta.append(r["repo"])
        meta.append("sources: " + ", ".join(r.get("sources", [])))
        if r.get("cat"):
            meta.append(r["cat"])
        if r.get("type") in ("server", "package", "extension", "theme"):
            label = {"server": "MCP server", "extension": "Pi extension", "theme": "Pi theme", "package": "Pi package"}[r["type"]]
            meta.append(label)
        lines.append("   " + " · ".join(meta))
        if r.get("desc"):
            d = r["desc"]
            lines.append("   " + (d[:240] + ("..." if len(d) > 240 else "")))
        if r.get("url"):
            lines.append("   " + r["url"])
        if r.get("npm"):
            lines.append("   install: pi install npm:" + r["name"])
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Search agent-skill / Pi-package directories with cross-source dedupe.")
    ap.add_argument("query", help="search terms, e.g. 'pdf' or 'code review'")
    ap.add_argument("--limit", type=int, default=10, help="max results (default 10)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--sources", default=",".join(SOURCES), help="comma list: " + ",".join(SOURCES))
    ap.add_argument("--skills-only", action="store_true", help="skip mcp.so's MCP-server index and pi.dev packages")
    args = ap.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip() in SOURCES]
    if args.skills_only:
        sources = [s for s in sources if s in ("skills.sh", "mcp.so")]
    payload = search(args.query, max(1, args.limit), sources, include_servers=not args.skills_only)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_text(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
