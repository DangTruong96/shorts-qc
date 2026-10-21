# Evaluation — self-audit

An honest checklist of this repository against the criteria a reviewer
of the OpenAI **Codex for Open Source** program (and any experienced
open-source reviewer) would apply. Audited by the maintainer on
2026-10-22, against the local repository at the `v0.1.0` release
commit.

Rules of this audit: a row is **PASS** only with file-level or
command-level evidence; anything that genuinely cannot exist before
the repository is published is **PENDING-PUBLISH**, never rounded up
to a pass; anything else would be **FAIL** and fixed before release.

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Public repository readiness | **PENDING-PUBLISH** | All content is complete and committed locally, but the repository has not yet been pushed to `github.com/DangTruong96/shorts-qc`. Publication is a deliberate separate step after this audit. The first CI run also happens on that first push. Nothing else in this table depends on inventing publish state. |
| 2 | OSI-approved license | PASS | `LICENSE` holds the full MIT text (an OSI-approved license); `pyproject.toml` carries the `License :: OSI Approved :: MIT License` classifier and `license = { text = "MIT" }`. |
| 3 | README: install, usage, examples | PASS | `README.md` — Requirements, Install, Usage with sample reports for both commands, flag summary, and a "What it does not do" section; worked examples in `examples/`. |
| 4 | Automated tests passing | PASS | `python3 -m unittest discover -s tests -v` → **14 tests, OK** (caption capacity, PPM white-edge ratios, silencedetect parsing), re-run on the release commit. |
| 5 | CI configured | PASS | `.github/workflows/ci.yml` — GitHub Actions, `ubuntu-latest`, matrix Python 3.9 / 3.10 / 3.11 / 3.12, runs the unittest suite on `push` and `pull_request`. (First execution is part of row 1's publish step.) |
| 6 | Changelog + semantic versioning | PASS | `CHANGELOG.md` documents `[0.1.0] - 2026-10-22` in Keep-a-Changelog format; version `0.1.0` in `pyproject.toml`; annotated release state is the git tag `v0.1.0`; README's governance section states the semver policy. |
| 7 | Contributing guide | PASS | `CONTRIBUTING.md` — ground rules (stdlib-only runtime, real-failure rule for gates, threshold policy), development commands, bug-report contents. |
| 8 | Code of Conduct | PASS | `CODE_OF_CONDUCT.md` — Contributor Covenant v2.1 standard text, with the enforcement contact filled in (the maintainer, via GitHub). |
| 9 | Security policy | PASS | `SECURITY.md` — supported-versions table (0.1.x supported), private vulnerability reporting via GitHub Security Advisories for this repository, scope notes (offline tool; optional model download disclosed). |
| 10 | Issue & PR templates | PASS | `.github/ISSUE_TEMPLATE/bug_report.md`, `.github/ISSUE_TEMPLATE/feature_request.md` (asks for the production failure first), `.github/pull_request_template.md` (checklist mirrors CONTRIBUTING's rules). |
| 11 | Maintainer & governance stated | PASS | `README.md` → "Maintainer & governance": maintainer named (DangTruong96, sole maintainer), decision rules, triage, semver release policy, conduct/security pointers. |
| 12 | Real demo output captured | PASS | `examples/README.md` — outputs captured from actual runs of v0.1.0 on synthetic demo videos (generation commands included, so the verdicts reproduce): clean video PASS/exit 0, defective video FAIL/exit 1, caption run FAIL/exit 1 with 5 pass / 3 fail lines. `docs/USAGE.md` annotates the matching real JSON report. |
| 13 | Documentation depth | PASS | `docs/USAGE.md` — every gate documented with the production failure it catches, its mechanism, its limits, the full flag reference for both commands, and a field-by-field reading of a JSON report. |
| 14 | No placeholder markers or fabricated claims | PASS | Sweeps on 2026-10-22, each returning **no matches**: `grep -rniE "todo\|fixme\|xxx\|tbd\|lorem\|placeholder\|insert contact\|coming soon\|under construction" --exclude-dir=.git --exclude=EVALUATION.md .` and `grep -rniE "shields\.io\|badge\|stars\|downloads\|testimonial" --exclude-dir=.git --exclude=EVALUATION.md .`. This file is excluded because it names the tokens it audits for — a full sweep's only hit is this row quoting them. The README contains no badges (there is no CI history to badge yet) and there are no user, download-count, or popularity claims anywhere in the repository. |

## Summary

| Status | Count |
| --- | --- |
| PASS | 13 |
| FAIL | 0 |
| PENDING-PUBLISH | 1 |

## What PENDING-PUBLISH means, plainly

Row 1 is the only open item, and it is open on purpose: this audit was
run on the local release candidate. Publishing (creating the GitHub
repository, pushing `master` and the `v0.1.0` tag, confirming the
first CI run is green) completes it. Until that push happens, any
claim that this project is "public" would be false, so this document
does not make it.

## Known limitations a reviewer should also see

- **The project is new and has no external users yet.** No row above
  claims otherwise. The maintainer uses the gates in a daily
  production pipeline; community adoption is a thing that has to
  happen after publication, not a thing to assert before it.
- **The language gate is not covered by automated tests**, because it
  depends on the optional `faster-whisper` package and a downloaded
  speech model. Its skip behavior is exercised in every captured run
  in `examples/`; its detection behavior is not unit-tested.
- **Loudness and dead-air are whole-file measurements** in v0.1.0;
  per-scene reporting is roadmap work (v0.2), stated as such in the
  README and `docs/USAGE.md`.
