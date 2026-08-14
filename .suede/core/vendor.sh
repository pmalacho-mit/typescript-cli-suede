#!/usr/bin/env bash
#
# Convert a release dependency into a vendored release dependency: move it
# inside release/ so its source ships with the release branch.
#
#   bash vendor.sh <entry-or-folder> [--dest <path-inside-release>]
#
# Reach for this when a release dependency cannot stay pristine - you have
# local modifications you can neither revert nor upstream - so a .gitrepo
# pointer would no longer describe what your shipped code depends on.
#
# The default destination is the top of release/, beside the code that imports
# it: a leading dot is unrepresentable in a Python import, so a folder nested
# under release/.suede/ is reachable in some languages and not in others.
#
# Afterwards its .gitrepo ships too, so consumers get a nested subrepo. That is
# a feature (they can still pull and push it independently) and a sharp edge.

set -euo pipefail

usage() { grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'; exit 0; }

TARGET=""
DEST=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest) DEST="${2-}"; shift 2 ;;
    -h|--help) usage ;;
    *) [[ -z "$TARGET" ]] || { printf 'vendor: unexpected argument %s\n' "$1" >&2; exit 2; }
       TARGET="$1"; shift ;;
  esac
done
[[ -n "$TARGET" ]] || usage

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# The argument may be the root entry (folder or symlink) or the backing folder
# itself. Resolve it to the (backing folder, root entry) pair before anything
# moves, and fail if the pair is not a release dependency.
resolve_backing_folder() {
  local given="${1%/}" resolved
  resolved="$(cd "$(dirname "$given")" && pwd -P)/$(basename "$given")"
  [[ -L "$resolved" ]] && resolved="$(cd "$given" && pwd -P)"
  printf '%s\n' "${resolved#"$(pwd -P)"/}"
}

root_entry_for() {
  local backing="$1" entry
  for entry in *; do
    [[ -L "$entry" ]] || continue
    [[ "$(resolve_backing_folder "$entry")" == "$backing" ]] && { printf '%s\n' "$entry"; return; }
  done
  [[ -d "$backing" && "$backing" != */* ]] && printf '%s\n' "$backing"
}

# The dependency's own name, which is the one thing that does not depend on how
# the author arranged their tree: the remote's basename, `.git` stripped. The
# root entry carries this project's `$repo$SEP` prefix, and that prefix means
# nothing inside release/ - it announces a release dependency at the root,
# which is exactly what this folder stops being. It is also the name `suede
# install --vendor` gives the same folder, so the two ways in agree.
dependency_name() {
  local remote
  remote="$(git config -f "$BACKING/.gitrepo" --get subrepo.remote 2>/dev/null || true)"
  remote="${remote%/}"; remote="${remote##*[:/]}"; remote="${remote%.git}"
  printf '%s\n' "${remote:-$(basename "$BACKING")}"
}

BACKING="$(resolve_backing_folder "$TARGET")"
[[ -f "$BACKING/.gitrepo" ]] || { printf 'vendor: %s has no .gitrepo\n' "$BACKING" >&2; exit 1; }
case "$BACKING" in release/*) printf 'vendor: %s is already vendored\n' "$BACKING" >&2; exit 1 ;; esac

ENTRY="$(root_entry_for "$BACKING")"
DEST="${DEST:-release/$(dependency_name)}"
[[ -e "$DEST" ]] && { printf 'vendor: %s already exists\n' "$DEST" >&2; exit 1; }

mkdir -p "$(dirname "$DEST")"
git mv "$BACKING" "$DEST"
[[ -n "$ENTRY" && -L "$ENTRY" ]] && git rm --quiet "$ENTRY"

printf 'vendor: moved %s -> %s\n' "$BACKING" "$DEST" >&2
printf '\nFiles referencing the old entry name - review these imports:\n' >&2
grep -rl -- "${ENTRY:-$BACKING}" release/ 2>/dev/null | grep -v "^$DEST" || printf '  (none)\n' >&2

# Vendored code ships whole, so whatever it imports has to ship with it. Its
# manifest names each sibling it expects; any that is not beside it now would
# reach a consumer as a dangling link, which is what `suede check` reports as
# an escaping edge.
report_siblings() {
  local manifest="$DEST/.suede/.dependencies" record entry missing=0
  [[ -d "$manifest" ]] || return 0
  for record in "$manifest"/*.gitrepo; do
    [[ -e "$record" ]] || continue
    entry="$(basename "$record" .gitrepo)"
    [[ -e "$(dirname "$DEST")/$entry" ]] && continue
    [[ $missing -eq 0 ]] && printf '\nSiblings it needs beside it inside release/ - vendor these too:\n' >&2
    missing=1
    printf '  %s\n' "$entry" >&2
  done
}

report_siblings
