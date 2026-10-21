# ShortsQC

Quality gates for AI-generated short-form video — a small CLI that checks
a finished video file for the specific ways AI/TTS production pipelines
actually break, before you publish.

It is one Python file, standard library only. ffmpeg/ffprobe do the media
work; everything else (silence-gap parsing, PPM frame analysis, caption
math) is plain Python.

## Why this exists

ShortsQC was extracted from the quality-control step of a real daily
production pipeline for Japanese faceless YouTube channels (script →
TTS → AI panels → assembly → upload). Every gate in it corresponds to a
failure that shipped, or nearly shipped, at least once:

- **Captions clipped at the edges.** A narration line was burnt in at
  fontsize 60 on a 720 px-wide vertical video and ran past the frame —
  glyphs were cut in half. Measuring a rendered frame caught it too
  late; the arithmetic catches it in advance: at that size, **11
  full-width characters per line is the maximum**.
- **TTS reading in the wrong language.** One narration part came back
  from the TTS engine in English even though the script was Japanese.
  Worse, the mistake survived a first QC pass because the short English
  segment was masked by the longer Japanese audio around it when
  checking per-scene files. The language gate therefore transcribes the
  audio of the *final* file with automatic language detection and flags
  any Latin-alphabet speech.
- **Dead air from padding.** Scenes padded to a planned duration
  instead of their actual speech length left multi-second silence gaps
  inside the video — one scene had 1.8 s of speech stretched to 10 s.
- **Panels fading to white.** AI-generated panels frequently had art
  covering only part of the frame, with one edge dissolving into white.
  On a small contact sheet this looks like a bright background; measured
  per-pixel, it is unmistakable.
- **Quiet dialogue.** One character's TTS voice measured 7–10 dB below
  the others, and that character's lines were barely audible on a phone
  speaker.

None of these need machine learning to catch. They need a checklist that
runs the same way every time. That is all this tool is.

## Features

`shorts-qc check <video>` runs:

| Gate | What it does |
| --- | --- |
| **probe** | ffprobe: duration, resolution, fps, orientation. Fails if the aspect ratio does not match `--expect 9:16` or `--expect 16:9`. |
| **loudness** | Integrated loudness via ffmpeg `loudnorm` analysis. Fails below **−24 LUFS** or above **−10 LUFS** (both configurable). Falls back to `volumedetect` (reported as a fallback, not LUFS) if loudnorm output cannot be parsed. |
| **dead-air** | ffmpeg `silencedetect` (noise=−40 dB, min duration 1.2 s). Fails if any silence gap *inside* the file exceeds `--max-gap` (default 2.5 s). Gaps touching only the first/last 0.3 s are ignored. |
| **white-edge** | Samples up to 5 frames evenly across the video, parses them as PPM in pure Python, and measures near-white pixels (all channels ≥ 250) in the outer 15% band of each edge. An edge band above **3%** near-white fails. |
| **language** *(optional)* | With `faster-whisper` installed and `--lang ja`, extracts the audio, transcribes with automatic language detection, and fails if any segment contains a run of ≥ 4 Latin-alphabet characters. Without the package it prints a clear `skipped` notice — it never crashes. |

`shorts-qc caption` is the caption-fit gate on its own:

- Capacity = `floor((width − fontsize) / fontsize)` full-width
  characters — the raw division `720 / 60 = 12` ignores the horizontal
  safety margin; reserving half a fontsize of padding on each side
  yields the production rule **720 px / fontsize 60 → 11 characters**.
- Character width: `unicodedata.east_asian_width` in `(W, F)` counts
  1.0; anything else counts 0.55.
- Prints a per-line PASS/FAIL verdict.

Exit codes everywhere: **0** = pass, **1** = a gate failed, **2** = tool
error (file missing, ffmpeg/ffprobe not found, probe failure).

## Requirements

