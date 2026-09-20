#!/usr/bin/env python3
"""ship-audit: mechanical production-readiness audit + optional Jev scoring pass.

Usage:
  python ship_audit.py --url https://example.com
  python ship_audit.py --url https://example.com --build-dir dist/
  python ship_audit.py --url https://example.com --repo /path/to/repo        # + repo/GitHub checks
  python ship_audit.py --url https://example.com --docs https://docs.example # + docs stub-crawl
  python ship_audit.py --url https://example.com --skip seo,agentic
  python ship_audit.py --url https://example.com --posture fast              # gates decide, rest = notes
  python ship_audit.py --url https://example.com --no-jev                    # mechanical only
  python ship_audit.py --url https://example.com --json                      # machine-readable

Modules: gates+quality always run; seo / agentic / repo(incl. GitHub via `gh`)
/ docs / oauth run by default and can be skipped. Skipped checks are excluded
from the score, not counted against it — checklists are a decision tree, not
a flat list.

Mechanical checks run in code (arithmetic belongs in code, never in a model).
The evidence bundle is then scored by TypeSafe Jev for judgment-only dimensions
(copy clarity, CTA focus, trust, agentic operability, TODO severity, overall
production grade). Jev never counts: the script counts, Jev judges quality.
Without TYPESAFE_API_KEY the audit degrades gracefully to mechanical-only.

Exit codes: 0 production grade, 1 not production grade,
2 blocked (site-killer gate failed), 3 error.
"""

import argparse
import json
import os
import random
import re
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
MAX_STATE_CHARS = 100_000
UA = "ship-audit/1.0 (production-readiness checklist)"

SECRETS_RE = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|"
    r"AIza[A-Za-z0-9_-]{35}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
PROTECTION_MARKERS = ("sso", "vercel", "netlify", "cloudflareaccess", "cloudflare_access")
AI_BOTS = ("GPTBot", "ClaudeBot", "Claude-Web", "anthropic-ai", "PerplexityBot",
           "Google-Extended", "CCBot", "Bytespider", "meta-externalagent")
TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
STUB_RE = re.compile(r"coming soon|under construction|work in progress|not yet (available|written)|lorem ipsum", re.I)
OAUTH_SIGNATURES = {
    "Google": "accounts.google.com/o/oauth",
    "GitHub": "github.com/login/oauth",
    "X/Twitter": "twitter.com/i/oauth",
    "Apple": "appleid.apple.com",
    "Microsoft": "login.microsoftonline.com",
    "Discord": "discord.com/oauth",
    "Auth0": "auth0.com",
    "Clerk": "clerk.",
    "Supabase": "supabase",
    "thirdweb": "thirdweb",
}
SPEC_FILES = ("TODO.md", "SPEC.md", "ROADMAP.md", "BACKLOG.md", "NOTES.md")

PASS, WARN, FAIL, INFO, NA = "PASS", "WARN", "FAIL", "INFO", "N/A"


def hget(headers, name):
    """Case-insensitive header lookup (headers arrive as an email Message)."""
    for k in headers:
        if k.lower() == name.lower():
            return headers[k]
    return ""


def run_cmd(argv, cwd=None, timeout=20):
    """Run a command, return (ok, stdout+stderr text). Never raises."""
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


# Judgment-only dimensions. High noul = production grade on that dimension.
# Criteria are literal; boundary cases spelled out; absence is handled in "true".
JEV_QUESTIONS = {
    "COPY_CLARITY": {
        "instructions": "Judging the supplied page evidence: are the titles, headings, and body copy specific, human, and free of placeholder or stub content?",
        "criteria": {
            "true": "Copy names the actual product/purpose, reads like it was written for this page, and contains no lorem ipsum, TODO markers, template placeholders, or obviously unfinished sections",
            "false": "Copy is placeholder/stub, generic template text, or self-contradictory about what the product is",
        },
    },
    "CTA_FOCUS": {
        "instructions": "Judging the homepage evidence: is there exactly one obvious primary action, with secondary paths visually quieter?",
        "criteria": {
            "true": "One clear primary call-to-action dominates the page; secondary links exist but do not compete for the same visual weight",
            "false": "No clear next step, or five equally-loud competing actions, or the only actions are navigation with no conversion path",
        },
    },
    "META_QUALITY": {
        "instructions": "Judging the supplied titles and meta descriptions across pages: are they real, specific summaries of their pages?",
        "criteria": {
            "true": "Each description summarizes that page's actual content in a sentence a human would write; titles distinguish pages from each other",
            "false": "Descriptions are missing, duplicated across pages, keyword-stuffed, or describe nothing on the page",
        },
    },
    "TRUST_LEGAL": {
        "instructions": "Judging the supplied privacy-policy and terms text (or its absence): do the legal pages cover the basics a visitor would need?",
        "criteria": {
            "true": "Policy names what data is collected and why, covers third parties (analytics/auth), and reads complete for this site's scope — or the site collects nothing needing a policy",
            "false": "Policy is lorem/placeholder, contradicts visible behavior (e.g. denies cookies that are set), missing entirely while analytics and auth are visibly present",
        },
    },
    "AGENTIC_OPERABILITY": {
        "instructions": "Judging the supplied structural evidence: could an autonomous browser agent (no vision, DOM and accessibility tree only) discover the site's purpose and complete its primary task?",
        "criteria": {
            "true": "Navigation uses real href links with descriptive text, forms have labeled inputs, headings describe structure, and the primary action is reachable from the homepage DOM",
            "false": "Primary actions are onclick-only or textless controls, inputs lack labels, structure is unreadable from DOM semantics, or content requires hover/vision-only affordances",
        },
    },
    "TODO_BLOCKING": {
        "instructions": "Judging the sampled TODO/FIXME markers and open spec items from the repository: does any of them mark functionality that is unfinished, stubbed, or known-broken for a launched user?",
        "criteria": {
            "true": "No markers are present, or every sampled one is a chore note, future idea, or internal refactor wish that cannot affect what a launched user sees or does",
            "false": "A marker or open spec item corresponds to unfinished user-facing functionality, a stubbed feature, or a known broken thing in what ships",
        },
    },
    "OVERALL_PRODUCTION_GRADE": {
        "instructions": "Taking the supplied mechanical audit results together with the evidence: is this site production grade for a public launch? Mechanical failures listed in the report are decisive for their dimension — do not excuse a listed failure because the surrounding evidence looks good.",
        "criteria": {
            "true": "No mechanical gate is failed and, in your judgment, a real user could complete the site's purpose today without embarrassment or breakage",
            "false": "A gate is failed, or the judgment dimensions (copy, CTA, trust, agentic, todos) are collectively below what a paying public would accept",
        },
    },
}


