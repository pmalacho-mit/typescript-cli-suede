#!/usr/bin/env bash
# .suede/core/sync.sh — update every piece of suede machinery vendored into
# this repository, in one command, from `main`.
#
#   bash .suede/core/sync.sh [<path> ...]
#
# It finds them rather than being told: every subrepo whose remote is the suede
# library itself. In a fully initialized dependency that is four things.
#
#   .suede/core                 the maintainer's tools (this folder)
#   release/.suede/core         the tools that ship to consumers
#   .github/workflows           main's workflows
#   release/.github/workflows   the release branch's workflows
#
# Never ./release, which tracks this repository's own release branch: that one
# is published by push-release.sh, not pulled.
#
# Two of the four have a `.gitrepo` whose `parent` is meaningless here, for two
# different reasons, and both make `git subrepo pull` refuse:
#
#   * The workflow subrepos were cloned into the *template* this repository was
#     created from. A repository made from a template starts a fresh history,
#     so their parent names a commit that does not exist here at all. (They are
#     cloned into the template rather than at init because an Action is
#     restricted in what it may do to .github/workflows.)
#   * A `.suede/core` vendored onto the `release` branch before the layout
#     changed has a parent that does exist but is a release-branch commit, and
#     so is not an ancestor of `main`.
#
# Either way the fix is the same shape - point `parent` at a commit that IS in
# this history - so this repairs it and retries rather than making you read the
# failure. git-subrepo names the right commit when it can; when it cannot (the
# template case, where it suggests an empty SHA) the last commit that touched
# the subrepo is the honest answer, because that is the state on disk.
#
# Inputs (env):
#   RELEASE_DIR         default: release
#   SUEDE_LIBRARY_URL   default: https://github.com/pmalacho-mit/suede.git

set -euo pipefail

usage() { grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'; exit 0; }
[[ "${1-}" == "-h" || "${1-}" == "--help" ]] && usage

RELEASE_DIR="${RELEASE_DIR:-release}"
LIBRARY_URL="${SUEDE_LIBRARY_URL:-https://github.com/pmalacho-mit/suede.git}"

die() { printf 'sync: %s\n' "$*" >&2; exit 1; }
say() { printf 'sync: %s\n' "$*" >&2; }

command -v git >/dev/null 2>&1 || die "git not found"

# An install can be present without being reachable - a devcontainer feature or
# a login shell profile a non-interactive script never sourced. This is the
# marker it leaves behind.
ensure_git_subrepo() {
  git subrepo --version >/dev/null 2>&1 && return 0
  [[ -n "${GIT_SUBREPO_ROOT-}" && -f "${GIT_SUBREPO_ROOT-}/.rc" ]] || return 1
  set +eu
  # shellcheck disable=SC1091
  source "$GIT_SUBREPO_ROOT/.rc"
  set -eu
  git subrepo --version >/dev/null 2>&1
}

ensure_git_subrepo \
  || die "git-subrepo is not installed (https://github.com/ingydotnet/git-subrepo)"

cd "$(git rev-parse --show-toplevel)" || die "not inside a git repository"
git diff --quiet && git diff --cached --quiet \
  || die "you have uncommitted changes - git subrepo pull refuses a dirty tree"

# One spelling for one repository, so ssh and https forms of the library - and
# the local paths the tests use - all compare equal.
identity() { # <url>
  local url="${1%/}"
  url="${url%.git}"
  url="${url#*://}"
  url="${url#*@}"
  printf '%s' "${url/:/\/}"
}

readonly LIBRARY_ID="$(identity "$LIBRARY_URL")"

remote_of() { git config -f "$1/.gitrepo" --get subrepo.remote 2>/dev/null || true; }

library_subrepos() {
  local gitrepo directory
  while IFS= read -r gitrepo; do
    directory="${gitrepo#./}"
    directory="${directory%/.gitrepo}"
    [[ "$directory" == "$gitrepo" ]] && continue   # a .gitrepo at the root
    [[ "$(identity "$(remote_of "$directory")")" == "$LIBRARY_ID" ]] && printf '%s\n' "$directory"
  done < <(find . -name .gitrepo -not -path './.git/*' | sort)
}

# git-subrepo's recommendation, when its refusal carries one. The template case
# suggests an empty SHA, which is how "it does not know either" reads.
recommended_parent() { # <output>
  printf '%s' "$1" | grep -oE "to '[0-9a-f]{7,40}'" | grep -oE '[0-9a-f]{7,40}' | head -1
}

repair_parent() { # <path> <pull output> -> 0 if it repaired something
  local path="$1" parent
  parent="$(recommended_parent "$2")"
  [[ -n "$parent" ]] || parent="$(git log -n 1 --format=%H -- "$path")"
  [[ -n "$parent" ]] || return 1
  say "$path: its recorded parent is not in this history - repointing at ${parent:0:7}"
  git config -f "$path/.gitrepo" subrepo.parent "$parent"
  git add "$path/.gitrepo"
  git commit --quiet -m "suede: repoint $path at a parent in this history"
}

PULLED=()
FAILED=()

pull_one() { # <path>
  local path="$1" output status=0
  output="$(git subrepo pull "$path" 2>&1)" || status=$?
  if [[ "$status" != 0 ]]; then
    repair_parent "$path" "$output" || { FAILED+=("$path"); say "$path: FAILED"; printf '%s\n' "$output" >&2; return 0; }
    output="$(git subrepo pull "$path" 2>&1)" || {
      FAILED+=("$path"); say "$path: FAILED"; printf '%s\n' "$output" >&2; return 0
    }
  fi
  PULLED+=("$path")
  if printf '%s' "$output" | grep -q 'is up to date'; then
    say "$path: already up to date"
  else
    say "$path: updated"
  fi
}

# Pulling a subrepo nested inside ./release leaves a `subrepo/<path>` branch and
# a .git/tmp/subrepo/<path> scratch directory behind. Refs are directories, so
# the branch makes `subrepo/release` uncreatable; the scratch directory sits
# where the push wants its worktree. Either stops the next `git subrepo push
# release`, which is the very next thing publishing does.
clear_nested_leftovers() {
  local path nested=0
  for path in ${PULLED[@]+"${PULLED[@]}"}; do
    [[ "$path" == "$RELEASE_DIR/"* ]] || continue
    git subrepo clean "$path" >/dev/null 2>&1 || true
    nested=1
  done
  [[ "$nested" == 1 ]] || return 0
  rm -rf .git/tmp/subrepo
  git worktree prune >/dev/null 2>&1 || true
  say "cleared the git-subrepo leftovers of the subrepos under $RELEASE_DIR/"
}

TARGETS=()
if [[ $# -gt 0 ]]; then
  TARGETS=("$@")
else
  while IFS= read -r found; do TARGETS+=("$found"); done < <(library_subrepos)
fi

[[ ${#TARGETS[@]} -gt 0 ]] || die "found no subrepos of $LIBRARY_URL in this repository"

for target in "${TARGETS[@]}"; do
  [[ -f "$target/.gitrepo" ]] || die "$target is not a subrepo (no .gitrepo)"
  pull_one "$target"
done

clear_nested_leftovers

[[ ${#FAILED[@]} -eq 0 ]] || die "could not update: ${FAILED[*]}"
say "commit is already made by git-subrepo; push main to publish anything under $RELEASE_DIR/"
