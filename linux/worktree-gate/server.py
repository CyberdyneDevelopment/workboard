#!/usr/bin/env python3
"""worktree-gate — the only sanctioned path to worktree lifecycle operations.

A dependency-free stdio MCP server. Every mutation is recorded in an append-only
ledger with who did it and why, before it is reported as done. Raw `git worktree`
/ `git merge` / `git pull` from Bash is blocked by the companion PreToolUse hook
(gate.py), so this is not a convenience wrapper — it is the path.

stdout carries JSON-RPC only. Everything else goes to stderr.
"""
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wtledger as L  # noqa: E402

SERVER = {"name": "worktree-gate", "version": "1.0.0"}
DEFAULT_PROTOCOL = "2024-11-05"

WHO = {
    "session": {"type": "string", "description":
                "Who is acting — your session name exactly as ListAgents prints it, "
                "or the role name you were briefed with. Required."},
    "description": {"type": "string", "description":
                    "Why, in one sentence. Goes in the ledger and is read by the "
                    "orchestrator. Required."},
    "task": {"type": "string", "description":
             "The task this work belongs to. Required. Either a board item id from the "
             "scope's .workboard/items.json (e.g. swallowed-factory-failure) or an issue "
             "key (e.g. NVS-87). Validated against the board when one exists."},
}


def tool(name, summary, props, required, mutates=True):
    """Mutating tools demand who and why. Read-only tools must not — friction on a
    read is how a tool stops being used, and there is nothing to attribute."""
    props = dict(WHO, **props) if mutates else dict(props)
    return {"name": name, "description": summary,
            "inputSchema": {"type": "object", "properties": props,
                            "required": (["session", "description", "task"] if mutates else []) + required}}


TOOLS = [
    tool("worktree_create",
         "Cut a new worktree from the main checkout's current LOCAL HEAD and record who "
         "cut it and why. Refuses if the main checkout is dirty. Never bases on origin/*.",
         {"repo": {"type": "string", "description": "Repo directory name under the workspace root."},
          "task": {"type": "string", "description":
                   "Task name. Branch is feature/<task>; worktree is <task>-<repo> unless "
                   "'worktree' overrides it. Use the SAME task name across repos for one cycle."},
          "worktree": {"type": "string", "description":
                       "Optional worktree directory name. Defaults to <task>-<repo>."}},
         ["repo", "task"]),
    tool("worktree_pull",
         "Fast-forward pull inside a worktree, recorded.",
         {"repo": {"type": "string"}, "worktree": {"type": "string"}},
         ["repo", "worktree"]),
    tool("worktree_sync",
         "Fetch and merge origin/<branch> into a repo's MAIN checkout. For a main checkout that "
         "has diverged from its remote -- the case worktree_pull cannot reach, because it only "
         "resolves worktrees. Never forces, rebases or resets: an unmergeable divergence is a "
         "conflict for a person.",
         {"repo": {"type": "string"},
          "branch": {"type": "string", "description": "Remote branch to merge, without the origin/ prefix."}},
         ["repo", "branch"]),
    tool("worktree_merge",
         "Merge a worktree's branch back into the branch it was cut from, then push. "
         "The merge target comes from the create record — if there is none, you must "
         "name 'into' explicitly; it is never guessed.",
         {"repo": {"type": "string"}, "worktree": {"type": "string"},
          "into": {"type": "string", "description":
                   "Merge target. Only needed when the worktree has no create record."},
          "push": {"type": "boolean", "description": "Push after merging. Default true."}},
         ["repo", "worktree"]),
    tool("worktree_prune",
         "Remove a worktree and delete its branch, recorded. Refuses while the worktree "
         "has uncommitted changes or unmerged commits unless force is set.",
         {"repo": {"type": "string"}, "worktree": {"type": "string"},
          "force": {"type": "boolean", "description":
                    "Discard the refusal on unmerged/dirty state. Recorded as forced."}},
         ["repo", "worktree"]),
    tool("worktree_adopt",
         "Record live worktrees the journal has never seen — worktrees created outside the "
         "gate (e.g. by the EnterWorktree tool). Lists them first; pass confirm=true to record. "
         "Never invents who originally cut them.",
         {"scope": {"type": "string"},
          "confirm": {"type": "boolean", "description": "Actually record. Without it, this only lists."},
          "only": {"type": "array", "items": {"type": "string"},
                   "description": "Adopt just these worktree names. Omit for all in scope."}},
         []),
    tool("item_subscribe",
         "Subscribe this session to a board item. When anyone updates it you are notified. "
         "Delivery is at your next worktree-gate call -- this server cannot interrupt a "
         "running session.",
         {"item": {"type": "string", "description": "Board item id."},
          "scope": {"type": "string"}},
         ["item"]),
    tool("item_unsubscribe", "Stop being notified about a board item.",
         {"item": {"type": "string"}, "scope": {"type": "string"}}, ["item"]),
    tool("item_update",
         "Change a board item and notify its subscribers. The ONLY sanctioned way to update "
         "the board: direct edits to items.json are blocked. Settable: status, owner, actual, "
         "fix, sev. Structure (title, why, evidence, links) belongs to the orchestrating "
         "session and is refused here.",
         {"item": {"type": "string"},
          "changes": {"type": "object", "description":
                      'e.g. {"status": "done", "actual": ["changed  path/File.cs"]}'},
          "scope": {"type": "string"}},
         ["item", "changes"]),
    tool("notifications_pending",
         "Board notifications waiting for this session. Reading them marks them delivered.",
         {"session": {"type": "string", "description": "Your session name."},
          "scope": {"type": "string"}},
         ["session"], mutates=False),
    tool("subscriptions", "Who is subscribed to what in this scope.",
         {"scope": {"type": "string"}}, [], mutates=False),
    tool("expert_list",
         "Declared domain experts: folder, what it answers, and when it was last active. An "
         "expert is a long-lived session rooted in a domain folder, not a worktree session.",
         {"scope": {"type": "string"}}, [], mutates=False),
    tool("expert_revive",
         "Restart a domain expert in its own folder, continuing its previous conversation so "
         "it keeps what it knew. Check ListAgents FIRST — this cannot see running processes, "
         "so reviving a live expert gives you two.",
         {"name": {"type": "string", "description": "The expert's declared name."},
          "message": {"type": "string", "description": "Optional first prompt."},
          "scope": {"type": "string"}},
         ["name"]),
    tool("doc_review_list",
         "Documentation reviews queued by merges. Every merge files one — this is how a "
         "documentation maintainer finds out what changed without being told.",
         {"scope": {"type": "string"},
          "reviewer": {"type": "string", "description": "Filter to one reviewer."},
          "all": {"type": "boolean", "description": "Include resolved reviews."}},
         [], mutates=False),
    tool("doc_review_resolve",
         "Mark a documentation review done. Append-only: the original request stays.",
         {"id": {"type": "string", "description": "e.g. DR-0003"},
          "scope": {"type": "string"}},
         ["id"]),
    tool("worktree_status",
         "Every live worktree with its ledger provenance — who cut it, when, why, and "
         "whether anyone has ever claimed it. Unattributed worktrees are listed first. "
         "Only this scope's worktrees; a folder with its own .workboard governs itself.",
         {"scope": {"type": "string", "description":
                    "Scope folder relative to the workspace root. Omit for the workspace itself."}},
         [], mutates=False),
    tool("worktree_history",
         "The ledger, newest last. Optionally filtered.",
         {"repo": {"type": "string"}, "worktree": {"type": "string"},
          "scope": {"type": "string", "description":
                    "Scope folder relative to the workspace root. Omit for the workspace itself."},
          "by_session": {"type": "string", "description": "Filter to one session's entries."},
          "action": {"type": "string", "description":
                     "Filter to one of: create, pull, merge, prune, denied."},
          "limit": {"type": "integer"}}, [], mutates=False),
]


