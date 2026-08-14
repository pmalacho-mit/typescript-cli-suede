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
#   DRY_RUN       set to 1 to stop before touching the remote
#   SUEDE_PY      where ./suede fetches the installer from (a path, for tests)

set -euo pipefail

RELEASE_DIR="${RELEASE_DIR:-release}"
DRY_RUN="${DRY_RUN:-0}"

# Resolved before the cd, because it sits next to this script.
CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$(git rev-parse --show-toplevel)"

say() { printf '[push-release] %s\n' "$*" >&2; }

# Anything written here also lands in the GitHub job summary, so a maintainer
# reads the reason on the run page rather than in the log.
report() {
  printf '%s\n' "$*" >&2
  [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] && printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"
  return 0
}

suede() { bash "$CORE_DIR/suede" "$@"; }

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

# Pulling a subrepo nested inside release/ - `.suede/core` is one in every
# dependency - leaves two things behind that stop the push below, in two
# different ways:
#
#   * a branch `subrepo/release/%2esuede/core`. Git refs are directories, so
#     while it exists `subrepo/release` cannot be created: "cannot lock ref".
#   * a scratch directory `.git/tmp/subrepo/release/%2esuede`. That leaves
#     `.git/tmp/subrepo/release` sitting there as an ordinary directory, and
#     the push wants exactly that path for its worktree: "this operation must
#     be run in a work tree".
#
# `git subrepo clean` clears the first and not the second. The pull that causes
# both happens on main, days earlier and by hand, which makes this the only
# place that can be relied on to tidy up after it.
release_nested_subrepos() {
  find "$RELEASE_DIR" -name .gitrepo -not -path "$RELEASE_DIR/.gitrepo" -not -path '*/.git/*' \
    | sed 's#/\.gitrepo$##' | sort
}

clear_nested_subrepo_refs() {
  local nested cleared=0
  while IFS= read -r nested; do
    [[ -n "$nested" ]] || continue
    git subrepo clean "$nested" >/dev/null 2>&1 || true
    cleared=1
  done < <(release_nested_subrepos)
  [[ "$cleared" == 1 ]] || return 0
  say "cleared the git-subrepo leftovers of the subrepos nested in $RELEASE_DIR"
  rm -rf .git/tmp/subrepo
  git worktree prune >/dev/null 2>&1 || true
}

# Pull first so the push lands on top of the current release tip (a release
# that advanced by some other route); "nothing to pull" is not a failure.
sync_release_branch() {
  git subrepo pull "$RELEASE_DIR" || true
  clear_nested_subrepo_refs
  git subrepo push "$RELEASE_DIR"
  git push   # propagate the .gitrepo pointer bump back to main
}

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