class Auditor:
    def __init__(self, base_url, timeout):
        self.base = base_url.rstrip("/")
        self.host = urlparse(base_url).netloc
        self.timeout = timeout
        self.checks = []
        self.pages = {}          # url -> PageInfo
        self.redirect_chain = []
        self.oauth_providers = []

    # ---- fetching ----
    def fetch(self, url, method="GET"):
        self.redirect_chain = []
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method=method)
        opener = urllib.request.build_opener(_RecordingRedirect(self))
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                body = resp.read()
                return resp.status, dict(resp.headers), body, self.redirect_chain[:]
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), (e.read() or b""), self.redirect_chain[:]
        except (urllib.error.URLError, OSError, ValueError) as e:
            return None, {}, b"", str(e)

    def text(self, url):
        status, headers, body, extra = self.fetch(url)
        if status is None or status >= 400:
            return None
        return body.decode("utf-8", errors="replace")

    def check(self, section, cid, title, status, evidence="", gate=False):
        self.checks.append({"section": section, "id": cid, "title": title,
                            "status": status, "evidence": evidence, "gate": gate})

    def skip_module(self, section, name, modlist):
        for cid, title in modlist:
            self.check(section, cid, title, NA, f"skipped (--skip {name})")

    # ---- URL audit ----
    def run(self, args):
        skip = {s.strip() for s in (args.skip or "").split(",") if s.strip()}
        root_status, root_headers, root_body, extra = self.fetch(self.base)
        if root_status is None:
            self.check(0, "SITE_UP", "Site reachable over HTTPS", FAIL,
                       f"could not fetch {self.base}: {extra}", gate=True)
            self.finish_modules(skip, args, crawled=False)
            return
        html = root_body.decode("utf-8", errors="replace")
        page = parse_html(html)
        self.pages[self.base] = page
        root_chain = extra if isinstance(extra, list) else []

        # 0 -- site-killers (gates)
        self.check(0, "SITE_UP", "Site reachable over HTTPS", PASS,
                   f"GET {self.base} -> {root_status}", gate=True)

        st, hdrs, _, chain = self.fetch("http://" + self.host + "/")
        loc = hget(hdrs, "Location")
        # 308 is not auto-followed by urllib (<3.11 has no http_error_308); 301/302/307 are.
        followed_to_https = st == 200 and any("https://" in c for c in chain)
        if (st in (301, 302, 307, 308) and loc.lower().startswith("https://")) or followed_to_https:
            self.check(0, "HTTPS_REDIRECT", "HTTP forced to HTTPS", PASS,
                       f"{st} -> {loc}" if loc else " -> ".join(chain), gate=True)
        else:
            self.check(0, "HTTPS_REDIRECT", "HTTP forced to HTTPS", FAIL,
                       f"http:// returned {st}" + (f", Location={loc}" if loc else " with no https redirect"),
                       gate=True)

        final = root_chain[-1] if root_chain else ""
        if any(m in final.lower() for m in PROTECTION_MARKERS) and root_status in (301, 302, 303, 307, 308):
            self.check(0, "ACCESS_PROTECTION", "Anonymous visitor reaches the site (no host access-protection gate)",
                       FAIL, f"root redirected to {final} — deploy protection is on; "
                             f"check logged out (Vercel fix: PATCH v9/projects {{\"ssoProtection\": null}})", gate=True)
        else:
            self.check(0, "ACCESS_PROTECTION", "Anonymous visitor reaches the site (no host access-protection gate)",
                       PASS, f"root -> {root_status} anonymously", gate=True)

        http_resources = [u for u in page.resources if u.startswith("http://")]
        self.check(0, "MIXED_CONTENT", "No plain-HTTP subresources on the HTTPS page",
                   FAIL if http_resources else PASS,
                   ", ".join(http_resources[:5]) if http_resources else "all subresources are https", gate=True)

        if args.build_dir:
            leaks = scan_secrets(args.build_dir)
            self.check(0, "SECRETS", "No credential patterns in the client build",
                       FAIL if leaks else PASS,
                       "; ".join(f"{p}: {', '.join(files[:3])}" for p, files in leaks.items()) if leaks
                       else f"scanned {args.build_dir}, no key patterns found", gate=True)
        else:
            self.check(0, "SECRETS", "No credential patterns in the client build", NA,
                       "pass --build-dir to scan the built output")

        # 1 -- trust headers (part of core, cheap)
        missing_hdr = [h for h, present in (
            ("Strict-Transport-Security", "strict-transport-security" in {k.lower() for k in root_headers}),
            ("X-Content-Type-Options", "x-content-type-options" in {k.lower() for k in root_headers}),
            ("X-Frame-Options/CSP frame-ancestors", any(k.lower() in ("x-frame-options", "content-security-policy") for k in root_headers)),
            ("Referrer-Policy", "referrer-policy" in {k.lower() for k in root_headers}),
            ("Permissions-Policy", "permissions-policy" in {k.lower() for k in root_headers}),
        ) if not present]
        self.check(1, "SECURITY_HEADERS", "Security headers present",
                   WARN if missing_hdr else PASS,
                   "missing: " + ", ".join(missing_hdr) if missing_hdr else "HSTS + XCTO + framing + referrer + permissions all present")

        cookies = [v for k, v in root_headers.items() if k.lower() == "set-cookie"]
        bad_flags = [c.split("=")[0] for c in cookies
                     if not (re.search(r";\s*secure", c, re.I) and re.search(r";\s*httponly", c, re.I))]
        self.check(1, "COOKIE_FLAGS", "Cookies set Secure+HttpOnly", WARN if bad_flags else PASS,
                   "missing flags on: " + ", ".join(bad_flags) if bad_flags
                   else ("no cookies on landing" if not cookies else "all cookies flagged"))

        self.detect_oauth(html)

        if "seo" in skip:
            self.skip_module(2, "seo", SEO_MODULE)
            self.check(2, "UNIQUE_TITLES", "Titles unique across pages", NA, "skipped (--skip seo)")
            self.check(2, "LANG", "html lang attribute set", NA, "skipped (--skip seo)")
            self.check(3, "VIEWPORT", "Mobile viewport meta present", NA, "skipped (--skip seo)")
        else:
            self.run_seo(page, root_headers)
        if "quality" in skip:
            self.skip_module(3, "quality", QUALITY_MODULE)
        else:
            self.run_quality(args, page)

        # crawl
        urls = [u for u in self.sitemap_urls if u.startswith("http")][:args.pages]
        if self.base not in urls:
            urls = [self.base] + urls
        for u in urls:
            if u in self.pages:
                continue
            s, _, b, _ = self.fetch(u)
            if s and s < 400 and b:
                self.pages[u] = parse_html(b.decode("utf-8", errors="replace"))

        if "quality" not in skip:
            imgs_total = sum(len(p.imgs) for p in self.pages.values())
            imgs_noalt = sum(1 for p in self.pages.values() for alt in p.imgs if alt is None)
            self.check(3, "IMG_ALT", "Images carry alt attributes (missing attribute, not empty=decorative)",
                       WARN if imgs_noalt else (PASS if imgs_total else INFO),
                       f"{imgs_noalt} of {imgs_total} images missing alt")
            self.run_link_checks(skip)

        if "oauth" not in skip:
            self.run_oauth()
        if "repo" not in skip and args.repo:
            self.run_repo(args.repo)
        elif "repo" not in skip:
            self.check("R", "REPO", "Repository readiness (git hygiene, TODOs, README, GitHub)", NA,
                       "pass --repo /path/to/repo to include")
        if "docs" not in skip:
            self.run_docs(args)
        if "agentic" in skip:
            self.skip_module("A", "agentic", AGENTIC_MODULE)

        self.check(1, "OPS_HIDDEN", "Items a URL scan cannot verify (answer in the report)", INFO,
                   "error monitoring wired; uptime signal; rollback tested; DB backups if DB-backed; "
                   "email sending verified end-to-end with SPF/DKIM/DMARC; dependency audit (npm audit); "
                   "2FA on host/registrar; admin routes unindexed; API authz + rate limiting"
                   + ("; OAuth: prod redirect URIs exact, state/PKCE enforced, secrets server-side, "
                      "account linking decided, one full prod login tested" if self.oauth_providers else ""))

    def detect_oauth(self, html):
        found = set()
        for provider, sig in OAUTH_SIGNATURES.items():
            if sig in html:
                found.add(provider)
        for t in self.pages[self.base].button_texts + self.pages[self.base].anchor_texts:
            lt = t.lower().strip()
            for provider in OAUTH_SIGNATURES:
                if lt.startswith(("sign in with", "continue with", "login with")) and provider.lower() in lt:
                    found.add(provider)
        self.oauth_providers = sorted(found)

    def run_seo(self, page, root_headers):
        self.check(2, "TITLE", "Page title present", PASS if page.title else WARN, page.title or "no <title>")
        desc = page.meta.get("description", "")
        self.check(2, "META_DESCRIPTION", "Meta description present", PASS if desc else WARN,
                   (desc[:120] + ("..." if len(desc) > 120 else "")) if desc else "missing")

        problems = [t for t in ("og:title", "og:image", "twitter:card") if t not in page.meta]
        og_img = page.meta.get("og:image", "")
        og_ev = f"og:image={og_img}" if og_img else "no og:image"
        if og_img:
            s, h, _, _ = self.fetch(og_img, method="HEAD")
            ctype = hget(h, "Content-Type")
            if s is None or s >= 400 or "image" not in ctype:
                problems.append(f"og:image fetch -> {s} (broken preview image)")
        self.check(2, "SOCIAL_PREVIEW", "Open Graph + twitter card tags with a live preview image",
                   WARN if problems else PASS,
                   "; ".join(problems) if problems else "og:title, og:image (live), twitter:card all present")

        fav = next((urljoin(self.base, h) for rel, h in page.links if "icon" in rel), None)
        fav_url = fav or urlparse(self.base).scheme + "://" + self.host + "/favicon.ico"
        fs, _, _, _ = self.fetch(fav_url, method="HEAD")
        if fs and fs >= 400:
            fs2, _, _, _ = self.fetch(fav_url)
            fs = fs2
        self.check(2, "FAVICON", "Favicon loads", PASS if fs and fs < 400 else WARN, f"{fav_url} -> {fs}")

        canon = next((urljoin(self.base, h) for rel, h in page.links if rel == "canonical"), None)
        self.check(2, "CANONICAL_TAG", "Canonical tag on landing page", PASS if canon else WARN,
                   canon or "no <link rel=canonical>")

        alt = ("www." + self.host) if not self.host.startswith("www.") else self.host[4:]
        ast, _, _, _ = self.fetch(urlparse(self.base).scheme + "://" + alt + "/", method="GET")
        self.check(2, "CANONICAL_HOST", "One canonical host (apex/www redirect)",
                   PASS if ast in (301, 308, 302, 307) else WARN,
                   f"{alt} -> {ast}" + ("" if ast in (301, 308, 302, 307) else " (serves independently — canonicalization gap)"))

        robots_txt = self.text(self.base + "/robots.txt")
        sm_txt = self.text(self.base + "/sitemap.xml")
        self.sitemap_urls = parse_sitemap(sm_txt) if sm_txt else []
        self.check(2, "SITEMAP", "sitemap.xml present and parseable",
                   PASS if self.sitemap_urls else WARN,
                   f"{len(self.sitemap_urls)} urls" if self.sitemap_urls else "missing or unparseable")
        self.check(2, "ROBOTS", "robots.txt present", PASS if robots_txt is not None else WARN,
                   (robots_txt[:100] + "...") if robots_txt else "missing")

        dup_titles = len(self.pages) - len({p.title for p in self.pages.values() if p.title})
        if len(self.pages) < 2:
            self.check(2, "UNIQUE_TITLES", "Titles unique across pages", NA,
                       "single page crawled (no sitemap to extend the crawl)")
        else:
            self.check(2, "UNIQUE_TITLES", "Titles unique across pages",
                       WARN if dup_titles else PASS, f"{dup_titles} duplicate title(s) across {len(self.pages)} pages")
        self.check(2, "LANG", "html lang attribute set", PASS if page.lang else WARN, page.lang or "missing")

    def run_quality(self, args, page):
        probe = self.base + "/ship-audit-404-" + "".join(random.choices(string.hexdigits.lower(), k=8))
        ps, _, pb, _ = self.fetch(probe)
        if ps is not None and ps < 400:
            t = parse_html(pb.decode("utf-8", errors="replace")).title
            self.check(3, "NOT_FOUND", "Garbage deep link returns a 404 status",
                       WARN, f"probe -> {ps} (SPA fallback serving HTML — no real 404) title={t!r}")
        else:
            self.check(3, "NOT_FOUND", "Garbage deep link returns a 404 status", PASS, f"probe -> {ps}")

        self.check(3, "VIEWPORT", "Mobile viewport meta present",
                   PASS if page.viewport else WARN, "viewport found" if page.viewport else "no viewport meta")

    def run_link_checks(self, skip):
        internal, external = {}, {}
        for u, p in self.pages.items():
            for href in p.anchors:
                absu = urljoin(u, href.split("#")[0])
                if not absu.startswith("http"):
                    continue
                if urlparse(absu).netloc == self.host:
                    internal.setdefault(absu, True)
                else:
                    external.setdefault(absu, True)
        broken = []
        checked = [u for u in internal if u not in self.pages and "/ship-audit-404-" not in u][:150]
        for u in checked:
            s, _, _, _ = self.fetch(u, method="HEAD")
            if s is None or s >= 400:
                s2, _, _, _ = self.fetch(u)
                if s2 is None or s2 >= 400:
                    broken.append(f"{u} -> {s2 or s}")
        n_int = len(checked) + len([u for u in internal if u in self.pages])
        ext_broken, ext_checked = [], 0
        if "seo" not in skip:
            for u in list(external)[:40]:
                ext_checked += 1
                s, _, _, _ = self.fetch(u, method="HEAD")
                if s is None or (s >= 400 and s not in (403, 405, 406, 429, 999)):
                    s2, _, _, _ = self.fetch(u)
                    if s2 is None or (s2 >= 400 and s2 not in (403, 405, 406, 429, 999)):
                        ext_broken.append(f"{u} -> {s2 or s}")
        all_broken = broken + ext_broken
        self.check(3, "BROKEN_LINKS",
                   f"Links resolve ({n_int} internal, {ext_checked} external checked)",
                   FAIL if all_broken else PASS,
                   "; ".join(all_broken[:8]) if all_broken else "no broken links")

    def run_oauth(self):
        if self.oauth_providers:
            self.check("O", "OAUTH_PROVIDERS", "OAuth providers detected (flow below is then CORE, not optional)",
                       INFO, ", ".join(self.oauth_providers))
        else:
            self.check("O", "OAUTH_PROVIDERS", "OAuth providers detected on landing page", INFO,
                       "none visible — if the app has login, confirm provider config manually")
        # the actionable OAuth items are interactive: redirect URIs, state/PKCE,
        # server-side secrets, account linking, one full prod login. See SKILL.md.

    def run_docs(self, args):
        docs_base = args.docs or (self.base + "/docs")
        s, _, b, _ = self.fetch(docs_base)
        if s is None or s >= 400:
            self.check("D", "DOCS", "Documentation present", NA,
                       f"no docs at {docs_base} (pass --docs URL if hosted elsewhere — or genuinely none, fine)")
            return
        docs_pages, todo = {docs_base}, [docs_base]
        stubs = []
        while todo and len(docs_pages) < 12:
            u = todo.pop()
            txt = self.text(u)
            if txt is None:
                continue
            if STUB_RE.search(txt):
                stubs.append(u)
            dp = parse_html(txt)
            for href in dp.anchors:
                absu = urljoin(u, href.split("#")[0])
                if absu.startswith(docs_base) and absu not in docs_pages:
                    docs_pages.add(absu)
                    todo.append(absu)
        self.check("D", "DOCS", f"Documentation present ({len(docs_pages)} page(s) crawled)",
                   PASS, docs_base)
        self.check("D", "DOCS_STUBS", "No stub/placeholder sections in docs",
                   WARN if stubs else PASS,
                   "; ".join(stubs[:5]) if stubs else "no stub markers found")

    def run_repo(self, repo):
        repo = os.path.abspath(repo)
        ok, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo)
        if not ok or out.strip() != "true":
            self.check("R", "REPO", "Repository readiness", NA, f"{repo} is not a git work tree")
            return

        self.check("R", "REPO", "Repository readiness", PASS, repo)

        _, por = run_cmd(["git", "status", "--porcelain"], cwd=repo)
        dirty = [l for l in por.splitlines() if l.strip()]
        self.check("R", "CLEAN_TREE", "Working tree clean (nothing uncommitted)",
                   WARN if dirty else PASS,
                   f"{len(dirty)} uncommitted file(s): {dirty[:5]}" if dirty else "clean")

        _, branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
        branch = branch.strip()
        ok, cnt = run_cmd(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=repo)
        ahead = int(cnt.strip()) if ok and cnt.strip().isdigit() else None
        ok2, cnt2 = run_cmd(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=repo)
        behind = int(cnt2.strip()) if ok2 and cnt2.strip().isdigit() else None
        if ahead is None:
            self.check("R", "SHIPPED", f"All commits pushed (branch {branch})", WARN,
                       "branch has no upstream — nothing to compare against")
        else:
            msg = f"{branch}: {ahead} unpushed, {behind or 0} unpulled"
            self.check("R", "SHIPPED", "All commits shipped (pushed and pulled)",
                       WARN if (ahead or behind) else PASS, msg if (ahead or behind) else msg)

        _, merged = run_cmd(["git", "branch", "--merged", "HEAD"], cwd=repo)
        branches = [b.strip().lstrip("* ") for b in merged.splitlines()
                    if b.strip() and not b.strip().startswith("*") and b.strip() not in ("main", "master")]
        self.check("R", "BRANCHES", "Merged branches cleaned up",
                   WARN if branches else PASS,
                   f"safe to delete: {', '.join(branches[:6])} (git branch -d ...)" if branches
                   else "no stale merged branches")

        _, wt = run_cmd(["git", "worktree", "list", "--porcelain"], cwd=repo)
        wts = [l[len("worktree "):] for l in wt.splitlines() if l.startswith("worktree ")]
        gone = [w for w in wts if not os.path.exists(w)]
        self.check("R", "WORKTREES", "Worktrees healthy",
                   WARN if gone else (INFO if len(wts) > 1 else PASS),
                   f"{len(wts)} worktree(s), stale: {gone} (git worktree prune)" if gone
                   else f"{len(wts)} worktree(s)")

        _, mcnt = run_cmd(["git", "rev-list", "--merges", "--count", "HEAD"], cwd=repo)
        n_merges = int(mcnt.strip()) if mcnt.strip().isdigit() else -1
        self.check("R", "LINEAR", "History shape (informational)", INFO,
                   f"{n_merges} merge commit(s)" + (" — linear" if n_merges == 0 else " — not linear"))

        _, hist = run_cmd(["git", "log", "--all", "--oneline", "--", ".env", ".env.local", ".env.production"],
                          cwd=repo)
        self.check(0, "ENV_IN_HISTORY", ".env never committed to history",
                   FAIL if hist.strip() else PASS,
                   f"found in history:\n{hist[:300]}" if hist.strip() else "no .env commits found", gate=True)

        todos, samples, scanned = {}, [], 0
        for dirpath, dirnames, filenames in os.walk(repo):
            dirnames[:] = [d for d in dirnames if d not in
                           (".git", "node_modules", ".next", "dist", "build", "vendor", ".venv", "venv")]
            for fn in filenames:
                if fn.endswith((".min.js", ".lock")) or fn.startswith(".env"):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(path) > 1_000_000:
                        continue
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            m = TODO_RE.search(line)
                            if m:
                                todos[m.group(1)] = todos.get(m.group(1), 0) + 1
                                if len(samples) < 10:
                                    rel = os.path.relpath(path, repo)
                                    samples.append(f"{rel}:{i}: {line.strip()[:100]}")
                            scanned += 1
                except OSError:
                    continue
        total_todos = sum(todos.values())
        self.check("R", "TODO_SCAN", f"TODO/FIXME/XXX/HACK markers in source ({scanned} lines scanned)",
                   WARN if total_todos else PASS,
                   f"{total_todos} marker(s) {todos}; samples:\n  " + "\n  ".join(samples[:8]) if total_todos
                   else "no markers found")
        self.todo_samples = samples

        spec_counts = {}
        for fn in SPEC_FILES + ("README.md",):
            p = os.path.join(repo, fn)
            if os.path.exists(p):
                try:
                    txt = open(p, encoding="utf-8", errors="ignore").read()
                    open_boxes = txt.count("- [ ]")
                    done_boxes = txt.count("- [x]") + txt.count("- [X]")
                    if open_boxes or done_boxes:
                        spec_counts[fn] = f"{open_boxes} open / {done_boxes} done"
                except OSError:
                    pass
        self.check("R", "SPECS", "Open spec/roadmap items (decision-tree: ship with them open?)",
                   INFO if spec_counts else PASS,
                   "; ".join(f"{k}: {v}" for k, v in spec_counts.items()) or "no checklist boxes in spec files")
        self.spec_open = spec_counts

        readme = next((os.path.join(repo, r) for r in
                       ("README.md", "readme.md", "README.txt", "Readme.md") if os.path.exists(os.path.join(repo, r))), None)
        if readme is None:
            self.check("R", "README", "README exists", WARN, "no README at repo root")
        else:
            txt = open(readme, encoding="utf-8", errors="ignore").read()
            stubby = STUB_RE.search(txt) or len(txt) < 400
            self.check("R", "README", "README exists and says something real",
                       WARN if stubby else PASS,
                       f"{len(txt)} chars" + ("; stub/placeholder content" if STUB_RE.search(txt) else ""))

        if not any(os.path.exists(os.path.join(repo, f)) for f in
                   ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE-MIT", "COPYING")):
            self.check("R", "LICENSE", "License file present", WARN,
                       "no LICENSE at root (required if the repo is public)")
        else:
            self.check("R", "LICENSE", "License file present", PASS, "found")

        self.run_github(repo)

    def run_github(self, repo):
        ok, _ = run_cmd(["gh", "--version"])
        if not ok:
            self.check("R", "GITHUB", "GitHub repo metadata (about/topics/CI)", NA,
                       "gh CLI not installed — check About description/homepage/topics and CI state manually")
            return
        ok, out = run_cmd(["gh", "repo", "view", "--json",
                           "name,description,homepageUrl,repositoryTopics,licenseInfo,isPrivate"],
                          cwd=repo, timeout=30)
        if not ok:
            self.check("R", "GITHUB", "GitHub repo metadata (about/topics/CI)", NA,
                       "gh could not read the repo (not a GitHub remote, or not authenticated)")
            return
        meta = {}
        try:
            meta = json.loads(out)
        except json.JSONDecodeError:
            pass
        missing = []
        if not meta.get("description"):
            missing.append("About description")
        if not meta.get("homepageUrl"):
            missing.append("homepage URL")
        if not meta.get("repositoryTopics"):
            missing.append("topics")
        self.check("R", "GITHUB_META", "GitHub About: description, homepage, topics set",
                   WARN if missing else PASS,
                   "missing: " + ", ".join(missing) if missing else "description, homepage, topics all set")
        self.check("R", "GITHUB_PRIVATE", "Repo visibility (decides LICENSE/SECURITY weight)", INFO,
                   "private" if meta.get("isPrivate") else "public")

        ok, out = run_cmd(["gh", "run", "list", "-L", "1", "--json", "conclusion,status"], cwd=repo, timeout=30)
        if ok and out.strip():
            try:
                runs = json.loads(out)
                if runs:
                    c = runs[0].get("conclusion") or runs[0].get("status")
                    self.check("R", "CI", "Latest CI run", PASS if c == "success" else WARN, f"latest run: {c}")
                    return
            except json.JSONDecodeError:
                pass
        self.check("R", "CI", "Latest CI run", NA, "no workflow runs (no Actions configured, or none finished)")

    def finish_modules(self, skip, args, crawled):
        if "repo" not in skip and args.repo:
            self.run_repo(args.repo)

    # ---- Jev evidence + scoring ----
    def evidence_text(self):
        parts = [f"=== SITE UNDER AUDIT: {self.base} ===", "=== MECHANICAL AUDIT RESULTS ==="]
        for c in self.checks:
            if c["status"] == "INFO":
                continue
            parts.append(f"[{c['status']}] ({'GATE ' if c['gate'] else ''}s{c['section']}) {c['title']}: {c['evidence']}")
        parts.append("=== PAGE EVIDENCE (first 6 pages) ===")
        for u, p in list(self.pages.items())[:6]:
            nav = [t for t in p.anchor_texts if t.strip()][:15]
            btns = p.button_texts[:10]
            parts.append(
                f"-- {u}\n title: {p.title}\n description: {p.meta.get('description','')}\n h1: {' | '.join(p.h1s)}\n"
                f" og:description: {p.meta.get('og:description','')}\n nav links: {nav}\n buttons: {btns}\n"
                f" forms: {p.forms}")
        for u, p in self.pages.items():
            for rel, href in p.links:
                if rel == "stylesheet":
                    continue
                if any(k in (rel + href).lower() for k in ("privacy", "terms", "legal")):
                    txt = self.text(urljoin(u, href))
                    if txt:
                        body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))
                        parts.append("=== POLICY PAGE (" + href + ") ===\n" + body[:1500])
                        break
        if getattr(self, "todo_samples", None) or getattr(self, "spec_open", None):
            parts.append("=== REPO HYGIENE EVIDENCE ===")
            if self.todo_samples:
                parts.append("TODO/FIXME samples:\n  " + "\n  ".join(self.todo_samples))
            if self.spec_open:
                parts.append("Open spec items: " + "; ".join(f"{k}: {v}" for k, v in self.spec_open.items()))
        if self.oauth_providers:
            parts.append("=== OAUTH ===\nProviders detected: " + ", ".join(self.oauth_providers))
        return "\n\n".join(parts)

    def score(self, jev_answers, posture):
        scored = [c for c in self.checks if c["status"] in (PASS, WARN, FAIL)
                  and not c["gate"] and c["section"] not in (0, "A", "O")]
        mech_pts = sum({PASS: 1, WARN: 0.5, FAIL: 0}[c["status"]] for c in scored)
        mech_pct = 100 * mech_pts / len(scored) if scored else 0
        gate_fails = [c for c in self.checks if c["gate"] and c["status"] == FAIL]

        ag_items = [c for c in self.checks if c["section"] == "A" and c["status"] in (PASS, WARN, FAIL)]
        ag_mech = 100 * sum({PASS: 1, WARN: 0.5, FAIL: 0}[c["status"]] for c in ag_items) / len(ag_items) if ag_items else None

        sem_pct, agentic_pct, overall_noul = None, None, None
        if jev_answers:
            sem_items = [k for k in JEV_QUESTIONS
                         if k not in ("OVERALL_PRODUCTION_GRADE", "AGENTIC_OPERABILITY", "TODO_BLOCKING")]
            sem_pct = 100 * sum(jev_answers[k] for k in sem_items) / len(sem_items)
            overall_noul = jev_answers["OVERALL_PRODUCTION_GRADE"]
            if ag_mech is not None:
                agentic_pct = 0.5 * ag_mech + 0.5 * 100 * jev_answers["AGENTIC_OPERABILITY"]

        # skipped pillars are re-weighted away, never counted as zero
        parts = [(mech_pct, 0.5)]
        if sem_pct is not None:
            parts.append((sem_pct, 0.35))
        if agentic_pct is not None:
            parts.append((agentic_pct, 0.15))
        elif ag_mech is not None:
            parts.append((ag_mech, 0.15))
        wsum = sum(w for _, w in parts)
        final = sum(v * w for v, w in parts) / wsum if wsum else 0

        if gate_fails:
            verdict = "BLOCKED"
        elif posture == "fast":
            verdict = "SHIP (FAST POSTURE — gates clear; report is notes, not blockers)"
        elif final >= 90 and (overall_noul is None or overall_noul >= 0.5):
            verdict = "PRODUCTION GRADE"
        elif final >= 75 and (overall_noul is None or overall_noul >= 0.5):
            verdict = "PRODUCTION GRADE WITH NOTES"
        else:
            verdict = "NOT PRODUCTION GRADE"
        if (overall_noul is not None and overall_noul <= 0.2
                and verdict.startswith("PRODUCTION") and posture != "fast"):
            verdict = "NOT PRODUCTION GRADE (Jev overall noul %.2f)" % overall_noul
        return {"mechanical_pct": round(mech_pct, 1), "semantic_pct": None if sem_pct is None else round(sem_pct, 1),
                "agentic_pct": None if agentic_pct is None else round(agentic_pct, 1), "final": round(final, 1),
                "overall_noul": overall_noul, "verdict": verdict, "gate_fails": [c["id"] for c in gate_fails],
                "posture": posture}


