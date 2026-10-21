# Contributing

Thanks for taking a look. ShortsQC is a small, single-file tool on
purpose — please help keep it that way.

## Ground rules

- **Runtime stays standard-library only.** ffmpeg/ffprobe are external
  binaries, not Python dependencies. The one optional extra is
  `faster-whisper` for the language gate, and the tool must keep working
  (with a clear "skipped" notice) when it is absent.
- **Every gate needs a real failure behind it.** New gates are welcome
  when they catch a concrete way an AI/TTS video pipeline actually
  breaks. Please describe the failure in the PR.
- **Thresholds are production rules, not guesses.** If you change a
  default (loudness floor, white-edge ratio, caption capacity…), explain
  the measurement or incident behind the new number.

## Development

```bash
git clone https://github.com/DangTruong96/shorts-qc
cd shorts-qc
python3 -m unittest discover -s tests -v
```

- Python 3.9+, no build step for the single module.
- Add or update tests for any parsing/math change (caption capacity,
  silencedetect parsing, PPM white-edge ratios are all covered today).
- Keep exit codes stable: `0` pass, `1` gate failure, `2` tool error.

## Reporting bugs

Open an issue with: the command you ran, the full report output
(`--json` helps), your ffmpeg version (`ffmpeg -version`), and — if you
can share it — a short clip that reproduces the problem.
