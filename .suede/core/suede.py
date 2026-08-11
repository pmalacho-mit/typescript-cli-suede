#!/usr/bin/env python3
"""suede - install, audit and publish suede dependencies.

One dependency-free file, so a consumer who hits a problem on an unusual system
can read it, patch it, and move on.

Sections run in a strict dependency direction: later sections use earlier ones,
never the reverse. Sections 9-11, 14 and 15's classification are pure functions
over the model; every call to git is confined to section 4.
"""

from __future__ import annotations

import sys

MINIMUM_PYTHON = (3, 9)


def refuse_old_python() -> None:
    if sys.version_info >= MINIMUM_PYTHON:
        return
    _ = sys.stderr.write( 
        "suede needs Python %d.%d or newer (this is %s).\n"
        "  macOS ships 3.9.6 with the Command Line Tools, which is enough.\n"
        "  Install a newer python3 and re-run.\n"
        % (MINIMUM_PYTHON[0], MINIMUM_PYTHON[1], ".".join(str(n) for n in sys.version_info[:3]))
    )
    raise SystemExit(3)


refuse_old_python()

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from collections import Counter, deque  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

# --------------------------------------------------------------------------- #
# 1. Constants                                                                 #
# --------------------------------------------------------------------------- #

RELEASE_DIR = "release"
MANIFEST_DIR = os.path.join(".suede", ".dependencies")
# Where pre-2.0 dependencies published their manifest. Read so a plan is right
# even when a dependency has not republished yet, and reported so it does.
LEGACY_MANIFEST_DIR = ".dependencies"
SEPARATOR_FILE = os.path.join(MANIFEST_DIR, "separator")
GITREPO = ".gitrepo"
RELEASE_BRANCH = "release"
SHORT_SHA = 7

# `.` and `__` are always legal, whatever a project declares for itself: a
# dependency's entries are named by its authors, not by us.
LEGAL_SEPARATORS = (".", "__")
DEFAULT_SEPARATOR = "."

# The separator must be legal inside a module identifier in the importing
# language. `.` works wherever an import is a path literal; `__` is required
# wherever a path segment surfaces as an identifier.
SEPARATOR_BY_EXTENSION = {
    "c": ".",
    "cjs": ".",
    "cpp": ".",
    "css": ".",
    "go": ".",
    "h": ".",
    "hpp": ".",
    "js": ".",
    "jsx": ".",
    "mjs": ".",
    "py": "__",
    "pyi": "__",
    "rb": "__",
    "rs": "__",
    "scss": ".",
    "sh": ".",
    "svelte": ".",
    "ts": ".",
    "tsx": ".",
    "vue": ".",
}

CACHE_DIR = os.path.join(".git", "suede-cache")
CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

NEVER_WALK = (".git", "node_modules")

# suede's own vendored machinery. These are subrepos, so they look exactly like
# dependencies, but they are how a dependency gets its workflows and core
# scripts - not something it depends on. Classifying them would put suede's
# plumbing in every `list` and announce it as vendored code on every install.
MACHINERY = (os.path.join(".suede", "core"), os.path.join(".github", "workflows"))

SUBREPO_METHOD = "merge"
SUBREPO_CMDVER = "0.4.9"

GITREPO_HEADER = (
    "; DO NOT EDIT (unless you know what you are doing)\n"
    ";\n"
    '; This subdirectory is a git "subrepo", and this file is maintained automatically\n'
    "; by the git-subrepo command. See https://github.com/ingydotnet/git-subrepo#readme\n"
    ";\n"
)

OP_ORDER = ("install", "reuse", "link", "copy", "record", "override", "npm")
MUTATING_OPS = ("install", "link", "copy", "record", "npm")


class Exit:
    OK = 0
    ERROR = 1
    USAGE = 2
    PRECONDITION = 3
    UNRESOLVED = 4
    CHECK_FAILED = 5


# --------------------------------------------------------------------------- #
# 2. Errors                                                                    #
# --------------------------------------------------------------------------- #


class SuedeError(Exception):
    code = Exit.ERROR


class Usage(SuedeError):
    code = Exit.USAGE


class Precondition(SuedeError):
    code = Exit.PRECONDITION


class PlanError(SuedeError):
    code = Exit.UNRESOLVED


# --------------------------------------------------------------------------- #
# 3. Model                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, order=True)
class Pin:
    remote: str
    commit: str
    branch: str = RELEASE_BRANCH

    @property
    def short(self) -> str:
        return self.commit[:SHORT_SHA]

    @property
    def name(self) -> str:
        """The dependency's identity: the remote's basename, `.git` stripped."""
        return self.remote.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


@dataclass(frozen=True)
class Entry:
    path: str  # repo-relative path of the entry itself
    name: str  # basename, verbatim
    kind: str  # "folder" | "symlink" | "dangling" | "file"
    target: Optional[str] = None  # repo-relative realpath when it resolves to a directory

    @property
    def backing(self) -> Optional[str]:
        """The directory this entry stands for, or None if it doesn't name one."""
        if self.kind == "folder":
            return self.path
        return self.target


@dataclass(frozen=True)
class Install:
    path: str  # repo-relative real directory
    pin: Pin
    parent: str = ""  # the .gitrepo `parent` field


@dataclass(frozen=True)
class Edge:
    dependent: str  # install path of the dependent
    entry_name: str  # manifest filename, verbatim
    pin: Pin  # what the dependent asked for


@dataclass(frozen=True)
class Manifest:
    edges: Mapping[str, Pin] = field(default_factory=dict)
    npm: Mapping[str, str] = field(default_factory=dict)
    legacy: bool = False  # published at the pre-2.0 path


EMPTY_MANIFEST = Manifest()


@dataclass(frozen=True)
class World:
    root: str
    repo: str
    sep: str
    sep_source: str  # "flag"|"file"|"entries"|"inferred"|"default"
    head: Optional[str]  # None => unborn HEAD
    dirty: bool = False
    has_release: bool = False
    installs: Mapping[str, Install] = field(default_factory=dict)  # path -> Install
    entries: Mapping[str, Entry] = field(default_factory=dict)  # path -> Entry
    edges: Tuple[Edge, ...] = ()
    vendored: Tuple[str, ...] = ()
    npm: Mapping[str, str] = field(default_factory=dict)
    records: Mapping[str, Pin] = field(default_factory=dict)  # what release/ already ships


@dataclass(frozen=True)
class Act:
    op: str  # see OP_ORDER
    entry: str
    pin: Optional[Pin] = None
    dest: Optional[str] = None
    target: Optional[str] = None
    reason: str = ""


@dataclass(frozen=True)
class Claim:
    dependent: Optional[str]  # dependency name of the claimant; None => the root project
    pin: Pin


@dataclass(frozen=True)
class Option:
    """A resolution offered for a conflict, stated as its filesystem outcome:
    what gets installed, and which demanded pin each install then satisfies."""

    id: str  # "coexist" | "unify" | "defer"
    label: str
    risk: str
    placements: Tuple[Tuple[Pin, str], ...] = ()  # pin -> entry name to install
    assignments: Tuple[Tuple[Pin, str], ...] = ()  # demanded pin -> entry satisfying it
    pin: Optional[Pin] = None  # the commit a `unify` option settles on
    backed_by: Optional[Pin] = None  # what an already-installed entry holds

    @property
    def entries(self) -> Tuple[str, ...]:
        return tuple(entry for _, entry in self.placements)


@dataclass(frozen=True)
class Conflict:
    remote: str
    claims: Tuple[Claim, ...]
    ancestry: str  # "ancestor"|"descendant"|"diverged"|"unknown"
    options: Tuple[Option, ...]
    involves_root: bool = False
    kind: str = "commit"  # "commit" | "ambiguous"


@dataclass(frozen=True)
class Plan:
    acts: Tuple[Act, ...] = ()
    conflicts: Tuple[Conflict, ...] = ()
    warnings: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()  # non-empty => refuse to apply

    @property
    def mutates(self) -> bool:
        return any(act.op in MUTATING_OPS for act in self.acts)


@dataclass(frozen=True)
class Request:
    pins: Tuple[Pin, ...] = ()
    name: Optional[str] = None  # --name, applies to a single requested pin
    target: str = ""  # --target, repo-relative; "" => flat at the repo root
    link_mode: str = "symlink"  # symlink | copy
    commit_suffix: bool = False  # pin the entry name to the commit as well


@dataclass(frozen=True)
class Policy:
    on_conflict: str = "defer"  # ask | coexist | unify-newest | defer
    npm: bool = True
    choices: Mapping[str, int] = field(default_factory=dict)  # remote -> option index


@dataclass(frozen=True)
class Naming:
    """What a pin's own root entry wants to be called. The override applies
    only to what was asked for by name; everything reached transitively is
    named by the rule."""

    repo: str
    sep: str
    override: Optional[str] = None
    requested: Tuple[Pin, ...] = ()
    commit_suffix: bool = False

    def preferred(self, pin: Pin) -> str:
        if self.override and pin in self.requested:
            return self.override
        name = self.repo + self.sep + pin.name
        return name + "-" + pin.short if self.commit_suffix and pin in self.requested else name


@dataclass(frozen=True)
class Finding:
    level: str  # "FAIL" | "WARN" | "INFO"
    code: str
    where: str
    message: str


@dataclass(frozen=True)
class Layout:
    """Where real installs live, and where the entries pointing at them go."""

    target: str = ""
    link_mode: str = "symlink"

    def install_path(self, entry: str) -> str:
        return os.path.join(self.target, entry) if self.target else entry

    def edge_paths(self, dependent_home: str, entry_name: str) -> Tuple[str, ...]:
        """An edge is satisfied by a sibling of its dependent. When the
        dependent does not live at the root, the entry goes in both places:
        Node resolves `../` through the realpath, a bundler with
        preserveSymlinks resolves it through the link, and the two disagree."""
        if not dependent_home:
            return (entry_name,)
        return (os.path.join(dependent_home, entry_name), entry_name)

    @property
    def link_mode_op(self) -> str:
        return "copy" if self.link_mode == "copy" else "link"


def relative_link(link_path: str, install_path: str) -> str:
    target = os.path.relpath(install_path, os.path.dirname(link_path) or ".")
    return target if target.startswith(".") else "./" + target


# --------------------------------------------------------------------------- #
# 4. Git - the only place subprocess appears                                   #
# --------------------------------------------------------------------------- #


