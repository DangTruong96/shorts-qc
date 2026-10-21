## What this changes

<!-- One or two sentences. Link the issue it closes, if any. -->

## The failure it catches (new or changed gates only)

<!-- Every gate in ShortsQC exists because a pipeline broke that way.
     Describe the incident or measurement behind this change. -->

## Checklist

- [ ] Runtime is still standard-library only (ffmpeg/ffprobe are external
      binaries; `faster-whisper` stays an optional extra and the tool
      still works, with a clear "skipped" notice, when it is absent)
- [ ] Tests added or updated for any parsing/math change, and
      `python3 -m unittest discover -s tests -v` passes
- [ ] Exit codes unchanged: `0` pass, `1` gate failure, `2` tool error
- [ ] Any changed threshold/default is explained above, with the
      measurement or incident behind the new number
- [ ] README / docs updated if behavior or flags changed
- [ ] CHANGELOG entry added under the current version