SEO_MODULE = [("TITLE", "Page title present"), ("META_DESCRIPTION", "Meta description present"),
              ("SOCIAL_PREVIEW", "Open Graph + twitter card"), ("FAVICON", "Favicon loads"),
              ("CANONICAL_TAG", "Canonical tag"), ("CANONICAL_HOST", "One canonical host"),
              ("SITEMAP", "sitemap.xml present"), ("ROBOTS", "robots.txt present")]
QUALITY_MODULE = [("NOT_FOUND", "Custom 404"), ("IMG_ALT", "Image alt attributes"),
                  ("BROKEN_LINKS", "Links resolve"), ("VIEWPORT", "Mobile viewport")]
AGENTIC_MODULE = [("REAL_LINKS", "Real href anchors"), ("FORM_LABELS", "Labeled inputs"),
                  ("TEXTLESS_CONTROLS", "Named controls"), ("H1", "One h1"), ("STRUCTURE", "Heading structure")]


class _RecordingRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, auditor):
        self.auditor = auditor

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.auditor.redirect_chain.append(f"{code}->{newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PageInfo:
    def __init__(self):
        self.title = ""
        self.meta = {}
        self.links = []        # (rel, href)
        self.h1s = []
        self.headings = 0
        self.imgs = []         # alt-or-None
        self.anchors = []      # hrefs
        self.anchor_texts = []
        self.js_links = []     # anchors without real href
        self.textless = []     # buttons/links with no accessible name
        self.forms = []
        self.unlabeled_inputs = 0
        self.viewport = False
        self.lang = ""
        self.json_ld = 0
        self.resources = []    # subresource urls
        self.button_texts = []


class PageParser(HTMLParser):
    LABELABLE = ("text", "email", "tel", "password", "search", "number", "url", "date")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.p = PageInfo()
        self._in_title = False
        self._btn_depth = 0
        self._btn_text = []
        self._in_label = False
        self._label_ids = set()
        self._inputs_seen = []   # (type, id, aria-label, wrapped-in-label)
        self._h1_parts = []

    @property
    def page(self):
        self.p.unlabeled_inputs = sum(
            1 for (t, iid, aria, wrapped) in self._inputs_seen
            if t in self.LABELABLE and not wrapped and not aria and iid not in self._label_ids)
        return self.p

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        p = self.p
        if tag == "html":
            p.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = a.get("name") or a.get("property") or ""
            if name:
                p.meta[name] = a.get("content", "")
                if name == "viewport":
                    p.viewport = True
        elif tag == "link":
            p.links.append((a.get("rel", ""), a.get("href", "")))
        elif tag == "h1":
            self._in_h1 = True
            self._h1_parts = []
            p.headings += 1
        elif tag in ("h2", "h3"):
            p.headings += 1
        elif tag == "img":
            p.imgs.append(a.get("alt"))  # None = attribute absent
            src = a.get("src")
            if src:
                p.resources.append(src)
        elif tag == "script":
            if a.get("type") == "application/ld+json":
                p.json_ld += 1
            src = a.get("src")
            if src:
                p.resources.append(src)
        elif tag == "a":
            href = a.get("href")
            self._anchor_text = []
            self._anchor_named = bool(a.get("aria-label"))
            if href and href not in ("#", "") and not href.startswith(("javascript:",)):
                p.anchors.append(href)
            else:
                p.js_links.append(a.get("onclick", "<no-href>"))
        elif tag == "button":
            self._btn_depth += 1
            self._btn_text = []
            if a.get("aria-label"):
                self._btn_text = [a["aria-label"]]
        elif tag == "input":
            self._inputs_seen.append((a.get("type", "text"), a.get("id", ""),
                                      a.get("aria-label", ""), self._in_label))
            if a.get("type") in ("submit", "button") and a.get("value"):
                p.button_texts.append(a["value"])
            src = a.get("src")
            if src:
                p.resources.append(src)
        elif tag == "form":
            p.forms.append(f"{a.get('action','(self)')} [{a.get('method','get')}]")
        elif tag == "label":
            self._in_label = True
            if a.get("for"):
                self._label_ids.add(a["for"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
            txt = " ".join(t for t in self._h1_parts if t.strip()).strip()
            if txt:
                self.p.h1s.append(txt)
        elif tag == "a":
            txt = " ".join(t for t in getattr(self, "_anchor_text", []) if t.strip()).strip()
            if txt:
                self.p.anchor_texts.append(txt)
            elif not getattr(self, "_anchor_named", False):
                self.p.textless.append("a")
        elif tag == "button":
            self._btn_depth = max(0, self._btn_depth - 1)
            txt = " ".join(self._btn_text).strip()
            if txt:
                self.p.button_texts.append(txt)
            else:
                self.p.textless.append("button")
        elif tag == "label":
            self._in_label = False

    def handle_data(self, data):
        if self._in_title:
            self.p.title += data.strip()
        elif getattr(self, "_in_h1", False):
            if data.strip():
                self._h1_parts.append(data.strip())
        elif self._btn_depth and data.strip():
            self._btn_text.append(data.strip())
        elif getattr(self, "_anchor_text", None) is not None and data.strip():
            self._anchor_text.append(data.strip())


def parse_html(html):
    parser = PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.page


def parse_sitemap(txt):
    try:
        root = ET.fromstring(txt.encode())
    except Exception:
        return []
    out = []
    if root.tag.endswith("sitemapindex"):
        for sm in root.iter("{*}loc"):
            child = None
            try:
                req = urllib.request.Request(sm.text.strip(), headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=10) as r:
                    child = r.read().decode("utf-8", errors="replace")
            except Exception:
                continue
            out.extend(parse_sitemap(child))
    else:
        for loc in root.iter("{*}loc"):
            if loc.text:
                out.append(loc.text.strip())
    return out


def parse_ai_bots(robots_txt):
    denies, allows = [], []
    agent = None
    for line in robots_txt.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            agent = val
        elif key == "disallow" and val and agent and any(a.lower() == agent for a in map(str.lower, AI_BOTS)):
            denies.append(agent)
        elif key == "allow" and val and agent and any(a.lower() == agent for a in map(str.lower, AI_BOTS)):
            allows.append(agent)
    return sorted(set(denies)), sorted(set(a for a in allows if a not in denies))


def scan_secrets(build_dir):
    hits = {}
    for dirpath, dirnames, filenames in os.walk(build_dir):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(path) > 5_000_000:
                    continue
                with open(path, encoding="utf-8", errors="ignore") as f:
                    data = f.read()
            except OSError:
                continue
            for m in SECRETS_RE.finditer(data):
                hits.setdefault(m.group(0)[:12] + "...", []).append(path)
    return hits


def call_jev(state, api_key):
    payload = {"model": MODEL, "state": state,
               "questions": {k: {"type": "noul", "instructions": q["instructions"], "criteria": q["criteria"]}
                             for k, q in JEV_QUESTIONS.items()}}
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(3):
        req = urllib.request.Request(API_URL, data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (429, 529) or e.code >= 500:
                last = RuntimeError(f"HTTP {e.code}: {detail}")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"TypeSafe HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"TypeSafe unreachable after retries: {last}")


def main():
    ap = argparse.ArgumentParser(description="production-readiness audit (mechanical + Jev)")
    ap.add_argument("--url", required=True)
    ap.add_argument("--build-dir", help="built client output to scan for secrets (.next, dist, assets)")
    ap.add_argument("--repo", help="project repo path for git-hygiene, TODO, README, GitHub checks")
    ap.add_argument("--docs", help="docs base URL to crawl for stubs (default: <url>/docs)")
    ap.add_argument("--pages", type=int, default=20, help="max sitemap pages to crawl (default 20)")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--skip", help="comma list of modules to skip: seo,quality,agentic,repo,docs,oauth")
    ap.add_argument("--posture", choices=("grade", "fast"), default="grade",
                    help="fast: only site-killer gates decide; everything else is notes")
    ap.add_argument("--no-jev", action="store_true", help="mechanical scoring only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if not args.url.startswith("http"):
        args.url = "https://" + args.url
    aud = Auditor(args.url, args.timeout)
    aud.sitemap_urls = []
    try:
        aud.run(args)
    except Exception as e:
        print(f"error: audit failed: {e}", file=sys.stderr)
        sys.exit(3)

    answers, tokens, jev_note = None, 0, ""
    if not args.no_jev:
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            jev_note = "TYPESAFE_API_KEY not set — verdict is mechanical-only (SEMANTIC=N/A)"
        else:
            try:
                state = aud.evidence_text()[:MAX_STATE_CHARS]
                resp = call_jev(state, key)
                tokens = resp.get("usage", {}).get("input_tokens", 0)
                answers = {k: v["noul"] for k, v in resp.get("answers", {}).items()}
                missing = [k for k in JEV_QUESTIONS if k not in answers]
                if missing:
                    jev_note = f"Jev did not return: {', '.join(missing)}"
                    for k in missing:
                        answers[k] = 0.5
            except Exception as e:
                jev_note = f"Jev pass failed ({e}) — verdict is mechanical-only"
                answers = None

    result = aud.score(answers, args.posture)
    result["url"] = aud.base
    result["jev_note"] = jev_note
    result["input_tokens"] = tokens
    result["checks"] = aud.checks

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        sec_names = {0: "0 SITE-KILLERS (gates)", 1: "1 LEGAL + TRUST", 2: "2 SHARE + SEO",
                     3: "3 QUALITY + PERFORMANCE", "R": "R REPO READINESS", "D": "D DOCS",
                     "O": "O OAUTH", "A": "A AGENTIC READINESS"}
        sec_order = {0: 0, 1: 1, 2: 2, 3: 3, "R": 4, "D": 5, "O": 6, "A": 7}
        cur = None
        for c in sorted(aud.checks, key=lambda c: sec_order[c["section"]]):
            if c["section"] != cur:
                cur = c["section"]
                print(f"\n== {sec_names[cur]} ==")
            mark = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]", "INFO": "[info]", "N/A": "[ n/a]"}[c["status"]]
            gate = "*" if c["gate"] else " "
            ev = f" — {c['evidence']}" if c["evidence"] else ""
            print(f"{mark}{gate} {c['title']}{ev}")
        print(f"\n== SCORE (posture: {args.posture}) ==")
        print(f"mechanical : {result['mechanical_pct']}%")
        print(f"semantic   : {result['semantic_pct'] if result['semantic_pct'] is not None else 'N/A (no Jev)'}"
              + (f"  (jev-latest, {tokens} in-tokens)" if tokens else ""))
        ag_disp = f"{result['agentic_pct']}%" if result["agentic_pct"] is not None else "excluded (skipped)"
        print(f"agentic    : {ag_disp}")
        print(f"FINAL      : {result['final']}/100")
        print(f"VERDICT    : {result['verdict']}")
        if result["gate_fails"]:
            print("gates failed: " + ", ".join(result["gate_fails"]))
        if jev_note:
            print(f"note       : {jev_note}")
        print("(* = hard gate; PASS=1.0 WARN=0.5 FAIL=0 within non-gate checks; "
              "final = 0.5*mech + 0.35*semantic + 0.15*agentic; skipped modules don't count against you)")

    gates_failed = bool(result["gate_fails"])
    if gates_failed:
        sys.exit(2)
    if args.posture == "fast":
        sys.exit(0)
    sys.exit(0 if result["verdict"].startswith("PRODUCTION") else 1)


if __name__ == "__main__":
    main()