# ------------------------------------------------------------------ operations

def op_create(a):
    repo, task = a["repo"], a["task"]
    name = a.get("worktree") or f"{task}-{repo}"
    scope = L.scope_of(os.path.join(L.WORKSPACE, repo))
    main = L.repo_path(repo, scope)
    if not L.is_clean(main):
        raise L.GateError(
            f"{repo} main checkout has uncommitted changes. Stop and ask — never stash or "
            f"discard in-progress work to make room for a worktree.")
    base = L.current_branch(main)
    # Why the workspace root and not the repo: one place to look, one place to prune.
    # A worktree under <repo>/.worktrees is invisible until you think to look there.
    path = os.path.join(L.worktree_dir_of(scope), name)
    if os.path.exists(path):
        raise L.GateError(f"{path} already exists. Prune it first, or pick another name.")
    branch = f"feature/{task}"
    L.git(["worktree", "add", path, "-b", branch, "HEAD"], main)
    L.record("create", a["session"], a["description"], repo, name,
             branch=branch, base=base, path=path,
             head=L.git(["rev-parse", "--short", "HEAD"], main), ok=True, task=a.get("_task"), board=a.get("_board"),
             taskVerified=a.get("_verified"))
    return (f"Cut {repo}/{name}\n  branch {branch}\n  from   {base} (local HEAD)\n"
            f"  path   {path}\nRecorded as {a['session']}.")


def op_pull(a):
    repo, name = a["repo"], a["worktree"]
    path = L.find_worktree(repo, name)["path"]
    out = L.git(["pull", "--ff-only"], path)
    L.record("pull", a["session"], a["description"], repo, name,
             branch=L.current_branch(path), result=out, ok=True, task=a.get("_task"), board=a.get("_board"),
             taskVerified=a.get("_verified"))
    return f"Pulled {repo}/{name}:\n{out or '(already up to date)'}"


def op_sync(a):
    """Integrate a remote branch into a repo's MAIN checkout.

    Why this exists: the gate governed worktree->main merges and nothing else, so a main
    checkout that had diverged from its remote had no recorded way forward. `git pull` and
    `git merge` are hook-blocked (correctly -- that is what makes ownership observed rather
    than asserted), and worktree_pull resolves through live_worktrees, which only covers
    worktrees the scope owns. The result was a real gap: three separate promotions stalled
    with the work committed locally and no legitimate way to move it.

    It fetches and merges origin/<branch> into whatever branch the main checkout is on. It
    does NOT force, rebase or reset -- a divergence that cannot be merged is a conflict for a
    person to resolve, not something to overwrite.
    """
    repo = a["repo"]
    branch = a["branch"]
    scope = L.scope_of(os.path.join(L.WORKSPACE, repo))
    main = L.repo_path(repo, scope)
    if not L.is_clean(main):
        raise L.GateError(
            f"{repo} main checkout has uncommitted changes. Commit them first -- never stash "
            f"or discard in-progress work to make room for a sync.")
    on = L.current_branch(main)
    L.git(["fetch", "origin"], main)
    before = L.git(["rev-parse", "--short", "HEAD"], main)
    try:
        out = L.git(["merge", f"origin/{branch}", "--no-edit"], main)
    except Exception as exc:
        L.record("sync", a["session"], a["description"], repo, "(main)",
                 branch=on, merged=branch, ok=False, result=str(exc)[:400], task=a.get("_task"), board=a.get("_board"),
             taskVerified=a.get("_verified"))
        raise L.GateError(
            f"merging origin/{branch} into {on} failed -- resolve it by hand in {main}:\n{exc}")
    after = L.git(["rev-parse", "--short", "HEAD"], main)
    L.record("sync", a["session"], a["description"], repo, "(main)",
             branch=on, merged=branch, before=before, head=after, ok=True, task=a.get("_task"), board=a.get("_board"),
             taskVerified=a.get("_verified"))
    return (f"Merged origin/{branch} -> {on} in {repo}\n{out}\n"
            f"  {before} -> {after}\nPush it: an unpushed merge is the failure this prevents.")


def op_merge(a):
    repo, name = a["repo"], a["worktree"]
    scope = L.scope_of(os.path.join(L.WORKSPACE, repo))
    path = L.find_worktree(repo, name, scope)["path"]
    branch = L.current_branch(path)
    created = L.creation_of(repo, name, scope)
    into = a.get("into") or (created or {}).get("base")
    if not into:
        # Why: guessing a merge target is how work lands on the wrong branch. The
        # ledger knows it, or the caller states it. There is no default.
        raise L.GateError(
            f"{repo}/{name} has no create record, so the branch it was cut from is "
            f"unknown. Pass 'into' explicitly — do not let it default.")
    if not L.is_clean(path):
        raise L.GateError(f"{path} has uncommitted changes. Commit them in the worktree first.")
    main = L.repo_path(repo, scope)
    on = L.current_branch(main)
    if on != into:
        raise L.GateError(f"{repo} main checkout is on {on!r} but this worktree was cut from "
                          f"{into!r}. Switch it, or pass into={on!r} if that is deliberate.")
    if not L.is_clean(main):
        raise L.GateError(f"{repo} main checkout is dirty; refusing to merge into it.")
    # Why capture before merging: after the merge, `into..branch` is empty and the
    # change set that a doc reviewer needs is no longer addressable.
    changed = L.git(["diff", "--stat", f"{into}...{branch}"], main, check=False)
    subjects = L.git(["log", "--format=%s", f"{into}..{branch}"], main, check=False)
    merge_out = L.git(["merge", branch, "--no-edit"], main)
    push_out = ""
    if a.get("push", True):
        push_out = L.git(["push"], main) or "pushed"
    L.record("merge", a["session"], a["description"], repo, name,
             supervisor=supervisor_of(scope),
             branch=branch, into=into, pushed=bool(a.get("push", True)),
             head=L.git(["rev-parse", "--short", "HEAD"], main), ok=True)

    # A merge is the moment the documentation went stale. Filing the review is part of
    # the merge, not a courtesy afterwards -- see file_doc_review.
    cfg, cfg_path = project_config(scope, repo)
    if cfg is None:
        review = setup_prompt(scope, repo)
    elif not (cfg.get("documentation") or {}).get("required", True):
        review = f"Documentation not required for {repo} (.registrar.json). No report filed."
    else:
        review = file_doc_review(scope, repo, name, branch, into, subjects, changed,
                                 a["session"], a["description"], a.get("_task"), a.get("_board"),
                                 L.git(["rev-parse", "HEAD"], main, check=False),
                                 cfg.get("documentation") or {})

    return (f"Merged {branch} -> {into} in {repo}\n{merge_out}\n"
            + (f"push: {push_out}\n" if push_out else "NOT PUSHED — an unpushed merge is the "
                                                      "failure the protocol exists to prevent.\n")
            + f"Now prune it: worktree_prune(repo={repo!r}, worktree={name!r}).\n\n"
            + review)


