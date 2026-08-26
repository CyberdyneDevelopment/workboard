#!/usr/bin/env python3
"""PreToolUse gate: worktree lifecycle git commands must go through the
worktree-gate MCP server, not raw Bash.

Denies create / pull / merge / prune verbs inside the cyberdynedevelopment
workspace and records the attempt — with the REAL session id, which only the
hook payload carries. Read-only git is untouched.

Exit 0 + deny JSON  -> blocked.
Exit 1              -> no decision; the other PreToolUse hooks decide as normal.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# Shell words that wrap another command; look past them for the real verb.
WRAPPERS = {"sudo", "env", "nohup", "timeout", "nice", "ionice", "command", "exec",
            "stdbuf", "xargs", "time"}
# git global flags that take a value, so the subcommand is one further along.
VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

GATED = {
    "worktree": ({"add", "remove", "move", "prune", "lock", "unlock", "repair"},
                 "worktree_create / worktree_prune"),
    "merge":    (None, "worktree_merge"),
    "pull":     (None, "worktree_pull"),
    "branch":   ({"-d", "-D", "--delete", "-m", "-M", "--move"}, "worktree_prune"),
    # A commit is the finest-grained signal that work moved, and it was invisible before:
    # nothing watched worktree HEADs, so a subscriber learned only when a status flipped.
    # Gating it makes the signal exact rather than inferred. It is also the most frequent
    # git operation there is, so the friction is real and deliberate.
    "commit":   (None, "worktree_commit"),
}

MESSAGE = """Blocked: worktree lifecycle operations go through the worktree-gate MCP server.

  {cmd}

Use `mcp__worktree-gate__{tool}` instead. It requires `session` and `description`,
does the same git work, and records who did it and why in
.worktree-gate/ledger.jsonl — which is what makes worktree ownership observed
rather than asserted.

If `mcp__worktree-gate__*` is not in your tool list, use the recorded CLI instead —
it runs the same validation and writes the same ledger entry:

  python3 {root}/.worktree-gate/server.py call {tool} '{{"session":"<you>","description":"<why>", ...}}'

Read-only git (status, log, diff, worktree list, branch --show-current) is not gated.
"""


HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


# ---------------------------------------------------------------- board write guard

BOARD_TARGETS = ("/.workboard/items.json", "\\.workboard\\items.json")


def board_write_denial(payload):
    """Deny a direct edit of items.json. The board is mutated through the MCP server or not
    at all -- otherwise 'one writer per layer' is a convention, and a convention that is not
    enforced is a comment.

    notes.jsonl is deliberately NOT guarded: it is append-only and anyone may add to it.
    """
    tool = payload.get("hook_event_name") == "PreToolUse" and payload.get("tool_name")
    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return None
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not any(t in path for t in BOARD_TARGETS):
        return None
    return (
        "Blocked: the board is not edited directly.\n\n"
        f"  {path}\n\n"
        "Use the worktree-gate MCP server:\n"
        "  item_update(item=..., changes={...}, session=..., description=..., task=...)\n\n"
        "It validates the item exists, refuses fields that belong to the orchestrating\n"
        "session, records the change in the ledger, and notifies every subscriber. A direct\n"
        "write does none of that, and a subscriber would never learn the item moved.\n\n"
        "Appending to .workboard/notes.jsonl is NOT gated -- notes are append-only and open."
    )


def strip_heredocs(command):
    """Remove heredoc bodies before scanning.

    Why: a heredoc payload is data, not commands. Writing a runbook that contains a
    worktree command on its own line would otherwise be blocked -- and AGENT-BRIEF.md
    literally contains one, so the gate would have blocked the document that explains
    the protocol."""
    lines, out, i = command.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        match = HEREDOC.search(line)
        i += 1
        if not match:
            continue
        delim = match.group(2)
        while i < len(lines) and lines[i].strip() != delim:
            i += 1
        i += 1  # drop the terminator too
    return "\n".join(out)


def segments(command):
    """Split a shell command into independently-executed segments."""
    return [s for s in re.split(r"&&|\|\||[;|\n]", strip_heredocs(command)) if s.strip()]


def verb(tokens):
    """Peel wrappers and env assignments; return tokens starting at the real command."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if "=" in t and not t.startswith("-") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            i += 1
        elif t in WRAPPERS or os.path.basename(t) in WRAPPERS:
            i += 1
            # timeout/nice take a numeric or flag argument first
            while i < len(tokens) and (tokens[i].startswith("-") or tokens[i].isdigit()):
                i += 1
        else:
            return tokens[i:]
    return []