class git:
    @staticmethod
    def run(*args: str, **kwargs) -> str:
        cwd = kwargs.pop("cwd", None)
        proc = subprocess.run(
            ("git",) + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        if proc.returncode != 0:
            raise SuedeError(
                "git %s failed (%d)\n%s" % (" ".join(args), proc.returncode, proc.stderr.strip())
            )
        return proc.stdout.strip()

    @staticmethod
    def ok(*args: str, **kwargs) -> bool:
        try:
            git.run(*args, **kwargs)
            return True
        except SuedeError:
            return False

    @staticmethod
    def toplevel() -> str:
        try:
            return git.run("rev-parse", "--show-toplevel")
        except SuedeError:
            raise Precondition("not inside a git repository - run suede from a working tree")

    @staticmethod
    def head(cwd: Optional[str] = None) -> Optional[str]:
        try:
            return git.run("rev-parse", "--verify", "HEAD", cwd=cwd)
        except SuedeError:
            return None

    @staticmethod
    def is_dirty(cwd: Optional[str] = None) -> bool:
        return bool(git.run("status", "--porcelain", cwd=cwd))

    @staticmethod
    def tracked_files(cwd: Optional[str] = None) -> List[str]:
        listing = git.run("ls-files", cwd=cwd)
        return listing.splitlines() if listing else []

    @staticmethod
    def remote_url(name: str = "origin", cwd: Optional[str] = None) -> Optional[str]:
        try:
            return git.run("remote", "get-url", name, cwd=cwd)
        except SuedeError:
            return None

    @staticmethod
    def resolve_branch(remote: str, branch: str) -> str:
        listing = git.run("ls-remote", "--exit-code", remote, "refs/heads/" + branch)
        return listing.split()[0]

    @staticmethod
    def fetch_commit(remote: str, commit: str, branch: str, dest: str) -> None:
        """Materialise one commit's tree at `dest`, history and all discarded."""
        os.makedirs(dest, exist_ok=True)
        git.run("init", "--quiet", cwd=dest)
        git.run("remote", "add", "origin", remote, cwd=dest)
        if not git.ok("fetch", "--quiet", "--depth", "1", "origin", commit, cwd=dest):
            git.run("fetch", "--quiet", "origin", "refs/heads/" + branch, cwd=dest)
        git.run("checkout", "--quiet", "--detach", commit, cwd=dest)

    @staticmethod
    def fetch_history(remote: str, branch: str, dest: str) -> None:
        """A blobless mirror - enough history to answer `is_ancestor`, no trees."""
        git.run("clone", "--quiet", "--bare", "--filter=blob:none", "--branch", branch, remote, dest)

    @staticmethod
    def is_ancestor(older: str, newer: str, cwd: str) -> bool:
        return git.ok("merge-base", "--is-ancestor", older, newer, cwd=cwd)

    @staticmethod
    def config_get(path: str, key: str) -> Optional[str]:
        try:
            return git.run("config", "-f", path, "--get", key)
        except SuedeError:
            return None

    @staticmethod
    def config_set(path: str, key: str, value: str) -> None:
        git.run("config", "-f", path, key, value)

    @staticmethod
    def add(paths: Sequence[str], cwd: str) -> None:
        if paths:
            git.run("add", "--", *paths, cwd=cwd)

    @staticmethod
    def commit(message: str, cwd: str) -> str:
        git.run("commit", "--quiet", "-m", message, cwd=cwd)
        return git.run("rev-parse", "--short", "HEAD", cwd=cwd)


# --------------------------------------------------------------------------- #
# 5. .gitrepo files                                                            #
# --------------------------------------------------------------------------- #


class gitrepo:
    """A `.gitrepo` is a git config file; read and write it as one."""

    @staticmethod
    def read(path: str) -> Optional[Pin]:
        remote = git.config_get(path, "subrepo.remote")
        commit = git.config_get(path, "subrepo.commit")
        if not remote or not commit:
            return None
        branch = git.config_get(path, "subrepo.branch") or RELEASE_BRANCH
        return Pin(remote=remote, commit=commit, branch=branch)

    @staticmethod
    def parent(path: str) -> str:
        return git.config_get(path, "subrepo.parent") or ""

    @staticmethod
    def write(path: str, pin: Pin, parent: Optional[str] = None) -> None:
        """A live `.gitrepo` - it drives `git subrepo pull` on the installed folder."""
        gitrepo._seed(path)
        gitrepo._set_pin(path, pin)
        git.config_set(path, "subrepo.parent", parent or "")
        git.config_set(path, "subrepo.method", SUBREPO_METHOD)
        git.config_set(path, "subrepo.cmdver", SUBREPO_CMDVER)

    @staticmethod
    def write_manifest_record(path: str, pin: Pin) -> None:
        """A shipped pointer. `parent` is a SHA in *our* repository and is
        meaningless downstream; `cmdver` records our local git-subrepo. Neither
        belongs in something a consumer resolves."""
        gitrepo._seed(path)
        gitrepo._set_pin(path, pin)

    @staticmethod
    def _seed(path: str) -> None:
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(GITREPO_HEADER)

    @staticmethod
    def _set_pin(path: str, pin: Pin) -> None:
        git.config_set(path, "subrepo.remote", pin.remote)
        git.config_set(path, "subrepo.branch", pin.branch)
        git.config_set(path, "subrepo.commit", pin.commit)

    @staticmethod
    def read_manifest(directory: str) -> Manifest:
        published, legacy = gitrepo._manifest_dir(directory)
        if published is None:
            return EMPTY_MANIFEST
        return Manifest(
            edges=gitrepo._records_in(published),
            npm=npm.declared_in(published),
            legacy=legacy,
        )

    @staticmethod
    def _manifest_dir(directory: str) -> Tuple[Optional[str], bool]:
        current = os.path.join(directory, MANIFEST_DIR)
        if os.path.isdir(current):
            return current, False
        legacy = os.path.join(directory, LEGACY_MANIFEST_DIR)
        if os.path.isdir(legacy):
            return legacy, True
        return None, False

    @staticmethod
    def _records_in(manifest_dir: str) -> Dict[str, Pin]:
        records = {}
        for filename in sorted(os.listdir(manifest_dir)):
            if not filename.endswith(GITREPO):
                continue
            pin = gitrepo.read(os.path.join(manifest_dir, filename))
            if pin:
                records[filename[: -len(GITREPO)]] = pin
        return records


class npm:
    """package.json's `dependencies`, the only part suede has an opinion about."""

    @staticmethod
    def declared_in(directory: str) -> Dict[str, str]:
        return npm.read(os.path.join(directory, "package.json"))

    @staticmethod
    def read(path: str) -> Dict[str, str]:
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        except (ValueError, OSError):
            return {}
        declared = document.get("dependencies")
        return dict(declared) if isinstance(declared, dict) else {}


# --------------------------------------------------------------------------- #
# 6. Context - $repo and $SEP                                                  #
# --------------------------------------------------------------------------- #


class context:
    @staticmethod
    def repo_name(root: str, override: Optional[str]) -> Tuple[str, Tuple[str, ...]]:
        """Classification hinges on knowing this verbatim, and forks and local
        renames are exactly where the two automatic sources disagree."""
        if override:
            return override, ()
        from_env = os.environ.get("SUEDE_REPO_NAME")
        if from_env:
            return from_env, ()
        directory = os.path.basename(root)
        from_remote = context._origin_basename(root)
        if not from_remote:
            return directory, ()
        if from_remote != directory:
            return from_remote, (context._name_disagreement(from_remote, directory),)
        return from_remote, ()

    @staticmethod
    def _origin_basename(root: str) -> Optional[str]:
        url = git.remote_url("origin", cwd=root)
        return Pin(remote=url, commit="").name if url else None

    @staticmethod
    def _name_disagreement(from_remote: str, directory: str) -> str:
        return (
            "repo name ambiguous: origin says '%s', the working tree is '%s'. "
            "Using '%s' - pass --repo-name to settle it." % (from_remote, directory, from_remote)
        )

    @staticmethod
    def separator(root: str, repo: str, override: Optional[str]) -> Tuple[str, str]:
        for resolve in (
            lambda: (override, "flag"),
            lambda: (context._declared_separator(root), "file"),
            lambda: (context._majority_separator(root, repo), "entries"),
            lambda: (context._inferred_separator(root), "inferred"),
        ):
            separator, source = resolve()
            if separator:
                return separator, source
        return DEFAULT_SEPARATOR, "default"

    @staticmethod
    def _declared_separator(root: str) -> Optional[str]:
        path = os.path.join(root, SEPARATOR_FILE)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip() or None

    @staticmethod
    def _majority_separator(root: str, repo: str) -> Optional[str]:
        votes = Counter()
        for name in os.listdir(root):
            for separator in LEGAL_SEPARATORS:
                if name.startswith(repo + separator):
                    votes[separator] += 1
        return context._winner(votes)

    @staticmethod
    def _inferred_separator(root: str) -> Optional[str]:
        """Tracked files only, so .gitignore is respected for free."""
        votes = Counter()
        for path in git.tracked_files(cwd=root):
            separator = SEPARATOR_BY_EXTENSION.get(path.rsplit(".", 1)[-1].lower())
            if separator:
                votes[separator] += 1
        return context._winner(votes)

    @staticmethod
    def _winner(votes: Counter) -> Optional[str]:
        ranked = votes.most_common()
        if not ranked:
            return None
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None
        return ranked[0][0]

    @staticmethod
    def evidence(root: str, sep: str, source: str) -> str:
        if source != "inferred":
            return ""
        counted = Counter(path.rsplit(".", 1)[-1].lower() for path in git.tracked_files(cwd=root))
        matching = [(ext, n) for ext, n in counted.items() if SEPARATOR_BY_EXTENSION.get(ext) == sep]
        if not matching:
            return ""
        extension, count = max(matching, key=lambda pair: pair[1])
        return "%d of %d tracked files are .%s" % (count, sum(counted.values()), extension)


# --------------------------------------------------------------------------- #
# 7. scan() -> World                                                           #
# --------------------------------------------------------------------------- #


def scan(root: str, repo: str, sep: str, sep_source: str) -> World:
    installs = _find_installs(root)
    return World(
        root=root,
        repo=repo,
        sep=sep,
        sep_source=sep_source,
        head=git.head(cwd=root),
        dirty=git.is_dirty(cwd=root),
        has_release=os.path.isdir(os.path.join(root, RELEASE_DIR)),
        installs=installs,
        entries=_find_entries(root, installs),
        edges=_read_edges(root, installs),
        vendored=_find_vendored(root),
        npm=npm.read(os.path.join(root, "package.json")),
        records=gitrepo.read_manifest(os.path.join(root, RELEASE_DIR)).edges,
    )


def _find_installs(root: str) -> Dict[str, Install]:
    """Every directory holding a `.gitrepo`, outside `release/`. Code inside
    `release/` ships verbatim and so can never satisfy an edge."""
    installs = {}
    for directory in _walk_outside_release(root):
        install = _read_install(root, directory)
        if install:
            installs[install.path] = install
    return installs


def _walk_outside_release(root: str) -> Iterable[str]:
    """Yields candidate directories, never descending into an install: what
    lives inside one is that dependency's business, not ours."""
    for directory, subdirs, _ in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if d not in NEVER_WALK)
        if directory == root:
            subdirs[:] = [d for d in subdirs if d != RELEASE_DIR]
        elif os.path.isfile(os.path.join(directory, GITREPO)):
            subdirs[:] = []
            yield directory


