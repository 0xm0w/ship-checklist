# Checks

Use this when a WARN/FAIL needs the exact bar or a manual fallback.

## 0. Site-killers (gates)

**Secrets out of the client bundle.** Everything in built JS is public. `NEXT_PUBLIC_*` / `VITE_*` are public by definition; live secret shapes (`sk_live_`, `AKIA…`, `ghp_`, PEM keys, service-role JWTs) are a rotate-now event. Removal from a later commit is not enough — rotate the credential. Enable with `--build-dir`. With `--repo`, also check `.env` never entered history (`git log --all -- .env`).

**Access-protection trap.** Vercel SSO, Netlify password protection, and Cloudflare Access on the custom domain send anonymous visitors to a login while preview URLs look fine. The owner cannot reproduce it while logged in. Check `/` logged out. Vercel fix: `PATCH /v9/projects/<name>` with `{"ssoProtection": null}`.

**HTTPS forced + cert live.** `http://` should 30x to `https://`. A refused HTTP probe plus a live HTTPS cert is acceptable at some edges. New aliases take a few minutes for the cert — do not panic-rollback in that window.

**Root route.** `GET /` is the product. Status 2xx/3xx, not a protection interstitial. Framework `redirect()` that flashes a meta-refresh on the entry path is a smell — prefer middleware/proxy.

**Prod env points at prod.** First-page HTML must not embed `localhost`, `127.0.0.1`, ngrok, or obvious staging API hosts. CORS, OAuth redirects, and webhooks are OPS_HIDDEN complements of this check.

**It actually shipped.** Git-integrated hosts ship the merged default-branch commit. CLI hosts ship the working tree. `--repo` fails the gate on a dirty tree. Confirm the domain's deployment SHA matches the SHA you think you shipped.

## 1. Legal + trust

Privacy policy and Terms in the footer when analytics, cookies, accounts, or payments exist. For `--launch-type auth` and `ecommerce` this family is a **gate**. Cookie consent only if non-essential cookies drop — absence is a WARN so the owner decides. Security headers expected: HSTS, `X-Content-Type-Options`, `Referrer-Policy`, plus framing (`X-Frame-Options` or CSP `frame-ancestors`). Missing headers are WARN.

## 2. Share + SEO

Per-page title and description (not `Home`, `Vite + React`, `Next.js`). OG `title` + `image` (1200×630 intended; verify in a card debugger — that last step is manual). `twitter:card`. Favicon link. `robots.txt` with a `User-agent` line. `sitemap.xml` that parses as `urlset` or `sitemapindex`. A canonical link. Skip this family with `--skip seo` or for `--launch-type internal`.

## 3. Quality

Unknown path `/ship-checklist-404-probe-9f3c1` must return **HTTP 404**, not a 200 SPA shell. Content images need `alt`. Viewport meta present. Internal links crawled (cap 30 in the script; bot-blocked 401/403/405/429/999 are not broken).

Not in the script — mark NOT AUDITED if you did not do them: LCP ~2.5s throttled on prod, WCAG AA contrast, 375px tap path for hover-only UI, analytics realtime event, one form submit.

## R. Repo (`--repo`)

Clean working tree. Branch not `ahead` of upstream; no-upstream is WARN. README exists and is not a stub. LICENSE required unless visibility is PRIVATE. SECURITY.md recommended when there is auth. TODO/FIXME/XXX/HACK counted outside vendored dirs; portfolio posture fails on user-facing samples. Unchecked `- [ ]` boxes in TODO/SPEC/ROADMAP/BACKLOG files are surfaced — ask whether they ship open. `gh repo view` for About description, homepage, topics, latest Actions conclusion; degrade if `gh` is missing.

## O. OAuth

The script only **detects** providers from home HTML (Google, GitHub, Apple, Microsoft, X, Discord, Auth0, Clerk, Supabase, thirdweb). Real checks are a walk with the owner: exact prod redirect URI (no localhost, no wildcard), state/PKCE, client secrets server-side, minimal scopes, account-linking rule, email-verified handling, one full prod login in the first hour.

## D. Docs (`--docs`)

Default probe `/docs` (`--docs-path` to override). Must be 200 with real body and no stub markers (`coming soon`, `under construction`, `work in progress`, `lorem ipsum`). Update docs in the same deploy as behavior changes.

## A. Agentic

An autonomous browser agent with DOM + accessibility tree, no vision, should be able to name the purpose and operate the primary task. Mechanical: real `href` anchors, named/labeled inputs, one `h1`, viewport, `html[lang]`. Informational (reported, not judged): `llms.txt`, AI-crawler tokens in `robots.txt`, JSON-LD. Failing this pillar usually means the site also fails screen readers.

## Manual first-hour

Logged-out GET `/` → 404 probe → one login (and one OAuth login) → one form submit → one analytics event in the prod realtime view → one cron/function without error.
