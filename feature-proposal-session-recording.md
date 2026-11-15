# Add headless GPU session recording to JSONL

## Problem

`--json` and `gputaskman diff` save individual moments. Memory and utilisation can spike while a model loads or answers a request, then settle before the next snapshot, so the pair misses that temporary change. Live charts help while you watch, but they hold about a minute of device history in memory and omit per-process and model detail. A recording would keep the series for later analysis.

Chart history is display-only. I did not find an overlapping issue or pull request; the nearest piece is `diff`.

## Proposed behaviour

```
gputaskman record --gpu 0 --interval 1 --duration 300 --output session.jsonl
gputaskman record --output session.jsonl
```

Each JSONL line would be one snapshot in the existing JSON representation, including its `taken` timestamp. Recording would follow a single selected GPU, defaulting to GPU 0 and a one-second interval.

`--duration` would stop after that many seconds. Without it, recording would continue until Ctrl+C. Each finished sample would be appended and flushed, so an interrupt would keep the lines already written. If the output path already exists, the command would refuse to overwrite it.

Unavailable values would stay `null`, and provenance fields would be preserved. Process and model details, including paths, would match a current `--json` snapshot.

## Implementation scope

A `record` subcommand could sit beside `diff`, keep one `Collector` alive for the selected GPU, and append `report.to_dict()` on each sample. Discovery would keep the TUI's bounded scan (150 ms per refresh) rather than the full scan `--once` uses.

Hardware-independent tests, with a stand-in collector, could check the JSONL shape, incremental writes, the refusal to overwrite, and a finite duration. The README usage section would document the command.

This proposal covers recording only. Replay, CSV export, summaries, and UI changes would be separate work, and none of this has been implemented.

## Limitations

Periodic sampling can miss a peak between samples. Backend availability and discovery delay would still apply, including a slow first host sample and the lag before a newly loaded model is named.

## Maintainer feedback

Does this fit the project's direction, and do you have a preference for the command or format before implementation begins?