- Python 3.9+
- `ffmpeg` and `ffprobe` on `PATH`
  (Debian/Ubuntu: `sudo apt install ffmpeg`; macOS: `brew install ffmpeg`)
- Optional: `pip install faster-whisper` for the language gate

## Install

```bash
git clone https://github.com/DangTruong96/shorts-qc
cd shorts-qc
pip install .
# …or just run the single file, no install needed:
python3 shorts_qc.py --help
```

## Usage

### Check a video

```bash
shorts-qc check final.mp4 --expect 9:16 --lang ja
```

Sample output — a clean video:

```text
ShortsQC report — clean.mp4
[PASS] probe: 360x640 (portrait), 30.0 fps, 9.0s — aspect matches 9:16
[PASS] loudness: -21.8 LUFS (allowed -24.0 .. -10.0)
[PASS] dead-air: no silence gap longer than 2.5s inside the file (0 gap(s) detected, all within tolerance or at the edges)
[PASS] white-edge: all edge bands <= 3% near-white across 5 frame(s)
[SKIP] language: skipped: pip install faster-whisper (language gate not available)
OVERALL: PASS
```

Sample output — a video with a 3-second silence gap in the middle and a
white right edge:

```text
ShortsQC report — gapped.mp4
[PASS] probe: 360x640 (portrait), 30.0 fps, 9.0s — aspect matches 9:16
[PASS] loudness: -21.9 LUFS (allowed -24.0 .. -10.0)
[FAIL] dead-air: silence gap of 2.995s at 3.018594s exceeds --max-gap 2.5s (1 offending gap(s))
[FAIL] white-edge: near-white edge band(s) over the 3% threshold: right 98.1%, top 15.8%, bottom 15.8% (art may be fading to white)
[SKIP] language: skipped: pip install faster-whisper (language gate not available)
OVERALL: FAIL
```

(The top/bottom numbers above are the corner overlap of the white right
column with the horizontal edge bands — the bands are measured
independently, corners included.)

Useful flags:

```bash
shorts-qc check final.mp4 \
  --expect 9:16 \        # or 16:9
  --min-lufs -24 \       # loudness floor
  --max-lufs -10 \       # loudness ceiling
  --max-gap 2.5 \        # max tolerated inside silence, seconds
  --lang ja \            # enable the language gate (needs faster-whisper)
  --json                 # machine-readable report
```

### Check caption lines

```bash
shorts-qc caption --width 720 --fontsize 60 "ちょうど十一文字ですよ"
# caption-fit: width=720px fontsize=60.0 -> capacity 11 full-width chars/line
# [PASS] (11.00/11) ちょうど十一文字ですよ
# caption-fit: PASS — all lines fit

shorts-qc caption --width 720 --fontsize 60 "ちょうど十一文字ですよね"
# [FAIL] (12.00/11) ちょうど十一文字ですよね
# caption-fit: FAIL — 1 line(s) would be clipped

shorts-qc caption --width 720 --fontsize 60 --file narration-lines.txt
```

## What it does not do

- It does not fix anything. It reports; your pipeline (or you) decides.
- The loudness gate measures the whole file, not per-scene dialogue —
  per-scene reporting is on the roadmap below.
- The language gate is only as good as the tiny model it runs locally;
  treat a pass as "nothing obvious", not as a proof.
- A white edge band is a *signal*, not a verdict: a scene that is
  intentionally white on one side will trip it. That is deliberate — in
  the pipeline this came from, that panel turned out to be broken far
  more often than it was intentional.

## Roadmap

- **v0.2** — per-scene reports (split by silence/scene boundaries and
  report loudness + gaps per scene instead of per file).
- **v0.2** — SRT input for the caption gate: check every subtitle cue
  against the frame size and the cue's own duration.
- Maybe: black-frame / frozen-frame detection.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version: keep the runtime
standard-library-only, and every new gate should correspond to a real
way a video pipeline broke.

## License

MIT — see [LICENSE](LICENSE).