# Why sonnet by default: this fires on EVERY merge, and the work is procedure-following
# with verification -- read a diff, follow the docs project's documented process, check each
# claim against source. That is the estate's own split (opus for design, sonnet for
# template-following), and opus-per-merge is a real cost for mostly mechanical work.
# Escalation is per-route via "model", and the prompt tells the reviewer to escalate rather
# than quietly rewrite doctrine it is not equipped to judge.
DOC_REVIEWERS_DEFAULT = {"default": {"agent": "fdw-anatomy-maintainer",
                                     "cwd": "fdw-anatomy", "spawn": True,
                                     "permissionMode": "acceptEdits",
                                     "model": "sonnet"}}


def doc_reviewers(scope):
    """Which agent reviews documentation for which repo.

    Configuration, not a constant: the routing is the part that differs per estate, and
    the eventual .NET implementation will configure this alongside which documentation
    set each repo maps to. `<scope>/.worktree-gate/reviewers.json`:

        { "default": "fdw-anatomy-maintainer", "nexus-vcs": "nexus-docs" }

    A malformed file is an error, not a silent fall back to the default -- routing a
    review to the wrong agent is worse than refusing to route it.
    """
    path = os.path.join(scope, ".worktree-gate", "reviewers.json")
    if not os.path.exists(path):
        return dict(DOC_REVIEWERS_DEFAULT)
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise L.GateError(f"{path} is not valid JSON: {exc}") from exc
    if "default" not in cfg:
        raise L.GateError(f"{path} has no 'default' reviewer. Every repo must route somewhere.")
    # A bare string is shorthand for {"agent": "<name>"} with the default's other settings.
    base = DOC_REVIEWERS_DEFAULT["default"]
    out = {}
    for k, v in cfg.items():
        out[k] = dict(base, agent=v) if isinstance(v, str) else dict(base, **v)
    return out


ISSUE_KEY = __import__("re").compile(r"^[A-Z][A-Z0-9]*-\d+$")


def project_config(scope, repo):
    """Per-checkout configuration, or None when the project has not been set up.

    Resolution: <repo>/.registrar.json, then <scope>/.registrar.json, then None. None is
    NOT a default -- the caller prompts for the missing details rather than guessing
    whether documentation is required. Guessing wrong is silent in both directions: a
    false negative loses the documentation trail, a false positive spawns a session in a
    corpus nobody nominated.
    """
    for path in (os.path.join(repo_dir_of(scope, repo), ".registrar.json"),
                 os.path.join(scope, ".registrar.json")):
        if not os.path.exists(path):
            continue
        try:
            return json.load(open(path, encoding="utf-8")), path
        except json.JSONDecodeError as exc:
            raise L.GateError(f"{path} is not valid JSON: {exc}") from exc
    return None, None


def repo_dir_of(scope, repo):
    d = os.path.join(scope, repo)
    if not os.path.isdir(os.path.join(d, ".git")):
        alt = os.path.join(scope, "docs", repo)
        if os.path.isdir(os.path.join(alt, ".git")):
            return alt
    return d


def supervisor_of(scope):
    """The session the user talks to. Everything spawned reports back to it.

    Read from <scope>/.registrar.json. Absent means nothing was nominated: spawned sessions
    then report only to whoever merged, which is correct but narrower -- the user may never
    see it."""
    path = os.path.join(scope, ".registrar.json")
    if not os.path.exists(path):
        return None
    try:
        return (json.load(open(path, encoding="utf-8")).get("supervisor") or {}).get("name")
    except json.JSONDecodeError:
        return None


def setup_prompt(scope, repo):
    """What to show when a project has no .registrar.json."""
    docs = os.path.join(scope, "docs")
    available = sorted(d for d in os.listdir(docs)
                       if os.path.isdir(os.path.join(docs, d))) if os.path.isdir(docs) else []
    return (
        f"\n{'-' * 68}\n"
        f"{repo} has no .registrar.json, so this gate does not know whether its merges\n"
        f"require a documentation report, or which corpus owns them.\n\n"
        f"ASK THE USER, then write it. Do not assume either answer.\n"
        f"  1. Do merges in {repo} require a documentation report?\n"
        f"  2. If yes, which documentation project owns it?"
        + (f"  Available: {', '.join(available)}" if available else
           "  (no docs/ projects exist yet)") + "\n"
        f"  3. Which tracker validates its task ids — the board, YouTrack, or neither?\n\n"
        f"Then run one of:\n"
        f"  registrar.sh add-project {repo} --docs <project> --agent <agent>\n"
        f"  registrar.sh add-project {repo} --no-docs\n"
        f"{'-' * 68}")


# --------------------------------------------------------------- domain experts

EXPERT_MARKER = ".expert.json"


def transcript_dir(folder):
    """Where Claude Code keeps conversations for a directory: the absolute path with
    every separator replaced by a dash."""
    return os.path.join(os.path.expanduser("~/.claude/projects"),
                        os.path.abspath(folder).replace(os.sep, "-"))