def _read_install(root: str, directory: str) -> Optional[Install]:
    path = os.path.join(directory, GITREPO)
    pin = gitrepo.read(path)
    if not pin:
        return None
    return Install(path=os.path.relpath(directory, root), pin=pin, parent=gitrepo.parent(path))


def _find_vendored(root: str) -> Tuple[str, ...]:
    """A subrepo inside `release/` ships with the release branch, source and
    all. Nothing to install - but a nested subrepo should never be a surprise."""
    release = os.path.join(root, RELEASE_DIR)
    if not os.path.isdir(release):
        return ()
    found = []
    for directory, subdirs, _ in os.walk(release):
        subdirs[:] = sorted(d for d in subdirs if d not in NEVER_WALK)
        # `release/.gitrepo` is the pointer for release/ itself - the folder
        # published to the release branch - not a dependency vendored into it.
        if directory == release:
            continue
        if os.path.isfile(os.path.join(directory, GITREPO)):
            subdirs[:] = []
            found.append(os.path.relpath(directory, root))
    return tuple(found)


def _find_entries(root: str, installs: Mapping[str, Install]) -> Dict[str, Entry]:
    """Root entries, plus the siblings of any install that lives elsewhere -
    an edge is satisfied next to its dependent, wherever that dependent is."""
    directories = {""}
    directories.update(os.path.dirname(path) for path in installs)
    entries = {}
    for directory in sorted(directories):
        for entry in _entries_in(root, directory):
            entries[entry.path] = entry
    return entries


def _entries_in(root: str, directory: str) -> Iterable[Entry]:
    absolute = os.path.join(root, directory) if directory else root
    if not os.path.isdir(absolute):
        return
    for name in sorted(os.listdir(absolute)):
        if name in NEVER_WALK:
            continue
        yield _describe_entry(root, os.path.join(directory, name) if directory else name)


def _describe_entry(root: str, path: str) -> Entry:
    absolute = os.path.join(root, path)
    name = os.path.basename(path)
    if os.path.islink(absolute):
        return Entry(path=path, name=name, kind=_link_kind(absolute), target=_target(root, absolute))
    if os.path.isdir(absolute):
        return Entry(path=path, name=name, kind="folder", target=path)
    return Entry(path=path, name=name, kind="file")


def _link_kind(absolute: str) -> str:
    return "symlink" if os.path.isdir(absolute) else "dangling"


def _target(root: str, absolute: str) -> Optional[str]:
    if not os.path.isdir(absolute):
        return None
    return os.path.relpath(os.path.realpath(absolute), os.path.realpath(root))


def _read_edges(root: str, installs: Mapping[str, Install]) -> Tuple[Edge, ...]:
    edges = []
    for path in sorted(installs):
        manifest = gitrepo.read_manifest(os.path.join(root, path))
        for entry_name in sorted(manifest.edges):
            edges.append(Edge(dependent=path, entry_name=entry_name, pin=manifest.edges[entry_name]))
    return tuple(edges)


# --------------------------------------------------------------------------- #
# 8. Classification - pure over World                                          #
# --------------------------------------------------------------------------- #


class declarations:
    """The classification rule, and the lookups everything downstream needs.

    A release dependency is announced by a root entry named `$repo$SEP<name>`
    whose backing folder sits outside `release/` and holds a `.gitrepo`. The
    separator is part of the match: `suede-extras/` in a repo named `suede`
    must not be silently promoted.
    """

    @staticmethod
    def is_machinery(path: str) -> bool:
        """suede's own vendored plumbing. A subrepo like any other on disk,
        which is why scan reports it - but not something the project depends
        on, so nothing downstream should treat it as one."""
        return any(path == place or path.endswith(os.sep + place) for place in MACHINERY)

    @staticmethod
    def is_prefixed(world: World, name: str) -> bool:
        return any(
            name.startswith(world.repo + separator)
            and len(name) > len(world.repo + separator)
            for separator in declarations._separators(world)
        )

    @staticmethod
    def _separators(world: World) -> Tuple[str, ...]:
        if world.sep in LEGAL_SEPARATORS:
            return LEGAL_SEPARATORS
        return LEGAL_SEPARATORS + (world.sep,)

    @staticmethod
    def root_entries(world: World) -> Dict[str, Entry]:
        return {path: entry for path, entry in world.entries.items() if os.path.dirname(path) == ""}

    @staticmethod
    def prefixed_entries(world: World) -> Dict[str, Entry]:
        return {
            name: entry
            for name, entry in declarations.root_entries(world).items()
            if declarations.is_prefixed(world, name)
        }

    @staticmethod
    def by_name(world: World) -> Dict[str, Install]:
        """Entry name -> the install it declares, for every release dependency."""
        declared = {}
        for name, entry in declarations.prefixed_entries(world).items():
            install = declarations.backing_install(world, entry)
            if install and not declarations.is_machinery(install.path):
                declared[name] = install
        return declared

    @staticmethod
    def backing_install(world: World, entry: Entry) -> Optional[Install]:
        backing = entry.backing
        return world.installs.get(backing) if backing else None

    @staticmethod
    def by_remote(world: World) -> Dict[str, Dict[Pin, str]]:
        """Remote -> {pin: entry name}. The planner's view of what is already
        resolved, and the reason a coexist install stays addressable."""
        grouped = {}
        for name, install in sorted(declarations.by_name(world).items()):
            grouped.setdefault(install.pin.remote, {}).setdefault(install.pin, name)
        return grouped

    @staticmethod
    def backing_paths(world: World) -> Dict[str, str]:
        """Install path -> the root entry declaring it."""
        return {install.path: name for name, install in declarations.by_name(world).items()}

    @staticmethod
    def resolved_by(world: World, path: str) -> Optional[Install]:
        """The declared install an entry path resolves to, if any. Undeclared
        is deliberately not the same as absent - see the declaration invariant."""
        entry = world.entries.get(path)
        backing = entry.backing if entry else None
        if backing and backing in declarations.backing_paths(world):
            return world.installs[backing]
        return None

    @staticmethod
    def effective_pin(world: World, dependent: Pin, entry_name: str, demanded: Pin) -> Pin:
        """What this edge actually resolves to today: the consumer's own
        resolution if they declared one, otherwise what was asked for."""
        return declarations.resolved_edge(world, dependent, entry_name) or demanded

    @staticmethod
    def resolved_edge(world: World, dependent: Pin, entry_name: str) -> Optional[Pin]:
        for path in declarations._sibling_candidates(world, dependent, entry_name):
            install = declarations.resolved_by(world, path)
            if install:
                return install.pin
        return None

    @staticmethod
    def _sibling_candidates(world: World, dependent: Pin, entry_name: str) -> Tuple[str, ...]:
        homes = {os.path.dirname(path) for path, install in world.installs.items()
                 if install.pin == dependent}
        homes.add("")
        return tuple(os.path.join(home, entry_name) if home else entry_name
                     for home in sorted(homes))

    @staticmethod
    def classify(world: World, install: Install) -> str:
        if install.path in declarations.backing_paths(world):
            return "release"
        return "development"


# --------------------------------------------------------------------------- #
# 9. stage() - the last I/O before the planner                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Staged:
    manifests: Mapping[Pin, Manifest] = field(default_factory=dict)
    trees: Mapping[Pin, str] = field(default_factory=dict)  # pin -> directory holding its bytes
    ancestry: Mapping[Tuple[str, str], bool] = field(default_factory=dict)


def stage(world: World, pins: Sequence[Pin], use_cache: bool = True) -> Staged:
    """Fetch every pin in the closure into `.git/suede-cache/` and read its
    manifest from there. Staging before planning is what lets the announce
    block name a dependency's dependencies before anything is installed."""
    cache.prune(world.root)
    installed = _installed_manifests(world)
    manifests: Dict[Pin, Manifest] = {}
    trees: Dict[Pin, str] = {}
    queue = deque(pins)
    while queue:
        pin = queue.popleft()
        if pin in manifests:
            continue
        manifests[pin] = installed.get(pin) or _fetch(world, pin, trees, use_cache)
        queue.extend(_wanted_by(world, pin, manifests[pin]))
    manifests.update({pin: manifest for pin, manifest in installed.items() if pin not in manifests})
    return Staged(manifests=manifests, trees=trees, ancestry=_ancestry(world, manifests, use_cache))


def _installed_manifests(world: World) -> Dict[Pin, Manifest]:
    """An installed copy is authoritative for its own manifest and saves a
    clone, which is what makes a re-run on a satisfied tree work offline."""
    return {
        install.pin: gitrepo.read_manifest(os.path.join(world.root, install.path))
        for install in world.installs.values()
    }


def _fetch(world: World, pin: Pin, trees: Dict[Pin, str], use_cache: bool) -> Manifest:
    trees[pin] = cache.fetch(world.root, pin, use_cache)
    return gitrepo.read_manifest(trees[pin])


def _wanted_by(world: World, dependent: Pin, manifest: Manifest) -> List[Pin]:
    """Following the consumer's own resolution here is what keeps staging from
    cloning a dependency they have already replaced."""
    return [
        declarations.effective_pin(world, dependent, entry_name, demanded)
        for entry_name, demanded in sorted(manifest.edges.items())
    ]


class cache:
    """`.git/suede-cache/` - under `.git/`, so it can never be committed and
    needs no .gitignore entry."""

    @staticmethod
    def directory(root: str) -> str:
        return os.path.join(root, CACHE_DIR)

    @staticmethod
    def fetch(root: str, pin: Pin, use_cache: bool) -> str:
        destination = os.path.join(cache.directory(root), pin.short)
        if os.path.isdir(destination) and use_cache:
            return destination
        shutil.rmtree(destination, ignore_errors=True)
        try:
            git.fetch_commit(pin.remote, pin.commit, pin.branch, destination)
        except SuedeError as failure:
            shutil.rmtree(destination, ignore_errors=True)
            raise SuedeError(_unreachable(pin, failure))
        return destination

    @staticmethod
    def history(root: str, remote: str, branch: str) -> Optional[str]:
        destination = os.path.join(cache.directory(root), "history", _slug(remote) + ".git")
        if os.path.isdir(destination):
            return destination
        try:
            git.fetch_history(remote, branch, destination)
        except SuedeError:
            shutil.rmtree(destination, ignore_errors=True)
            return None
        return destination

    @staticmethod
    def prune(root: str) -> None:
        directory = cache.directory(root)
        if not os.path.isdir(directory):
            return
        cutoff = time.time() - CACHE_MAX_AGE_SECONDS
        for name in os.listdir(directory):
            entry = os.path.join(directory, name)
            if os.path.isdir(entry) and os.path.getmtime(entry) < cutoff:
                shutil.rmtree(entry, ignore_errors=True)


def _unreachable(pin: Pin, failure: SuedeError) -> str:
    return (
        "could not fetch %s@%s from %s.\n"
        "  If the remote needs authentication, `git clone` it once by hand so your\n"
        "  credential helper caches it. If the repository has no `%s` branch it is\n"
        "  not a published suede dependency yet.\n%s"
        % (pin.name, pin.short, pin.remote, pin.branch, failure)
    )


