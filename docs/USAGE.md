# ShortsQC usage guide

This is the deep guide. The [README](../README.md) covers installation
and the short version; this document covers every gate, the production
failure behind it, every CLI flag, and how to read a JSON report.

Two commands:

```bash
shorts-qc check <video> [flags]     # all gates on a finished video file
shorts-qc caption [flags] [lines…]  # the caption-fit gate on its own
```

Exit codes, for both commands:

| Code | Meaning |
| --- | --- |
| `0` | Every gate passed (skips count as passing). |
| `1` | At least one gate failed, or a gate reported an error. |
| `2` | Tool error: bad invocation, file not found, or `ffmpeg`/`ffprobe` missing. Nothing was checked. |

---

## The gates

### 1. probe — the wrong file would ship unnoticed

**What it catches.** A render in the wrong aspect ratio going out as
if it were correct: a landscape assembly uploaded to a vertical slot,
a square test render mixed up with the final 9:16 export. Without an
explicit expectation the gate only reports facts; with `--expect` it
fails on a mismatch (tolerance ±0.02 on the ratio).

**How it works.** `ffprobe` on the first video stream: width, height,
frame rate, container duration, derived orientation and aspect ratio.

**Flags.** `--expect 9:16` or `--expect 16:9` (optional; default: no
expectation).

### 2. loudness — one voice quietly disappears

**The production failure.** In a multi-character video, one
character's TTS voice measured 7–10 dB below the others. On a TV it
was merely quieter; on a phone speaker that character was effectively
inaudible — and nobody noticed until after publication, because each
voice part sounded fine on its own. Separately, an over-hot master is
just as broken. The gate measures the *finished file*, so level
differences between parts are baked into the result.

**How it works.** ffmpeg `loudnorm` in analysis mode; the integrated
loudness (`input_i`, LUFS) must sit inside the allowed window. If
loudnorm output cannot be parsed, the gate falls back to
`volumedetect`'s `mean_volume` and says so in its summary — a fallback
result is reported as a fallback, never dressed up as LUFS, and is not
enforced.

**Flags.**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--min-lufs` | `-24.0` | Fail below this integrated loudness. |
| `--max-lufs` | `-10.0` | Fail above this integrated loudness. |

**Limit (stated plainly).** The measurement is whole-file. A file can
pass while one scene is much quieter than the rest; per-scene loudness
reporting is planned for v0.2.

### 3. dead-air — padding becomes silence

**The production failure.** Scenes were timed to a *planned* duration
from the storyboard instead of the actual length of the generated
speech. One scene held 1.8 s of narration stretched across a 10 s
slot; the result was seconds of silence inside the video that read as
"the video froze". The fix in production was to time scenes from
measured speech — this gate is the safety net that catches any
regression of that bug.

**How it works.** ffmpeg `silencedetect` (noise floor −40 dB, minimum
silence duration 1.2 s). Each detected gap is clipped to the inside of
the file — silence touching only the first or last 0.3 s is treated as
normal head/tail padding and ignored. A gap whose *inside* remainder
exceeds the limit fails.

A file with **no audio stream** reports this gate as an error, not a
pass: there is no audio to measure silence in (ffmpeg itself exits 0
on such a file without running the filter, which is exactly how a
missing soundtrack once slipped through as "no gaps detected").

**Flags.**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--max-gap` | `2.5` | Longest tolerated inside silence, in seconds. |

### 4. white-edge — AI panels fade into the background

**The production failure.** AI image generators regularly return
panels whose art covers only part of the frame, with one edge
dissolving into white. In one production incident, most of an
episode's panels had art covering roughly two-thirds of the width and
a white right side — and a thumbnail-sized contact sheet made it look
like an intentionally bright background, so the episode passed a
visual check and had to be re-rendered after a human spotted it.
Measured per-pixel, the defect is unmistakable and impossible to argue
with.

**How it works.** Five frames are sampled evenly across the video
(10 %, 30 %, 50 %, 70 %, 90 % of the duration), written as PPM, and
parsed in pure Python. A pixel counts as near-white when all three
channels are ≥ 250. For each edge, the near-white ratio of the outer
15 % band is computed — bands are measured independently, corners
included, so a white right column also raises the top and bottom
ratios by its share of those bands. The worst ratio per edge across
the sampled frames decides: above **3 %** fails that edge.

**Flags.** None — the 15 % band, the ≥ 250 threshold, and
the 3 % limit are the production rule and are reported in the JSON
details of every run.

**Reading a failure.** A white edge band is a signal, not a verdict.
An intentionally white scene trips it too — deliberately: in the
pipeline this came from, the panel was broken far more often than it
was intentional.

### 5. freeze-black — the picture stops, or disappears

**The failure it catches.** Dead air has a visual counterpart, and it
ships the same way: an encoder or compositor hang repeats one frame
for seconds while the audio carries on, or a panel render fails and
the picture drops to black mid-scene. Viewers read both as "the video
froze" — and a contact-sheet spot check misses them exactly like it
misses silence, because every sampled thumbnail looks plausible on
its own.

