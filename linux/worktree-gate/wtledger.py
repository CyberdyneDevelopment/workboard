#!/usr/bin/env python3
"""Append-only ledger of every worktree lifecycle event, plus the git helpers
the MCP server and the gate hook both need.

The ledger is the record. `.workboard/worktrees.json` becomes a derived export
(see export_claims) rather than a hand-maintained file, so ownership stops being
asserted and starts being observed.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
# The outermost scope: where the gate is installed. Nothing above this is ever touched.

# A directory containing .workboard/ IS a scope. It governs itself: its own journal, its
# own repos, its own worktrees. The scope that contains it must ignore it entirely --
# otherwise two boards report the same worktree and can disagree about who owns it.
SCOPE_MARKER = ".workboard"


def is_scope(path):
    return os.path.isdir(os.path.join(path, SCOPE_MARKER))


def workspace_of(path):
    """The OUTERMOST directory containing .workboard, walking up from `path`, bounded at $HOME.

    This is what lets ONE installation serve every location instead of only the tree it happens
    to sit in. A workspace is defined by the marker on disk, not by where the gate is installed,
    so the same binary governs any number of workspaces and each keeps its own board and its own
    journal.

    Outermost, not nearest: the nearest .workboard is the SCOPE (a sub-scope governs itself);
    the outermost is the workspace, which bounds the walk and roots relative paths. Both
    behaviours are preserved -- only the source of the boundary changes.

    Returns None outside any workspace. That is not a failure: the gate simply has no business
    there and says so, rather than guessing a boundary.
    """
    found, p = None, os.path.abspath(path)
    while p.startswith(HOME) and p != HOME and p != os.path.dirname(p):
        if is_scope(p):
            found = p
        p = os.path.dirname(p)
    return found


# Resolution order, no fallback: an explicit override, then the caller's location. The install
# path is deliberately NOT consulted -- deriving the workspace from where the code lives is the
# thing that tied one install to one tree.
WORKSPACE = os.environ.get("WORKTREE_GATE_WORKSPACE") or workspace_of(os.getcwd())

def scope_of(path):
    """Nearest enclosing scope for a path, bounded at that path's own workspace.

    The bound comes from the location, not from where the gate is installed, so one
    installation governs any number of workspaces and each keeps its own board and journal."""
    path = os.path.abspath(path)
    bound = WORKSPACE if WORKSPACE and (path == WORKSPACE or path.startswith(WORKSPACE + os.sep)) \
        else workspace_of(path)
    if bound is None:
        raise GateError(f"{path} is not inside any workspace — no .workboard was found above it. "
                        f"The gate acts only inside a workspace.")
    while path != bound:
        if is_scope(path):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return bound


def ledger_of(scope):
    return os.path.join(scope, ".worktree-gate", "ledger.jsonl")


def worktree_dir_of(scope):
    return os.path.join(scope, ".worktrees")


def nested_scopes(scope):
    """Immediate children of `scope` that are scopes in their own right."""
    return sorted(d for d in os.listdir(scope)
                  if os.path.isdir(os.path.join(scope, d)) and d != SCOPE_MARKER
                  and is_scope(os.path.join(scope, d)))


# Back-compat aliases for the root scope (the gate's own installation).
ROOT = WORKSPACE
LEDGER = ledger_of(WORKSPACE)
WORKTREE_DIR = worktree_dir_of(WORKSPACE)

ACTIONS = ("create", "pull", "sync", "merge", "prune", "revive", "board",
           "commit", "denied")


class GateError(Exception):
    """Refusal. The message is shown to the caller verbatim."""


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append(record, scope=None):
    """One JSON object per line. Append only, never rewrite.

    Written to the journal of the scope that owns the work, never to a parent's."""
    path = ledger_of(scope or WORKSPACE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def read_ledger(scope=None):
    path = ledger_of(scope or WORKSPACE)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for n, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                # Why: a silently skipped line is a worker who believes it reported
                # and is invisible. The ledger names the bad line instead.
                raise GateError(f"ledger.jsonl line {n} is not valid JSON: {exc}") from exc
    return rows


def record(action, session, description, repo, worktree, **extra):
    if action not in ACTIONS:
        raise GateError(f"unknown action {action!r}; expected one of {', '.join(ACTIONS)}")
    if not (session or "").strip():
        raise GateError("session is required — say which session is acting")
    if not (description or "").strip():
        raise GateError("description is required — say why, in a sentence")
    scope = scope_of(os.path.join(WORKSPACE, repo)) if repo else WORKSPACE
    entry = {"ts": now(), "action": action, "session": session.strip(),
             "description": description.strip(), "repo": repo, "worktree": worktree,
             "scope": os.path.relpath(scope, WORKSPACE)}
    entry.update(extra)
    return append(entry, scope)


# --------------------------------------------------------------------------- git

def git(args, cwd, check=True):
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=180)
    if check and proc.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed in {cwd}:\n"
                        f"{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def repo_path(repo, scope=None):
    scope = scope or WORKSPACE
    path = os.path.join(scope, repo)
    if not os.path.isdir(os.path.join(path, ".git")):
        alt = os.path.join(scope, "docs", repo)      # accept a bare docs-project name
        if os.path.isdir(os.path.join(alt, ".git")):
            path = alt
    if not os.path.isdir(os.path.join(path, ".git")):
        raise GateError(f"{repo!r} is not a git repo in scope {os.path.relpath(scope, WORKSPACE) or '.'}. "
                        f"Repos here: {', '.join(known_repos(scope)) or '(none)'}")
    if is_scope(path):
        raise GateError(f"{repo!r} has its own .workboard — it governs itself. "
                        f"Run the gate from that scope instead.")
    return path


