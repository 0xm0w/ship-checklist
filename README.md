# ship-checklist

**The final check pass before any web app goes live.** A scored production-readiness audit — mechanical checks in code, judgment calls scored by [TypeSafe Jev](https://docs.typesafe.ai), and an is-agentic measurement — ending in one verdict:

```
BLOCKED · NOT PRODUCTION GRADE · PRODUCTION GRADE WITH NOTES · PRODUCTION GRADE
```

Minor details become major post-launch issues. This exists so the final pass is foolproof: **arithmetic lives in code, judgment lives in the model, and nothing passes without evidence.** The checklist is a decision tree, not a flat list — skipped modules are excluded from the score, never counted against you.

## Quickstart

```bash
# gates + core checks, scored end-to-end (needs TYPESAFE_API_KEY for the Jev pass)
python scripts/ship_audit.py --url https://yoursite.com --build-dir dist/

# include the repo: git hygiene, TODO scan, spec boxes, README, GitHub About/topics/CI
python scripts/ship_audit.py --url https://yoursite.com --repo /path/to/repo

# decision tree: skip what doesn't apply (SEO for an auth-gated app, say)
python scripts/ship_audit.py --url https://yoursite.app --skip seo,agentic

# ship-fast posture: only site-killer gates decide, everything else is notes
python scripts/ship_audit.py --url https://yoursite.com --posture fast

# machine-readable
python scripts/ship_audit.py --url https://yoursite.com --json
```

Exit codes: `0` production grade · `1` not production grade · `2` blocked (site-killer gate failed) · `3` error. Stdlib-only Python 3.10+ — no dependencies to install.

## What it checks

| Section | Highlights |
|---|---|
| **0 Site-killers (gates)** | secrets in the client build & `.env` in git history, host access-protection traps (Vercel SSO protection et al.), HTTPS force, actually-shipped (deploy models & alias drift), prod env pointing at prod, mixed content |
| **1 Legal + trust** | security headers, cookie flags, policy presence, failure visibility |
| **2 Share + SEO** | titles/descriptions, OG + twitter card with a *live* preview image, favicon, sitemap/robots, canonical host + tag |
| **3 Quality + performance** | broken links (internal + outbound), 404 handling, image alts, viewport |
| **R Repo & GitHub** (`--repo`) | clean tree, all commits shipped, stale branches/worktrees, TODO/FIXME scan with samples, open spec boxes, README, license, GitHub About/topics/CI |
| **O OAuth** | provider detection + the interactive deep-dive (redirect URIs, state/PKCE, prod login test) |
| **D Docs** (`--docs`) | docs crawl with stub-marker detection ("coming soon" & friends) |
| **A Agentic readiness** | the is-agentic measurement: can an autonomous browser agent (DOM only, no vision) complete the primary task — real hrefs, labeled inputs, named controls, heading structure, llms.txt / AI-crawler policy / JSON-LD |

Items no URL scan can see land in an explicit **OPS_HIDDEN** list you close out by hand: error monitoring, rollback tested, DB backups, email deliverability (SPF/DKIM/DMARC), dependency audit, 2FA on host/registrar, admin routes, API authz + rate limiting.

## Scoring

```
FINAL = 0.5 · mechanical + 0.35 · semantic + 0.15 · agentic
```

- **Mechanical** — PASS 1.0 / WARN 0.5 / FAIL 0 over applicable checks. Skipped modules are re-weighted away.
- **Semantic** — Jev nouls on copy clarity, CTA focus, meta quality, trust/legal, TODO severity. Jev never counts; the script counts, Jev judges quality.
- **Gates override everything**: any site-killer failure → `BLOCKED`, no score softens it. Jev's overall noul can downgrade a high score but never rescue a low one.
- Bands: ≥90 `PRODUCTION GRADE` · 75–89 `WITH NOTES` · <75 `NOT PRODUCTION GRADE`.

Without `TYPESAFE_API_KEY` the audit degrades gracefully to mechanical-only (`--no-jev` forces it). A full Jev pass costs fractions of a cent.

## As an agent skill

This repo *is* the skill: drop it in your agent's skills directory (e.g. `~/.agents/skills/ship-checklist/`) and `SKILL.md` carries the full flow — including the interactive profiling step (what's shipping? which modules? what posture?) that turns the flat checklist into a decision tree before anything runs.

## Honest limitations

- The URL audit sees what an anonymous visitor sees — no authed-flow checks beyond the OAuth deep-dive, no JS-rendered SPA internals (it reads served HTML).
- CI/About checks need the `gh` CLI authenticated; docs/repo modules need their flags.
- Scanned for common key shapes, not every possible secret format. Rotate anything that ever leaked — removal is not rotation.

MIT licensed. Ship fast; the details are covered.
