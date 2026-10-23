# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.2.2] - 2026-10-23

### Fixed

- **Display rotation is honoured by the probe gate.** A clip stored
  landscape with a 90/270-degree display matrix (phone portrait
  footage) was reported with its stored dimensions and failed
  `--expect 9:16`, while the frame gates — which decode with
  ffmpeg's autorotation — saw the portrait picture. The probe now
  reads the Display Matrix side data (and the legacy `rotate` tag),
  swaps width/height for quarter turns, and reports the rotation.
- **Cover art is no longer mistaken for a video stream.** An audio
  file with an embedded picture, renamed to `.mp4`, sailed through
  the probe as a "500x500, 90000 fps" video and failed later gates
  with confusing errors. Streams flagged `attached_pic` are now
  ignored when selecting the picture track; a file with only cover
  art gets the clear "no video stream" error.
- **Frames and silence are no longer judged against a lying
  duration.** When container metadata overstated the duration
  (observed: an FLV claiming 600s for 4s of media), every white-edge
  frame seek landed past the end of the file and the gate errored
  on a playable video; a trailing silence could likewise be
  "closed" at the fake duration. The white-edge gate now falls back
  to the first frame when all fraction seeks fail, and dead-air
  clamps gap judging to the last timecode ffmpeg actually
  processed.
- **Caption files: BOM stripped, non-UTF-8 rejected cleanly.** A
  UTF-8 BOM counted as 0.55 width units and failed a line that was
  exactly at capacity; a Shift-JIS file crashed with an uncaught
  `UnicodeDecodeError`. Files are now read as UTF-8-SIG, and
  undecodable input exits 2 with a clear message.
- **Invalid limits are usage errors, not verdicts.** Negative
  `--max-gap` / `--max-black` / `--max-freeze` values made a clean
  video FAIL with nonsense summaries ("black picture for 0.0s at
  Nones (limit -5.0s)"); they now exit 2 at parse time, as does an
  inverted `--min-lufs` > `--max-lufs` range. `--width` for
  captions must be positive too.
- 12 regression tests — 40 tests total.

## [0.2.1] - 2026-10-23

### Fixed

- **dead-air no longer passes vacuously on a video with no audio
  stream.** ffmpeg exits 0 on such a file without ever running
  `silencedetect`, so the gate parsed zero silence gaps and reported
  PASS — on a file whose entire soundtrack is missing. The probe now
  reports whether an audio stream exists (`has_audio` in the probe
  details), and the dead-air gate reports an **error** for a file
  with no audio track, mirroring the loudness gate's verdict on the
  same file.
- **A file with no video stream now gets a clear error.** An
  audio-only file used to exit 2 with "could not parse ffprobe
  output: list index out of range" — a leaked Python internal that
  blamed the probe output. It now says the file has no video stream.
- 5 regression tests for the probe's stream handling and the
  dead-air no-audio case — 28 tests total.

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
