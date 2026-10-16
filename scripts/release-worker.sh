#!/usr/bin/env bash
# Ship the current main to the Runpod endpoint.
#
# The endpoint builds from the `deploy` branch, so pushing docs or app changes to main never
# triggers an image rebuild. This script runs the tests, shows what changed in the image since the
# last release, and fast-forwards `deploy` to `main` only after you confirm.
set -euo pipefail
cd "$(dirname "$0")/.."

# The machine's default gh account is a work account; authenticate as randyharrogates per command.
git_personal() {
  git -c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$(gh auth token --user randyharrogates)"; }; f' "$@"
}

git_personal fetch -q origin
if [[ "$(git rev-parse main)" != "$(git rev-parse origin/main)" ]]; then
  echo "main is not pushed yet; push it first." >&2
  exit 1
fi

# All three endpoints run the same worker image. On 16 Oct the 8B and fine-tuned templates were found
# stuck on an old image while only the agent template followed releases, so check they agree.
TEMPLATES=(ej6398fg30 avnw8y6zs7 nygdqtrllt)  # 8B, fine-tuned 4B, agent
if command -v runpodctl >/dev/null; then
  tags=()
  for t in "${TEMPLATES[@]}"; do
    tag=$(runpodctl template get "$t" | python3 -c 'import json,sys; print(json.load(sys.stdin)["imageName"].rsplit(":", 1)[-1])')
    echo "template $t image $tag"
    tags+=("$tag")
  done
  if [[ $(printf '%s\n' "${tags[@]}" | sort -u | wc -l) -gt 1 ]]; then
    echo "WARNING: the endpoint templates run different images. Point them all at the newest one:" >&2
    echo "  runpodctl template update <template-id> --image <image>" >&2
  fi
fi

uv run ruff check . && uv run pytest -q

echo
echo "Changes that affect the image since the last release (origin/deploy..main):"
git diff --stat origin/deploy main -- worker src pyproject.toml uv.lock .dockerignore
read -r -p "Release to the endpoint (Runpod will rebuild)? [y/N] " answer
[[ "$answer" == "y" ]] || { echo "Not released."; exit 0; }

git_personal push origin main:deploy
echo "Released $(git rev-parse --short main). Watch the build in the Runpod console."
