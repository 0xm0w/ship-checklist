# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository (Security tab → Report a vulnerability) rather than a public issue. You'll get an acknowledgment within a few days.

## What this tool does with your data (read before auditing private things)

- The auditor fetches **public URLs anonymously** and reads local files you explicitly point it at (`--build-dir`, `--repo`).
- **If `TYPESAFE_API_KEY` is set, an evidence bundle is sent to the TypeSafe API** (`api.typesafe.ai`) for judgment scoring. The bundle contains page text (titles, descriptions, nav labels, policy excerpts) and repo-hygiene samples (TODO lines, spec boxes). It does not contain your source code, environment values, or scanned build output — secret *patterns* found by the scanner are reported locally and truncated.
- If you are auditing a site you don't want described to a third-party service, run with `--no-jev`. Everything mechanical stays on your machine.

## Supported versions

Main branch only — this is a small, fast-moving tool.
