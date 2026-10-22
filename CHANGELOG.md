# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.2.0] - 2026-10-23

### Added

- **freeze-black gate** in `check`: the visual counterpart of
  dead-air. Samples frames at 2 fps (scaled to 96 px wide) in a single
  ffmpeg pass and measures them in pure Python (mean Rec. 601 luma;
  mean absolute pixel difference between consecutive samples). Fails
  on continuous black picture longer than `--max-black` (default
  1.5 s) or a frozen, unchanging picture longer than `--max-freeze`
  (default 3.0 s), reporting each defect's duration and start time.
  Catches encoder/compositor hangs that repeat one frame and panel
  renders that drop the picture to black mid-video.
- 9 unit tests for the frame-series analysis (luma, frame diffs,
  black/frozen run measurement, thresholds, mismatched frame sizes)
  — 23 tests total.
- `examples/` gains a third synthetic demo video, `stuck.mp4`
  (2.5 s of black followed by 4 s of a static frame), with captured
  real output; README and `docs/USAGE.md` document the new gate,
  its flags, and its JSON details.

## [0.1.0] - 2026-10-22

First public release. Extracted from the quality gates of a daily
AI-video production pipeline.

### Added

- `check` command running all gates on a video file, with `--json` output:
  - **probe** — duration, resolution, fps, orientation, optional expected
    aspect ratio (`--expect 9:16` / `16:9`).
  - **loudness** — integrated loudness via ffmpeg `loudnorm` analysis;
    fails below −24 LUFS or above −10 LUFS (configurable), with a
    `volumedetect` fallback.
  - **dead-air** — `silencedetect` scan; fails on any inside silence gap
    longer than `--max-gap` (default 2.5 s).
  - **white-edge** — samples up to 5 frames and measures near-white pixels
    in the outer 15% band of each edge (pure-Python PPM parsing); fails an
    edge above 3%.
  - **language** (optional) — with `faster-whisper` installed and
    `--lang ja`, flags segments containing Latin-alphabet speech; skips
    cleanly when the package is missing.
- `caption` command implementing the production caption-fit rule
  (720 px / fontsize 60 → max 11 full-width characters per line).
- Unit tests for caption capacity math, white-edge ratios on synthetic
  PPM data, and silencedetect output parsing.
- GitHub Actions CI (`.github/workflows/ci.yml`) running the unit
  tests on Python 3.9, 3.10, 3.11, and 3.12.
- Community and governance files: CODE_OF_CONDUCT.md (Contributor
  Covenant 2.1), SECURITY.md, issue templates (bug report, feature
  request), a pull-request template, and a "Maintainer & governance"
  section in the README.
- `docs/USAGE.md` — per-gate guide with the production failure behind
  each gate, the full flag reference, and an annotated JSON report.
- `examples/` — sample caption file and captured real output from
  running the tool on synthetic demo videos.
- `EVALUATION.md` — self-audit of the repository against common
  open-source project criteria.
