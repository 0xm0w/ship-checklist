---
name: ship-checklist
description: The production-grade bible for shipping web apps — a scored final check pass, not a vibes review. Use when asked if an app or site is ready to ship, deploy, launch, or go live, when someone says "ship it" or asks for a pre-launch/final check, or right before any production deploy. Profiles the launch with interactive multi-select questions, runs a mechanical auditor (site + repo + GitHub + OAuth + docs), scores judgment dimensions with TypeSafe Jev (including an is-agentic measurement), and returns a BLOCKED / NOT PRODUCTION GRADE / PRODUCTION GRADE verdict.
---

# Ship checklist — the production-grade bible

Minor details become major post-launch issues. This skill exists to make the final pass **foolproof**: arithmetic lives in code, judgment lives in Jev, and nothing passes without evidence. The checklist is a **decision tree, not a flat list** — profile first, then only what applies counts.

## Step 0 — Profile the launch (always ask first)

Before running anything, ask the user with the platform's interactive question tool (multi-select where offered). Never guess these — they decide which checks are gates, which are core, and which don't exist for this launch:

**Q1 — What is shipping?** (single select)
Marketing site / portfolio · Authenticated app (logins, sessions) · API + docs · E-commerce or payments · Internal tool (private users only)

**Q2 — Which modules are in scope?** (multi-select)
- Repo & GitHub readiness (`--repo`) — git hygiene, TODOs, README, About/topics, CI
- OAuth deep-dive — provider config, redirect URIs, one full prod login
- Docs check (`--docs`) — stub/placeholder crawl
- Agentic readiness pillar
- Jev scoring (off if no `TYPESAFE_API_KEY` — use `--no-jev`)

**Q3 — Posture?** (single select)
- **Ship fast** (`--posture fast`) — only site-killer gates block; everything else is notes
- **Production grade** (default) — full bar, scored verdict
- **Portfolio polish** — production grade + TODO/spec items get judged as blockers, not notes

Run the auditor with the matching flags, then ask targeted follow-ups only for enabled branches (OAuth providers in use? does it send email? is there a DB? open spec items — ship with them open?). Skipped modules are excluded from the score — they never count against the launch.

## The flow (after profiling)

1. **Run the auditor against the production URL** (never localhost — several traps only exist in prod):

   ```bash
   python scripts/ship_audit.py --url https://site.example --build-dir dist/ --repo /path/to/repo
   python ship_audit.py --url https://site.example --posture fast          # gates decide
   python ship_audit.py --url https://site.example --skip seo,agentic      # decision tree in action
   python ship_audit.py --url https://site.example --json                  # machine-readable
   ```

   `--build-dir` (built client output: `.next/`, `dist/`, `assets/`) enables the secrets gate. `--repo` enables git hygiene, TODO scan, spec boxes, README/license, and GitHub About/topics/CI checks (via `gh`, degraded gracefully if absent).

2. **Read the report as evidence, not suggestions.** Every mechanical PASS carries what was actually fetched. Never re-assert a mechanical item by hand — if the script says WARN, the finding stands until a re-run says otherwise.

3. **Close out the OPS_HIDDEN list** — items no URL scan can see. Check the repo or ask the owner; each needs a real answer: error monitoring wired, uptime signal, rollback tested (redeploy the previous build in minutes), DB backups + restore if DB-backed, email sending verified end-to-end (SPF/DKIM/DMARC — password resets landing in spam is a launch-killer), dependency audit (`npm audit`), 2FA on hosting/registrar accounts, admin routes unindexed + protected, API authz on every endpoint (ownership checks, not just authn) + rate limiting. When OAuth is detected: prod redirect URIs exact (no localhost), state/PKCE enforced, client secrets server-side only, account-linking behavior decided, and **one full login completed on prod**.

4. **Verdict.** The script's verdict is the verdict: `BLOCKED` (a site-killer gate failed — exit 2), `NOT PRODUCTION GRADE` (< 75 — exit 1), `PRODUCTION GRADE WITH NOTES` (75–89), `PRODUCTION GRADE` (≥ 90 — exit 0). Fast posture: gates clear → SHIP, full stop. Jev's overall noul ≤ 0.2 downgrades a high score; gates override everything; Jev can never rescue a low score.

5. **Present the improvement plan — the score is the half the user already has.** Every report ends with an `IMPROVEMENT PLAN`: each FAIL/WARN and each weak Jev dimension translated into a prioritized fix — **MUST FIX** (blocks launch) / **SHOULD FIX** (costs the score) / **WORTH DOING** (cheap half-credit wins) / **NOT AUDITED** (rerun with the missing flag) / **OPTIONAL** (never scored, keep on radar), each with what was observed, why it matters, and the exact fix — plus a `solid already:` strengths line so the report isn't pure negativity. Walk the user through the top items in order and offer to fix them; don't just relay the number.

