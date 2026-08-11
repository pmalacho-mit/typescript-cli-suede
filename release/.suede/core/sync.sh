#!/usr/bin/env bash
#
# `git subrepo pull`, runnable from any working directory.
#
#   bash sync.sh <path> [<path> ...]
#
# Two things it does that a bare `git subrepo pull` will not:
#   * runs from the repo root with a root-relative path, so the caller's cwd
#     does not matter;
#   * dereferences a symlink to its real folder first - `git subrepo pull` on a
#     symlink path fails outright, and edge entries are symlinks by default.
#
# `git subrepo pull` also requires a clean working tree, while install does
# not. If you have just installed, commit before syncing.

set -euo pipefail

usage() { grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'; exit 0; }
[[ $# -eq 0 || "${1-}" == "-h" || "${1-}" == "--help" ]] && usage

command -v git >/dev/null 2>&1 || { printf 'sync: git not found\n' >&2; exit 1; }
git subrepo --version >/dev/null 2>&1 || {
  printf 'sync: git-subrepo is not installed (https://github.com/ingydotnet/git-subrepo)\n' >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel)"

root_relative_real_path() {
  local given="$1" resolved
  resolved="$(cd "$(dirname "$given")" && pwd -P)/$(basename "$given")"
  [[ -L "$resolved" ]] && resolved="$(cd "$(dirname "$resolved")" && cd "$(readlink "$resolved")" && pwd -P)"
  [[ -d "$resolved" ]] || { printf 'sync: %s is not a directory\n' "$given" >&2; exit 1; }
  printf '%s\n' "${resolved#"$(cd "$ROOT" && pwd -P)"/}"
}

for target in "$@"; do
  path="$(root_relative_real_path "$target")"
  [[ -f "$ROOT/$path/.gitrepo" ]] || {
    printf 'sync: %s has no .gitrepo - it is not a subrepo\n' "$path" >&2
    exit 1
  }
  ( cd "$ROOT" && git subrepo pull "$path" )
done
