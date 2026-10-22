# Examples

Everything below is **real output captured from running ShortsQC
v0.2.0** (Python 3.12, ffmpeg from the system package) — nothing here
is written by hand. Exact numbers (LUFS, timestamps, ratios) may vary
slightly with a different ffmpeg version, but the verdicts reproduce.

## The demo videos

The three videos are synthetic files generated with ffmpeg's built-in
test sources — a moving test pattern plus a sine tone. `gapped.mp4` is
the same file with two defects built in on purpose: **3 seconds of
silence** in the middle of the audio and a **solid white column**
drawn over the right 54 px of the picture. `stuck.mp4` builds in the
visual counterpart: **2.5 seconds of black picture** (2.5 s–5 s)
followed by **4 seconds of one static red frame** (5 s–9 s), with the
tone running throughout.

```bash
# clean.mp4 — 9 s, 360x640 (9:16), test pattern + 440 Hz tone
ffmpeg -y -f lavfi -i testsrc2=duration=9:size=360x640:rate=30 \
  -f lavfi -i sine=frequency=440:duration=9 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest clean.mp4

# gapped.mp4 — same, plus 3 s of silence (3s–6s) and a white right column
ffmpeg -y -f lavfi -i testsrc2=duration=9:size=360x640:rate=30 \
  -f lavfi -i sine=frequency=440:duration=3 \
  -f lavfi -i anullsrc=r=44100:cl=mono \
  -f lavfi -i sine=frequency=440:duration=3 \
  -filter_complex "[0:v]drawbox=x=iw-54:y=0:w=54:h=ih:color=white:t=fill[v];[2:a]atrim=0:3[a2];[1:a][a2][3:a]concat=n=3:v=0:a=1[a]" \
  -map "[v]" -map "[a]" -t 9 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac gapped.mp4

# stuck.mp4 — 2.5 s moving, then 2.5 s black, then 4 s of a static red frame
ffmpeg -y -f lavfi -i testsrc2=duration=2.5:size=360x640:rate=30 \
  -f lavfi -i color=c=black:duration=2.5:size=360x640:rate=30 \
  -f lavfi -i color=c=red:duration=4:size=360x640:rate=30 \
  -f lavfi -i sine=frequency=440:duration=9 \
  -filter_complex "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]" \
  -map "[v]" -map 3:a -shortest \
  -c:v libx264 -pix_fmt yuv420p -c:a aac stuck.mp4
```

## `check` on the clean video

```bash
python3 shorts_qc.py check clean.mp4 --expect 9:16 --lang ja
```

```text
ShortsQC report — clean.mp4
[PASS] probe: 360x640 (portrait), 30.0 fps, 9.0s — aspect matches 9:16
[PASS] loudness: -21.8 LUFS (allowed -24.0 .. -10.0)
[PASS] dead-air: no silence gap longer than 2.5s inside the file (0 gap(s) detected, all within tolerance or at the edges)
[PASS] white-edge: all edge bands <= 3% near-white across 5 frame(s)
[PASS] freeze-black: no black run over 1.5s and no frozen run over 3.0s across 18 sampled frame(s) (longest black 0.0s, longest freeze 0.0s)
[SKIP] language: skipped: pip install faster-whisper (language gate not available)
OVERALL: PASS
```

Exit code: `0`. The language gate skips because `faster-whisper` is
not installed on this machine — that is the designed behavior, not an
error.

## `check` on the defective video

```bash
python3 shorts_qc.py check gapped.mp4 --expect 9:16 --lang ja
```

```text
ShortsQC report — gapped.mp4
[PASS] probe: 360x640 (portrait), 30.0 fps, 9.0s — aspect matches 9:16
[PASS] loudness: -21.9 LUFS (allowed -24.0 .. -10.0)
[FAIL] dead-air: silence gap of 3.0s at 2.999977s exceeds --max-gap 2.5s (1 offending gap(s))
[FAIL] white-edge: near-white edge band(s) over the 3% threshold: right 99.5%, top 14.9%, bottom 15.0% (art may be fading to white)
[PASS] freeze-black: no black run over 1.5s and no frozen run over 3.0s across 18 sampled frame(s) (longest black 0.0s, longest freeze 0.0s)
[SKIP] language: skipped: pip install faster-whisper (language gate not available)
OVERALL: FAIL
```

Exit code: `1`. Both built-in defects are caught. The top/bottom
white-edge numbers are the corner overlap of the white right column
with the horizontal edge bands (the column is 54/360 = 15% of the
width); the bands are measured independently, corners included.

The same run with `--json` produces the machine-readable report shown
and annotated in [docs/USAGE.md](../docs/USAGE.md#annotated-json-report).

## `check` on the stuck video (v0.2.0)

```bash
python3 shorts_qc.py check stuck.mp4 --expect 9:16 --lang ja
```

```text
ShortsQC report — stuck.mp4
[PASS] probe: 360x640 (portrait), 30.0 fps, 9.0s — aspect matches 9:16
[PASS] loudness: -21.8 LUFS (allowed -24.0 .. -10.0)
[PASS] dead-air: no silence gap longer than 2.5s inside the file (0 gap(s) detected, all within tolerance or at the edges)
[PASS] white-edge: all edge bands <= 3% near-white across 5 frame(s)
[FAIL] freeze-black: black picture for 2.5s at 2.5s (limit 1.5s); frozen picture for 3.5s at 5.0s (limit 3.0s)
[SKIP] language: skipped: pip install faster-whisper (language gate not available)
OVERALL: FAIL
```

Exit code: `1`. The audio and the frame edges are fine in this file —
only the picture misbehaves, and only the freeze-black gate sees it:
2.5 s of black starting exactly where the black segment was built in,
then a picture that provably does not change for 3.5 s (the 4 s
static segment, measured between the 2 fps samples).

## `caption` on the sample lines

[`sample_captions.txt`](sample_captions.txt) holds eight Japanese
caption lines: five that fit a 720 px frame at fontsize 60 (capacity:
11 full-width characters) and three that do not — one a single
character over, the others realistic over-long narration lines. One
passing line mixes half-width Latin text (`STEP1`), which counts
0.55 per character.

```bash
python3 shorts_qc.py caption --width 720 --fontsize 60 --file examples/sample_captions.txt
```

```text
caption-fit: width=720px fontsize=60.0 -> capacity 11 full-width chars/line
[PASS] (11.00/11) ちょうど十一文字ですよ
[PASS] (11.00/11) 保存版の裏ワザ三選です
[PASS] (10.00/11) まず材料をそろえます
[PASS] (11.00/11) 次にフライパンを温める
[PASS] (8.30/11) STEP1 材料を切る
[FAIL] (12.00/11) ちょうど十一文字ですよね
[FAIL] (13.00/11) 冷凍すると美味しくなる野菜
[FAIL] (17.00/11) これは長すぎるキャプションの例です
caption-fit: FAIL — 3 line(s) would be clipped
```

Exit code: `1`, because three lines would be clipped at the frame
edge — in production, those three lines go back to the script for a
rewrite before any video is rendered.