## Decision tree (which items apply to which launch)

| Check | Marketing | Auth'd app | API+docs | Internal tool |
|---|---|---|---|---|
| Site-killer gates (secrets, access-protection, HTTPS, shipped, env) | **GATE** | **GATE** | **GATE** | **GATE** |
| Cookie consent, privacy/terms | CORE if analytics | **GATE-ish** (auth = personal data) | CORE | OPTIONAL |
| SEO/share (OG, sitemap, robots, canonical) | CORE | OPTIONAL (skip with `--skip seo`) | OPTIONAL | skip |
| Custom 404, mobile, contrast, links | CORE | CORE | docs links only | OPTIONAL |
| OAuth flow items | skip | **CORE** | OPTIONAL | OPTIONAL |
| Docs present + no stubs | OPTIONAL | OPTIONAL | **CORE** | OPTIONAL |
| Repo/GitHub readiness | CORE if public repo | CORE if public repo | CORE if public repo | OPTIONAL |
| Agentic pillar | CORE | CORE | CORE | OPTIONAL |
| DB backups / email deliverability | skip | **CORE** if present | **CORE** if present | OPTIONAL |

GATE = blocks launch. CORE = required for a production-grade verdict. OPTIONAL = nice-to-have; skipping is free, doing it well earns points.

## Scoring model (why the number means something)

- **Gates (section 0):** any FAIL → BLOCKED. No score softens a leak, a protection-walled domain, or a broken entry route.
- **Mechanical %:** PASS=1.0, WARN=0.5, FAIL=0 over all applicable non-gate checks (sections 1–3 + repo + docs). Skipped and N/A checks are excluded from the denominator.
- **Semantic %:** Jev nouls on COPY_CLARITY, CTA_FOCUS, META_QUALITY, TRUST_LEGAL (+ TODO_BLOCKING informs the repo story) — judgment-only dimensions where greps lie.
- **Agentic %:** 0.5 × agentic mechanical checks + 0.5 × Jev's AGENTIC_OPERABILITY noul — the **is-agentic measurement**, one pillar of the score, not the whole score.
- **FINAL = 0.5·mechanical + 0.35·semantic + 0.15·agentic**, re-weighted when a pillar is skipped (skipping never scores zero).
- Without `TYPESAFE_API_KEY` the audit degrades to mechanical-only (SEMANTIC=N/A, exit codes still honest). `--no-jev` forces it.

## The checklist (what each check means and what to do on WARN/FAIL)

### 0. Site-killers (hard gates — all must pass)