def find_experts(scope=None):
    """Declared domain experts: any folder holding .expert.json.

    An expert is a LONG-LIVED session rooted in a domain folder -- the code it knows, plus a
    CLAUDE.md holding what it knows. It is not a worktree session and is not per-task.
    Discovery is by marker file so an expert declares itself, the same way a documentation
    project declares its coverage.

    WHY `covers` EXISTS, and why it survives a source-tree refactor
    ---------------------------------------------------------------
    The obvious design is "the expert owns its folder": open a session in
    src/connections/ and it knows connections. That is the target shape, and FDW cannot do it
    today -- src/ holds 336 flat project folders, and the connections domain is 29 siblings
    (Fdw.Services.Connections, .Abstractions, .MsSql, .Http, ReferenceConnections.*) with no
    directory containing them.

    So an expert declares `covers`: workspace-relative globs naming the code it speaks for,
    independent of where its own marker sits. Flat today:

        "covers": ["fractaldataworks/public/src/Fdw.Services.Connections*",
                   "reference-servicetypes/public/src/ReferenceConnections*"]

    After a refactor that groups the tree by domain, the same expert moves its marker into
    src/connections/ and `covers` collapses to ["**"] -- or is dropped entirely, in which case
    the expert's own directory is its scope. Nothing else changes: not the marker name, not the
    schema, not the tools, not the routing. The refactor is a move, not a migration.
    """
    scope = scope or L.WORKSPACE
    out = []
    skip = {".git", "obj", "bin", "node_modules", ".worktrees", ".workboard", ".worktree-gate"}
    for root, dirs, files in os.walk(scope):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        if EXPERT_MARKER not in files:
            continue
        path = os.path.join(root, EXPERT_MARKER)
        try:
            spec = json.load(open(path, encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise L.GateError(f"{path} is not valid JSON: {exc}") from exc
        if "name" not in spec:
            raise L.GateError(f"{path} has no 'name'. An expert nobody can address is not an expert.")
        spec["folder"] = root
        td = transcript_dir(root)
        sessions = sorted(glob.glob(os.path.join(td, "*.jsonl")),
                          key=os.path.getmtime, reverse=True) if os.path.isdir(td) else []
        spec["lastSession"] = os.path.basename(sessions[0])[:-6] if sessions else None
        spec["lastActive"] = (datetime.fromtimestamp(os.path.getmtime(sessions[0]))
                              .isoformat(timespec="seconds")) if sessions else None
        spec["hasClaudeMd"] = os.path.exists(os.path.join(root, "CLAUDE.md"))
        # No `covers` means the expert's own directory is its scope -- the post-refactor shape.
        spec["covers"] = spec.get("covers") or [os.path.relpath(root, scope) + "/**"]
        spec["resolved"] = sorted(
            os.path.relpath(m, scope)
            for pat in spec["covers"]
            for m in glob.glob(os.path.join(scope, pat.rstrip("/*")) + "*")
            if os.path.isdir(m))
        out.append(spec)
    return sorted(out, key=lambda e: e["name"])


def op_expert_list(a):
    experts = find_experts(_scope_arg(a))
    if not experts:
        return ("No domain experts declared in this scope.\n"
                "An expert is a folder holding .expert.json and a CLAUDE.md — the code it knows\n"
                "plus what it knows about it. Declare one and a session opened there is addressable.")
    out = [f"{len(experts)} declared expert(s).", ""]
    for e in experts:
        out.append(f"{e['name']}")
        out.append(f"  folder     {os.path.relpath(e['folder'], L.WORKSPACE)}")
        out.append(f"  CLAUDE.md  {'yes' if e['hasClaudeMd'] else 'MISSING — the session has no domain rules'}")
        out.append(f"  answers    {', '.join(e.get('answers', [])) or '(undeclared)'}")
        out.append(f"  covers     {len(e['resolved'])} folder(s) via {len(e['covers'])} pattern(s)")
        if not e["resolved"]:
            out.append("             NOTHING MATCHES — the expert speaks for no code on disk")
        out.append(f"  last seen  {e['lastActive'] or 'never started'}")
        if e.get("lastSession"):
            out.append(f"  session    {e['lastSession']}")
    out.append("")
    out.append("Check ListAgents FIRST. Revive only an expert that is not listed there — this")
    out.append("tool cannot see running processes, so reviving a live expert gives you two.")
    return "\n".join(out)


def experts_for_paths(scope, paths):
    """Which declared experts speak for these changed paths.

    The same matching a documentation project's coverage.json gets, applied to code rather
    than documents. One mechanism, two consumers -- when the source tree is regrouped by
    domain, both keep working because both match on paths, not on directory nesting.
    """
    hits = []
    for e in find_experts(scope):
        for folder in e["resolved"]:
            if any(p.startswith(folder) for p in paths):
                hits.append(e)
                break
    return hits


def op_expert_revive(a):
    """Restart a domain expert in its own folder, continuing its previous conversation."""
    scope = _scope_arg(a)
    name = a["name"]
    match = [e for e in find_experts(scope) if e["name"] == name]
    if not match:
        raise L.GateError(f"no expert named {name!r}. Declared: "
                          + (", ".join(e["name"] for e in find_experts(scope)) or "(none)"))
    e = match[0]
    if not e["hasClaudeMd"]:
        raise L.GateError(f"{name} has no CLAUDE.md in {e['folder']}. Reviving it would start a "
                          f"session with no domain knowledge, which is worse than no expert.")

    # --continue resumes the most recent conversation for that directory, so the expert comes
    # back with what it already knew. With no prior conversation it starts fresh.
    cmd = ["claude"]
    if e["lastSession"]:
        cmd += ["--continue"]
    cmd += ["-p", a.get("message") or f"You are {name}. Resuming. Await instructions from the "
                                      f"supervisor; do not start work unprompted.",
            "--permission-mode", e.get("permissionMode", "acceptEdits")]
    if e.get("model"):
        cmd += ["--model", e["model"]]

    log = os.path.join(scope, ".worktree-gate", "expert.log")
    try:
        with open(log, "a", encoding="utf-8") as h:
            h.write(f"\n===== {L.now()} revive {name} in {e['folder']}\n")
            subprocess.Popen(cmd, cwd=e["folder"], stdin=subprocess.DEVNULL, stdout=h,
                             stderr=subprocess.STDOUT, env=dict(os.environ),
                             start_new_session=True)
    except Exception as exc:  # noqa: BLE001
        raise L.GateError(f"could not start {name}: {exc}") from exc

    L.record("revive", a["session"], a["description"], None, None,
             expert=name, folder=os.path.relpath(e["folder"], L.WORKSPACE),
             continued=bool(e["lastSession"]), ok=True)
    return (f"Revived {name} in {os.path.relpath(e['folder'], L.WORKSPACE)}"
            + (f", continuing session {e['lastSession']}." if e["lastSession"]
               else " as a new session.")
            + f"\nIt takes a moment to appear. Then message it by the row name ListAgents "
              f"prints — not by this name if they differ.")


# ------------------------------------------------------- board: subscribe & notify

# Fields a worker session may change. Structure -- what the item IS -- stays with the
# orchestrating session, so "one writer per layer" survives having many writers.
WORKER_FIELDS = {"status", "owner", "actual", "fix", "sev"}
STRUCTURE_FIELDS = {"id", "title", "why", "evidence", "links", "expected", "repo", "was"}


def subs_path(scope):
    return os.path.join(scope, ".workboard", "subscriptions.jsonl")


def notif_path(scope):
    return os.path.join(scope, ".workboard", "notifications.jsonl")


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as h:
        for n, line in enumerate(h, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise L.GateError(f"{path} line {n} is not valid JSON: {exc}") from exc
    return rows


def _append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as h:
        h.write(json.dumps(obj, sort_keys=True) + "\n")


def active_subscribers(scope, item):
    """Latest state wins: a later unsubscribe cancels an earlier subscribe."""
    state = {}
    for r in _read_jsonl(subs_path(scope)):
        if r.get("item") == item:
            state[r["subscriber"]] = r.get("active", True)
    return sorted(k for k, v in state.items() if v)


def find_item(scope, item_id):
    board = board_path(scope)
    if not os.path.exists(board):
        raise L.GateError(f"no board at {board}")
    data = json.load(open(board, encoding="utf-8"))
    for g in data.get("groups", []):
        for it in g.get("items", []):
            if it.get("id") == item_id:
                return data, g, it
    raise L.GateError(f"no item {item_id!r} on the board. Open items: "
                      + ", ".join(sorted(i["id"] for g in data.get("groups", [])
                                         for i in g.get("items", [])
                                         if i.get("status") != "done")[:14]))


def op_item_subscribe(a):
    scope = _scope_arg(a)
    find_item(scope, a["item"])                      # refuse to subscribe to nothing
    _append_jsonl(subs_path(scope), {
        "ts": L.now(), "item": a["item"], "subscriber": a["session"],
        "reason": a["description"], "active": True})
    return (f"{a['session']} subscribed to {a['item']}.\n"
            f"Notifications arrive in the result of your NEXT worktree-gate call — this server "
            f"cannot interrupt a running session, so delivery is at your next tool use, not "
            f"instant. Call notifications_pending to check on demand.")


def op_item_unsubscribe(a):
    scope = _scope_arg(a)
    _append_jsonl(subs_path(scope), {
        "ts": L.now(), "item": a["item"], "subscriber": a["session"],
        "reason": a["description"], "active": False})
    return f"{a['session']} unsubscribed from {a['item']}."


def fan_out(scope, item, event, by, detail):
    """One notification per active subscriber, minus the actor. Returns who was notified."""
    targets = [s for s in active_subscribers(scope, item) if s != by]
    for t in targets:
        _append_jsonl(notif_path(scope), {
            "ts": L.now(), "item": item, "subscriber": t, "event": event,
            "by": by, "detail": detail, "delivered": False})
    return targets


def pending_for(scope, session):
    """Undelivered notifications for one session, oldest first."""
    delivered = {(r["ts"], r["subscriber"]) for r in _read_jsonl(notif_path(scope))
                 if r.get("delivered")}
    return [r for r in _read_jsonl(notif_path(scope))
            if r.get("subscriber") == session and not r.get("delivered")
            and (r["ts"], r["subscriber"]) not in delivered]


def mark_delivered(scope, rows):
    for r in rows:
        _append_jsonl(notif_path(scope), dict(r, delivered=True, deliveredAt=L.now()))


def deliver_banner(scope, session):
    """Pending notifications, rendered to sit on top of a tool result.

    This is the ONLY delivery channel that is guaranteed. The server cannot push into a
    running session, so a subscriber learns at its next gated call. Everything else --
    a message from the supervisor, a respawn -- is faster, not surer.
    """
    if not session:
        return ""
    rows = pending_for(scope, session)
    if not rows:
        return ""
    mark_delivered(scope, rows)
    out = [f"{'=' * 68}", f"{len(rows)} board notification(s) for {session}:"]
    for r in rows:
        out.append(f"  [{r['ts'][:16]}] {r['item']} — {r['event']} by {r['by']}")
        if r.get("detail"):
            out.append(f"      {r['detail']}")
    out.append("=" * 68)
    return "\n".join(out) + "\n\n"


def op_item_update(a):
    scope = _scope_arg(a)
    data, group, item = find_item(scope, a["item"])
    changes = a.get("changes") or {}
    if not changes:
        raise L.GateError("changes is empty. Say what you are changing.")
    bad = set(changes) - WORKER_FIELDS
    if bad:
        why = ("structure belongs to the orchestrating session"
               if bad & STRUCTURE_FIELDS else "not a known item field")
        raise L.GateError(f"cannot set {', '.join(sorted(bad))} — {why}. "
                          f"Settable here: {', '.join(sorted(WORKER_FIELDS))}.")

    before = {k: item.get(k) for k in changes}
    item.update(changes)
    board = board_path(scope)
    tmp = board + ".tmp"
    with open(tmp, "w", encoding="utf-8") as h:
        json.dump(data, h, indent=1)
    os.replace(tmp, board)                      # atomic: a reader never sees a half-written board

    detail = "; ".join(f"{k}: {before[k]!r} -> {v!r}" for k, v in changes.items())
    L.record("board", a["session"], a["description"], None, None,
             item=a["item"], changes=list(changes), ok=True)
    notified = fan_out(scope, a["item"], "updated", a["session"], detail)

    out = [f"{a['item']} updated: {detail}"]
    if notified:
        out.append(f"\n{len(notified)} subscriber(s) queued: {', '.join(notified)}")
        out.append("They receive it at their next worktree-gate call. To reach them sooner:")
        out.append('  ToolSearch("select:ListAgents,SendMessage") then message each by its'
                   " ListAgents row name.")
        out.append("If one is not listed there it is dead — expert_revive it, or leave it: the"
                   " notification is queued and survives.")
    else:
        out.append("No subscribers.")
    return "\n".join(out)


def op_notifications_pending(a):
    scope = _scope_arg(a)
    rows = pending_for(scope, a.get("session") or "")
    if not rows:
        return f"Nothing pending for {a.get('session')!r}."
    mark_delivered(scope, rows)
    return "\n".join([f"{len(rows)} notification(s):"]
                      + [f"  [{r['ts'][:16]}] {r['item']} — {r['event']} by {r['by']}"
                         + (f"\n      {r['detail']}" if r.get("detail") else "")
                         for r in rows])


def op_subscriptions(a):
    scope = _scope_arg(a)
    rows = _read_jsonl(subs_path(scope))
    if not rows:
        return "No subscriptions in this scope."
    items = sorted({r["item"] for r in rows})
    out = []
    for it in items:
        subs = active_subscribers(scope, it)
        if subs:
            out.append(f"  {it}: {', '.join(subs)}")
    return "\n".join(["Active subscriptions:"] + out) if out else "No active subscriptions."


def board_path(scope):
    return os.path.join(scope, ".workboard", "items.json")


def resolve_task(scope, task):
    """Validate a task reference and return (task, board_path_or_None).

    Why validate rather than just record: a free-text task field decays into 'stuff' and
    'misc' within a week, and then the ledger tells you a worktree existed but not what it
    was for. Where a board exists, the task must name a real item on it. An issue key is
    accepted without lookup because the tracker is a different system this cannot reach.

    No board and no issue-key shape means nothing can be checked -- accepted, but recorded
    as unverified rather than silently treated as valid.
    """
    task = (task or "").strip()
    board = board_path(scope)
    if not os.path.exists(board):
        return task, None, ISSUE_KEY.match(task) is not None
    try:
        data = json.load(open(board, encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise L.GateError(f"{board} is not valid JSON: {exc}") from exc
    ids = [i["id"] for g in data.get("groups", []) for i in g.get("items", [])]
    if task in ids:
        return task, board, True
    if ISSUE_KEY.match(task):
        return task, board, True
    raise L.GateError(
        f"task {task!r} is not an item on {os.path.relpath(board, L.WORKSPACE)} and is not an "
        f"issue key like NVS-87.\nOpen items: "
        + ", ".join(sorted(i["id"] for g in data.get("groups", []) for i in g.get("items", [])
                           if i.get("status") != "done")[:14])
        + "\nFile the work on the board first, or pass the issue key.")


def doc_review_path(scope):
    return os.path.join(scope, ".worktree-gate", "doc-review.jsonl")


def file_doc_review(scope, repo, worktree, branch, into, subjects, changed,
                    session, description, task=None, board=None, sha=None, doccfg=None):
    """Queue a documentation review for a merge. Returns the text to show the merger.

    Why this is inside the merge and not a separate call: a merge is the exact moment
    documentation goes stale, and anything a caller has to remember afterwards is a
    thing that will not happen. The QUEUE is forced -- the notification is not, because
    this process cannot message a session. That split is deliberate and honest: the
    durable record can never be lost, the live nudge is best effort on top of it.

    A queue failure never fails the merge. The merge already happened; refusing to
    report it would be worse than a missed review.
    """
    try:
        routing = doc_reviewers(scope)
    except L.GateError as exc:
        return f"DOC REVIEW NOT FILED: {exc}"
    route = dict(routing.get(repo, routing["default"]))
    # .registrar.json wins over reviewers.json: it is the project's own declaration.
    for k, v in (doccfg or {}).items():
        if k == "project":
            route["cwd"] = v
        elif v is not None:
            route[k] = v
    reviewer = route["agent"]
    try:
        existing = read_doc_reviews(scope)
        rid = f"DR-{len(existing) + 1:04d}"
        with open(doc_review_path(scope), "a", encoding="utf-8") as h:
            h.write(json.dumps({
                "ts": L.now(), "id": rid, "state": "open", "reviewer": reviewer,
                "repo": repo, "worktree": worktree, "branch": branch, "into": into,
                "mergedBy": session, "reason": description,
                "task": task, "board": board, "sha": sha,
                "commits": [x for x in (subjects or "").splitlines() if x.strip()],
                "changed": changed or "",
            }, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001 - never fail a completed merge over this
        return (f"DOC REVIEW NOT FILED: {exc}\n"
                f"Tell {reviewer} by hand what merged, or the docs go stale silently.")

    n = len([x for x in (subjects or "").splitlines() if x.strip()])
    spawned = spawn_reviewer(scope, route, rid, repo, branch, into, session, subjects,
                             changed, task, board, sha)
    return (f"Doc review {rid} filed for '{reviewer}' — {n} commit(s) in {repo}.\n"
            f"{spawned}\n"
            f"NOTIFY IT NOW. Load the tool if you have not: ToolSearch(\"select:SendMessage\")\n"
            f"  SendMessage(to=\"{reviewer}\", message=\"Doc review {rid}: {repo} "
            f"{branch} -> {into} merged. Run doc_review_list to see the change set.\")\n"
            f"If you skip this the review still exists in the queue — it just waits longer.")


def spawn_reviewer(scope, route, rid, repo, branch, into, merged_by, subjects, changed,
                   task=None, board=None, sha=None):
    """Start a headless Claude Code session to do the documentation update.

    This is what makes the notification real rather than advisory. The MCP process cannot
    message a running session -- but it CAN start one, and a session can message. So the
    gate spawns the reviewer, and the reviewer talks to whoever merged.

    Detached and never blocking: the merge already happened, and a documentation review
    that is slow must not hold up the person who did the work. A spawn failure is reported,
    never raised -- the queue entry is the durable record and survives either way.
    """
    if not route.get("spawn", True):
        return f"  spawn disabled for this route — {route['agent']} must poll doc_review_list."
    if os.environ.get("WORKTREE_GATE_NO_SPAWN"):
        return "  spawn suppressed (WORKTREE_GATE_NO_SPAWN set)."
    # Why: a spawned reviewer that merges would file a review that spawns a reviewer.
    if os.environ.get("WORKTREE_GATE_DEPTH"):
        return "  spawn skipped — already inside a spawned review (recursion guard)."

    cwd = route.get("cwd") or "."
    cwd = cwd if os.path.isabs(cwd) else os.path.join(L.WORKSPACE, cwd)
    if not os.path.isdir(cwd):
        return f"  SPAWN FAILED: {cwd} does not exist. {route['agent']} must poll instead."

    prompt = f"""Documentation review {rid}. A merge just landed and the docs may now be stale.

WHAT MERGED
  repo    {repo}
  branch  {branch} -> {into}
  commit  {sha or '(unknown)'}
  task    {task or '(none recorded)'}
  board   {os.path.join(L.WORKSPACE, board) if board else '(no board in this scope)'}
  merged by session: {merged_by}
  supervisor:        {supervisor_of(scope) or '(none nominated)'}

COMMITS
{chr(10).join('  ' + x for x in (subjects or '').splitlines() if x.strip()) or '  (none recorded)'}

CHANGED FILES
{chr(10).join('  ' + x for x in (changed or '').splitlines()[:40]) or '  (none recorded)'}

WHAT TO DO
1. Follow this project's CLAUDE.md. It is the authority here and it OUTRANKS this prompt.
   If it defines an inbound protocol for "I just updated..." reports, this is one of those
   reports -- run that protocol exactly, and ignore any step below that contradicts it.
2. READ THE BOARD FIRST. The task above is an item on that board, and the item carries what
   a diff cannot: why the work exists, its evidence, what it gates, and every note filed
   while it was done. That is most of "what moved", already written down.
   Notes are in the sibling notes.jsonl, filtered on "item".
3. Do NOT edit documentation pages to match this change unless your CLAUDE.md says that is
   your job. In a registrar-style corpus it is not: you record the claim, name the affected
   documents by name, and mark them suspect. A page changes when someone re-verifies it
   against source and re-stamps it -- a different act, by a different agent, later.
4. Ask the merging session only what the board AND the diff both leave unanswered: intent,
   why an approach was rejected, what a mechanism is FOR. Load the tools first:
     ToolSearch("select:ListAgents,SendMessage")
   then find the row for {merged_by!r} and message that row's NAME (not its ref). Ask
   specific questions; a vague "any context?" wastes their turn.
5. Reply in whatever shape your CLAUDE.md prescribes, and ask them to confirm your reading
   of what moved before you close this. If a supervisor is named above, copy your reply to
   it -- it is the session the user is actually watching, and a finding only the merging
   session sees may never reach a person.
6. Then close the review:
     mcp__worktree-gate__doc_review_resolve(id="{rid}", session="<you>",
       task="{task}", description="<what you recorded, or why nothing was affected>")

ESCALATE, DO NOT GUESS
If this change CONTRADICTS a documented mechanism rather than merely dating an inventory,
say so plainly in what you record and in your reply. Do not resolve the contradiction
yourself. Quietly rewriting an architectural claim to match a diff is how documentation
stops being an authority.

If nothing needs changing, resolve it saying so. An unresolved review is indistinguishable
from an ignored one."""

    log = os.path.join(scope, ".worktree-gate", "doc-review.log")
    env = dict(os.environ, WORKTREE_GATE_DEPTH="1")
    cmd = ["claude", "-p", prompt, "--permission-mode", route.get("permissionMode", "acceptEdits")]
    if route.get("model"):
        cmd += ["--model", route["model"]]
    try:
        with open(log, "a", encoding="utf-8") as h:
            h.write(f"\n===== {L.now()} {rid} -> {route['agent']} in {cwd}\n")
            subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=h,
                             stderr=subprocess.STDOUT, env=env, start_new_session=True)
    except Exception as exc:  # noqa: BLE001
        return f"  SPAWN FAILED: {exc}. {route['agent']} must poll doc_review_list."
    return (f"  Spawned {route['agent']} headless in {os.path.relpath(cwd, L.WORKSPACE)} "
            f"({route.get('permissionMode', 'acceptEdits')}). It will message you for detail "
            f"and ask you to review. Log: .worktree-gate/doc-review.log")


def read_doc_reviews(scope):
    path = doc_review_path(scope)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as h:
        for n, line in enumerate(h, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise L.GateError(f"doc-review.jsonl line {n} is not valid JSON: {exc}") from exc
    return rows


def op_doc_review_list(a):
    scope = _scope_arg(a)
    rows = read_doc_reviews(scope)
    resolved = {r["id"] for r in rows if r.get("state") == "resolved"}
    if a.get("reviewer"):
        rows = [r for r in rows if r.get("reviewer") == a["reviewer"]]
    openr = [r for r in rows if r.get("state") == "open" and r["id"] not in resolved]
    if not a.get("all") and not openr:
        return "No open documentation reviews in this scope."
    show = rows if a.get("all") else openr
    out = [f"{len(openr)} open documentation review(s)."]
    for r in show:
        out.append("")
        out.append(f"{r['id']}  [{r.get('state')}]  {r['repo']} — {r['branch']} -> {r['into']}")
        out.append(f"  merged by {r['mergedBy']} · {r['ts'][:16]}")
        out.append(f"  reason: {r['reason']}")
        for c in r.get("commits", [])[:12]:
            out.append(f"    · {c}")
        if r.get("changed"):
            out.append("  changed:")
            out.extend("    " + x for x in r["changed"].splitlines()[:20])
    return "\n".join(out)


def op_doc_review_resolve(a):
    scope = _scope_arg(a)
    rows = read_doc_reviews(scope)
    if not any(r["id"] == a["id"] for r in rows):
        raise L.GateError(f"no doc review {a['id']!r} in this scope. "
                          f"Known: {', '.join(r['id'] for r in rows) or '(none)'}")
    with open(doc_review_path(scope), "a", encoding="utf-8") as h:
        h.write(json.dumps({"ts": L.now(), "id": a["id"], "state": "resolved",
                            "resolvedBy": a["session"], "resolution": a["description"]},
                           sort_keys=True) + "\n")
    return f"{a['id']} resolved by {a['session']}: {a['description']}"


def op_prune(a):
    repo, name = a["repo"], a["worktree"]
    scope = L.scope_of(os.path.join(L.WORKSPACE, repo))
    path = L.find_worktree(repo, name, scope)["path"]
    branch = L.current_branch(path)
    main = L.repo_path(repo, scope)
    forced = bool(a.get("force"))
    if not forced:
        if not L.is_clean(path):
            raise L.GateError(f"{path} has uncommitted changes. Commit or merge first, "
                              f"or pass force=true to discard them.")
        # Why skipped when detached: "HEAD..HEAD" is not a range that says anything about
        # whether work would be lost, and a detached worktree has no branch whose commits
        # could be stranded by removing it.
        unmerged = "" if branch in (None, "", "HEAD") else L.git(
            ["log", "--oneline", f"{L.current_branch(main)}..{branch}"], main, check=False)
        if unmerged:
            raise L.GateError(
                f"{branch} has commits not in {L.current_branch(main)}:\n{unmerged}\n"
                f"Merge it first, or pass force=true to delete the work.")
    # Why detached is checked rather than assumed away: current_branch returns "HEAD" for a
    # worktree checked out at a bare commit, and `git branch -d HEAD` fails. There is no branch
    # to delete, so the deletion is skipped rather than attempted and reported as a failure.
    detached = branch in (None, "", "HEAD")

    L.git(["worktree", "remove"] + (["--force"] if forced else []) + [path], main)

    # Why the record is written between the two git calls: removing the worktree is the
    # destructive step and it has already succeeded here. Recording after the branch delete
    # meant a failure in that second call raised before anything was written, so a prune that
    # genuinely happened was reported as REFUSED and left no ledger entry at all — which is how
    # databases-seed/seedp-develop was removed while the caller was told the operation failed.
    L.record("prune", a["session"], a["description"], repo, name,
             branch=None if detached else branch, forced=forced, ok=True, task=a.get("_task"), board=a.get("_board"),
             taskVerified=a.get("_verified"))

    if not detached:
        L.git(["branch", "-D" if forced else "-d", branch], main, check=not forced)

    removed = f"Pruned {repo}/{name}"
    return (removed + (" (detached — no branch to delete)." if detached
                       else f" and deleted {branch}.")
            + (" (FORCED)" if forced else ""))


def _scope_arg(a):
    """Resolve the scope a read is asking about. Default is the gate's own workspace."""
    name = (a.get("scope") or "").strip()
    if not name or name == ".":
        return L.WORKSPACE
    return L.scope_of(os.path.join(L.WORKSPACE, name))


def op_adopt(a):
    """Record live worktrees the journal has never seen.

    Why this exists: the gate hook only ever saw Bash, so worktrees created by the
    EnterWorktree tool never reached it -- the gate governed the exit and not the
    entrance. Those worktrees are real and in use; refusing to acknowledge them helps
    nobody. Adoption records that they EXIST and who adopted them. It does NOT invent
    who originally cut them -- `originalActor` is explicitly null, because a fabricated
    attribution is worse than an admitted gap."""
    scope = _scope_arg(a)
    known = {(r["repo"], r["worktree"]) for r in L.read_ledger(scope)
             if r.get("action") == "create" and r.get("ok", True)}
    pruned = {(r["repo"], r["worktree"]) for r in L.read_ledger(scope)
              if r.get("action") == "prune" and r.get("ok", True)}
    orphans = [w for w in L.live_worktrees(scope)
               if (w[0], w[1]) not in known or (w[0], w[1]) in pruned]
    only = a.get("only")
    if only:
        orphans = [w for w in orphans if w[1] in only or f"{w[0]}/{w[1]}" in only]
    if not orphans:
        return "Nothing to adopt — every live worktree in this scope is already in the journal."
    if not a.get("confirm"):
        return (f"{len(orphans)} unattributed worktree(s) in this scope:\n"
                + "\n".join(f"  {r}/{n}  [{b}]" for r, n, b, _ in orphans)
                + "\n\nRe-run with confirm=true to record them as adopted. The original "
                  "actor is unknown and will be recorded as such, not guessed.")
    for repo, name, branch, path in orphans:
        L.record("create", a["session"], a["description"], repo, name,
                 branch=branch, path=path, adopted=True, originalActor=None, ok=True, task=a.get("_task"), board=a.get("_board"),
             taskVerified=a.get("_verified"))
    return (f"Adopted {len(orphans)} worktree(s) into the {os.path.relpath(scope, L.WORKSPACE)} "
            f"journal, each marked adopted with originalActor=null.")


def op_status(a):
    scope = _scope_arg(a)
    rows, ledger = L.live_worktrees(scope), L.read_ledger(scope)
    creates = {(r["repo"], r["worktree"]): r for r in ledger
               if r.get("action") == "create" and r.get("ok", True)}
    events = {}
    for r in ledger:
        events.setdefault((r.get("repo"), r.get("worktree")), []).append(r)
    known = [r for r in rows if (r[0], r[1]) in creates]
    orphan = [r for r in rows if (r[0], r[1]) not in creates]
    rel = os.path.relpath(scope, L.WORKSPACE)
    nested = L.nested_scopes(scope)
    out = [f"scope: {rel if rel != '.' else '<workspace>'}",
           f"{len(rows)} live worktrees — {len(known)} attributed, {len(orphan)} unattributed."]
    if nested:
        out.append(f"ignored — these govern themselves: {', '.join(nested)}")
    out.append("")
    if orphan:
        out.append(f"UNATTRIBUTED ({len(orphan)}) — nobody has claimed these:")
        for repo, name, branch, _ in orphan:
            out.append(f"  {repo}/{name}  [{branch}]")
        out.append("")
    if known:
        out.append(f"ATTRIBUTED ({len(known)}):")
        for repo, name, branch, _ in known:
            c = creates[(repo, name)]
            n = len(events.get((repo, name), []))
            out.append(f"  {repo}/{name}  [{branch}]  from {c.get('base','?')}  "
                       f"{c['session']}  {c['ts'][:16]}  ({n} events)")
            out.append(f"      {c['description']}")
    return "\n".join(out)


def op_history(a):
    rows = L.read_ledger(_scope_arg(a))
    for key in ("repo", "worktree", "action"):
        if a.get(key):
            rows = [r for r in rows if r.get(key) == a[key]]
    if a.get("by_session"):
        rows = [r for r in rows if r.get("session") == a["by_session"]]
    limit = a.get("limit")
    if limit:
        rows = rows[-int(limit):]
    if not rows:
        return "No matching ledger entries."
    return "\n".join(
        f"{r['ts'][:19]}  {r['action']:<7} {r.get('repo','-')}/{r.get('worktree','-')}"
        f"  {r['session']}\n      {r['description']}" for r in rows)


OPS = {"item_subscribe": op_item_subscribe, "item_unsubscribe": op_item_unsubscribe,
       "item_update": op_item_update, "notifications_pending": op_notifications_pending,
       "subscriptions": op_subscriptions,
       "expert_list": op_expert_list, "expert_revive": op_expert_revive,
       "doc_review_list": op_doc_review_list, "doc_review_resolve": op_doc_review_resolve,
       "worktree_sync": op_sync, "worktree_adopt": op_adopt, "worktree_create": op_create, "worktree_pull": op_pull, "worktree_merge": op_merge,
       "worktree_prune": op_prune, "worktree_status": op_status, "worktree_history": op_history}
MUTATING = {"item_subscribe", "item_unsubscribe", "item_update", "expert_revive", "doc_review_resolve", "worktree_sync", "worktree_adopt", "worktree_create", "worktree_pull", "worktree_merge", "worktree_prune"}


def require_who(name, args):
    """Validate attribution BEFORE any git runs.

    Why: L.record() enforces this too, but it is called after the operation. A blank
    description there cut a worktree and then refused to record it -- producing exactly
    the unattributed worktree this server exists to prevent. Attribution is a
    precondition, not a post-condition."""
    if name not in MUTATING:
        return
    for field in ("session", "description", "task"):
        if not (args.get(field) or "").strip():
            raise L.GateError(
                f"{field} is required before {name} will touch anything — "
                f"say who is acting, why, and which task this belongs to.")
    # Why _scope_arg and not a repo-only derivation: a caller working in a sub-scope passes
    # `scope` and often no `repo` at all. Deriving from repo alone silently validated the task
    # against the WORKSPACE board instead of the sub-scope's, so a legitimate task in a nested
    # scope was refused while naming items from a board the caller was not using.
    scope = _scope_arg(args)
    task, board, verified = resolve_task(scope, args["task"])
    args["_task"], args["_verified"] = task, verified
    args["_board"] = os.path.relpath(board, L.WORKSPACE) if board else None
    args["_scope"] = scope


# ------------------------------------------------------------------ jsonrpc

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def result(rid, payload):
    send({"jsonrpc": "2.0", "id": rid, "result": payload})


def handle(msg):
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        result(rid, {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion", DEFAULT_PROTOCOL),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER,
            "instructions": "Every worktree create/pull/merge/prune in this workspace goes "
                            "through these tools. Raw git worktree/merge/pull from the shell "
                            "is blocked. session and description are mandatory on mutations.",
        })
    elif method == "ping":
        result(rid, {})
    elif method == "tools/list":
        result(rid, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            if name not in OPS:
                raise L.GateError(f"unknown tool {name!r}")
            require_who(name, args)
            text, is_error = OPS[name](args), False
            # Why here and not in each op: this is the one place every call passes through,
            # so a subscriber cannot miss a notification by using a tool nobody thought to
            # instrument. The server cannot push -- this is the guaranteed channel.
            if name != "notifications_pending":
                text = deliver_banner(_scope_arg(args), args.get("session") or "") + text
        except L.GateError as exc:
            text, is_error = f"REFUSED: {exc}", True
        except Exception as exc:  # noqa: BLE001 - surface, never swallow
            text, is_error = f"{type(exc).__name__}: {exc}", True
            try:
                L.record("denied", args.get("session") or "unknown",
                         args.get("description") or f"{name} raised {type(exc).__name__}",
                         args.get("repo"), args.get("worktree"), tool=name, ok=False,
                         error=str(exc))
            except Exception:
                pass
        result(rid, {"content": [{"type": "text", "text": text}], "isError": is_error})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid,
              "error": {"code": -32601, "message": f"method not found: {method}"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"worktree-gate: bad JSON on stdin: {exc}", file=sys.stderr)


def cli(argv):
    """Recorded escape hatch: `python3 server.py call <tool> '<json args>'`.

    Why: a session whose tool list lacks this MCP server would otherwise be hard-blocked
    by the gate with no sanctioned path at all. This runs the identical op through the
    identical validation and ledger, so the escape hatch is still fully attributed."""
    if len(argv) < 2 or argv[0] != "call":
        print("usage: server.py call <tool> '<json-args>'\n"
              f"tools: {', '.join(OPS)}", file=sys.stderr)
        return 2
    name = argv[1]
    args = json.loads(argv[2]) if len(argv) > 2 else {}
    try:
        if name not in OPS:
            raise L.GateError(f"unknown tool {name!r}; tools: {', '.join(OPS)}")
        require_who(name, args)
        out = OPS[name](args)
        if name != "notifications_pending":
            out = deliver_banner(_scope_arg(args), args.get("session") or "") + out
        print(out)
        return 0
    except L.GateError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]) if len(sys.argv) > 1 else main())
