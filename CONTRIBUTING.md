# Contributing

Small tool, small rules.

## Setup

Nothing to install — Python 3.10+ standard library only. Run it against any public site:

```bash
python scripts/ship_audit.py --url https://example.com --no-jev
```

With `TYPESAFE_API_KEY` set you get the Jev judgment pass; without it everything still works, mechanically scored.

## The design law (please keep it)

1. **Arithmetic lives in code, judgment lives in Jev.** If a check can be decided by fetching and comparing, it is a mechanical check. If it needs taste (is this copy good?), it is a noul question with literal criteria. Jev never counts.
2. **Skipped is never zero.** Modules that don't apply to a launch get re-weighted out of the score.
3. **Evidence or it didn't happen.** Every check prints what it actually fetched.
4. **Criteria are literal.** When adding a Jev question: spell out the boundary cases in `criteria.true` / `criteria.false`, and handle absence explicitly.
5. Boring over clever (this includes you, metaprogramming).

## Before a PR

- Run the auditor on at least one real site (mechanical `--no-jev` pass, plus a Jev pass if you touched scoring/questions) and paste the relevant output.
- `python -m json.tool` your `--json` output once — the machine-readable shape is a contract.
- Update `CHANGELOG.md`.

## Reporting bugs

Open an issue with the command you ran, the URL class it failed on (no need for private URLs), and the traceback. Security-adjacent disclosures: see SECURITY.md.