**How it works.** One ffmpeg pass samples the video at 2 fps, scaled
to 96 px wide, as PPM frames (the same pure-Python parsing as the
white-edge gate). For each sampled frame the mean Rec. 601 luma is
computed; a frame at or below luma 12 counts as black, and a run of N
black frames is N × 0.5 s of black picture. Consecutive frames are
also compared by mean absolute per-channel difference: a difference
at or below 1 (of 255) is a "frozen step", and a run of M frozen steps
is M × 0.5 s during which the picture provably did not change.

The freeze epsilon is deliberately tiny. A slow zoompan over a still
panel — the normal look of illustrated shorts — moves pixels
measurably between samples half a second apart; a truly repeated
frame does not. Likewise, short black transitions under the limit
are normal editing and pass.

**Flags.**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--max-black` | `1.5` | Longest tolerated continuous black picture, in seconds. |
| `--max-freeze` | `3.0` | Longest tolerated frozen (unchanging) picture, in seconds. |

**Reading a failure.** The summary names the defect, its duration,
and where it starts: `black picture for 2.5s at 2.5s (limit 1.5s)`.
The JSON details carry the longest black and frozen runs (seconds
and start time) plus the number of frames sampled.

### 6. caption — text clipped at the frame edge

**The production failure.** A narration line was burnt into a 720 px
wide vertical video at fontsize 60 and simply ran out of frame —
the last glyph was sliced in half. It was caught by eye on a rendered
frame, after the render. The arithmetic version of that check is
trivial and runs *before* rendering: at that frame size and font
size, **11 full-width characters per line is the maximum**.

**How it works.** Capacity in full-width characters is

```
capacity = floor((width − fontsize) / fontsize)
```

The raw division `720 / 60 = 12` is wrong in practice: it leaves no
horizontal safety margin. Reserving half a fontsize of padding on
each side gives `floor((720 − 60) / 60) = 11`, and the formula scales
to other sizes. Character widths come from
`unicodedata.east_asian_width`: characters classed `W` or `F`
(Japanese kana, kanji, full-width forms) count 1.0; everything else
(Latin letters, digits, spaces) counts 0.55. A line fails when its
total width exceeds capacity.

**Usage.** Lines as arguments, or `--file` for a UTF-8 file with one
line per caption:

```bash
shorts-qc caption --width 720 --fontsize 60 "ちょうど十一文字ですよ"
shorts-qc caption --width 720 --fontsize 60 --file narration-lines.txt
```

**Flags.**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--width` | `720` | Frame width in px that captions must fit. |
| `--fontsize` | `60` | Burnt-in caption font size in px. |
| `--file` | — | Read caption lines from this UTF-8 text file. |

In the originating pipeline this gate runs on the script before TTS:
a failing line is rewritten, not squeezed.

### 7. language — the TTS reads the script in the wrong language

**The production failure.** A narration part came back from the TTS
engine **in English**, even though the script fed to it was Japanese
and the request asked for Japanese. The first QC pass checked the
per-scene audio files and passed them: the short English segment sat
inside a scene whose audio was mostly Japanese, and a check forced to
the expected language "heard" Japanese. The defect reached the
assembled video. Two lessons are baked into this gate:

1. Check the **final file**, not intermediates.
2. Detect language **automatically** (never force the expected
   language) and flag foreign-script speech per segment.

**How it works.** With `--lang ja` and the optional `faster-whisper`
package installed, the audio is extracted to a 16 kHz mono WAV,
transcribed with the tiny model and automatic language detection,
and any segment containing a run of ≥ 4 Latin-alphabet characters
fails the gate. Only `ja` targets get a check: any other `--lang`
value reports `skip` (see below) instead of a PASS for a check that
never ran.

**Flags.**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--lang` | — (gate off) | Expected narration language, e.g. `ja`. Enables the gate. |

**When it skips.** If `faster-whisper` is not installed, the gate
reports `skip` with the exact install hint and never crashes the run;
if `--lang` is not given, it reports `skip` because there is no
expectation to check against; and if `--lang` names anything other
than `ja`, it reports `skip` naming that target, because the gate
implements no check for it — a PASS there would claim a verification
that never happened. A skip does not fail the run — but it
also does not mean the language was verified. In the production
pipeline, the final-file language scan is a hard pre-delivery gate
run separately for exactly that reason.

**Known false positives.** Very short utterances (1–2 s) can be
misdetected by the tiny model. Treat a failure as "listen to this
segment", not as proof.

---

## Full flag reference — `check`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--expect` | — | Expected aspect: `9:16` or `16:9`. |
| `--min-lufs` | `-24.0` | Loudness floor (LUFS). |
| `--max-lufs` | `-10.0` | Loudness ceiling (LUFS). |
| `--max-gap` | `2.5` | Max tolerated inside silence (seconds). |
| `--max-black` | `1.5` | Max tolerated continuous black picture (seconds). |
| `--max-freeze` | `3.0` | Max tolerated frozen picture (seconds). |
| `--lang` | — | Expected narration language; enables the language gate. |
| `--json` | off | Print the report as JSON instead of text. |

