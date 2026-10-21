---
name: codex-workflow
description: How to hand an implementation step to Codex and review the result in this repo. Use whenever there is concrete code to write that is more than a trivial edit.
---

# Claude plans and reviews, Codex implements

1. **Brief.** Write the task to a scratch file: the goal, the exact files/dirs Codex may touch (and
   which it must not — say if another task is editing elsewhere), verified facts (model names, image
   tags, dataset formats — check them first), required tests, and the finish command
   (`uv run ruff check . && uv run ruff format --check . && uv run pytest -q`). Tell it not to commit.
2. **Run.** From the repo root, in the background:
   `command codex exec -m gpt-6.1-sol -s workspace-write "$(cat brief.md)" < /dev/null > codex.log 2>&1`
   (`< /dev/null` is required or it waits on stdin; `command` bypasses a shell wrapper).
3. **Know its sandbox.** No network (no installs, no downloads, no `npm install`), and headless
   Chromium is blocked — so rendering tests skip and frontends aren't built. Re-run everything
   outside the sandbox yourself.
4. **Review like it matters.** Read the diff. Run the full suite. Build the frontend. **Render and look**
   at outputs: generated pages, UI screenshots via a headless browser, endpoint responses. In this
   project, tests passed while the generator was a fixed template and the demo samples were text
   dumps — only looking caught it.
5. **Fix small things directly**; send larger rework back as a new, specific brief.
6. **Commit** reviewed work only (`git add` the specific paths if another Codex task is running), with
   a note of what was done.
