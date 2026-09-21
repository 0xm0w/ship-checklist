# Changelog

All notable changes to ship-checklist. Format: keep-a-changelog-ish, dates UTC.

## [1.3.0] - 2026-09-21

Rebased on the spec-complete rewrite (cleaner typed architecture), then ported back everything v1.2.0 learned in live fire.

- **Launch-type applicability** (`--launch-type marketing|auth|api|ecommerce|internal`): the GATE/CORE/OPTIONAL decision tree is now enforced by the script, not just the docs. Privacy/terms are a **gate** for auth and e-commerce launches. E-commerce adds an OPS row (webhook signatures, no card data in logs, receipt email).
- **`--posture production|fast|portfolio`** (was `grade`): portfolio posture escalates user-facing TODOs to blockers.
- **Localhost refused** (exit 2) — several traps only exist on the production hostname.
- **Staging/localhost URL scan** of the landing page's HTML as a site-killer gate (dead-tunnel class of failure, now mechanical).
- **CORE cap**: a failed core check caps the verdict at `PRODUCTION GRADE WITH NOTES` — CORE means required for production grade, regardless of the weighted score.
- **Outbound link checking ported back**: a 404 to your own GitHub is still your 404 (found live on purrbook.xyz: `github.com/moww20/purrbook -> 404`).
- **Git-hygiene depth ported back**: merged-branch cleanup, worktree health, linear-history shape.
- **TODO_BLOCKING gating ported back**: only asked when the repo module actually gathered evidence (Jev judging absence guesses conservatively).
- **References split**: `references/scoring.md` (formula, re-weighting, bands) and `references/checks.md` (exact bar per check + manual fallback).
- Honest correction from ground truth: purrbook.xyz has a live `/privacy` page — the mechanical legal check is right, and v1.2.0's Jev TRUST_LEGAL judgment was unfairly low because the evidence bundle missed the link.

## [1.2.0] - 2026-09-21

- **Improvement plan output**: every FAIL/WARN and each weak Jev dimension (noul < 0.8) becomes a prioritized fix — MUST FIX / SHOULD FIX / WORTH DOING / NOT AUDITED / OPTIONAL — with observed evidence, why it matters, and the exact fix. Plus a `solid already:` strengths line.
- Fixed: agentic section restored after module refactor (regression: silent pillar loss re-weighted the score without any signal).
- Fixed: TODO_BLOCKING is only asked when repo evidence exists (judging absence produced unfair weak scores).
- Fixed: NOT AUDITED items no longer inherit fix text meant for found leaks.

## [1.1.0] - 2026-09-21

- **Decision-tree profiling**: modules (seo / quality / agentic / repo / docs / oauth) are skippable; skipped checks are re-weighted out of the score, never counted as zero.
- **Repo & GitHub module** (`--repo`): clean tree, commits shipped (pushed AND pulled), merged-branch cleanup, worktree health, history shape, `.env`-in-history as a hard gate, TODO/FIXME scan with line samples, open spec boxes, README, LICENSE, GitHub About/topics/CI via `gh`.
- **OAuth module**: provider detection + interactive deep-dive items.
- **Docs module** (`--docs`): crawl with stub-marker detection.
- **Outbound link checking** (bot-blocked statuses not counted as broken).
- `--posture fast`: gates decide, everything else is notes.
- New Jev dimension: TODO_BLOCKING.

## [1.0.0] - 2026-09-21

- First release: mechanical auditor (stdlib-only Python 3.10+), Jev scoring pass, is-agentic measurement, site-killer gates with BLOCKED exit, scoring model `0.5·mechanical + 0.35·semantic + 0.15·agentic`, `--json` / `--no-jev` / `--build-dir`.
