#!/bin/bash
# Fails if a path the .dockerignore allowlist (`!entry` lines) lets into the build
# context holds files git does not track; local builds would ship them to Docker Hub.
set -euo pipefail

repo_root="$(git -C "$(dirname "$(realpath "$0")")" rev-parse --show-toplevel)"
cd "${repo_root}"

mapfile -t allowed < <(sed -n 's|^!/\{0,1\}\(.*[^/]\)/\{0,1\}$|\1|p' .dockerignore)
(( ${#allowed[@]} > 0 )) || { echo "check_build_context: no !-entries in .dockerignore" >&2; exit 1; }

offenders="$(git status --porcelain --ignored --untracked-files=all -- "${allowed[@]}" \
  | grep -E '^(\?\?|!!) ' | cut -c4- | grep -vE '(^|/)__pycache__/|\.py[co]$' || true)"

if [[ -n "${offenders}" ]]; then
  echo "check_build_context: untracked or ignored files inside the image build context:" >&2
  sed 's/^/    /' <<<"${offenders}" >&2
  echo "Refusing to build. Remove them, or commit them if they belong in the image." >&2
  exit 1
fi
echo "ok   build context (${allowed[*]})"
