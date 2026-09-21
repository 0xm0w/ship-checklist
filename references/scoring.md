# Scoring

Re-read this when the agent needs the exact number, not when profiling.

## Pillars

- **Gates (section 0).** Any `FAIL` on an applicable gate → `BLOCKED`, exit 2. `N/A` gates (no `--build-dir`, no `--repo`) do not block. They surface as `NOT AUDITED`.
- **Mechanical %.** `PASS=1.0`, `WARN=0.5`, `FAIL=0` over every applicable non-gate check that actually ran (`PASS|WARN|FAIL`). `SKIP` and `N/A` are out of the denominator.
- **Semantic %.** Mean of Jev nouls `COPY_CLARITY`, `CTA_FOCUS`, `META_QUALITY`, `TRUST_LEGAL`. Missing key or `--no-jev` → pillar omitted (`SEMANTIC=N/A`).
- **Agentic %.** `0.5 * agentic mechanical + 0.5 * AGENTIC_OPERABILITY` when both exist. If Jev is off, agentic mechanical alone is the pillar. `--no-agentic` omits the pillar.
- **TODO_BLOCKING** is not a pillar. It informs the repo story. Under `--posture portfolio`, noul ≥ 0.7 is treated as a gate-equivalent fail.

## Final

```
FINAL = (0.50·mechanical + 0.35·semantic + 0.15·agentic) / sum(weights of pillars that exist)
```

Skipping a pillar re-weights the rest. Skipping never scores as zero.

Examples:

- All three present — 50 / 35 / 15.
- No Jev — mechanical 0.50 and agentic 0.15, divided by 0.65.
- No Jev and no agentic — mechanical is the whole score.
- `--skip seo` only drops those checks from the mechanical denominator.

Scale FINAL to 0–100 and round to a whole number.

## Verdicts

- Gate fail → `BLOCKED` (exit 2). Score is still printed but does not save the launch.
- `--posture fast` and gates clear → `SHIP` (exit 0). Other findings stay in the plan as notes.
- Else FINAL < 75 → `NOT PRODUCTION GRADE` (exit 1).
- 75–89 → `PRODUCTION GRADE WITH NOTES` (exit 0).
- ≥ 90 → `PRODUCTION GRADE` (exit 0).

Jev overall (mean of the four semantic nouls) ≤ 0.2 downgrades `SHIP` / `PRODUCTION GRADE*` to `NOT PRODUCTION GRADE` and caps the printed score at 74. Jev cannot lift a mechanical score across a band.

## Improvement plan priorities

- **MUST FIX** — gate FAIL, or core FAIL outside fast posture.
- **SHOULD FIX** — core WARN, optional FAIL, or a Jev dimension < 0.6.
- **WORTH DOING** — optional WARN.
- **NOT AUDITED** — gate or module that did not run (missing `--build-dir` / `--repo` / `--docs`).
- **OPTIONAL** — never scored; keep on radar (OPS_HIDDEN leftovers the owner declined).
