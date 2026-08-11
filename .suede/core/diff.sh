#!/usr/bin/env bash
#
# Show every release dependency that differs from the commit its .gitrepo
# names. Non-empty output means "this pointer is dishonest" - you would ship a
# pointer to code that is not what you built against.
#
#   bash .suede/core/diff.sh
#
# Vendored dependencies are exempt: one exists precisely *because* it diverges,
# and it ships as source rather than as a pointer.
#
# The rule lives in the installer, which already knows how the tree classifies,
# so CI and this command cannot disagree about what counts.
set -euo pipefail

usage() { grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'; exit 0; }
[[ "${1-}" == "-h" || "${1-}" == "--help" ]] && usage

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUEDE="${SUEDE:-$CORE_DIR/suede.py}"

[[ -f "$SUEDE" ]] || {
  printf 'diff: cannot find the installer at %s (set SUEDE to its path)\n' "$SUEDE" >&2
  exit 1
}

exec python3 "$SUEDE" diff "$@"
