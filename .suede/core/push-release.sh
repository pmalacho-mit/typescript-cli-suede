#!/usr/bin/env bash
#
# The whole main-side release flow, so the workflow that calls it can stay a
# caller. Run on `main` after a change under release/ has landed.
#
#   extract -> guard -> commit the manifest -> sync out -> propagate
#
# The guard is the reason this is worth having in one place: a release
# dependency ships as a *pointer*, so before that pointer goes out we check it
# is honest (nothing diverged from its pinned commit) and that nothing is
# resolved implicitly (`suede check`). A failure here stops the push and
# writes the reason into the job summary, rather than publishing a lie.
#
# Inputs (env):
#   RELEASE_DIR   default: release
#   SUEDE         default: .suede/core/suede.py
#   DRY_RUN       set to 1 to stop before touching the remote

set -euo pipefail

RELEASE_DIR="${RELEASE_DIR:-release}"
SUEDE="${SUEDE:-.suede/core/suede.py}"
DRY_RUN="${DRY_RUN:-0}"

cd "$(git rev-parse --show-toplevel)"

say() { printf '[push-release] %s\n' "$*" >&2; }

# Anything written here also lands in the GitHub job summary, so a maintainer
# reads the reason on the run page rather than in the log.
report() {
  printf '%s\n' "$*" >&2
  [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] && printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"
  return 0
}

python_runtime() {
  local candidate
  for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
      >/dev/null 2>&1 && { printf '%s\n' "$candidate"; return 0; }
  done
  say "no python3 >= 3.9 found"
  return 1
}

suede() { "$PYTHON" "$SUEDE" "$@"; }

require_release_folder() {
  [[ -d "$RELEASE_DIR" ]] || { say "no ./$RELEASE_DIR folder - nothing to publish"; exit 1; }
}

# The manifest is generated, never hand-edited, so regenerate it every run and
# commit only when it actually moved.
refresh_manifest() {
  suede extract
  git add "$RELEASE_DIR" >/dev/null
  if git diff --cached --quiet -- "$RELEASE_DIR"; then
    say "manifest unchanged"
    return 0
  fi
  git commit --quiet -m "chore(suede): update dependency artifacts"
  say "committed refreshed dependency artifacts"
}

# Two different failures, two different fixes, so they are reported separately.
guard() {
  local failed=0
  if ! suede diff > "$WORKSPACE/diff.txt" 2>&1; then
    report "### suede: a release dependency has diverged from its pin"
    report ''
    report '```'
    report "$(cat "$WORKSPACE/diff.txt")"
    report '```'
    failed=1
  fi
  if ! suede check > "$WORKSPACE/check.txt" 2>&1; then
    report "### suede: check failed"
    report ''
    report '```'
    report "$(cat "$WORKSPACE/check.txt")"
    report '```'
    failed=1
  fi
  return "$failed"
}

# Pull first so the push lands on top of the current release tip (a release
# that advanced by some other route); "nothing to pull" is not a failure.
sync_release_branch() {
  git subrepo pull "$RELEASE_DIR" || true
  git subrepo push "$RELEASE_DIR"
  git push   # propagate the .gitrepo pointer bump back to main
}

PYTHON="$(python_runtime)"
WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$WORKSPACE"' EXIT

require_release_folder
refresh_manifest

if ! guard; then
  say "refusing to publish - the release branch is unchanged"
  exit 1
fi

[[ "$DRY_RUN" == "1" ]] && { say "dry run: stopping before the push"; exit 0; }
sync_release_branch
say "published $RELEASE_DIR to the release branch"