def gated_call(segment):
    """(subcommand, suggested tool) if this segment is a gated git mutation."""
    try:
        tokens = verb(segment.split())
    except Exception:
        return None
    if not tokens or os.path.basename(tokens[0].strip("'\"")) != "git":
        return None
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        if tokens[i] in VALUED:
            i += 2
        else:
            i += 1
    if i >= len(tokens):
        return None
    sub = tokens[i]
    if sub not in GATED:
        return None
    triggers, tool = GATED[sub]
    rest = tokens[i + 1:]
    if triggers is None:
        return (sub, tool)
    if any(r in triggers for r in rest):
        return (f"{sub} {' '.join(r for r in rest if r in triggers)}", tool)
    return None


def record_created(payload):
    """PostToolUse on a tool that makes worktrees without touching Bash.

    Why reconcile instead of parsing the tool payload: the gate only ever matched Bash,
    so worktrees cut by the EnterWorktree tool never reached it -- it governed the exit
    and not the entrance, and every organic worktree went unattributed. Rather than guess
    at that tool's payload shape, compare what is live against what the journal knows and
    record the difference. Shape-independent, and correct even if the payload changes.

    This RECORDS, it never denies. EnterWorktree is a first-class tool; blocking it would
    teach people to route around the gate, which is how this happened in the first place.
    """
    sys.path.insert(0, HERE)
    import wtledger as L

    cwd = payload.get("cwd") or ""
    try:
        scope = L.scope_of(cwd) if cwd.startswith(ROOT) else L.WORKSPACE
    except L.GateError:
        scope = L.WORKSPACE

    last = {}
    for row in L.read_ledger(scope):
        if row.get("repo") and row.get("action") in ("create", "prune") and row.get("ok", True):
            last[(row["repo"], row["worktree"])] = row["action"]

    hint = (payload.get("tool_input") or {})
    label = hint.get("name") or hint.get("path") or "unnamed"
    recorded = 0
    for repo, name, branch, path in L.live_worktrees(scope):
        if last.get((repo, name)) == "create":
            continue
        L.record("create", payload.get("session_id", "unknown"),
                 f"{payload.get('tool_name', 'tool')} ({label}) — recorded by the gate, not routed through it",
                 repo, name, branch=branch, path=path,
                 viaTool=payload.get("tool_name"), ok=True)
        recorded += 1
    if recorded:
        print(f"worktree-gate: recorded {recorded} worktree(s) created outside the gate",
              file=sys.stderr)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(1)

    if payload.get("hook_event_name") == "PostToolUse":
        try:
            record_created(payload)
        except Exception as exc:
            print(f"worktree-gate: could not record: {exc}", file=sys.stderr)
        sys.exit(0)

    denial = board_write_denial(payload)
    if denial:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": denial}}))
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(1)
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = payload.get("cwd") or ""
    # Why: the gate never acts above its own workspace. Anything outside keeps normal git.
    if not (cwd.startswith(ROOT) or ROOT in command):
        sys.exit(1)

    for segment in segments(command):
        hit = gated_call(segment)
        if not hit:
            continue
        sub, tool = hit
        try:
            import wtledger as L
            # Why: the denial belongs to the scope the caller is standing in. A folder
            # with its own .workboard keeps its own journal; writing into a parent's
            # would put one scope's events on another scope's board.
            try:
                scope = L.scope_of(cwd) if cwd.startswith(ROOT) else L.WORKSPACE
            except L.GateError:
                scope = L.WORKSPACE
            L.append({"ts": L.now(), "action": "denied",
                      "session": payload.get("session_id", "unknown"),
                      "description": f"raw bash `git {sub}` blocked by gate",
                      "repo": None, "worktree": None, "ok": False,
                      "scope": os.path.relpath(scope, L.WORKSPACE),
                      "command": command[:400], "cwd": cwd, "suggested": tool}, scope)
        except Exception as exc:
            print(f"worktree-gate: could not record denial: {exc}", file=sys.stderr)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": MESSAGE.format(
                cmd=segment.strip()[:200], tool=tool.split(" / ")[0], root=ROOT),
        }}))
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