- **Secrets out of the client bundle.** Everything in built JS is public; `NEXT_PUBLIC_*`/`VITE_*` by definition, keys get there by accident too. The script greps the build for key shapes, and (with `--repo`) checks `.env` never entered git history. If a key leaked: **rotate it** — removal from a later commit is not enough.
- **Access-protection trap.** Hosts ship new projects with deploy protection on by default (Vercel's SSO protection is the notorious one; Netlify password protection and Cloudflare Access behave the same): the custom domain redirects anonymous visitors to a login while preview URLs look fine — the site appears dead and the logged-in owner can't reproduce it. The script checks root anonymously and flags redirects to protection hosts. Vercel fix: `PATCH https://api.vercel.com/v9/projects/<name>` with `{"ssoProtection": null}`.
- **HTTPS forced + cert live.** `http://` must 30x to `https://`. Edge certs take a few minutes after aliasing — don't panic-rollback in that window.
- **It actually shipped.** Deploy models differ, and each has a silent failure mode. Git-integrated hosting: only a **merged PR to the default branch** ships. CLI deploys: the **working tree** ships — run `git status` first (concurrent edits happen). Monorepo path filters can skip the build silently — compare the deployment's commit hash to local HEAD. A manually-aliased custom domain can point at an older deployment. Confirm what the domain serves is the commit you think. The repo module adds the flip side: uncommitted changes and unpushed/unpulled commits (SHIPPED check).
- **Root route + entry flows.** `/` is the product. Framework redirects can degrade (Next.js page-level `redirect()` renders a 1s meta-refresh flash — use middleware/proxy-level for entry paths). Sign-out lands somewhere sane. Deep-link one authenticated URL.
- **Prod env points at prod.** Stale staging/dead-tunnel URLs are a classic silent failure — the page renders, only the calls fail. CORS allowlists, OAuth redirect URIs, webhooks, and internal service endpoints must name the prod domain.

### 1. Legal + trust

Privacy policy + Terms when analytics/cookies/third-party auth exist (footer, reachable). Cookie consent only if non-essential cookies drop — decide deliberately. Forms: server-side validation (client-side is decoration), honeypot + rate limit; third-party rate limits bite post-launch. Security headers (HSTS, XCTO, framing, Referrer-Policy, Permissions-Policy) — the script WARNs on missing. Then the OPS_HIDDEN items from the flow above.

### 2. Share + SEO

Unique titles/descriptions per page. OG 1200×630 + twitter:card, **verified via card debugger** (opengraph.xyz or the platform's own); screenshot previews go stale on redesign — recapture is part of the deploy. Favicon + apple-touch-icon 200. sitemap.xml parseable, robots.txt present (block `/api`, staging hosts), one canonical host (apex/www 301) + canonical tag. Custom 404: garbage deep link returns a real 404 status, not a 200 SPA fallback.

### 3. Quality + performance

No broken links — internal (150/page cap in script) and outbound (bot-blocked statuses 403/405/429/999 don't count as broken). Hardcoded counts lie — compute them; a shipped site once tabbed "10 projects" over a 9-item grid. Images: alt attributes, compressed, explicit dimensions. LCP < ~2.5s on prod, throttled. WCAG AA contrast (4.5:1 body, 3:1 large). Mobile at 375px; touch has no hover — hover-only affordances need a tap path. Visible keyboard focus. External assets fail gracefully (onerror fallbacks). Analytics firing in the prod realtime view. One clear CTA per page.

### R. Repo & GitHub readiness (`--repo`)

- **Git hygiene:** clean working tree; all commits shipped (pushed AND pulled — a branch with no upstream can't prove anything); stale merged branches deleted; dead worktrees pruned; history shape reported (linear or not — informational, workflows differ).
- **TODO/FIXME/XXX/HACK scan:** the script counts and samples them across the source (excluding vendored/minified code); Jev judges whether any sample marks unfinished user-facing functionality vs harmless chore notes. Portfolio polish posture treats the bad ones as blockers.
- **Open spec items:** unchecked `- [ ]` boxes in TODO/SPEC/ROADMAP/BACKLOG files — surfaced, and the profiling follow-up asks: ship with these open?
- **README:** exists, real content, no stub markers. **License:** present (required for public repos). **SECURITY.md** recommended for anything with auth.
- **GitHub (via `gh`):** About description, homepage URL, and topics set; latest CI run green; visibility noted (it decides how heavy LICENSE/SECURITY weight is).

### O. OAuth (CORE when the app has login)

The script detects providers from the login page (Google/GitHub/Apple/Microsoft/X/Discord/Auth0/Clerk/Supabase/thirdweb). The real checks are interactive — walk them with the owner: every provider's redirect URI is the exact prod URL (no localhost, no wildcard), state/PKCE enforced (CSRF), client secrets server-side only, minimal scopes, account-linking behavior decided (existing email + new provider), email-verified handling, and **one full login completed on prod** during the first-hour pass.

### D. Docs (`--docs`, default probes `/docs`)

Docs crawl checks: present, crawlable, and **no stub markers** ("coming soon", "under construction", "work in progress", lorem). Docs that contradict the shipped UI are the stale-screenshot problem in another costume — update docs in the same deploy as behavior changes.

### A. Agentic readiness (the is-agentic measurement)

AI agents are users now; the question is whether an autonomous browser agent (DOM + accessibility tree only, no vision) can discover the purpose and complete the primary task. Mechanical: real `href` anchors (not onclick-only), labeled form inputs, named controls, one h1 + real heading structure, mobile viewport, `lang`. Informational: `llms.txt` (increasingly standard), AI-crawler policy in robots.txt (deliberate choice, reported not judged), JSON-LD structured data. Jev judges the whole from the evidence. A site that fails for agents is also failing screen readers — this pillar is accessibility with a deadline.

## Jev integration (same contract as the doctrine checker)

`JEV_QUESTIONS` in the script hold noul questions with literal true/false criteria; high noul = production grade on that dimension. Jev never counts — the script counts, Jev judges quality. State is the evidence bundle (mechanical results + page evidence + policy text + repo hygiene + OAuth providers), truncated at 100k chars. Retries 429/529/5xx. Cost ~$0.042/Mtok in-only (a full pass is fractions of a cent). To add a dimension: literal phrasing, spell out boundary cases in the criteria, mirror the scoring wiring in `score()`.

## When the script can't run (no network, no Python, private staging)

Fall back to the checklist by hand, in order, with evidence per item — but say so in the verdict line ("manual pass, no script"). Gates stay gates: a hand-checked ✅ on a gate item must name the exact command or observation behind it.

## First hour after go-live (do not skip)

Curl the domain logged-out → hit a 404 → complete one full login (and one OAuth login if applicable) → submit the form once → watch one analytics event land in realtime → confirm any cron/function ran once without error.
