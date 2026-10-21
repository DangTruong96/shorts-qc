# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

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
