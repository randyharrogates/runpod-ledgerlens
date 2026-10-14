Recordings are real event streams captured from the live agent by `train/agent_live_check.py` and replayed by the app's demo mode; they are never hand-written. Each `<name>.json` is a JSON list of worker event objects, with a `t` field giving seconds since the turn started. The app replays these gaps, capped at 1.5 seconds each, for the corresponding suggested prompt and sample business.

`ask_data`, `ask_data_month` and `knowledge` were re-recorded on 15 Oct 2026 from the live agent
endpoint (base Qwen3-VL-4B with the reader LoRA, RTX PRO 6000 MIG slice) by polling its `/stream`
once a second, so their `t` values are arrival times and coarser than the Pod recordings.
`memo_preread` and `edit_memo` are still the Pod recordings from 14 Oct.
