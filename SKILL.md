---
name: ship-checklist
description: The production-grade bible for shipping web apps — a scored final check pass, not a vibes review. Use when asked if an app or site is ready to ship, deploy, launch, or go live, when someone says "ship it" or asks for a pre-launch/final check, or right before any production deploy. Profiles the launch with interactive multi-select questions, runs a mechanical auditor (site + repo + GitHub + OAuth + docs), scores judgment dimensions with TypeSafe Jev (including an is-agentic measurement), and returns a BLOCKED / NOT PRODUCTION GRADE / PRODUCTION GRADE verdict.
---

# Ship checklist

Final pass for a production URL. Arithmetic lives in `scripts/ship_audit.py`. Judgment lives in Jev when a key exists. Nothing passes without evidence.

Read `references/scoring.md` for the formula and `references/checks.md` when a finding needs the exact bar.

## 0. Profile first — never guess

Ask with `ask_user_question` before any fetch. Do not infer launch type from the URL.

**Q1 — What is shipping?** (single select)
- Marketing site / portfolio → `--launch-type marketing`
- Authenticated app → `--launch-type auth`
- API + docs → `--launch-type api`
- E-commerce or payments → `--launch-type ecommerce`
- Internal tool → `--launch-type internal`

**Q2 — Modules in scope?** (multi-select)
- Repo and GitHub readiness → `--repo /path`
- OAuth deep-dive → keep oauth in scope (default for auth/ecommerce)
- Docs check → `--docs`
- Agentic pillar → default on; `--no-agentic` to skip
- Jev scoring → on when `TYPESAFE_API_KEY` is set; otherwise `--no-jev`

**Q3 — Posture?** (single select)
- Ship fast → `--posture fast` (gates only block)
- Production grade → `--posture production` (default)
- Portfolio polish → `--posture portfolio` (user-facing TODOs become blockers)

Also collect the production URL (never localhost), `--build-dir` if a client bundle exists, and `--repo` if the git checkout is available.

Then ask only the follow-ups the profile enabled:
- OAuth providers in use? Has one full prod login been completed?
- Does it send email? Is there a DB?
- Open spec boxes — ship with them open?

Skipped modules are excluded from the score. They never count against the launch.

## 1. Run the auditor

Resolve `SKILL_DIR` as the directory that contains this `SKILL.md`. Hit the production hostname.

```bash
python3 "$SKILL_DIR/scripts/ship_audit.py" \
  --url https://example.com \
  --launch-type marketing \
  --posture production \
  --build-dir /path/to/dist \
  --repo /path/to/repo \
  --docs
```

Useful flags:

```bash
python3 "$SKILL_DIR/scripts/ship_audit.py" --url https://example.com --posture fast
python3 "$SKILL_DIR/scripts/ship_audit.py" --url https://example.com --skip seo,agentic
python3 "$SKILL_DIR/scripts/ship_audit.py" --url https://example.com --no-jev --json
python3 "$SKILL_DIR/scripts/ship_audit.py" --url https://example.com --no-agentic
```

`--build-dir` enables the secrets gate (`.next/`, `dist/`, `out/`, `assets/`). `--repo` enables git hygiene, TODOs, spec boxes, README/license, and `gh` About/topics/CI (degrades if `gh` is missing).

If Python or the network is unavailable, walk `references/checks.md` by hand and label the verdict `manual pass, no script`. A hand-checked gate must name the exact command or observation.

## 2. Treat the report as evidence

Do not re-assert a mechanical item. If the script says WARN, it stays WARN until a re-run says otherwise.

Gates override the score. Jev cannot rescue a low mechanical score. Jev overall noul ≤ 0.2 downgrades a high score to `NOT PRODUCTION GRADE`.

## 3. Close OPS_HIDDEN

The script cannot see these. Ask the owner or check the repo. Each needs a real yes/no plus evidence:

- Error monitoring wired; a test event in prod
- Uptime signal on the production hostname
- Rollback tested (previous build in minutes)
- DB backups plus a restore drill if DB-backed
- Email end-to-end (reset mail not in spam); SPF/DKIM/DMARC
- Dependency audit reviewed
- 2FA on hosting and registrar
- Admin routes unindexed and protected
- API authz (ownership, not just authn) plus rate limiting
- OAuth — exact prod redirect URIs, state/PKCE, secrets server-side only, account-linking decided, one full prod login
- E-commerce — webhook signatures verified, no card data in logs, receipt email lands

Also do the items listed under `NOT AUDITED BY SCRIPT` (LCP, card debugger, analytics realtime, one form submit).

## 4. Verdict — the script is the verdict

| Verdict | When | Exit |
|---|---|---|
| BLOCKED | any site-killer gate FAIL | 2 |
| NOT PRODUCTION GRADE | score < 75, or Jev overall ≤ 0.2 | 1 |
| PRODUCTION GRADE WITH NOTES | 75–89 | 0 |
| PRODUCTION GRADE | ≥ 90 | 0 |
| SHIP | `--posture fast` and gates clear | 0 |

Present the number, the pillars, `solid already`, then the `IMPROVEMENT PLAN` in order — MUST FIX / SHOULD FIX / WORTH DOING / NOT AUDITED / OPTIONAL. For each — observed, why, exact fix. Offer to apply the top MUST FIX items. Do not stop at the score.

## Decision tree

| Check | Marketing | Auth | API+docs | E-commerce | Internal |
|---|---|---|---|---|---|
| Site-killer gates | GATE | GATE | GATE | GATE | GATE |
| Privacy/terms | CORE if analytics | GATE | CORE | GATE | OPTIONAL |
| SEO/share | CORE | OPTIONAL | OPTIONAL | CORE | skip |
| 404, mobile, links | CORE | CORE | docs links | CORE | OPTIONAL |
| OAuth | skip | CORE | OPTIONAL | CORE | OPTIONAL |
| Docs present, no stubs | OPTIONAL | OPTIONAL | CORE | OPTIONAL | OPTIONAL |
| Repo/GitHub | CORE if public | CORE if public | CORE if public | CORE if public | OPTIONAL |
| Agentic pillar | CORE | CORE | CORE | CORE | OPTIONAL |
| DB backups / email | skip | CORE if present | CORE if present | CORE | OPTIONAL |
| Payments webhooks / no card logs | skip | skip | skip | CORE (OPS_HIDDEN) | skip |

GATE blocks launch. CORE is required for a production-grade verdict. OPTIONAL is free to skip; doing it well still earns points.

## First hour after go-live

Logged-out GET of `/` → hit a 404 → one full login (and one OAuth login if applicable) → submit one form → one analytics event in the prod realtime view → one cron/function ran without error.
