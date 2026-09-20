# Changelog

All notable changes to ship-checklist. Format: keep-a-changelog-ish, dates UTC.

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