def _slug(remote: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in remote).strip("-")


def _ancestry(
    world: World, manifests: Mapping[Pin, Manifest], use_cache: bool
) -> Dict[Tuple[str, str], bool]:
    """"Newer" is a question about history, never about dates - so answer it
    with `merge-base`, and only for the remotes that actually disagree."""
    table = {}
    for remote, pins in _remotes_wanted_twice(world, manifests).items():
        repository = cache.history(world.root, remote, pins[0].branch) if use_cache else None
        if not repository:
            continue
        for older in pins:
            for newer in pins:
                if older != newer:
                    table[(older.commit, newer.commit)] = git.is_ancestor(
                        older.commit, newer.commit, cwd=repository
                    )
    return table


def _remotes_wanted_twice(
    world: World, manifests: Mapping[Pin, Manifest]
) -> Dict[str, List[Pin]]:
    by_remote = {}
    for pin in _every_pin(world, manifests):
        by_remote.setdefault(pin.remote, [])
        if pin not in by_remote[pin.remote]:
            by_remote[pin.remote].append(pin)
    return {remote: pins for remote, pins in by_remote.items() if len(pins) > 1}


def _every_pin(world: World, manifests: Mapping[Pin, Manifest]) -> Iterable[Pin]:
    for install in world.installs.values():
        yield install.pin
    for pin, manifest in manifests.items():
        yield pin
        for demanded in manifest.edges.values():
            yield demanded


# --------------------------------------------------------------------------- #
# 10. plan() - pure                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Demand:
    """An edge, before either end of it has a place on disk."""

    dependent: Pin
    entry_name: str
    pin: Pin  # what the dependent asked for
    substitute: Optional[Pin] = None  # what the consumer already backed it with

    @property
    def effective(self) -> Pin:
        return self.substitute or self.pin


class Names:
    """Allocates root entry names. An existing entry is never renamed - the
    project's own imports of `../app.C` are invisible from here - so a
    newcomer that wants a taken name gets the suffix instead."""

    def __init__(self, taken: Iterable[str]):
        self._taken = set(taken)

    def clone(self) -> "Names":
        return Names(self._taken)

    def reserve(self, preferred: str, distinguisher: str) -> str:
        name = preferred if preferred not in self._taken else preferred + "-" + distinguisher
        attempt = 2
        while name in self._taken:
            name = "%s-%s-%d" % (preferred, distinguisher, attempt)
            attempt += 1
        self._taken.add(name)
        return name


@dataclass
class Resolution:
    """Which entry backs each pin, and what remains unresolved."""

    entry_of: Dict[Pin, str] = field(default_factory=dict)
    pin_of_entry: Dict[str, Pin] = field(default_factory=dict)
    installs: List[Tuple[Pin, str]] = field(default_factory=list)
    reuses: List[Tuple[Pin, str]] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)

    def satisfy(self, pin: Pin, entry: str, backed_by: Pin) -> None:
        self.entry_of[pin] = entry
        self.pin_of_entry[entry] = backed_by

    def place(self, pin: Pin, entry: str) -> None:
        self.installs.append((pin, entry))
        self.satisfy(pin, entry, pin)


def plan(
    world: World,
    request: Request,
    policy: Policy,
    manifests: Mapping[Pin, Manifest],
    ancestry: Optional[Mapping[Tuple[str, str], bool]] = None,
) -> Plan:
    blockers = _preconditions(world)
    if blockers:
        return Plan(blockers=blockers)
    pins, demands = _closure(world, request, manifests)
    resolution = _resolve(world, request, policy, pins, demands, ancestry or {})
    acts, warnings = _acts(world, request, policy, manifests, demands, resolution)
    return Plan(
        acts=_ordered(acts),
        conflicts=tuple(sorted(resolution.conflicts, key=lambda conflict: conflict.remote)),
        warnings=tuple(warnings)
        + tuple(_legacy_manifest_warnings(manifests))
        + _situational_warnings(world, request, resolution),
        blockers=tuple(_npm_blockers(world, policy, manifests)),
    )


def _preconditions(world: World) -> Tuple[str, ...]:
    if world.head is None:
        return (
            "this repository has no commits yet. Install would write an empty `parent`"
            " into every .gitrepo and the first `git subrepo pull` would fail with"
            " 'refusing to merge unrelated histories'. Commit something first.",
        )
    return ()


def _closure(
    world: World, request: Request, manifests: Mapping[Pin, Manifest]
) -> Tuple[Tuple[Pin, ...], Tuple[Demand, ...]]:
    """Every pin reachable from the request, and the demand that reached it.
    The visited set is what makes a cycle terminate rather than a promise."""
    pins: List[Pin] = []
    demands: List[Demand] = []
    queue = deque(request.pins)
    while queue:
        pin = queue.popleft()
        if pin in pins:
            continue
        pins.append(pin)
        for entry_name in sorted(manifests.get(pin, EMPTY_MANIFEST).edges):
            demand = _demand(world, pin, entry_name, manifests[pin].edges[entry_name])
            demands.append(demand)
            queue.append(demand.effective)
    return tuple(pins), tuple(demands)


def _demand(world: World, dependent: Pin, entry_name: str, demanded: Pin) -> Demand:
    """A consumer who has already backed this edge with a declared entry of
    their own has resolved it - a fork, a patched build, their own
    implementation. Follow their resolution, do not re-flatten ours."""
    effective = declarations.effective_pin(world, dependent, entry_name, demanded)
    return Demand(
        dependent=dependent,
        entry_name=entry_name,
        pin=demanded,
        substitute=effective if effective != demanded else None,
    )


def _resolve(
    world: World,
    request: Request,
    policy: Policy,
    pins: Sequence[Pin],
    demands: Sequence[Demand],
    ancestry: Mapping[Tuple[str, str], bool],
) -> Resolution:
    resolution = Resolution()
    naming = Naming(
        repo=world.repo,
        sep=world.sep,
        override=request.name,
        requested=request.pins,
        commit_suffix=request.commit_suffix,
    )
    names = Names(declarations.root_entries(world))
    declared = declarations.by_remote(world)
    wanted_by_remote = _wanted_by_remote(pins)
    for remote in sorted(wanted_by_remote):
        _resolve_remote(
            remote=remote,
            wanted=wanted_by_remote[remote],
            declared=declared.get(remote, {}),
            claims=_claims(remote, request, demands),
            involves_root=any(pin.remote == remote for pin in request.pins),
            naming=naming,
            policy=policy,
            ancestry=ancestry,
            names=names,
            resolution=resolution,
        )
    return resolution


def _wanted_by_remote(pins: Sequence[Pin]) -> Dict[str, List[Pin]]:
    wanted: Dict[str, List[Pin]] = {}
    for pin in pins:
        wanted.setdefault(pin.remote, [])
        if pin not in wanted[pin.remote]:
            wanted[pin.remote].append(pin)
    return wanted


def _claims(remote: str, request: Request, demands: Sequence[Demand]) -> Tuple[Claim, ...]:
    claims = [Claim(dependent=None, pin=pin) for pin in request.pins if pin.remote == remote]
    claims += [
        Claim(dependent=demand.dependent.name, pin=demand.effective)
        for demand in demands
        if demand.effective.remote == remote
    ]
    return tuple(sorted(claims, key=lambda claim: (claim.dependent or "", claim.pin.commit)))


def _resolve_remote(
    remote: str,
    wanted: Sequence[Pin],
    declared: Mapping[Pin, str],
    claims: Tuple[Claim, ...],
    involves_root: bool,
    naming: Naming,
    policy: Policy,
    ancestry: Mapping[Tuple[str, str], bool],
    names: Names,
    resolution: Resolution,
) -> None:
    unmatched = _reuse_exact_matches(wanted, declared, resolution)
    if not unmatched:
        return
    if len(declared) == 1:
        _defer_to_the_consumer(unmatched, declared, resolution)
        return
    if len(declared) > 1:
        resolution.conflicts.append(_ambiguous(remote, claims, declared, involves_root))
        return
    if len(unmatched) == 1:
        resolution.place(
            unmatched[0], names.reserve(naming.preferred(unmatched[0]), unmatched[0].short)
        )
        return
    _resolve_competing_commits(
        remote, unmatched, claims, involves_root, naming, policy, ancestry, names, resolution
    )


def _reuse_exact_matches(
    wanted: Sequence[Pin], declared: Mapping[Pin, str], resolution: Resolution
) -> List[Pin]:
    """An exact commit match is always the answer, whatever else is declared."""
    unmatched = []
    for pin in wanted:
        if pin in declared:
            resolution.satisfy(pin, declared[pin], pin)
            resolution.reuses.append((pin, declared[pin]))
        else:
            unmatched.append(pin)
    return unmatched


def _defer_to_the_consumer(
    unmatched: Sequence[Pin], declared: Mapping[Pin, str], resolution: Resolution
) -> None:
    """Exactly one root-declared entry for this remote and no exact match: the
    consumer has already resolved it. Their declaration wins - announce it."""
    entry = list(declared.values())[0]
    backing = list(declared.keys())[0]
    for pin in unmatched:
        resolution.satisfy(pin, entry, backing)


def _resolve_competing_commits(
    remote: str,
    unmatched: Sequence[Pin],
    claims: Tuple[Claim, ...],
    involves_root: bool,
    naming: Naming,
    policy: Policy,
    ancestry: Mapping[Tuple[str, str], bool],
    names: Names,
    resolution: Resolution,
) -> None:
    conflict = _conflict(remote, unmatched, claims, involves_root, naming, ancestry, names)
    chosen = _policy_choice(policy, conflict, ancestry)
    if chosen is None:
        resolution.conflicts.append(conflict)
        return
    _adopt(chosen, names, resolution)


def _adopt(option: Option, names: Names, resolution: Resolution) -> None:
    for pin, entry in option.placements:
        names.reserve(entry, pin.short)
        resolution.installs.append((pin, entry))
    for pin, entry in option.assignments:
        resolution.satisfy(pin, entry, _placed_pin(option, entry))


def _placed_pin(option: Option, entry: str) -> Pin:
    for pin, placed in option.placements:
        if placed == entry:
            return pin
    if option.backed_by:
        return option.backed_by
    raise PlanError("option %s assigns %s with nothing installed there" % (option.id, entry))


def _policy_choice(
    policy: Policy, conflict: Conflict, ancestry: Mapping[Tuple[str, str], bool]
) -> Optional[Option]:
    if conflict.remote in policy.choices:
        return _chosen(conflict, policy.choices[conflict.remote])
    if conflict.kind == "ambiguous":
        return None  # which declared entry was meant is not a thing to guess
    if policy.on_conflict == "coexist":
        return _option_by_id(conflict, "coexist")
    if policy.on_conflict == "unify-newest":
        return _newest_unify_option(conflict, ancestry)
    return None