## Annotated JSON report

Real output of:

```bash
python3 shorts_qc.py check gapped.mp4 --expect 9:16 --lang ja --json
```

(run on the synthetic demo file from [examples/](../examples/), which
has 3 s of silence and a white right column built in; exit code `1`.)

```json
{
  "video": "gapped.mp4",
  "overall": "fail",
  "gates": [
    {
      "name": "probe",
      "status": "pass",
      "summary": "360x640 (portrait), 30.0 fps, 9.0s — aspect matches 9:16",
      "details": {
        "duration": 9.0,
        "width": 360,
        "height": 640,
        "fps": 30.0,
        "orientation": "portrait",
        "aspect": 0.5625,
        "has_audio": true
      }
    },
    {
      "name": "loudness",
      "status": "pass",
      "summary": "-21.9 LUFS (allowed -24.0 .. -10.0)",
      "details": {
        "input_i_lufs": -21.92,
        "min_lufs": -24.0,
        "max_lufs": -10.0
      }
    },
    {
      "name": "dead-air",
      "status": "fail",
      "summary": "silence gap of 3.0s at 2.999977s exceeds --max-gap 2.5s (1 offending gap(s))",
      "details": {
        "max_gap": 2.5,
        "offending_gaps": [
          {
            "start": 2.999977,
            "end": 6.000045,
            "duration": 3.000068,
            "inside_duration": 3.0
          }
        ],
        "all_gaps": [
          {
            "start": 2.999977,
            "end": 6.000045,
            "duration": 3.000068
          }
        ]
      }
    },
    {
      "name": "white-edge",
      "status": "fail",
      "summary": "near-white edge band(s) over the 3% threshold: right 99.5%, top 14.9%, bottom 15.0% (art may be fading to white)",
      "details": {
        "frames_checked": 5,
        "worst_ratios": {
          "left": 0.0,
          "right": 0.9953,
          "top": 0.1494,
          "bottom": 0.1495
        },
        "threshold": 0.03
      }
    },
    {
      "name": "freeze-black",
      "status": "pass",
      "summary": "no black run over 1.5s and no frozen run over 3.0s across 18 sampled frame(s) (longest black 0.0s, longest freeze 0.0s)",
      "details": {
        "max_black": 1.5,
        "max_freeze": 3.0,
        "frames": 18,
        "interval": 0.5,
        "black": {
          "longest_seconds": 0.0,
          "longest_at": null
        },
        "freeze": {
          "longest_seconds": 0.0,
          "longest_at": null
        }
      }
    },
    {
      "name": "language",
      "status": "skip",
      "summary": "skipped: pip install faster-whisper (language gate not available)",
      "details": {
        "target_lang": "ja"
      }
    }
  ]
}
```

How to read it:

- **`overall`** — `"pass"` only when every gate passed or skipped and
  none errored. Here it is `"fail"`, and the process exit code was 1.
- **`gates[].status`** — one of `pass`, `fail`, `skip`, `error`.
  `skip` means "not verified" (missing optional dependency, or no
  `--lang` given) — never read it as a pass.
- **`probe.details`** — the measured facts the aspect verdict is based
  on; `aspect` is `width / height` (0.5625 = 9:16). `has_audio` says
  whether the file carries an audio stream at all; when it is `false`
  the loudness and dead-air gates report errors instead of measuring
  nothing.
- **`loudness.details.input_i_lufs`** — integrated loudness as
  measured by loudnorm (−21.92), with the enforced window next to it.
- **`dead-air.details.offending_gaps`** — only the gaps that failed,
  each with `inside_duration`: the gap's length after the 0.3 s
  head/tail allowance is clipped away. `all_gaps` lists everything
  silencedetect found. Here the single gap runs 3.0 s → 6.0 s, exactly
  the silence built into the demo file.
- **`white-edge.details.worst_ratios`** — worst near-white ratio per
  edge across the 5 sampled frames. `right: 0.9953` is the built-in
  white column; `top`/`bottom` ≈ 0.15 is that same column's share of
  the horizontal bands (54 px of 360 px). `left: 0.0` is the control:
  the untouched edge measures clean.
- **`freeze-black.details`** — the longest black and frozen runs
  found across the 18 frames sampled at 0.5 s intervals. Both are
  zero here: this demo file's defects are audio and edges, not
  picture motion. On the `stuck.mp4` demo in
  [examples/](../examples/) the same fields report a 2.5 s black run
  and a 3.5 s frozen run, and the gate fails.
- **`language`** — skipped on this machine (no `faster-whisper`), and
  the summary says why. With the package installed this gate reports
  `pass`/`fail` based on the transcription instead.
