#!/usr/bin/env python3
"""Mechanical ship auditor. Arithmetic lives here; Jev only judges quality."""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

UA = "ship-checklist-auditor/1.0 (+https://local.skill)"
TIMEOUT = 12
MAX_JEV_STATE = 100_000
MAX_INTERNAL_LINKS = 30
MAX_BODY = 400_000

SECRET_PATTERNS = [
    ("stripe_live", re.compile(r"sk_live_[0-9a-zA-Z]{16,}")),
    ("stripe_secret", re.compile(r"rk_live_[0-9a-zA-Z]{16,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"ghp_[0-9A-Za-z]{20,}")),
    ("github_fine_grained", re.compile(r"github_pat_[0-9A-Za-z_]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("google_api", re.compile(r"AIza[0-9A-Za-z\-_]{20,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai", re.compile(r"sk-(?:proj-)?[0-9A-Za-z]{20,}")),
    ("supabase_service", re.compile(r"service_role['\"]?\s*[:=]\s*['\"]eyJ")),
    ("jwt_service", re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}")),
]

PUBLIC_PREFIX_OK = re.compile(r"(NEXT_PUBLIC_|VITE_|PUBLIC_|REACT_APP_)")
STUB_MARKERS = re.compile(
    r"coming soon|under construction|work in progress|lorem ipsum|todo:\s*write|placeholder copy",
    re.I,
)
TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b[:\s].{0,80}")
USER_FACING_TODO = re.compile(
    r"\b(TODO|FIXME|XXX|HACK)\b.*\b(login|signup|pay|checkout|page|ui|user|auth|broken|implement|unfinished|ship)\b",
    re.I,
)
SPEC_BOX = re.compile(r"^\s*[-*]\s*\[\s*\]\s+", re.M)
ENV_FILE_NAMES = {".env", ".env.local", ".env.production", ".env.development"}
SKIP_DIR_NAMES = {
    ".git", "node_modules", "vendor", "dist", "build", ".next", "coverage",
    "__pycache__", ".venv", "venv", "minified",
}
PROTECTION_HINTS = (
    "vercel.com/login",
    "sso.vercel",
    "vercel_sso",
    "cloudflareaccess.com",
    "cdn-cgi/access",
    "netlify.app/.netlify/identity",
    "access.redhat.com",  # harmless-ish; kept out of primary list via startswith checks below
)
PRIMARY_PROTECTION = (
    "vercel.com/login",
    "sso.vercel",
    "cloudflareaccess.com",
    "cdn-cgi/access",
)

JEV_QUESTIONS = {
    "COPY_CLARITY": {
        "type": "noul",
        "instructions": "Is the shipped copy clear enough that a first-time visitor can say what this page is for in one sentence?",
        "criteria": {
            "true": "Purpose is explicit in the first screen of copy. No lorem, no internal jargon as the only explanation, no contradictory headlines.",
            "false": "Headline is vague, placeholder, or the page does not state what the product or site is.",
        },
    },
    "CTA_FOCUS": {
        "type": "noul",
        "instructions": "Does the page present one primary action a visitor can take?",
        "criteria": {
            "true": "A single primary CTA is visually and verbally dominant. Secondary actions exist but do not compete equally.",
            "false": "No CTA, many equal CTAs, or the main action is hidden in a nav-only control.",
        },
    },
    "META_QUALITY": {
        "type": "noul",
        "instructions": "Are title, description, and social tags specific to this page rather than a site-wide stub?",
        "criteria": {
            "true": "Title and description name the product and page purpose. OG tags match the page.",
            "false": "Generic 'Home', framework defaults, missing description, or OG that contradicts the page.",
        },
    },
    "TRUST_LEGAL": {
        "type": "noul",
        "instructions": "Does the site look trustworthy enough to hand personal data or money, given the launch type?",
        "criteria": {
            "true": "Privacy/terms reachable when personal data is collected; no obvious scam or unfinished-legal feel.",
            "false": "Auth or payments with no legal pages, or legal pages that are stubs.",
        },
    },
    "TODO_BLOCKING": {
        "type": "noul",
        "instructions": "Do sampled TODO/FIXME comments mark unfinished user-facing functionality?",
        "criteria": {
            "true": "Yes — comments describe missing pages, auth, payments, or broken UI that a user would hit.",
            "false": "No — comments are chores, refactors, or test notes a user would never see.",
        },
    },
    "AGENTIC_OPERABILITY": {
        "type": "noul",
        "instructions": "Could an autonomous browser agent using only the DOM and accessibility tree discover the purpose and complete the primary task?",
        "criteria": {
            "true": "Real links, labeled inputs, a real heading structure, and the primary task is exposed as a normal control.",
            "false": "onclick-only navigation, unlabeled fields, no h1, or the primary task is hidden behind hover or canvas.",
        },
    },
}


@dataclass
class Check:
    id: str
    section: str
    title: str
    status: str  # PASS WARN FAIL SKIP N/A
    evidence: str
    severity: str = "core"  # gate core optional info


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: str
    error: str | None = None


class MiniHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._in_title = False
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.anchors: list[tuple[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.forms: list[dict[str, Any]] = []
        self._cur_form: dict[str, Any] | None = None
        self.headings: list[tuple[str, str]] = []
        self._cur_heading: str | None = None
        self._heading_parts: list[str] = []
        self.html_lang = ""
        self.scripts_jsonld: list[str] = []
        self._in_ld = False
        self.text_bits: list[str] = []
        self.inputs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.html_lang = a["lang"]
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            self.metas.append(a)
        if tag == "link":
            self.links.append(a)
        if tag == "a":
            self.anchors.append((a.get("href", ""), a.get("aria-label", "")))
        if tag == "img":
            self.images.append(a)
        if tag == "form":
            self._cur_form = {"attrs": a, "inputs": []}
            self.forms.append(self._cur_form)
        if tag in {"input", "textarea", "select"}:
            rec = {"tag": tag, **a}
            self.inputs.append(rec)
            if self._cur_form is not None:
                self._cur_form["inputs"].append(rec)
        if tag in {"h1", "h2", "h3", "h4"}:
            self._cur_heading = tag
            self._heading_parts = []
        if tag == "script" and "ld+json" in a.get("type", ""):
            self._in_ld = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "form":
            self._cur_form = None
        if tag == self._cur_heading:
            self.headings.append((tag, "".join(self._heading_parts).strip()))
            self._cur_heading = None
        if tag == "script":
            self._in_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._cur_heading:
            self._heading_parts.append(data)
        if self._in_ld:
            self.scripts_jsonld.append(data)
        if data and data.strip():
            self.text_bits.append(data.strip())

    @property
    def title(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.title_parts)).strip()

    def meta(self, name: str) -> str:
        name_l = name.lower()
        for m in self.metas:
            key = (m.get("name") or m.get("property") or m.get("http-equiv") or "").lower()
            if key == name_l:
                return m.get("content", "")
        return ""


def fetch(url: str, method: str = "GET", max_body: int = MAX_BODY) -> Page:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "*/*"})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            raw = resp.read(max_body + 1)
            body = raw[:max_body].decode("utf-8", "replace")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return Page(url, resp.geturl(), getattr(resp, "status", 200), headers, body)
    except urllib.error.HTTPError as e:
        raw = e.read(max_body) if e.fp else b""
        body = raw.decode("utf-8", "replace")
        headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        return Page(url, e.geturl() if hasattr(e, "geturl") else url, e.code, headers, body, str(e))
    except Exception as e:  # noqa: BLE001 — auditor must keep going
        return Page(url, url, 0, {}, "", str(e))


def origin_of(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def with_scheme(url: str, scheme: str) -> str:
    p = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((scheme, p.netloc, p.path or "/", p.params, p.query, ""))


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def run_cmd(args: list[str], cwd: str | None = None, timeout: int = 20) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def parse_html(body: str) -> MiniHTML:
    parser = MiniHTML()
    try:
        parser.feed(body)
        parser.close()
    except Exception:
        pass
    return parser


def is_skipped(check_id: str, skips: set[str]) -> bool:
    groups = {
        "seo": {"seo_title", "seo_description", "seo_og", "seo_twitter", "seo_canonical", "seo_robots", "seo_sitemap", "seo_favicon"},
        "agentic": {"ag_hrefs", "ag_labels", "ag_h1", "ag_viewport", "ag_lang", "ag_llms", "ag_jsonld", "ag_robots_ai"},
        "legal": {"legal_privacy", "legal_terms", "legal_headers", "legal_consent"},
        "quality": {"q_404", "q_links", "q_images", "q_mobile_viewport"},
        "repo": {"repo_status", "repo_unpushed", "repo_branches", "repo_worktrees", "repo_linear", "repo_readme", "repo_license", "repo_todos", "repo_specs", "repo_security_md", "repo_github"},
        "docs": {"docs_present", "docs_stubs"},
        "oauth": {"oauth_detected"},
    }
    if check_id in skips:
        return True
    for g, ids in groups.items():
        if g in skips and check_id in ids:
            return True
    return False


def applicable_map(launch: str) -> dict[str, str]:
    """Return default applicability: gate|core|optional|skip for families."""
    base = {
        "gates": "gate",
        "legal": "core",
        "seo": "core",
        "quality": "core",
        "oauth": "skip",
        "docs": "optional",
        "repo": "core",
        "agentic": "core",
        "ops": "optional",
    }
    if launch == "marketing":
        base.update(oauth="skip", docs="optional", seo="core")
    elif launch == "auth":
        base.update(seo="optional", oauth="core", legal="gate", docs="optional")
    elif launch == "api":
        base.update(seo="optional", oauth="optional", docs="core", quality="optional")
    elif launch == "ecommerce":
        base.update(seo="core", oauth="core", legal="gate", docs="optional")
    elif launch == "internal":
        base.update(seo="skip", legal="optional", quality="optional", oauth="optional",
                    docs="optional", repo="optional", agentic="optional")
    return base


def family_of(check_id: str, section: str) -> str:
    if section == "0":
        return "gates"
    if check_id.startswith("legal_") or section == "1":
        return "legal"
    if check_id.startswith("seo_") or section == "2":
        return "seo"
    if check_id.startswith("q_") or section == "3":
        return "quality"
    if check_id.startswith("repo_") or section == "R":
        return "repo"
    if check_id.startswith("oauth") or section == "O":
        return "oauth"
    if check_id.startswith("docs_") or section == "D":
        return "docs"
    if check_id.startswith("ag_") or section == "A":
        return "agentic"
    return "quality"


class Auditor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.url = normalize_url(args.url)
        self.origin = origin_of(self.url)
        self.skips = {s.strip().lower() for s in (args.skip or "").split(",") if s.strip()}
        if args.no_jev:
            self.skips.add("jev")
        if not args.repo:
            self.skips.add("repo")
        if not args.docs:
            self.skips.add("docs")
        if not args.agentic:
            self.skips.add("agentic")
        self.appl = applicable_map(args.launch_type)
        self.checks: list[Check] = []
        self.home: Page | None = None
        self.html: MiniHTML | None = None
        self.oauth_providers: list[str] = []
        self.todo_samples: list[str] = []
        self.spec_items: list[str] = []

    def add(self, chk: Check) -> None:
        if is_skipped(chk.id, self.skips) or chk.status == "SKIP":
            chk.status = "SKIP"
        fam = family_of(chk.id, chk.section)
        role = self.appl.get(fam, "core")
        if role == "skip" and chk.status != "SKIP":
            chk.status = "N/A"
            chk.evidence = (chk.evidence + " — not applicable for this launch type").strip(" —")
        if role == "gate":
            chk.severity = "gate"
        elif role == "optional":
            chk.severity = "optional"
        elif role == "core":
            chk.severity = "core"
        self.checks.append(chk)

    def run(self) -> dict[str, Any]:
        self.home = fetch(self.url)
        if self.home.body:
            self.html = parse_html(self.home.body)
        self.check_gates()
        self.check_legal()
        self.check_seo()
        self.check_quality()
        if self.args.repo:
            self.check_repo()
        self.check_oauth()
        if self.args.docs:
            self.check_docs()
        if self.args.agentic:
            self.check_agentic()
        jev = self.run_jev() if "jev" not in self.skips else None
        return self.score(jev)

    def check_gates(self) -> None:
        # HTTPS forced
        http_url = with_scheme(self.url, "http")
        http_page = fetch(http_url)
        https_ok = False
        if http_page.status in {301, 302, 303, 307, 308}:
            loc = http_page.headers.get("location", "")
            https_ok = loc.startswith("https://")
        elif http_page.final_url.startswith("https://"):
            https_ok = True
        elif http_page.status == 0 and self.home and self.home.status and self.home.final_url.startswith("https://"):
            # many stacks refuse raw HTTP at the edge; live HTTPS is the real gate
            https_ok = True
            http_page.error = http_page.error or "http probe failed; https live"
        self.add(Check(
            "gate_https", "0", "HTTPS forced + cert live",
            "PASS" if (self.home and self.home.status and self.home.final_url.startswith("https://") and https_ok)
            else "FAIL",
            f"https status={self.home.status if self.home else 0} final={self.home.final_url if self.home else ''} "
            f"http status={http_page.status} loc={http_page.headers.get('location','')} err={http_page.error or ''}",
            "gate",
        ))

        # Access protection
        final = (self.home.final_url if self.home else "").lower()
        loc_bits = " ".join([final, (self.home.headers.get("location", "") if self.home else "")]).lower()
        protected = any(h in loc_bits for h in PRIMARY_PROTECTION)
        if self.home and self.home.status in {401, 403} and "vercel" in loc_bits:
            protected = True
        self.add(Check(
            "gate_access", "0", "Access-protection trap",
            "FAIL" if protected else "PASS",
            f"status={self.home.status if self.home else 0} final={self.home.final_url if self.home else ''} "
            f"protected={protected}",
            "gate",
        ))

        # Root route shipped
        root_ok = bool(self.home and self.home.status and 200 <= self.home.status < 400 and not protected)
        self.add(Check(
            "gate_root", "0", "Root route responds",
            "PASS" if root_ok else "FAIL",
            f"GET {self.url} -> {self.home.status if self.home else 0} {self.home.error or ''}",
            "gate",
        ))

        # Secrets in build
        if self.args.build_dir:
            findings = scan_build_secrets(Path(self.args.build_dir))
            self.add(Check(
                "gate_secrets", "0", "Secrets out of the client bundle",
                "FAIL" if findings else "PASS",
                ("leaked: " + "; ".join(findings[:8])) if findings else f"scanned {self.args.build_dir}, no key shapes",
                "gate",
            ))
        else:
            self.add(Check(
                "gate_secrets", "0", "Secrets out of the client bundle",
                "N/A",
                "pass --build-dir (.next/, dist/, out/, assets/) to enable this gate",
                "gate",
            ))

        # Prod env hints on the page
        body = (self.home.body if self.home else "")
        staging_hits = re.findall(
            r"https?://[^\s\"']+(?:ngrok|localhost|127\.0\.0\.1|staging\.|dev\.|vercel\.app/api)",
            body,
            re.I,
        )
        staging_hits = [h for h in staging_hits if "localhost" in h or "ngrok" in h or "127.0.0.1" in h][:6]
        self.add(Check(
            "gate_env", "0", "Prod env points at prod",
            "FAIL" if staging_hits else "PASS",
            ("staging/localhost URLs in HTML: " + ", ".join(staging_hits)) if staging_hits
            else "no localhost/ngrok/staging API hosts in first-page HTML",
            "gate",
        ))

        if self.args.repo:
            code, out, err = run_cmd(["git", "status", "--porcelain"], cwd=self.args.repo)
            dirty = bool(out) if code == 0 else True
            self.add(Check(
                "gate_shipped", "0", "Working tree shipped",
                "FAIL" if code == 0 and dirty else ("PASS" if code == 0 else "WARN"),
                out[:400] if out else (err or "clean working tree"),
                "gate",
            ))
            hist_code, hist_out, _ = run_cmd(
                ["git", "log", "--all", "--pretty=format:", "--name-only", "--",
                 ".env", ".env.local", ".env.production", ".env.development"],
                cwd=self.args.repo,
            )
            env_files = sorted({ln.strip() for ln in hist_out.splitlines() if ln.strip()}) if hist_code == 0 else []
            self.add(Check(
                "gate_env_history", "0", ".env never entered git history",
                "FAIL" if env_files else ("PASS" if hist_code == 0 else "WARN"),
                ("found in history: " + ", ".join(env_files) + " — rotate anything that lived there")
                if env_files else "no .env files in git history",
                "gate",
            ))
        else:
            self.add(Check(
                "gate_shipped", "0", "It actually shipped",
                "N/A",
                "pass --repo to compare working tree vs HEAD; confirm the domain serves the intended commit",
                "gate",
            ))

    def check_legal(self) -> None:
        html = self.html
        body = (self.home.body if self.home else "").lower()
        hrefs = [h.lower() for h, _ in (html.anchors if html else [])]
        blob = " ".join(hrefs) + " " + body[:8000]
        privacy = any(x in blob for x in ("/privacy", "privacy-policy", "privacy policy"))
        terms = any(x in blob for x in ("/terms", "terms-of", "terms of service", "terms and conditions"))
        consent = bool(re.search(r"cookie (consent|banner|settings)|gdpr|we use cookies", body))
        headers = self.home.headers if self.home else {}
        needed = ["strict-transport-security", "x-content-type-options", "referrer-policy"]
        missing = [h for h in needed if h not in headers]
        if "content-security-policy" not in headers:
            missing.append("content-security-policy")
        xfo = "x-frame-options" in headers or "frame-ancestors" in headers.get("content-security-policy", "")
        if not xfo:
            missing.append("x-frame-options/frame-ancestors")

        self.add(Check("legal_privacy", "1", "Privacy policy reachable",
                       "PASS" if privacy else "FAIL",
                       "found privacy link or copy" if privacy else "no privacy policy link on home"))
        self.add(Check("legal_terms", "1", "Terms reachable",
                       "PASS" if terms else "WARN",
                       "found terms link or copy" if terms else "no terms link on home"))
        self.add(Check("legal_consent", "1", "Cookie consent only if needed",
                       "PASS" if consent else "WARN",
                       "consent copy present" if consent else "no cookie-consent copy detected — decide deliberately if non-essential cookies drop"))
        self.add(Check("legal_headers", "1", "Security headers",
                       "PASS" if not missing else "WARN",
                       "missing: " + ", ".join(missing) if missing else "HSTS, XCTO, referrer-policy present"))

    def check_seo(self) -> None:
        html = self.html
        title = html.title if html else ""
        desc = html.meta("description") if html else ""
        og_title = html.meta("og:title") if html else ""
        og_desc = html.meta("og:description") if html else ""
        og_img = html.meta("og:image") if html else ""
        tw = html.meta("twitter:card") if html else ""
        canonical = ""
        favicon = False
        if html:
            for ln in html.links:
                rel = ln.get("rel", "").lower()
                if "canonical" in rel:
                    canonical = ln.get("href", "")
                if "icon" in rel:
                    favicon = True
        robots = fetch(urllib.parse.urljoin(self.origin + "/", "/robots.txt"))
        sitemap = fetch(urllib.parse.urljoin(self.origin + "/", "/sitemap.xml"))

        def stubby(s: str) -> bool:
            return (not s) or s.lower() in {"home", "untitled", "document", "react app", "vite + react", "next.js"}

        self.add(Check("seo_title", "2", "Unique title",
                       "FAIL" if stubby(title) else "PASS",
                       f"title={title!r}"))
        self.add(Check("seo_description", "2", "Meta description",
                       "FAIL" if not desc else ("WARN" if len(desc) < 40 else "PASS"),
                       f"description={desc[:180]!r}"))
        og_ok = bool(og_title and og_img)
        self.add(Check("seo_og", "2", "Open Graph tags",
                       "PASS" if og_ok else "WARN",
                       f"og:title={og_title!r} og:image={'yes' if og_img else 'no'} og:description={bool(og_desc)}"))
        self.add(Check("seo_twitter", "2", "twitter:card",
                       "PASS" if tw else "WARN",
                       f"twitter:card={tw!r}"))
        self.add(Check("seo_canonical", "2", "Canonical host/tag",
                       "PASS" if canonical else "WARN",
                       f"canonical={canonical!r}"))
        self.add(Check("seo_favicon", "2", "Favicon",
                       "PASS" if favicon else "WARN",
                       "link rel=icon present" if favicon else "no icon link"))
        robots_ok = robots.status == 200 and "user-agent" in robots.body.lower()
        self.add(Check("seo_robots", "2", "robots.txt present",
                       "PASS" if robots_ok else "WARN",
                       f"status={robots.status} bytes={len(robots.body)}"))
        sm_ok = sitemap.status == 200 and ("<urlset" in sitemap.body or "<sitemapindex" in sitemap.body)
        self.add(Check("seo_sitemap", "2", "sitemap.xml parseable",
                       "PASS" if sm_ok else "WARN",
                       f"status={sitemap.status} snippet={sitemap.body[:80]!r}"))

    def check_quality(self) -> None:
        probe = fetch(urllib.parse.urljoin(self.origin + "/", "/ship-checklist-404-probe-9f3c1"))
        good_404 = probe.status == 404
        self.add(Check("q_404", "3", "Custom 404 returns 404",
                       "PASS" if good_404 else "FAIL",
                       f"probe status={probe.status} final={probe.final_url}"))

        html = self.html
        missing_alt = 0
        if html:
            for img in html.images:
                if not img.get("alt") and img.get("src") and not img.get("src").startswith("data:"):
                    missing_alt += 1
        self.add(Check("q_images", "3", "Images have alt",
                       "PASS" if missing_alt == 0 else "WARN",
                       f"{missing_alt} content images missing alt"))

        vp = False
        if html:
            for m in html.metas:
                if m.get("name", "").lower() == "viewport":
                    vp = True
        self.add(Check("q_mobile_viewport", "3", "Mobile viewport",
                       "PASS" if vp else "FAIL",
                       "viewport meta present" if vp else "no viewport meta"))

        broken = []
        checked = 0
        if html:
            seen = set()
            for href, _ in html.anchors:
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absu = urllib.parse.urljoin(self.origin + "/", href)
                if urllib.parse.urlparse(absu).netloc != urllib.parse.urlparse(self.origin).netloc:
                    continue
                if absu in seen:
                    continue
                seen.add(absu)
                if checked >= MAX_INTERNAL_LINKS:
                    break
                page = fetch(absu)
                checked += 1
                if page.status >= 400 and page.status not in {401, 403, 405, 429, 999}:
                    broken.append(f"{absu} -> {page.status}")
        # outbound links count too — a 404 to your own GitHub is still your 404
        ext_broken, ext_checked = [], 0
        if html:
            seen_ext = set()
            for href, _ in html.anchors:
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absu = urllib.parse.urljoin(self.origin + "/", href)
                if urllib.parse.urlparse(absu).netloc == urllib.parse.urlparse(self.origin).netloc:
                    continue
                if absu in seen_ext:
                    continue
                seen_ext.add(absu)
                if ext_checked >= 40:
                    break
                ext_checked += 1
                page = fetch(absu, method="HEAD")
                if page.status == 0 or page.status >= 400:
                    page = fetch(absu)  # some stacks reject HEAD; retry with GET
                if page.status == 0 or (page.status >= 400 and page.status not in {401, 403, 405, 406, 429, 999}):
                    ext_broken.append(f"{absu} -> {page.status or 'unreachable'}")
        broken.extend(ext_broken)
        self.add(Check("q_links", "3", "Links resolve (internal + outbound)",
                       "FAIL" if broken else "PASS",
                       f"checked={checked} internal, {ext_checked} outbound; broken={broken[:8] or 'none'}"))

    def check_repo(self) -> None:
        repo = self.args.repo
        code, out, err = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo)
        if code != 0:
            self.add(Check("repo_status", "R", "Git repo", "FAIL", err or out or "not a git repo"))
            return
        code, out, _ = run_cmd(["git", "status", "--porcelain"], cwd=repo)
        self.add(Check("repo_status", "R", "Clean working tree",
                       "PASS" if not out else "WARN",
                       out[:400] or "clean"))

        code, out, err = run_cmd(["git", "status", "-sb"], cwd=repo)
        unpushed = False
        if code == 0:
            unpushed = "ahead" in out or "[gone]" in out or ("..." not in out and not out.startswith("## HEAD"))
            # "ahead" is the real signal; no upstream is a warn
            no_upstream = "..." not in out
        else:
            no_upstream = True
        self.add(Check("repo_unpushed", "R", "Commits pushed and pulled",
                       "FAIL" if "ahead" in (out or "") else ("WARN" if no_upstream else "PASS"),
                       out or err))

        code, out, _ = run_cmd(["git", "branch", "--merged", "HEAD"], cwd=repo)
        if code == 0:
            merged = [b.strip().lstrip("* ") for b in out.splitlines()
                      if b.strip() and not b.strip().startswith("*")
                      and b.strip() not in ("main", "master")]
            self.add(Check("repo_branches", "R", "Merged branches cleaned up",
                           "WARN" if merged else "PASS",
                           f"safe to delete: {', '.join(merged[:6])} (git branch -d ...)" if merged
                           else "no stale merged branches"))
        code, out, _ = run_cmd(["git", "worktree", "list", "--porcelain"], cwd=repo)
        if code == 0:
            wts = [l[len("worktree "):] for l in out.splitlines() if l.startswith("worktree ")]
            gone = [w for w in wts if not Path(w).exists()]
            self.add(Check("repo_worktrees", "R", "Worktrees healthy",
                           "WARN" if gone else "PASS",
                           f"stale: {gone} (git worktree prune)" if gone
                           else f"{len(wts)} worktree(s) healthy"))
        code, out, _ = run_cmd(["git", "rev-list", "--merges", "--count", "HEAD"], cwd=repo)
        if code == 0 and out.strip().isdigit():
            n = int(out.strip())
            self.add(Check("repo_linear", "R", "History shape (informational)",
                           "N/A", f"{n} merge commit(s)" + (" — linear" if n == 0 else " — not linear")))

        readme = find_first(repo, ["README.md", "README.mdx", "README"])
        stub = False
        if readme:
            text = Path(readme).read_text("utf-8", "replace")[:4000]
            stub = bool(STUB_MARKERS.search(text)) or len(text.strip()) < 80
        self.add(Check("repo_readme", "R", "README exists and is real",
                       "FAIL" if not readme else ("WARN" if stub else "PASS"),
                       str(readme) if readme else "no README"))

        license_p = find_first(repo, ["LICENSE", "LICENSE.md", "LICENCE", "COPYING"])
        vis = github_visibility(repo)
        need_license = vis != "PRIVATE"
        self.add(Check("repo_license", "R", "License present",
                       "PASS" if license_p else ("FAIL" if need_license else "WARN"),
                       f"{license_p or 'missing'} visibility={vis}"))

        sec = find_first(repo, ["SECURITY.md", "docs/SECURITY.md"])
        self.add(Check("repo_security_md", "R", "SECURITY.md",
                       "PASS" if sec else "WARN",
                       str(sec) if sec else "recommended when the app has auth"))

        todos, samples = scan_todos(repo)
        self.todo_samples = samples
        blocking_guess = [s for s in samples if USER_FACING_TODO.search(s)]
        status = "WARN" if todos else "PASS"
        if self.args.posture == "portfolio" and blocking_guess:
            status = "FAIL"
        self.add(Check("repo_todos", "R", "TODO/FIXME scan",
                       status,
                       f"count={todos} samples={samples[:6]} blocking_guess={blocking_guess[:4]}"))

        specs = scan_spec_boxes(repo)
        self.spec_items = specs
        self.add(Check("repo_specs", "R", "Open spec boxes",
                       "WARN" if specs else "PASS",
                       f"{len(specs)} unchecked items; ask: ship with these open? sample={specs[:5]}"))

        gh = github_about(repo)
        if gh.get("ok"):
            missing = [k for k in ("description", "homepage", "topics") if not gh.get(k)]
            ci = gh.get("ci")
            self.add(Check("repo_github", "R", "GitHub About / CI",
                           "WARN" if missing or ci not in {"pass", "success", None, "unknown"} else "PASS",
                           json.dumps({k: gh.get(k) for k in ("description", "homepage", "topics", "visibility", "ci")})))
        else:
            self.add(Check("repo_github", "R", "GitHub About / CI",
                           "N/A",
                           gh.get("error", "gh not available; degraded")))

    def check_oauth(self) -> None:
        body = (self.home.body if self.home else "")
        providers = detect_oauth(body, self.html)
        self.oauth_providers = providers
        if self.appl.get("oauth") == "skip" and not providers:
            self.add(Check("oauth_detected", "O", "OAuth providers", "N/A", "not in scope for this launch type"))
            return
        self.add(Check(
            "oauth_detected", "O", "OAuth providers detected",
            "PASS" if providers else "WARN",
            f"providers={providers or 'none'} — walk redirect URIs, PKCE, server-side secrets, one prod login",
        ))

    def check_docs(self) -> None:
        path = self.args.docs_path or "/docs"
        page = fetch(urllib.parse.urljoin(self.origin + "/", path))
        present = page.status == 200 and len(page.body) > 80
        stubs = bool(present and STUB_MARKERS.search(page.body))
        self.add(Check("docs_present", "D", f"Docs at {path}",
                       "PASS" if present else "FAIL",
                       f"status={page.status} bytes={len(page.body)}"))
        self.add(Check("docs_stubs", "D", "Docs are not stubs",
                       "FAIL" if stubs else ("PASS" if present else "N/A"),
                       "stub markers found" if stubs else "no stub markers"))

    def check_agentic(self) -> None:
        html = self.html
        hrefs = [h for h, _ in (html.anchors if html else []) if h and not h.startswith(("javascript:",))]
        real_hrefs = [h for h in hrefs if h and h != "#"]
        self.add(Check("ag_hrefs", "A", "Real href anchors",
                       "PASS" if real_hrefs else "FAIL",
                       f"{len(real_hrefs)} real hrefs / {len(hrefs)} anchors"))
        labeled = 0
        unlabeled = 0
        if html:
            labeled_ids = set()
            # naive: input with name/id/aria-label or associated label-for not parsed; use attrs
            for inp in html.inputs:
                if inp.get("type") == "hidden":
                    continue
                if inp.get("aria-label") or inp.get("name") or inp.get("id") or inp.get("placeholder"):
                    labeled += 1
                else:
                    unlabeled += 1
        self.add(Check("ag_labels", "A", "Labeled form inputs",
                       "PASS" if unlabeled == 0 else "WARN",
                       f"labeled_or_named={labeled} unlabeled={unlabeled}"))
        h1s = [t for tag, t in (html.headings if html else []) if tag == "h1"]
        self.add(Check("ag_h1", "A", "One h1 + heading structure",
                       "PASS" if len(h1s) == 1 else ("WARN" if h1s else "FAIL"),
                       f"h1={h1s[:2]} headings={len(html.headings) if html else 0}"))
        vp = any(m.get("name", "").lower() == "viewport" for m in (html.metas if html else []))
        self.add(Check("ag_viewport", "A", "Mobile viewport",
                       "PASS" if vp else "FAIL",
                       "viewport meta present" if vp else "missing"))
        lang = html.html_lang if html else ""
        self.add(Check("ag_lang", "A", "html lang",
                       "PASS" if lang else "WARN",
                       f"lang={lang!r}"))
        llms = fetch(urllib.parse.urljoin(self.origin + "/", "/llms.txt"))
        self.add(Check("ag_llms", "A", "llms.txt (informational)",
                       "PASS" if llms.status == 200 else "N/A",
                       f"status={llms.status}"))
        robots = fetch(urllib.parse.urljoin(self.origin + "/", "/robots.txt"))
        ai_policy = bool(re.search(r"gptbot|claudebot|google-extended|ccbot|anthropic", robots.body, re.I))
        self.add(Check("ag_robots_ai", "A", "AI crawler policy in robots.txt",
                       "N/A",
                       "deliberate AI-crawler rules found" if ai_policy else "no AI-crawler tokens; reported not judged"))
        ld = bool(html.scripts_jsonld) if html else False
        self.add(Check("ag_jsonld", "A", "JSON-LD structured data",
                       "PASS" if ld else "N/A",
                       f"{len(html.scripts_jsonld) if html else 0} ld+json blocks"))

    def run_jev(self) -> dict[str, Any] | None:
        key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not key:
            return None
        state = self.jev_state()
        # only ask about TODOs when the repo module gathered evidence — Jev judging
        # absence guesses conservatively and scores unfair weaks
        questions = JEV_QUESTIONS
        if not (getattr(self, "todo_samples", None) or getattr(self, "spec_items", None)):
            questions = {k: v for k, v in JEV_QUESTIONS.items() if k != "TODO_BLOCKING"}
        payload = {
            "state": state,
            "model": os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest"),
            "questions": questions,
        }
        req = urllib.request.Request(
            "https://api.typesafe.ai/v1/systemone",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": UA,
            },
        )
        last_err = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                return {"ok": True, "raw": data, "nouls": extract_nouls(data)}
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code in {429, 529} or e.code >= 500:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                body = e.read().decode("utf-8", "replace")[:300]
                return {"ok": False, "error": f"{last_err} {body}"}
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                time.sleep(0.4 * (2 ** attempt))
        return {"ok": False, "error": last_err or "jev failed"}

    def jev_state(self) -> str:
        html = self.html
        home = self.home
        blob = {
            "url": self.url,
            "launch_type": self.args.launch_type,
            "posture": self.args.posture,
            "status": home.status if home else 0,
            "title": html.title if html else "",
            "description": html.meta("description") if html else "",
            "og": {
                "title": html.meta("og:title") if html else "",
                "description": html.meta("og:description") if html else "",
                "image": bool(html.meta("og:image")) if html else False,
            },
            "h1": [t for tag, t in (html.headings if html else []) if tag == "h1"],
            "headings": (html.headings[:12] if html else []),
            "cta_guess": [re.sub(r"\s+", " ", t) for t in (html.text_bits if html else []) if len(t) < 48][:30],
            "privacy_terms_evidence": [c.id + "=" + c.status for c in self.checks if c.id.startswith("legal_")],
            "oauth_providers": self.oauth_providers,
            "todo_samples": self.todo_samples[:12],
            "spec_items": self.spec_items[:12],
            "mechanical": [{"id": c.id, "status": c.status, "evidence": c.evidence[:180]} for c in self.checks],
            "text_sample": " ".join((html.text_bits if html else [])[:80])[:2500],
        }
        raw = json.dumps(blob, ensure_ascii=False)
        return raw[:MAX_JEV_STATE]

    def score(self, jev: dict[str, Any] | None) -> dict[str, Any]:
        gates = [c for c in self.checks if c.severity == "gate" and c.status not in {"SKIP", "N/A"}]
        gate_fail = [c for c in gates if c.status == "FAIL"]
        mech_pool = [
            c for c in self.checks
            if c.severity in {"core", "optional"} and c.status in {"PASS", "WARN", "FAIL"}
        ]
        # optional checks that passed/failed still count if they ran; skipped stay out
        mech_score = mean([{"PASS": 1.0, "WARN": 0.5, "FAIL": 0.0}[c.status] for c in mech_pool]) if mech_pool else None

        nouls = (jev or {}).get("nouls") if jev and jev.get("ok") else None
        sem_keys = ["COPY_CLARITY", "CTA_FOCUS", "META_QUALITY", "TRUST_LEGAL"]
        sem_vals = [nouls[k] for k in sem_keys if nouls and k in nouls and isinstance(nouls[k], (int, float))]
        semantic = mean(sem_vals) if sem_vals else None

        ag_pool = [c for c in self.checks if c.id.startswith("ag_") and c.status in {"PASS", "WARN", "FAIL"}]
        ag_mech = mean([{"PASS": 1.0, "WARN": 0.5, "FAIL": 0.0}[c.status] for c in ag_pool]) if ag_pool else None
        ag_jev = nouls.get("AGENTIC_OPERABILITY") if nouls and "AGENTIC_OPERABILITY" in nouls else None
        if ag_mech is not None and isinstance(ag_jev, (int, float)):
            agentic = 0.5 * ag_mech + 0.5 * ag_jev
        elif ag_mech is not None:
            agentic = ag_mech
        elif isinstance(ag_jev, (int, float)):
            agentic = ag_jev
        else:
            agentic = None

        weights = []
        if mech_score is not None:
            weights.append(("mechanical", 0.50, mech_score))
        if semantic is not None:
            weights.append(("semantic", 0.35, semantic))
        if agentic is not None:
            weights.append(("agentic", 0.15, agentic))
        total_w = sum(w for _, w, _ in weights) or 1.0
        final = sum(w * v for _, w, v in weights) / total_w if weights else 0.0
        final_pct = round(final * 100)

        todo_blocking = None
        if nouls and "TODO_BLOCKING" in nouls:
            todo_blocking = nouls["TODO_BLOCKING"]
        if self.args.posture == "portfolio" and todo_blocking is not None and todo_blocking >= 0.7:
            # treat as a gate-equivalent blocker
            gate_fail.append(Check("todo_blocking", "R", "User-facing TODOs (Jev)", "FAIL",
                                   f"TODO_BLOCKING noul={todo_blocking}", "gate"))

        posture = self.args.posture
        # CORE means required for a production-grade verdict: a failed core check
        # caps the band at WITH NOTES no matter how high the weighted score is
        core_fails = [c for c in self.checks
                      if c.status == "FAIL" and c.severity == "core" and c not in gate_fail]
        if gate_fail:
            verdict = "BLOCKED"
            exit_code = 2
        elif posture == "fast":
            verdict = "SHIP"
            exit_code = 0
        elif final_pct < 75:
            verdict = "NOT PRODUCTION GRADE"
            exit_code = 1
        elif final_pct < 90:
            verdict = "PRODUCTION GRADE WITH NOTES"
            exit_code = 0
        else:
            verdict = "PRODUCTION GRADE"
            exit_code = 0

        # Jev overall noul <= 0.2 downgrades a high score; cannot rescue a low one
        if semantic is not None and semantic <= 0.2 and verdict in {"PRODUCTION GRADE", "PRODUCTION GRADE WITH NOTES", "SHIP"}:
            verdict = "NOT PRODUCTION GRADE"
            exit_code = 1
            final_pct = min(final_pct, 74)
        if core_fails and verdict == "PRODUCTION GRADE":
            verdict = "PRODUCTION GRADE WITH NOTES"

        plan = improvement_plan(self.checks, nouls, self.args)
        strengths = [c.title for c in self.checks if c.status == "PASS" and c.severity in {"gate", "core"}][:8]

        report = {
            "url": self.url,
            "launch_type": self.args.launch_type,
            "posture": posture,
            "skipped": sorted(self.skips),
            "oauth_providers": self.oauth_providers,
            "open_spec_items": self.spec_items,
            "todo_samples": self.todo_samples[:10],
            "gates": [check_dict(c) for c in self.checks if c.severity == "gate"],
            "checks": [check_dict(c) for c in self.checks],
            "pillars": {
                "mechanical": None if mech_score is None else round(mech_score * 100, 1),
                "semantic": None if semantic is None else round(semantic * 100, 1),
                "agentic": None if agentic is None else round(agentic * 100, 1),
            },
            "final": final_pct,
            "verdict": verdict,
            "exit_code": exit_code,
            "jev": None if jev is None else {"ok": jev.get("ok"), "nouls": nouls, "error": jev.get("error")},
            "solid_already": strengths,
            "improvement_plan": plan,
            "ops_hidden": OPS_HIDDEN,
            "manual_required": MANUAL_REQUIRED,
        }
        return report


def check_dict(c: Check) -> dict[str, str]:
    return {
        "id": c.id,
        "section": c.section,
        "title": c.title,
        "status": c.status,
        "severity": c.severity,
        "evidence": c.evidence,
    }


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def extract_nouls(data: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    answers = data.get("answers") or data.get("questions") or data
    if not isinstance(answers, dict):
        return out
    for key, val in answers.items():
        if isinstance(val, (int, float)):
            out[key] = float(val)
            continue
        if not isinstance(val, dict):
            continue
        for field_name in ("noul", "p", "probability", "yes", "score"):
            if field_name in val and isinstance(val[field_name], (int, float)):
                out[key] = float(val[field_name])
                break
        else:
            dist = val.get("distribution") or val.get("probs")
            if isinstance(dist, dict) and "true" in dist:
                try:
                    out[key] = float(dist["true"])
                except (TypeError, ValueError):
                    pass
    return out


def scan_build_secrets(root: Path) -> list[str]:
    hits: list[str] = []
    if not root.exists():
        return [f"build-dir missing: {root}"]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".js", ".mjs", ".cjs", ".map", ".html", ".json", ".css"}:
            continue
        if path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text("utf-8", "replace")
        except Exception:
            continue
        for name, pat in SECRET_PATTERNS:
            if pat.search(text):
                rel = str(path)
                hits.append(f"{name} in {rel}")
                if len(hits) >= 20:
                    return hits
    return hits


def scan_todos(repo: str) -> tuple[int, list[str]]:
    samples: list[str] = []
    count = 0
    root = Path(repo)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".md", ".rb", ".php", ".vue", ".svelte"}:
            continue
        if path.stat().st_size > 400_000:
            continue
        try:
            text = path.read_text("utf-8", "replace")
        except Exception:
            continue
        for m in TODO_RE.finditer(text):
            count += 1
            if len(samples) < 15:
                samples.append(f"{path.relative_to(root)}: {m.group(0).strip()[:100]}")
    return count, samples


def scan_spec_boxes(repo: str) -> list[str]:
    items: list[str] = []
    root = Path(repo)
    names = ("TODO", "SPEC", "ROADMAP", "BACKLOG", "FIXME")
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        upper = path.name.upper()
        if not any(n in upper for n in names):
            continue
        try:
            text = path.read_text("utf-8", "replace")
        except Exception:
            continue
        for line in text.splitlines():
            if SPEC_BOX.match(line):
                items.append(f"{path.name}: {line.strip()[:120]}")
                if len(items) >= 30:
                    return items
    return items


def find_first(repo: str, names: list[str]) -> str | None:
    root = Path(repo)
    for n in names:
        p = root / n
        if p.exists():
            return str(p)
    return None


def github_visibility(repo: str) -> str:
    code, out, _ = run_cmd(["gh", "repo", "view", "--json", "visibility", "-q", ".visibility"], cwd=repo)
    if code == 0 and out:
        return out.strip().upper()
    return "UNKNOWN"


def github_about(repo: str) -> dict[str, Any]:
    code, out, err = run_cmd(
        ["gh", "repo", "view", "--json", "description,homepageUrl,repositoryTopics,visibility"],
        cwd=repo,
    )
    if code != 0:
        return {"ok": False, "error": err or "gh repo view failed"}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "error": "unparseable gh output"}
    topics = data.get("repositoryTopics") or data.get("topics") or []
    if isinstance(topics, list) and topics and isinstance(topics[0], dict):
        topics = [t.get("name") or t.get("topic") for t in topics]
    ci_code, ci_out, _ = run_cmd(
        ["gh", "run", "list", "--limit", "1", "--json", "conclusion,status"],
        cwd=repo,
    )
    ci = "unknown"
    if ci_code == 0 and ci_out:
        try:
            runs = json.loads(ci_out)
            if runs:
                ci = runs[0].get("conclusion") or runs[0].get("status") or "unknown"
        except json.JSONDecodeError:
            ci = "unknown"
    return {
        "ok": True,
        "description": data.get("description") or "",
        "homepage": data.get("homepageUrl") or "",
        "topics": topics,
        "visibility": (data.get("visibility") or "").upper(),
        "ci": ci,
    }


def detect_oauth(body: str, html: MiniHTML | None) -> list[str]:
    blob = body.lower()
    found = []
    mapping = {
        "Google": ["accounts.google.com", "google.com/o/oauth", "sign in with google", "signinwithgoogle"],
        "GitHub": ["github.com/login/oauth", "sign in with github"],
        "Apple": ["appleid.apple.com", "sign in with apple"],
        "Microsoft": ["login.microsoftonline.com", "sign in with microsoft"],
        "X": ["api.twitter.com/oauth", "sign in with x", "sign in with twitter"],
        "Discord": ["discord.com/api/oauth2", "sign in with discord"],
        "Auth0": ["auth0.com", "auth0-lock"],
        "Clerk": ["clerk.accounts", "clerk.dev", "clerk.com"],
        "Supabase": ["supabase.co/auth", "supabase.auth"],
        "thirdweb": ["thirdweb.com"],
    }
    for name, needles in mapping.items():
        if any(n in blob for n in needles):
            found.append(name)
    return found


OPS_HIDDEN = [
    "Error monitoring wired (Sentry/Bugsnag/equivalent) and a test event received in prod",
    "Uptime signal on the production hostname",
    "Rollback tested — previous build redeployable in minutes",
    "DB backups + a restore drill if the app is DB-backed",
    "Email end-to-end (password reset not in spam). SPF/DKIM/DMARC aligned",
    "Dependency audit (npm audit / equivalent) reviewed",
    "2FA on hosting + registrar accounts",
    "Admin routes unindexed and protected",
    "API authz on every endpoint (ownership, not just authn) + rate limiting",
    "OAuth: exact prod redirect URIs, state/PKCE, secrets server-side, account-linking decided, one full prod login",
    "E-commerce: webhook signatures verified, no card data in logs, receipt email lands",
]

MANUAL_REQUIRED = [
    "LCP on a throttled prod profile (target ~2.5s) — not inferred from HTML",
    "OG image verified in a card debugger after this deploy",
    "Analytics event visible in the prod realtime view",
    "One form submit + one authenticated deep link if the app has auth",
    "First-hour pass: logged-out curl, 404, login, form, analytics, one cron/function",
]


def improvement_plan(checks: list[Check], nouls: dict[str, float] | None, args: argparse.Namespace) -> list[dict[str, str]]:
    plan = []
    for c in checks:
        if c.status not in {"FAIL", "WARN"}:
            continue
        if c.status == "FAIL" and c.severity == "gate":
            pri = "MUST FIX"
        elif c.status == "FAIL" and c.severity == "core":
            pri = "MUST FIX" if args.posture != "fast" else "SHOULD FIX"
        elif c.status == "FAIL":
            pri = "SHOULD FIX"
        else:
            pri = "SHOULD FIX" if c.severity in {"gate", "core"} else "WORTH DOING"
        plan.append({
            "priority": pri,
            "item": c.title,
            "observed": c.evidence,
            "why": why_it_matters(c.id),
            "fix": how_to_fix(c.id),
        })
    for c in checks:
        if c.status == "N/A" and c.id == "gate_secrets":
            plan.append({
                "priority": "NOT AUDITED",
                "item": c.title,
                "observed": c.evidence,
                "why": "Client bundles are public. A leaked live key is a site-killer.",
                "fix": "Re-run with --build-dir pointing at the production client output.",
            })
    if nouls:
        labels = {
            "COPY_CLARITY": "Copy clarity",
            "CTA_FOCUS": "CTA focus",
            "META_QUALITY": "Meta quality",
            "TRUST_LEGAL": "Trust / legal feel",
            "AGENTIC_OPERABILITY": "Agentic operability",
        }
        for key, label in labels.items():
            val = nouls.get(key)
            if isinstance(val, (int, float)) and val < 0.6:
                plan.append({
                    "priority": "SHOULD FIX",
                    "item": f"Jev {label} ({val:.2f})",
                    "observed": f"noul={val:.2f}",
                    "why": "Judgment dimension is below a production-grade bar.",
                    "fix": JEV_QUESTIONS[key]["criteria"]["true"],
                })
    order = {"MUST FIX": 0, "SHOULD FIX": 1, "WORTH DOING": 2, "NOT AUDITED": 3, "OPTIONAL": 4}
    plan.sort(key=lambda x: order.get(x["priority"], 9))
    return plan


def why_it_matters(cid: str) -> str:
    return {
        "gate_https": "Browsers and OAuth providers treat mixed/expired HTTP as hostile.",
        "gate_access": "SSO/deploy protection on the custom domain makes the site look dead to everyone but you.",
        "gate_root": "If / does not serve the product, nothing else matters.",
        "gate_secrets": "Anything in the client bundle is public. Rotate, do not just delete the commit.",
        "gate_env": "The page can render while every API call still hits staging or a dead tunnel.",
        "gate_shipped": "Uncommitted or unpushed work is not what production will run.",
        "legal_privacy": "Auth and analytics process personal data. Missing privacy is a launch-killer in many jurisdictions.",
        "legal_terms": "Needed when you take accounts, payments, or user content.",
        "legal_headers": "Cheap browser guarantees (HTTPS stickiness, MIME sniffing, framing).",
        "seo_title": "Share previews and search results will show the framework default.",
        "seo_og": "Unfurls on chat apps decide whether anyone clicks.",
        "q_404": "A 200 SPA fallback tells crawlers and agents the missing page exists.",
        "q_links": "Broken internal links are the fastest way to look unshipped.",
        "repo_todos": "User-facing TODOs are unfinished product, not chore notes.",
        "docs_present": "API launches without docs are incomplete products.",
        "ag_hrefs": "onclick-only navigation is invisible to agents and many assistive tools.",
        "ag_h1": "Without a real heading, neither humans nor agents can name the page.",
    }.get(cid, "Fails or warns the production-grade bar for this launch type.")


def how_to_fix(cid: str) -> str:
    return {
        "gate_https": "Force HTTPS at the host and wait for the edge cert before announcing the domain.",
        "gate_access": "Disable deploy/SSO protection on the production hostname. Vercel: PATCH project ssoProtection to null.",
        "gate_root": "Confirm the production alias points at the deployment that contains the root route.",
        "gate_secrets": "Rotate every leaked key. Rebuild without it. Check git history for .env.",
        "gate_env": "Point CORS, OAuth redirects, webhooks, and public API URLs at the prod hostname.",
        "gate_shipped": "Commit, push, and confirm the host built that SHA.",
        "legal_privacy": "Add a reachable /privacy that describes real data flows. Link it in the footer.",
        "legal_terms": "Add /terms and link it next to privacy.",
        "legal_headers": "Set HSTS, X-Content-Type-Options=nosniff, Referrer-Policy, and a frame policy.",
        "seo_title": "Set a per-page title that names the product.",
        "seo_og": "Add og:title, og:description, og:image 1200x630, then recapture in a card debugger.",
        "q_404": "Make unknown paths return HTTP 404, not the 200 app shell.",
        "q_links": "Fix or remove the listed hrefs.",
        "repo_todos": "Close user-facing TODOs or drop portfolio posture.",
        "docs_present": "Ship /docs in the same deploy as the API.",
        "ag_hrefs": "Use real <a href> for navigation.",
        "ag_h1": "One h1 that states the page purpose; keep a heading outline.",
    }.get(cid, "See references/checks.md for the exact bar.")


def render_text(report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"SHIP CHECK  {report['url']}")
    lines.append(f"type={report['launch_type']}  posture={report['posture']}  skipped={','.join(report['skipped']) or '—'}")
    lines.append(f"VERDICT  {report['verdict']}  score={report['final']}  exit={report['exit_code']}")
    p = report["pillars"]
    lines.append(
        f"pillars  mechanical={fmt_pillar(p['mechanical'])}  "
        f"semantic={fmt_pillar(p['semantic'])}  agentic={fmt_pillar(p['agentic'])}"
    )
    if report.get("oauth_providers"):
        lines.append("oauth  " + ", ".join(report["oauth_providers"]))
    lines.append("")
    lines.append("GATES")
    for c in report["gates"]:
        lines.append(f"  {c['status']:<4} {c['title']}  — {c['evidence'][:160]}")
    lines.append("")
    lines.append("CHECKS")
    for c in report["checks"]:
        if c["severity"] == "gate":
            continue
        lines.append(f"  {c['status']:<4} [{c['severity']}] {c['title']}  — {c['evidence'][:140]}")
    if report.get("jev"):
        lines.append("")
        lines.append("JEV")
        if report["jev"].get("ok"):
            for k, v in (report["jev"].get("nouls") or {}).items():
                lines.append(f"  {k}={v:.3f}" if isinstance(v, float) else f"  {k}={v}")
        else:
            lines.append(f"  degraded: {report['jev'].get('error')}")
    if report.get("solid_already"):
        lines.append("")
        lines.append("SOLID ALREADY")
        for s in report["solid_already"]:
            lines.append(f"  + {s}")
    lines.append("")
    lines.append("IMPROVEMENT PLAN")
    if not report["improvement_plan"]:
        lines.append("  (none)")
    for item in report["improvement_plan"]:
        lines.append(f"  [{item['priority']}] {item['item']}")
        lines.append(f"      observed: {item['observed'][:200]}")
        lines.append(f"      why: {item['why']}")
        lines.append(f"      fix: {item['fix']}")
    lines.append("")
    lines.append("OPS_HIDDEN (ask the owner — not visible from a URL scan)")
    for x in report["ops_hidden"]:
        lines.append(f"  - {x}")
    lines.append("")
    lines.append("NOT AUDITED BY SCRIPT (do by hand or mark NOT AUDITED)")
    for x in report["manual_required"]:
        lines.append(f"  - {x}")
    if report.get("open_spec_items"):
        lines.append("")
        lines.append("OPEN SPEC ITEMS")
        for x in report["open_spec_items"][:20]:
            lines.append(f"  - {x}")
    return "\n".join(lines) + "\n"


def fmt_pillar(v: float | None) -> str:
    return "N/A" if v is None else f"{v:.1f}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Production-grade ship auditor")
    p.add_argument("--url", required=True, help="Production URL. Never localhost.")
    p.add_argument("--build-dir", dest="build_dir", default=None)
    p.add_argument("--repo", default=None)
    p.add_argument("--docs", action="store_true")
    p.add_argument("--docs-path", default="/docs")
    p.add_argument("--agentic", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--posture", choices=["fast", "production", "portfolio"], default="production")
    p.add_argument("--launch-type", dest="launch_type",
                   choices=["marketing", "auth", "api", "ecommerce", "internal"], default="marketing")
    p.add_argument("--skip", default="", help="Comma list: seo,agentic,legal,quality,repo,docs,oauth,jev")
    p.add_argument("--no-jev", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    parsed = urllib.parse.urlparse(normalize_url(args.url))
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        print("Refusing localhost. Several traps only exist on the production hostname.", file=sys.stderr)
        return 2
    auditor = Auditor(args)
    report = auditor.run()
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