def _chosen(conflict: Conflict, index: int) -> Optional[Option]:
    option = conflict.options[index]
    return None if option.id == "defer" else option


def _option_by_id(conflict: Conflict, wanted: str) -> Optional[Option]:
    for option in conflict.options:
        if option.id == wanted:
            return option
    return None


def _newest_unify_option(
    conflict: Conflict, ancestry: Mapping[Tuple[str, str], bool]
) -> Optional[Option]:
    unifications = [option for option in conflict.options if option.id == "unify"]
    newest = _newest([option.pin for option in unifications if option.pin], ancestry)
    if newest is None:
        return None
    return next(option for option in unifications if option.pin == newest)


def _newest(pins: Sequence[Pin], ancestry: Mapping[Tuple[str, str], bool]) -> Optional[Pin]:
    """The one every other commit is an ancestor of. Diverged history has no
    newest, and guessing one is how a dependent silently gets a commit its
    author never tested."""
    for candidate in pins:
        others = [pin for pin in pins if pin != candidate]
        if all(ancestry.get((pin.commit, candidate.commit)) for pin in others):
            return candidate
    return None


# --------------------------------------------------------------------------- #
# 11. Conflict options - pure                                                  #
# --------------------------------------------------------------------------- #


def _conflict(
    remote: str,
    unmatched: Sequence[Pin],
    claims: Tuple[Claim, ...],
    involves_root: bool,
    naming: Naming,
    ancestry: Mapping[Tuple[str, str], bool],
    names: Names,
) -> Conflict:
    return Conflict(
        remote=remote,
        claims=claims,
        ancestry=_describe_ancestry(unmatched, ancestry),
        options=_options(unmatched, claims, involves_root, naming, ancestry, names),
        involves_root=involves_root,
    )


def _describe_ancestry(pins: Sequence[Pin], ancestry: Mapping[Tuple[str, str], bool]) -> str:
    if len(pins) != 2:
        return "unknown"
    first, second = pins[0].commit, pins[1].commit
    if (first, second) not in ancestry and (second, first) not in ancestry:
        return "unknown"
    if ancestry.get((first, second)):
        return "ancestor"
    if ancestry.get((second, first)):
        return "descendant"
    return "diverged"


def _options(
    unmatched: Sequence[Pin],
    claims: Tuple[Claim, ...],
    involves_root: bool,
    naming: Naming,
    ancestry: Mapping[Tuple[str, str], bool],
    names: Names,
) -> Tuple[Option, ...]:
    """Every option states its concrete filesystem outcome and its own risk.
    Nothing is preselected; when the root project is a claimant, coexist leads,
    because unifying changes what its own source compiles against."""
    offers = [_coexist(unmatched, naming, names)]
    offers += [
        _unify(pin, unmatched, claims, naming, names) for pin in _newest_first(unmatched, ancestry)
    ]
    offers.append(_defer())
    return tuple(offers)


def _newest_first(pins: Sequence[Pin], ancestry: Mapping[Tuple[str, str], bool]) -> List[Pin]:
    newest = _newest(pins, ancestry)
    if newest is None:
        return list(pins)
    return [newest] + [pin for pin in pins if pin != newest]


def _coexist(unmatched: Sequence[Pin], naming: Naming, names: Names) -> Option:
    scratch = names.clone()
    placements = tuple(
        (pin, scratch.reserve(naming.preferred(pin), pin.short)) for pin in unmatched
    )
    return Option(
        id="coexist",
        label="two installs, each dependent keeps its own pin",
        risk="two runtime copies - breaks singletons, instanceof, shared framework context",
        placements=placements,
        assignments=placements,
    )


def _unify(
    pin: Pin, unmatched: Sequence[Pin], claims: Tuple[Claim, ...], naming: Naming, names: Names
) -> Option:
    entry = names.clone().reserve(naming.preferred(pin), pin.short)
    return Option(
        id="unify",
        label="one install at %s; every dependent points at it" % pin.short,
        risk=_unify_risk(pin, claims),
        placements=((pin, entry),),
        assignments=tuple((wanted, entry) for wanted in unmatched),
        pin=pin,
    )


def _unify_risk(pin: Pin, claims: Tuple[Claim, ...]) -> str:
    losers = sorted({claim.dependent or "this project" for claim in claims if claim.pin != pin})
    if not losers:
        return ""
    return "%s was built and tested against a different commit" % ", ".join(losers)


def _defer() -> Option:
    return Option(
        id="defer",
        label="install nothing here; print what is needed",
        risk="",
    )




def _ambiguous(
    remote: str, claims: Tuple[Claim, ...], declared: Mapping[Pin, str], involves_root: bool
) -> Conflict:
    """Several root-declared entries share this remote and none matches the
    commit asked for. Coexist installs are only resolvable downstream because
    we prompt here instead of guessing which one was meant."""
    return Conflict(
        remote=remote,
        claims=claims,
        ancestry="unknown",
        options=tuple(
            [
                Option(
                    id="existing",
                    label="satisfy from the declared entry %s" % entry,
                    risk="that entry pins %s" % pin.short,
                    assignments=tuple((claim.pin, entry) for claim in claims),
                    backed_by=pin,
                    pin=pin,
                )
                for pin, entry in sorted(declared.items(), key=lambda item: item[1])
            ]
            + [_defer()]
        ),
        involves_root=involves_root,
        kind="ambiguous",
    )


# --------------------------------------------------------------------------- #
# 12. Acts - pure                                                              #
# --------------------------------------------------------------------------- #


def _acts(
    world: World,
    request: Request,
    policy: Policy,
    manifests: Mapping[Pin, Manifest],
    demands: Sequence[Demand],
    resolution: Resolution,
) -> Tuple[List[Act], List[str]]:
    layout = Layout(target=request.target, link_mode=request.link_mode)
    edges, warnings = _edge_acts(world, demands, resolution, layout)
    acts = (
        _install_acts(request, demands, resolution, layout)
        + _reuse_acts(world, resolution)
        + edges
        + _record_acts(world, resolution)
        + _npm_acts(world, policy, manifests)
    )
    return _drop_notes_when_nothing_changes(acts), warnings


def _drop_notes_when_nothing_changes(acts: List[Act]) -> List[Act]:
    """`reuse` and `override` explain a change; with no change to explain, a
    re-run on a satisfied tree should say nothing at all."""
    return acts if any(act.op in MUTATING_OPS for act in acts) else []


def _install_acts(
    request: Request, demands: Sequence[Demand], resolution: Resolution, layout: Layout
) -> List[Act]:
    return [
        Act(
            op="install",
            entry=entry,
            pin=pin,
            dest=layout.install_path(entry),
            reason=_why(pin, request, demands),
        )
        for pin, entry in resolution.installs
    ]


def _why(pin: Pin, request: Request, demands: Sequence[Demand]) -> str:
    if pin in request.pins:
        return "requested"
    for demand in demands:
        if demand.effective == pin:
            return "required by " + demand.dependent.name
    return "flattening"


def _reuse_acts(world: World, resolution: Resolution) -> List[Act]:
    declared = declarations.by_name(world)
    return [
        Act(
            op="reuse",
            entry=entry,
            pin=pin,
            dest=declared[entry].path if entry in declared else entry,
            reason="already present",
        )
        for pin, entry in resolution.reuses
    ]


def _edge_acts(
    world: World, demands: Sequence[Demand], resolution: Resolution, layout: Layout
) -> Tuple[List[Act], List[str]]:
    acts: List[Act] = []
    warnings: List[str] = []
    for demand in demands:
        acts += _override_act(demand, resolution)
        for path in _edge_paths(world, demand, resolution, layout):
            act, warning = _one_edge(world, path, demand, resolution, layout)
            acts += act
            warnings += warning
    return acts, warnings


def _edge_paths(
    world: World, demand: Demand, resolution: Resolution, layout: Layout
) -> Tuple[str, ...]:
    dependent = resolution.entry_of.get(demand.dependent)
    if dependent is None or demand.effective not in resolution.entry_of:
        return ()
    home = os.path.dirname(_where(world, dependent, layout))
    return layout.edge_paths(home, demand.entry_name)


def _one_edge(
    world: World, path: str, demand: Demand, resolution: Resolution, layout: Layout
) -> Tuple[List[Act], List[str]]:
    install = _where(world, resolution.entry_of[demand.effective], layout)
    existing = world.entries.get(path)
    if existing is None:
        return [_link(layout, path, install)], []
    if existing.backing == install:
        return [], []
    return [], [_resolved_elsewhere(path, existing, install)]


def _link(layout: Layout, path: str, install: str) -> Act:
    return Act(op=layout.link_mode_op, entry=path, target=relative_link(path, install), dest=install)


def _resolved_elsewhere(path: str, existing: Entry, install: str) -> str:
    return "%s already exists and resolves to %s, not %s - left untouched; run `suede check`" % (
        path,
        existing.backing or "nothing",
        install,
    )


def _where(world: World, entry: str, layout: Layout) -> str:
    """The directory an entry names: where it already is, or where it will go."""
    declared = declarations.by_name(world)
    if entry in declared:
        return declared[entry].path
    return layout.install_path(entry)


def _override_act(demand: Demand, resolution: Resolution) -> List[Act]:
    """A consumer who resolved an edge differently gets told, not challenged."""
    entry = resolution.entry_of.get(demand.effective)
    backing = resolution.pin_of_entry.get(entry) if entry else None
    if backing is None or backing == demand.pin:
        return []
    return [
        Act(
            op="override",
            entry=demand.entry_name,
            pin=demand.pin,
            target=entry,
            reason="%s pins %s; you declare %s"
            % (demand.dependent.name, demand.pin.short, backing.short),
        )
    ]


def _record_acts(world: World, resolution: Resolution) -> List[Act]:
    """A project records its whole transitive closure as its own release
    dependencies - which is what makes its manifest a complete recipe."""
    if not world.has_release:
        return []
    return [
        Act(
            op="record",
            entry=entry,
            pin=pin,
            dest=os.path.join(RELEASE_DIR, MANIFEST_DIR, entry + GITREPO),
        )
        for entry, pin in sorted(resolution.pin_of_entry.items())
        if world.records.get(entry) != pin
    ]


def _npm_acts(world: World, policy: Policy, manifests: Mapping[Pin, Manifest]) -> List[Act]:
    additions, _ = _npm_diff(world, policy, manifests)
    return [
        Act(op="npm", entry="%s@%s" % (package, wanted), reason="new")
        for package, wanted in sorted(additions.items())
    ]


def _npm_diff(
    world: World, policy: Policy, manifests: Mapping[Pin, Manifest]
) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    """Missing entries are additions; a range that disagrees is a conflict, and
    unifying ranges is a different problem with its own semantics."""
    additions: Dict[str, str] = {}
    conflicts: Dict[str, Tuple[str, str]] = {}
    if not policy.npm:
        return additions, conflicts
    for manifest in manifests.values():
        for package, wanted in manifest.npm.items():
            declared = world.npm.get(package)
            if declared is None:
                additions[package] = wanted
            elif declared != wanted:
                conflicts[package] = (wanted, declared)
    return additions, conflicts