def known_repos(scope=None):
    """Git repos owned by this scope, including documentation projects under docs/.

    A child with its own .workboard is NOT owned by this scope -- it governs itself,
    journals for itself, and is skipped entirely.

    Why docs/ is walked one level deeper: a documentation project is a git repository like
    any other and gets worktrees like any other. Moving it under docs/ so the router can
    enumerate it must not remove it from the gate's reach."""
    scope = scope or WORKSPACE
    skip = set(nested_scopes(scope))
    repos = [r for r in os.listdir(scope)
             if r not in skip and os.path.isdir(os.path.join(scope, r, ".git"))]
    docs = os.path.join(scope, "docs")
    if os.path.isdir(docs):
        repos += [os.path.join("docs", d) for d in os.listdir(docs)
                  if os.path.isdir(os.path.join(docs, d, ".git"))]
    # Dedupe by REAL path. A documentation project may be a symlink into a checkout that also
    # sits at the scope root -- which is the right way to enumerate one without moving a live
    # session out from under it. Counted twice, the same repo could take two worktrees under
    # two names, and status would double-count them.
    seen, out = {}, []
    for r in repos:
        real = os.path.realpath(os.path.join(scope, r))
        if real in seen:
            # Prefer the shallower name: the real location, not the alias.
            if r.count(os.sep) < seen[real].count(os.sep):
                out[out.index(seen[real])] = r
                seen[real] = r
            continue
        seen[real] = r
        out.append(r)
    return sorted(out)


def current_branch(path):
    return git(["rev-parse", "--abbrev-ref", "HEAD"], path)


def is_clean(path):
    return git(["status", "--porcelain"], path) == ""


def live_worktrees(scope=None):
    """(repo, name, branch, path) for worktrees this scope owns.

    Ownership follows the REPO, not the worktree's path -- worktrees live in several
    places (`<workspace>/.worktrees/`, `<repo>/.worktrees/`, `<repo>/.claude-worktrees/`)
    and all of them belong to whoever owns the repo. Excluded: each repo's own main
    checkout, and anything sitting inside a nested scope, which governs itself."""
    scope = scope or WORKSPACE
    nested = [os.path.join(scope, d) + os.sep for d in nested_scopes(scope)]
    rows = []
    for repo in known_repos(scope):
        repo_dir = os.path.join(scope, repo)
        out = git(["worktree", "list", "--porcelain"], repo_dir, check=False)
        current = {}
        for line in out.splitlines() + [""]:
            if line.startswith("worktree "):
                current = {"path": line.split(" ", 1)[1]}
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
            elif not line.strip() and current:
                path = os.path.abspath(current["path"])
                is_main = path == os.path.abspath(repo_dir)
                in_nested = any(path.startswith(n) for n in nested)
                if not is_main and not in_nested:
                    rows.append((repo, os.path.basename(path),
                                 current.get("branch", "-"), path))
                current = {}
    return rows


def find_worktree(repo, name, scope=None):
    """Locate an existing worktree by repo+name within a scope.

    Why look it up rather than build the path: worktrees legitimately live in several
    places (workspace-level, repo-nested), so a guessed path is wrong for some of them
    and would fail loudly on work that is perfectly valid."""
    for r, n, branch, path in live_worktrees(scope):
        if r == repo and n == name:
            return {"repo": r, "name": n, "branch": branch, "path": path}
    raise GateError(f"no worktree {name!r} in {repo}. "
                    f"Known: {', '.join(n for r, n, _, _ in live_worktrees(scope) if r == repo) or '(none)'}")


def creation_of(repo, worktree, scope=None):
    """The create record for a worktree, or None. Later creates win (re-cut)."""
    match = [r for r in read_ledger(scope)
             if r.get("action") == "create" and r.get("repo") == repo
             and r.get("worktree") == worktree and r.get("ok", True)]
    return match[-1] if match else None


def export_claims(path=None):
    """Regenerate .workboard/worktrees.json from the ledger. Derived, not maintained."""
    path = path or os.path.join(ROOT, ".workboard", "worktrees.json")
    pruned = {(r["repo"], r["worktree"]) for r in read_ledger()
              if r.get("action") == "prune" and r.get("ok", True)}
    claims = {"_comment": "GENERATED from .worktree-gate/ledger.jsonl by "
                          "`python3 .worktree-gate/wtledger.py export`. Do not hand-edit — "
                          "cut and prune worktrees through the worktree-gate MCP and this "
                          "regenerates. An em dash means no session has ever claimed it."}
    for row in read_ledger():
        if row.get("action") != "create" or not row.get("ok", True):
            continue
        key = f"{row['repo']}/{row['worktree']}"
        if (row["repo"], row["worktree"]) in pruned:
            continue
        claims[key] = row["session"]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(claims, handle, indent=1)
        handle.write("\n")
    return path, len(claims) - 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        where, count = export_claims()
        print(f"wrote {where} ({count} claims)")
    else:
        for row in read_ledger():
            print(f"{row['ts']}  {row['action']:<7} {row.get('repo','-')}/"
                  f"{row.get('worktree','-')}  {row['session']}  {row['description']}")
