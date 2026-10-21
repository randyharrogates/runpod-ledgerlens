Recordings are real event streams captured from the live agent by `train/agent_live_check.py` and replayed by the app's demo mode; they are never hand-written. Each `<name>.json` is a JSON list of worker event objects, with a `t` field giving seconds since the turn started. The app replays these gaps, capped at 1.5 seconds each, for the corresponding suggested prompt and sample business.

All five were re-recorded on 15 Oct 2026 from the live agent endpoint (worker image `0ca3cde`,
base Qwen3-VL-4B with the reader LoRA, RTX PRO 6000 MIG slice) by polling its `/stream` once a
second, so their `t` values are arrival times; every event also carries the worker's `ts`. The
memo turn includes the code-built `report` event the app draws as tables. The edit ("Make the
red flags section shorter.") came back unchanged, 59 words against a 48-word limit, and the
recording keeps the checker's flag as it was: that is the failure seen in 7 of 30 E5 edits.