def _npm_blockers(
    world: World, policy: Policy, manifests: Mapping[Pin, Manifest]
) -> List[str]:
    _, conflicts = _npm_diff(world, policy, manifests)
    return [
        "npm dependency %s: a dependency asks for %s, your package.json declares %s."
        " Unify the range yourself - suede will not guess." % (package, wanted, declared)
        for package, (wanted, declared) in sorted(conflicts.items())
    ]


def _legacy_manifest_warnings(manifests: Mapping[Pin, Manifest]) -> List[str]:
    """Its entry names were chosen before the `$repo$SEP` rule, so the siblings
    it asks for are not the ones its own code imports. Reading it is better
    than being blind, but only republishing fixes it."""
    return [
        "%s publishes its manifest at the pre-2.0 path (%s). Its entry names predate the"
        " $repo$SEP rule, so what it asks for may not match what its code imports."
        " Run `suede extract` on that dependency and republish it."
        % (pin.name, LEGACY_MANIFEST_DIR)
        for pin, manifest in sorted(manifests.items())
        if manifest.legacy
    ]


def _situational_warnings(world: World, request: Request, resolution: Resolution) -> Tuple[str, ...]:
    warnings = []
    if world.dirty:
        warnings.append(
            "working tree is dirty. Install is fine with that, but `git subrepo pull` is not."
        )
    if request.target:
        warnings.append(
            "--target relocates the real install. Edge entries are written both beside it and at"
            " the repo root, because Node dereferences symlinks and bundlers with preserveSymlinks"
            " do not. If your build resolves neither, you own the fix."
        )
    for path in world.vendored:
        if declarations.is_machinery(path):
            continue
        warnings.append("%s is vendored inside release/ - it ships as source, nothing to install" % path)
    return tuple(warnings)


def _ordered(acts: List[Act]) -> Tuple[Act, ...]:
    return tuple(sorted(acts, key=lambda act: (OP_ORDER.index(act.op), act.entry)))


# --------------------------------------------------------------------------- #
# 13. announce() and plan_json() - pure                                        #
# --------------------------------------------------------------------------- #


def announce(world: World, plan: Plan, request: Request, evidence: str = "") -> str:
    lines = _header(world, request, evidence)
    if plan.blockers:
        return "\n".join(lines + _titled("BLOCKED", plan.blockers))
    lines += _act_lines(plan)
    lines += _conflict_lines(plan)
    lines += _titled("WARNINGS", plan.warnings)
    return "\n".join(lines)


def _titled(title: str, lines: Sequence[str]) -> List[str]:
    if not lines:
        return []
    return [title, ""] + ["  " + line for line in lines] + [""]


def _header(world: World, request: Request, evidence: str) -> List[str]:
    subject = ", ".join(pin.name for pin in request.pins) or world.repo
    return [
        "suede - %s" % subject,
        "",
        "  repo:       %s" % world.repo,
        "  separator:  %s          (%s)" % (world.sep, _separator_note(world, evidence)),
        "  layout:     %s" % (request.target or "flat (repo root)"),
        "",
    ]


def _separator_note(world: World, evidence: str) -> str:
    if world.sep_source == "inferred":
        return "inferred: %s" % (evidence or "from tracked file extensions")
    if world.sep_source == "default":
        return "fallback - nothing to measure"
    return world.sep_source


def _act_lines(plan: Plan) -> List[str]:
    if not plan.acts:
        return ["Nothing to do - every declared dependency is already installed.", ""]
    lines = ["PLAN", ""]
    for op in OP_ORDER:
        lines += [_render(act) for act in plan.acts if act.op == op]
    return lines + [""]


def _render(act: Act) -> str:
    body = "  %-9s %-42s" % (act.op, act.entry)
    if act.op in ("link", "copy"):
        return body + " -> " + (act.target or "")
    if act.pin:
        return (body + " @ " + act.pin.short + ("   (%s)" % act.reason if act.reason else "")).rstrip()
    return (body + ("   " + act.reason if act.reason else "")).rstrip()


def _conflict_lines(plan: Plan) -> List[str]:
    lines: List[str] = []
    for conflict in plan.conflicts:
        lines += conflict_prompt(conflict).splitlines() + [""]
    return lines


def conflict_prompt(conflict: Conflict) -> str:
    lines = ["CONFLICT  %s is wanted at two commits" % _remote_name(conflict.remote), ""]
    lines += ["    %s   %s" % (claim.pin.short, _claimant(claim)) for claim in conflict.claims]
    lines += ["", "  " + _ancestry_sentence(conflict), ""]
    for index, option in enumerate(conflict.options, start=1):
        lines += _option_lines(index, option)
    return "\n".join(lines)


def _remote_name(remote: str) -> str:
    return Pin(remote=remote, commit="").name


def _claimant(claim: Claim) -> str:
    return "required by " + claim.dependent if claim.dependent else "required by this project"


def _ancestry_sentence(conflict: Conflict) -> str:
    if conflict.kind == "ambiguous":
        return "several declared entries share this remote; none matches the commit asked for."
    described = {
        "ancestor": "the first commit is an ancestor of the second.",
        "descendant": "the second commit is an ancestor of the first.",
        "diverged": "the two commits have diverged - neither contains the other.",
    }
    return described.get(conflict.ancestry, "the relationship between the commits is unknown.")


def _option_lines(index: int, option: Option) -> List[str]:
    lines = ["  %d) %-16s %s" % (index, option.id.capitalize(), option.label)]
    lines += ["       -> %s @ %s" % (entry, pin.short) for pin, entry in option.placements]
    if option.risk:
        lines.append("       !  " + option.risk)
    return lines


def plan_json(world: World, plan: Plan, request: Request) -> str:
    document = {
        "version": 1,
        "repo": world.repo,
        "separator": world.sep,
        "separator_source": world.sep_source,
        "layout": request.target or "flat",
        "blockers": list(plan.blockers),
        "warnings": list(plan.warnings),
        "acts": [_act_json(act) for act in plan.acts],
        "conflicts": [_conflict_json(conflict) for conflict in plan.conflicts],
    }
    return json.dumps(document, indent=2, sort_keys=False)


def _act_json(act: Act) -> Dict[str, object]:
    document: Dict[str, object] = {"op": act.op, "entry": act.entry}
    if act.pin:
        document["pin"] = _pin_json(act.pin)
    for key, value in (("dest", act.dest), ("target", act.target), ("reason", act.reason)):
        if value:
            document[key] = value
    return document


def _pin_json(pin: Pin) -> Dict[str, str]:
    return {"remote": pin.remote, "branch": pin.branch, "commit": pin.commit}


def _conflict_json(conflict: Conflict) -> Dict[str, object]:
    return {
        "remote": conflict.remote,
        "kind": conflict.kind,
        "ancestry": conflict.ancestry,
        "involves_root": conflict.involves_root,
        "claims": [
            {"dependent": claim.dependent, "commit": claim.pin.commit} for claim in conflict.claims
        ],
        "options": [
            {"id": option.id, "entries": list(option.entries), "risk": option.risk}
            for option in conflict.options
        ],
    }


def findings_json(findings: Sequence[Finding]) -> str:
    return json.dumps(
        {
            "version": 1,
            "findings": [
                {
                    "level": finding.level,
                    "code": finding.code,
                    "where": finding.where,
                    "message": finding.message,
                }
                for finding in findings
            ],
        },
        indent=2,
    )


# --------------------------------------------------------------------------- #
# 14. Prompting - always /dev/tty                                              #
# --------------------------------------------------------------------------- #


class tty:
    """The bootstrap pipes a `.gitrepo` into our stdin, so a prompt that reads
    stdin would hang or eat the manifest. Ask the terminal directly."""

    @staticmethod
    def available() -> bool:
        return os.path.exists("/dev/tty") and sys.stdin.isatty()

    @staticmethod
    def ask(question: str, answers: Sequence[str], default: Optional[str] = None) -> str:
        with open("/dev/tty", "r+") as terminal:
            while True:
                terminal.write(question)
                terminal.flush()
                given = terminal.readline().strip().lower()
                if not given and default:
                    return default
                if given in answers:
                    return given


def choose_resolutions(conflicts: Sequence[Conflict], out) -> Dict[str, int]:
    """Nothing is preselected: silent version selection is not a feature."""
    choices = {}
    for conflict in conflicts:
        out.write(conflict_prompt(conflict) + "\n")
        numbers = [str(index) for index in range(1, len(conflict.options) + 1)]
        choices[conflict.remote] = int(tty.ask("  [1-%s] " % numbers[-1], numbers)) - 1
    return choices


def confirm(out) -> bool:
    if not tty.available():
        return False
    return tty.ask("Proceed? [Y/n] ", ("y", "n"), default="y") == "y"


# --------------------------------------------------------------------------- #
# 15. apply() - journal and rollback                                           #
# --------------------------------------------------------------------------- #


class Journal:
    """`git checkout -- .` would undo unrelated work, so remember exactly what
    we created and exactly what we overwrote, and undo only that."""

    def __init__(self, root: str):
        self.root = root
        self._created: List[str] = []
        self._original: Dict[str, Optional[str]] = {}

    def creating(self, path: str) -> str:
        self._created.append(path)
        return os.path.join(self.root, path)

    def modifying(self, path: str) -> str:
        absolute = os.path.join(self.root, path)
        if path not in self._original:
            self._original[path] = _read_text(absolute)
        return absolute

    def rollback(self) -> None:
        for path in reversed(self._created):
            _remove(os.path.join(self.root, path))
        for path, content in self._original.items():
            absolute = os.path.join(self.root, path)
            if content is None:
                _remove(absolute)
            else:
                _write_text(absolute, content)


def _read_text(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _remove(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def apply(world: World, plan: Plan, staged: Staged) -> Tuple[str, ...]:
    """Acts arrive in OP_ORDER, which is also the only safe order to run them:
    real installs, then the entries pointing at them, then the manifest."""
    journal = Journal(world.root)
    touched: List[str] = []
    try:
        for act in plan.acts:
            touched += APPLIERS.get(act.op, _apply_nothing)(world, act, staged, journal)
    except BaseException:
        journal.rollback()
        raise
    git.add(touched, cwd=world.root)
    return tuple(touched)


def _apply_nothing(world: World, act: Act, staged: Staged, journal: Journal) -> List[str]:
    return []


def _apply_install(world: World, act: Act, staged: Staged, journal: Journal) -> List[str]:
    destination = journal.creating(act.dest)
    shutil.copytree(
        staged.trees[act.pin], destination, ignore=shutil.ignore_patterns(".git"), symlinks=True
    )
    gitrepo.write(os.path.join(destination, GITREPO), act.pin, parent=world.head)
    return [act.dest]


def _apply_link(world: World, act: Act, staged: Staged, journal: Journal) -> List[str]:
    path = journal.creating(act.entry)
    os.makedirs(os.path.dirname(path) or world.root, exist_ok=True)
    os.symlink(act.target, path)
    return [act.entry]


def _apply_copy(world: World, act: Act, staged: Staged, journal: Journal) -> List[str]:
    path = journal.creating(act.entry)
    shutil.copytree(os.path.join(world.root, act.dest), path, symlinks=True)
    return [act.entry]


def _apply_record(world: World, act: Act, staged: Staged, journal: Journal) -> List[str]:
    path = act.dest
    absolute = journal.modifying(path) if os.path.exists(os.path.join(world.root, path)) else journal.creating(path)
    gitrepo.write_manifest_record(absolute, act.pin)
    return [path]


def _apply_npm(world: World, act: Act, staged: Staged, journal: Journal) -> List[str]:
    """Never touches package-lock.json - that is `npm install`'s job."""
    package, wanted = act.entry.rsplit("@", 1)
    path = journal.modifying("package.json")
    document = json.loads(_read_text(path) or "{}")
    document.setdefault("dependencies", {})[package] = wanted
    _write_text(path, json.dumps(document, indent=2) + "\n")
    return ["package.json"]


APPLIERS = {
    "install": _apply_install,
    "link": _apply_link,
    "copy": _apply_copy,
    "record": _apply_record,
    "npm": _apply_npm,
}


# --------------------------------------------------------------------------- #
# 16. check() - pure                                                           #
# --------------------------------------------------------------------------- #

LEVEL_ORDER = ("FAIL", "WARN", "INFO")


def check(world: World) -> Tuple[Finding, ...]:
    findings = list(_edge_findings(world)) + list(_entry_findings(world))
    return tuple(sorted(findings, key=lambda f: (LEVEL_ORDER.index(f.level), f.where, f.code)))


def _edge_findings(world: World) -> Iterable[Finding]:
    declared = declarations.backing_paths(world)
    for edge in world.edges:
        path = _sibling_path(edge)
        entry = world.entries.get(path)
        backing = entry.backing if entry else None
        if backing is None:
            yield _missing_edge(edge, path, entry)
        elif backing not in declared:
            yield _undeclared_edge(edge, path, backing)
        else:
            for finding in _pin_notes(world, edge, path, backing):
                yield finding


def _sibling_path(edge: Edge) -> str:
    home = os.path.dirname(edge.dependent)
    return os.path.join(home, edge.entry_name) if home else edge.entry_name


def _missing_edge(edge: Edge, path: str, entry: Optional[Entry]) -> Finding:
    return Finding(
        level="FAIL",
        code="missing-edge",
        where=path,
        message="%s expects a sibling named %s and %s. Install %s, or declare your own"
        " resolution at the repo root."
        % (
            os.path.basename(edge.dependent),
            edge.entry_name,
            "it is dangling" if entry else "nothing is there",
            edge.pin.name,
        ),
    )


def _undeclared_edge(edge: Edge, path: str, backing: str) -> Finding:
    """The declaration invariant. It compares no remotes and no commits - only
    that a resolution was declared, which is what frees the pin notes below to
    stay informational."""
    return Finding(
        level="FAIL",
        code="undeclared-edge",
        where=path,
        message="%s resolves to %s, which no root entry declares as a release dependency."
        " That is an implicit dependency: give it a root entry so it ships in your manifest."
        % (path, backing),
    )


def _pin_notes(world: World, edge: Edge, path: str, backing: str) -> Iterable[Finding]:
    """You took ownership of the resolution. Different commit, different
    remote, or an entirely hand-written implementation are all legitimate."""
    resolved = world.installs[backing].pin
    if resolved.remote != edge.pin.remote:
        yield Finding(
            level="INFO",
            code="remote-differs",
            where=path,
            message="%s asks for %s; you resolved it to %s"
            % (os.path.basename(edge.dependent), edge.pin.remote, resolved.remote),
        )
    elif resolved.commit != edge.pin.commit:
        yield Finding(
            level="INFO",
            code="pin-differs",
            where=path,
            message="%s asks for %s; you declare %s"
            % (os.path.basename(edge.dependent), edge.pin.short, resolved.short),
        )


def _entry_findings(world: World) -> Iterable[Finding]:
    for name, entry in sorted(declarations.prefixed_entries(world).items()):
        if declarations.backing_install(world, entry) is None:
            yield _dangling(name, entry)
    for lowered, names in sorted(_by_lowercase(world).items()):
        if len(names) > 1:
            yield _case_collision(names)


def _dangling(name: str, entry: Entry) -> Finding:
    reason = "does not resolve" if entry.backing is None else "has no .gitrepo"
    return Finding(
        level="WARN",
        code="dangling-entry",
        where=name,
        message="%s is named like a release dependency but %s. The name signals intent,"
        " so this is either an unfinished install or a leftover." % (name, reason),
    )


def _by_lowercase(world: World) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for name in declarations.root_entries(world):
        grouped.setdefault(name.lower(), []).append(name)
    return grouped


def _case_collision(names: Sequence[str]) -> Finding:
    return Finding(
        level="WARN",
        code="case-collision",
        where=sorted(names)[0],
        message="%s differ only by case. They are the same entry on macOS and two"
        " different ones on Linux CI." % " and ".join(sorted(names)),
    )


def worst(findings: Sequence[Finding]) -> str:
    for level in LEVEL_ORDER:
        if any(finding.level == level for finding in findings):
            return level
    return "OK"


def render_findings(findings: Sequence[Finding]) -> str:
    if not findings:
        return "check: no problems found"
    return "\n".join(
        "%-5s %-40s %s" % (finding.level, finding.where, finding.message) for finding in findings
    )


# --------------------------------------------------------------------------- #
# 17. list, extract, remove                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Listing:
    entry: str
    kind: str  # "release" | "development" | "vendored"
    path: str
    pin: Optional[Pin]


def listing(world: World) -> Tuple[Listing, ...]:
    """Classification is implicit in naming, so a one-command view of what the
    tree currently means is the cheapest fix for 'naming is promotion'."""
    declared = declarations.backing_paths(world)
    rows = [
        Listing(entry=declared.get(path, ""), kind=declarations.classify(world, install),
                path=path, pin=install.pin)
        for path, install in sorted(world.installs.items())
        if not declarations.is_machinery(path)
    ]
    rows += [Listing(entry="", kind="vendored", path=path, pin=_vendored_pin(world, path))
             for path in world.vendored
             if not declarations.is_machinery(path)]
    return tuple(rows)


def _vendored_pin(world: World, path: str) -> Optional[Pin]:
    return gitrepo.read(os.path.join(world.root, path, GITREPO))


def render_listing(rows: Sequence[Listing]) -> str:
    if not rows:
        return "no suede dependencies found"
    header = "%-12s %-38s %-38s %s" % ("KIND", "ENTRY", "PATH", "PIN")
    body = [
        "%-12s %-38s %-38s %s"
        % (row.kind, row.entry or "-", row.path, row.pin.short if row.pin else "-")
        for row in rows
    ]
    return "\n".join([header] + body)


def listing_json(rows: Sequence[Listing]) -> str:
    return json.dumps(
        {
            "version": 1,
            "dependencies": [
                {
                    "entry": row.entry,
                    "kind": row.kind,
                    "path": row.path,
                    "pin": _pin_json(row.pin) if row.pin else None,
                }
                for row in rows
            ],
        },
        indent=2,
    )


def extract(world: World) -> Tuple[str, ...]:
    """Write `release/.suede/.dependencies/` from the classification. A pure
    application has no `release/`, publishes nothing, and so needs none of it."""
    if not world.has_release:
        return ()
    destination = os.path.join(world.root, RELEASE_DIR, MANIFEST_DIR)
    os.makedirs(destination, exist_ok=True)
    written = _write_records(world, destination) + _copy_dependency_files(world, destination)
    return tuple(written) + _prune_stale_records(world, destination)


def _write_records(world: World, destination: str) -> List[str]:
    for name, install in sorted(declarations.by_name(world).items()):
        gitrepo.write_manifest_record(os.path.join(destination, name + GITREPO), install.pin)
    return [name + GITREPO for name in sorted(declarations.by_name(world))]


def _copy_dependency_files(world: World, destination: str) -> List[str]:
    written = []
    if world.npm:
        _write_text(
            os.path.join(destination, "package.json"),
            json.dumps({"dependencies": world.npm}, indent=2) + "\n",
        )
        written.append("package.json")
    requirements = os.path.join(world.root, "requirements.txt")
    if os.path.isfile(requirements):
        shutil.copyfile(requirements, os.path.join(destination, "requirements.txt"))
        written.append("requirements.txt")
    return written


def _prune_stale_records(world: World, destination: str) -> Tuple[str, ...]:
    expected = {name + GITREPO for name in declarations.by_name(world)}
    removed = []
    for filename in sorted(os.listdir(destination)):
        if filename.endswith(GITREPO) and filename not in expected:
            os.remove(os.path.join(destination, filename))
            removed.append(filename)
    return tuple(removed)


@dataclass(frozen=True)
class Divergence:
    entry: str
    path: str
    pin: Pin
    changed: Tuple[str, ...]


def divergence_targets(world: World) -> Tuple[Tuple[str, Install], ...]:
    """Release dependencies only.

    A release dependency must match its pinned commit - that is what makes the
    shipped pointer honest. A vendored dependency exists precisely *because* it
    diverges, and it ships as source, so the rule would be backwards there.
    Development dependencies ship nothing at all.
    """
    return tuple(sorted(declarations.by_name(world).items()))


def diff(world: World, use_cache: bool = True) -> Tuple[Divergence, ...]:
    diverged = []
    for entry, install in divergence_targets(world):
        pinned = cache.fetch(world.root, install.pin, use_cache)
        changed = _changed_files(pinned, os.path.join(world.root, install.path))
        if changed:
            diverged.append(
                Divergence(entry=entry, path=install.path, pin=install.pin, changed=changed)
            )
    return tuple(diverged)


def _changed_files(pinned: str, local: str) -> Tuple[str, ...]:
    """`.gitrepo` is excluded: it is local metadata and always differs."""
    before, after = _digests(pinned), _digests(local)
    return tuple(sorted(set(before) ^ set(after) | {
        path for path in set(before) & set(after) if before[path] != after[path]
    }))


def _digests(directory: str) -> Dict[str, str]:
    digests = {}
    for parent, subdirs, filenames in os.walk(directory):
        subdirs[:] = sorted(name for name in subdirs if name != ".git")
        for filename in filenames:
            if filename == GITREPO:
                continue
            path = os.path.join(parent, filename)
            digests[os.path.relpath(path, directory)] = _digest(path)
    return digests


def _digest(path: str) -> str:
    if os.path.islink(path):
        return "link:" + os.readlink(path)
    marks = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            marks.update(block)
    return marks.hexdigest()


def render_divergence(diverged: Sequence[Divergence]) -> str:
    if not diverged:
        return "diff: every release dependency matches its pinned commit"
    lines = []
    for divergence in diverged:
        lines.append(
            "%s has local modifications relative to %s (%s)"
            % (divergence.entry, divergence.pin.short, divergence.pin.remote)
        )
        lines += ["    " + path for path in divergence.changed]
    lines += [
        "",
        "A release dependency ships as a pointer, so the pointer has to be honest.",
        "Either revert these changes, upstream them (.suede/core/upstream), or vendor",
        "the dependency with .suede/core/vendor.sh so the code actually ships.",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class Removal:
    entry: str
    removed: Tuple[str, ...]
    orphans: Tuple[str, ...]


def plan_removal(world: World, entry: str) -> Removal:
    """Flattening creates orphans: remove B and C stays declared, possibly
    unreferenced. Report them - this project may have started importing C
    directly, and no tool here can see imports."""
    install = declarations.by_name(world).get(entry)
    if install is None:
        raise Usage("%s is not a declared release dependency - `suede list` shows what is" % entry)
    removed = [entry, install.path] + _records_for(world, entry)
    return Removal(entry=entry, removed=tuple(removed), orphans=_orphans_without(world, install))


def _records_for(world: World, entry: str) -> List[str]:
    record = os.path.join(RELEASE_DIR, MANIFEST_DIR, entry + GITREPO)
    return [record] if entry in world.records else []


def _orphans_without(world: World, removed: Install) -> Tuple[str, ...]:
    still_wanted = {
        edge.pin for edge in world.edges if edge.dependent != removed.path
    }
    return tuple(
        sorted(
            name
            for name, install in declarations.by_name(world).items()
            if install.path != removed.path and install.pin not in still_wanted
        )
    )


# --------------------------------------------------------------------------- #
# 18. CLI                                                                      #
# --------------------------------------------------------------------------- #


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="suede", description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo-name", help="override $repo detection")
    common.add_argument("--separator", help="override $SEP for this project's own entries")
    commands = parser.add_subparsers(dest="command")
    _install_parser(commands, common)
    _audit_parsers(commands, common)
    return parser


def _install_parser(commands, common: argparse.ArgumentParser) -> None:
    install = commands.add_parser("install", parents=[common], help="install a suede dependency")
    source = install.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo", help="OWNER/REPO, or any git remote URL")
    source.add_argument("--gitrepo", help="path to a .gitrepo file, or - for stdin")
    install.add_argument("--branch", default=RELEASE_BRANCH, help="branch to install from")
    install.add_argument("--at", help="install this commit instead of the branch tip")
    install.add_argument("-r", dest="repo", help=argparse.SUPPRESS)
    install.add_argument("--name", help="override the entry name")
    install.add_argument(
        "--commit-suffix", action="store_true", help="pin the entry name to the commit too"
    )
    install.add_argument("--target", default="", help="relocate the real install (at your own risk)")
    install.add_argument("--link-mode", choices=("symlink", "copy"), default="symlink")
    install.add_argument("--on-conflict", choices=("ask", "coexist", "unify-newest", "defer"))
    install.add_argument("--no-npm", action="store_true", help="do not merge npm dependencies")
    install.add_argument("--no-cache", action="store_true", help="ignore .git/suede-cache")
    install.add_argument("--dry-run", action="store_true", help="plan and announce, change nothing")
    install.add_argument("--plan-json", action="store_true", help="emit the plan as JSON")
    install.add_argument("--yes", action="store_true", help="accept the plan without asking")
    install.add_argument("--commit", action="store_true", help="commit the result")


def _audit_parsers(commands, common: argparse.ArgumentParser) -> None:
    check_command = commands.add_parser("check", parents=[common], help="audit the tree")
    check_command.add_argument("--plan-json", action="store_true")
    list_command = commands.add_parser("list", parents=[common], help="show every dependency")
    list_command.add_argument("--json", action="store_true")
    commands.add_parser("extract", parents=[common], help="write release/.suede/.dependencies/")
    diff_command = commands.add_parser(
        "diff", parents=[common], help="show release dependencies that differ from their pin"
    )
    diff_command.add_argument("--no-cache", action="store_true")
    remove = commands.add_parser("remove", parents=[common], help="drop a declared entry")
    remove.add_argument("entry")
    remove.add_argument("--yes", action="store_true")


def _open_world(args) -> Tuple[World, Tuple[str, ...]]:
    root = git.toplevel()
    os.chdir(root)
    repo, notes = context.repo_name(root, args.repo_name)
    separator, source = context.separator(root, repo, args.separator)
    return scan(root, repo, separator, source), notes


def remote_from(repo: str) -> str:
    """Any git remote works: `git clone` takes the URL verbatim and a
    dependency's name is just its basename. OWNER/REPO is a GitHub shorthand,
    not a restriction."""
    if "://" in repo or repo.startswith("git@") or os.path.isdir(repo):
        return repo
    if repo.count("/") == 1 and all(part for part in repo.split("/")):
        return "https://github.com/" + repo
    raise Usage("--repo wants OWNER/REPO or a git remote URL (got %s)" % repo)


def _requested_pin(args) -> Pin:
    if args.gitrepo:
        return _pin_from_gitrepo(args.gitrepo)
    remote = remote_from(args.repo)
    commit = args.at or git.resolve_branch(remote, args.branch)
    return Pin(remote=remote, commit=commit, branch=args.branch)


def _pin_from_gitrepo(source: str) -> Pin:
    path = source if source != "-" else _stdin_to_temporary_file()
    pin = gitrepo.read(path)
    if pin is None:
        raise Usage("%s is not a readable .gitrepo (needs `remote` and `commit`)" % source)
    return pin


def _stdin_to_temporary_file() -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".gitrepo", delete=False, encoding="utf-8")
    handle.write(sys.stdin.read())
    handle.close()
    return handle.name


def _request(args) -> Request:
    return Request(
        pins=(_requested_pin(args),),
        name=args.name,
        target=args.target.strip("/"),
        link_mode=args.link_mode,
        commit_suffix=args.commit_suffix,
    )


def _policy(args) -> Policy:
    fallback = "ask" if tty.available() else "defer"
    return Policy(on_conflict=args.on_conflict or fallback, npm=not args.no_npm)


def install_command(args) -> int:
    world, notes = _open_world(args)
    request = _request(args)
    policy = _policy(args)
    staged = stage(world, request.pins, use_cache=not args.no_cache)
    proposal = _propose(world, request, policy, staged)
    if args.plan_json:
        print(plan_json(world, proposal, request))
        return _plan_exit_code(proposal)
    _report(notes, announce(world, proposal, request, context.evidence(world.root, world.sep, world.sep_source)))
    if proposal.blockers or proposal.conflicts or args.dry_run or not proposal.mutates:
        return _plan_exit_code(proposal)
    if not (args.yes or confirm(sys.stdout)):
        return Exit.UNRESOLVED
    return _carry_out(world, proposal, staged, args)


def _propose(world: World, request: Request, policy: Policy, staged: Staged) -> Plan:
    proposal = plan(world, request, policy, staged.manifests, staged.ancestry)
    if not (proposal.conflicts and policy.on_conflict == "ask" and tty.available()):
        return proposal
    chosen = Policy(
        on_conflict="ask",
        npm=policy.npm,
        choices=choose_resolutions(proposal.conflicts, sys.stdout),
    )
    return plan(world, request, chosen, staged.manifests, staged.ancestry)


def _plan_exit_code(proposal: Plan) -> int:
    if proposal.blockers:
        return Exit.PRECONDITION
    return Exit.UNRESOLVED if proposal.conflicts else Exit.OK


def _report(notes: Sequence[str], text: str) -> None:
    for note in notes:
        sys.stderr.write("warning: %s\n" % note)
    print(text)


def _carry_out(world: World, proposal: Plan, staged: Staged, args) -> int:
    apply(world, proposal, staged)
    _persist_separator(world)
    if args.commit:
        print("committed %s" % git.commit(_commit_message(proposal), cwd=world.root))
    return _verify(world)


def _persist_separator(world: World) -> None:
    """Written so later installs resolve the same separator without measuring."""
    path = os.path.join(world.root, SEPARATOR_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_text(path, world.sep + "\n")
    git.add([os.path.relpath(path, world.root)], cwd=world.root)


def _commit_message(proposal: Plan) -> str:
    installed = [act.entry for act in proposal.acts if act.op == "install"]
    return "Add suede dependencies: " + ", ".join(installed) if installed else "Update suede dependencies"


def _verify(world: World) -> int:
    """A failure here is a bug in apply, not in the tree."""
    findings = check(scan(world.root, world.repo, world.sep, world.sep_source))
    failures = [finding for finding in findings if finding.level == "FAIL"]
    if failures:
        sys.stderr.write("install finished but check failed - this is a bug in suede:\n")
        sys.stderr.write(render_findings(failures) + "\n")
        return Exit.CHECK_FAILED
    return Exit.OK


def diff_command(args) -> int:
    world, notes = _open_world(args)
    diverged = diff(world, use_cache=not args.no_cache)
    _report(notes, render_divergence(diverged))
    return Exit.CHECK_FAILED if diverged else Exit.OK


def check_command(args) -> int:
    world, notes = _open_world(args)
    findings = check(world)
    _report(notes, findings_json(findings) if args.plan_json else render_findings(findings))
    return Exit.CHECK_FAILED if worst(findings) == "FAIL" else Exit.OK


def list_command(args) -> int:
    world, notes = _open_world(args)
    rows = listing(world)
    _report(notes, listing_json(rows) if args.json else render_listing(rows))
    return Exit.OK


def extract_command(args) -> int:
    world, _ = _open_world(args)
    if not world.has_release:
        print("no release/ directory - nothing to extract")
        return Exit.OK
    written = extract(world)
    print("extract: wrote %d entries into %s" % (len(written), os.path.join(RELEASE_DIR, MANIFEST_DIR)))
    return Exit.OK


def remove_command(args) -> int:
    world, _ = _open_world(args)
    removal = plan_removal(world, args.entry)
    print(_render_removal(removal))
    if not (args.yes or confirm(sys.stdout)):
        return Exit.UNRESOLVED
    for path in removal.removed:
        _remove(os.path.join(world.root, path))
    git.add(list(removal.removed), cwd=world.root)
    return Exit.OK


def _render_removal(removal: Removal) -> str:
    lines = ["remove %s" % removal.entry] + ["  delete   %s" % path for path in removal.removed]
    if removal.orphans:
        lines += [
            "",
            "  These stay declared and are now referenced by nothing. They are not deleted:",
            "  you may have started importing them directly, and no tool here can see imports.",
        ] + ["    %s" % orphan for orphan in removal.orphans]
    return "\n".join(lines)


COMMANDS = {
    "install": install_command,
    "check": check_command,
    "diff": diff_command,
    "list": list_command,
    "extract": extract_command,
    "remove": remove_command,
}


def main(argv: Sequence[str]) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return Exit.USAGE
    try:
        return COMMANDS[args.command](args)
    except SuedeError as failure:
        sys.stderr.write("suede: %s\n" % failure)
        return failure.code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
