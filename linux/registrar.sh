#!/usr/bin/env bash
# registrar — set up and check a workboard/registrar environment.
#
#   registrar doctor [scope]              audit a scope; exit 1 if anything is wrong
#   registrar init-scope <path> [--docs]  create a scope (board, gate link, worktrees)
#   registrar add-project <repo> [opts]   write <repo>/.registrar.json and check CLAUDE.md
#   registrar add-docs <name> [opts]      create docs/<name> with CLAUDE.md + coverage.json
#
# Everything it writes is a file you can read and delete. It never edits your Claude Code
# settings -- those are printed for you to paste, because a tool that silently rewrites your
# configuration is worse than one that asks.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ok=0; warn=0; bad=0
say()  { printf '  %s\n' "$*"; }
pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; ok=$((ok+1)); }
flag() { printf '  \033[33m!\033[0m %s\n' "$*"; warn=$((warn+1)); }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; bad=$((bad+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# --------------------------------------------------------------------------- doctor

doctor() {
  local scope="${1:-$PWD}"
  scope="$(cd "$scope" && pwd)"
  printf '\033[1mregistrar doctor\033[0m  %s\n' "$scope"

  head_ "Scope"
  [ -d "$scope/.workboard" ] && pass ".workboard/ present — this directory is a scope" \
                             || fail ".workboard/ missing — not a scope. Run: registrar init-scope $scope"
  [ -f "$scope/.workboard/items.json" ] && pass "items.json present" || fail "items.json missing"
  [ -f "$scope/.workboard/notes.jsonl" ] && pass "notes.jsonl present" || flag "notes.jsonl missing (created on first note)"
  [ -d "$scope/.worktrees" ] && pass ".worktrees/ present" || flag ".worktrees/ missing (created on first worktree)"

  head_ "Gate"
  if [ -e "$scope/.worktree-gate/server.py" ]; then
    pass "gate present at .worktree-gate/"
    [ -L "$scope/.worktree-gate" ] && pass "linked to a repo checkout (stays canonical)" \
                                   || flag "gate is a COPY, not a link — it will drift from the repo"
  else
    fail ".worktree-gate/ missing. Run: ./install.sh $scope"
  fi
  [ -f "$scope/.worktree-gate/ledger.jsonl" ] && pass "ledger.jsonl present ($(wc -l < "$scope/.worktree-gate/ledger.jsonl") entries)" \
                                              || flag "ledger.jsonl empty/absent — nothing recorded yet"

  head_ "Hook and MCP registration"
  local settings="$HOME/.claude/settings.json"
  if [ -f "$settings" ] && grep -q "worktree-gate" "$settings" 2>/dev/null; then
    grep -q '"PostToolUse"' "$settings" && pass "PostToolUse hook registered (entrance attribution)" \
                                        || flag "PostToolUse hook NOT registered — worktrees made by EnterWorktree go unattributed"
    pass "PreToolUse hook registered"
  else
    fail "gate hook not registered in ~/.claude/settings.json"
  fi
  if grep -qs "worktree-gate" "$scope/.mcp.json" "$HOME/.claude.json" 2>/dev/null; then
    pass "MCP server registered"
  else
    flag "MCP server not registered — sessions must use the CLI form"
  fi

  head_ "Sub-scopes (excluded from this scope)"
  local nested=0
  for d in "$scope"/*/; do
    [ -d "${d}.workboard" ] && { say "$(basename "$d") — governs itself"; nested=$((nested+1)); }
  done
  [ "$nested" -eq 0 ] && say "(none)"

  head_ "Projects"
  local repos=0 cfg=0 cmd=0
  for d in "$scope"/*/; do
    d="${d%/}"; [ -d "$d/.git" ] || continue
    [ -d "$d/.workboard" ] && continue                 # a sub-scope, not a project here
    repos=$((repos+1))
    [ -f "$d/.registrar.json" ] && cfg=$((cfg+1))
    [ -f "$d/CLAUDE.md" ] && cmd=$((cmd+1))
  done
  [ "$repos" -gt 0 ] && say "$repos repositories in this scope"
  [ "$cfg" -eq "$repos" ] && pass "all have .registrar.json" \
                          || fail "$((repos-cfg)) of $repos missing .registrar.json — the gate cannot tell if docs are required"
  [ "$cmd" -eq "$repos" ] && pass "all have CLAUDE.md" \
                          || flag "$((repos-cmd)) of $repos missing CLAUDE.md"

  head_ "Documentation"
  if [ -d "$scope/docs" ]; then
    pass "docs/ present"
    [ -f "$scope/docs/CLAUDE.md" ] && pass "docs/CLAUDE.md (router instructions)" || fail "docs/CLAUDE.md missing — nothing tells the router how to route"
    [ -f "$scope/docs/_routes.json" ] && pass "_routes.json" || flag "_routes.json missing — no precedence or fallback"
    local dp=0 dc=0 dm=0
    for d in "$scope"/docs/*/; do
      [ -d "$d" ] || continue; dp=$((dp+1))
      [ -f "${d}coverage.json" ] && dc=$((dc+1))
      [ -f "${d}CLAUDE.md" ] && dm=$((dm+1))
    done
    say "$dp documentation project(s)"
    [ "$dc" -eq "$dp" ] && pass "all declare coverage.json" || fail "$((dp-dc)) of $dp missing coverage.json — unroutable"
    [ "$dm" -eq "$dp" ] && pass "all have CLAUDE.md"        || fail "$((dp-dm)) of $dp missing CLAUDE.md — the registrar would invent a process"
  else
    fail "docs/ missing — documentation projects are not enumerable. Run: registrar add-docs <name>"
  fi

  printf '\n\033[1m%d ok · %d warn · %d wrong\033[0m\n' "$ok" "$warn" "$bad"
  [ "$bad" -eq 0 ]
}

# --------------------------------------------------------------------------- init-scope

init_scope() {
  local path="${1:?usage: registrar init-scope <path> [--docs]}"
  mkdir -p "$path"; path="$(cd "$path" && pwd)"
  mkdir -p "$path/.workboard" "$path/.worktrees"

  [ -f "$path/.workboard/items.json" ] || {
    cp "$HERE/../shared/board/items.example.json" "$path/.workboard/items.json"
    say "created .workboard/items.json from the example — edit it"
  }
  [ -f "$path/.workboard/notes.jsonl" ] || : > "$path/.workboard/notes.jsonl"
  ln -sfn "$HERE/board/build.py" "$path/.workboard/build.py"
  ln -sfn "$HERE/worktree-gate"  "$path/.worktree-gate"
  say "linked .worktree-gate -> $HERE/worktree-gate"

  [ -f "$path/CLAUDE.md" ] || {
    cat > "$path/CLAUDE.md" <<'EOF'
# CLAUDE.md — workspace supervisor

Rules that hold in every repository in this workspace.

## Worktrees are gated
Raw `git worktree` / `merge` / `pull` / `branch -d` from a shell is blocked. Use the
`worktree-gate` MCP tools. `session`, `description` and `task` are mandatory and are
validated before any git runs. Read-only git is untouched.

## Worktrees live at the scope root
`<scope>/.worktrees/<task>-<repo>`, cut from the main checkout's local HEAD — never from
`origin/*`. Merge back into the branch it was cut from, push, then prune, in one pass.

## A `.workboard/` folder defines a scope
A subdirectory with its own `.workboard/` governs itself: its own board, journal and
worktrees. The parent ignores it entirely.

## The board
`.workboard/items.json` is written by the orchestrating session only.
`.workboard/notes.jsonl` is append-only and anyone may add to it. `WORKBOARD.html` is
generated — never edit it.

## Every task is on the board
The gate refuses a mutation whose `task` is not an item on the board or a tracker issue key.
EOF
    say "created CLAUDE.md (supervisor)"
  }

  if [ "${2:-}" = "--docs" ]; then mkdir -p "$path/docs"; write_docs_root "$path"; fi

  cat <<EOF

Scope ready at $path

Register the hook — add to ~/.claude/settings.json under hooks.PreToolUse (FIRST):
  { "matcher": "Bash", "hooks": [ { "type": "command",
      "command": "python3 $path/.worktree-gate/gate.py", "timeout": 5 } ] }
and under hooks.PostToolUse:
  { "matcher": "EnterWorktree", "hooks": [ { "type": "command",
      "command": "python3 $path/.worktree-gate/gate.py", "timeout": 10 } ] }

Register the MCP server — $path/.mcp.json:
  { "mcpServers": { "worktree-gate": { "type": "stdio", "command": "python3",
      "args": ["$path/.worktree-gate/server.py"] } } }

Then: registrar doctor $path
EOF
}

write_docs_root() {
  local path="$1"
  [ -f "$path/docs/_routes.json" ] || cat > "$path/docs/_routes.json" <<'EOF'
{
  "fallback": null,
  "unrouted": "_unrouted.jsonl",
  "note": "fallback null means an unmatched change is queued in _unrouted.jsonl rather than sent to a default project. Set it to a project name only if one project genuinely owns everything unclaimed."
}
EOF
  [ -f "$path/docs/CLAUDE.md" ] || cat > "$path/docs/CLAUDE.md" <<'EOF'
# CLAUDE.md — documentation router

You select which documentation project owns a change. You do not write documentation.

## How to route
1. Read every `*/coverage.json` in this directory. A project with no `coverage.json` is
   unroutable — name it in your answer, every time, until it has one.
2. Match the report's `repo` and changed paths against each `covers` entry, then remove
   anything matching that project's `ignores`.
3. More than one match: highest `precedence` wins. Record which projects matched and which
   you chose.
4. No match: append the report to `_unrouted.jsonl`. Do not send it to a default project
   unless `_routes.json` names one. A subsystem no document covers is a finding.
5. Write your decision into the review record. An unrecorded route cannot be audited.

## What you never do
Open the source, edit a documentation page, or decide whether a claim is still true. You
choose an owner and hand off.
EOF
  say "created docs/CLAUDE.md and docs/_routes.json"
}

# --------------------------------------------------------------------------- add-project

add_project() {
  local repo="${1:?usage: registrar add-project <repo> [--docs <project>] [--agent <name>] [--no-docs] [--tracker workboard|youtrack|none]}"
  shift
  local scope docs_project="" agent="" required="true" tracker="workboard"
  scope="$(cd "$(dirname "$repo")" && pwd)"; repo="$(cd "$repo" && pwd)"
  while [ $# -gt 0 ]; do
    case "$1" in
      --docs)    docs_project="$2"; shift 2;;
      --agent)   agent="$2"; shift 2;;
      --no-docs) required="false"; shift;;
      --tracker) tracker="$2"; shift 2;;
      *) echo "unknown option: $1" >&2; return 2;;
    esac
  done
  [ -d "$repo/.git" ] || { echo "$repo is not a git repository" >&2; return 1; }

  local name; name="$(basename "$repo")"
  local tracker_block
  case "$tracker" in
    workboard) tracker_block='{ "kind": "workboard", "path": "../.workboard/items.json" }';;
    youtrack)  tracker_block='{ "kind": "youtrack", "keyPattern": "^[A-Z][A-Z0-9]*-[0-9]+$" }';;
    none)      tracker_block='{ "kind": "none" }';;
    *) echo "tracker must be workboard, youtrack or none" >&2; return 2;;
  esac

  local doc_block
  if [ "$required" = "false" ]; then
    doc_block='{ "required": false }'
  elif [ -n "$docs_project" ]; then
    doc_block="{ \"required\": true, \"project\": \"docs/$docs_project\"$([ -n "$agent" ] && echo ", \"agent\": \"$agent\"" ), \"model\": \"sonnet\" }"
  else
    doc_block='{ "required": true, "model": "sonnet" }'
  fi

  cat > "$repo/.registrar.json" <<EOF
{
  "project": "$name",
  "claudeMd": "CLAUDE.md",
  "tracker": $tracker_block,
  "documentation": $doc_block
}
EOF
  say "wrote $repo/.registrar.json"
  [ -f "$repo/CLAUDE.md" ] || flag "$name has no CLAUDE.md — a session here has no project rules"
  [ "$required" = "true" ] && [ -z "$docs_project" ] && say "documentation project unset — the router will choose from coverage.json"
}

# --------------------------------------------------------------------------- add-docs

add_docs() {
  local name="${1:?usage: registrar add-docs <name> [--scope <path>] [--agent <name>] [--covers <repo>]}"
  shift
  local scope="$PWD" agent="" covers=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --scope)  scope="$2"; shift 2;;
      --agent)  agent="$2"; shift 2;;
      --covers) covers="$2"; shift 2;;
      *) echo "unknown option: $1" >&2; return 2;;
    esac
  done
  scope="$(cd "$scope" && pwd)"
  local d="$scope/docs/$name"
  mkdir -p "$d/content"
  [ -f "$scope/docs/CLAUDE.md" ] || write_docs_root "$scope"

  cat > "$d/coverage.json" <<EOF
{
  "project": "$name",
  "agent": "${agent:-$name-maintainer}",
  "model": "sonnet",
  "covers": [$([ -n "$covers" ] && echo "
    { \"repo\": \"$covers\", \"paths\": [\"**\"] }
  ")],
  "ignores": ["**/*.Tests/**", "**/obj/**", "**/bin/**"],
  "answers": [],
  "precedence": 10
}
EOF
  [ -f "$d/CLAUDE.md" ] || cat > "$d/CLAUDE.md" <<EOF
# CLAUDE.md — $name

# YOU ARE THE REGISTRAR OF THIS CORPUS, NOT A RESEARCHER

You record what changed and which documents it affects. You do not verify claims against
source, and you do not edit pages to match a diff. A page changes when someone re-verifies
it against source, cites it, and re-stamps it — a different act, by a different agent.

## Where the index lives
- \`MEMORY.md\` — the index, one line per document, loaded at session start.
- \`doc_<slug>.md\` — one per document: its pages, what it answers, its stamp, what is known
  wrong. This is the backbone of the index.
- \`reference_corpus_roster.md\` — every slug, its stamp, its status.
- \`content/<slug>.json\` + \`content/<slug>.meta.json\` — the pages and their
  \`{repo, commit, verified}\` stamps.

## Inbound protocol — "I just updated…"
1. **Pin the change.** repo, branch, commit, and what moved. A change you cannot resolve to
   a ref cannot be checked later — ask for the sha in the same reply if it is missing.
2. **Answer from the index, in the reply.** Which documents and pages are affected, by name.
3. **Write \`change_<yyyymmdd>_<topic>.md\`** — reporter, repo, ref, what moved, affected
   documents, and the precise claim that is now wrong. Quote the sentence.
4. **Mark each affected \`doc_<slug>.md\` \`Status: suspect\`** with a one-line why and a link
   to the change memory.
5. **If nothing covers it, write \`gap_<topic>.md\`** and say so. A new subsystem with no
   document is a finding, not a silence.

## What you never do
Open the source to confirm a report, re-stamp a \`meta.json\`, or edit a page to match.
EOF
  say "created $d with coverage.json and CLAUDE.md"
  say "edit coverage.json: fill in covers[] and answers[] before the router can use it"
}

# --------------------------------------------------------------------------- sweep

sweep() {
  local scope="${1:-$PWD}" days="${2:-30}"
  scope="$(cd "$scope" && pwd)"
  printf '\033[1mregistrar sweep\033[0m  %s  (loose files older than %s days)\n' "$scope" "$days"
  printf '\nNothing is deleted. Each file is classified and the EVIDENCE is shown, because a\n'
  printf 'reference count alone cannot tell a dependency from a mention in prose.\n'

  local linked=() mentioned=() orphan=()
  while IFS= read -r f; do
    local base; base="$(basename "$f")"
    case "$base" in CLAUDE.md|README.md|GEMINI.md) continue;; esac
    # A LINK is something that breaks: markdown link syntax, or a path-ish reference.
    local hit
    hit=$(grep -rn -- "]($base\|](\./$base\|\`$base\`\|/$base" \
            "$scope"/*.md "$scope"/docs "$HOME"/.claude/projects/*/memory 2>/dev/null | head -1)
    if [ -n "$hit" ]; then linked+=("$base|$hit"); continue; fi
    # A MENTION is prose naming it. Not a reason to keep, but a reason to look.
    hit=$(grep -rn -- "$base" "$scope"/*.md "$scope"/docs "$HOME"/.claude/projects/*/memory 2>/dev/null | head -1)
    if [ -n "$hit" ]; then mentioned+=("$base|$hit"); else orphan+=("$base|"); fi
  done < <(find "$scope" -maxdepth 1 -type f \( -name '*.md' -o -name '*.txt' -o -name '*.log' \
             -o -name '*.zip' -o -name '*.tsv' -o -name '*.html' \) -mtime "+$days")

  show() { local label="$1" colour="$2"; shift 2
    [ $# -eq 0 ] && return
    printf '\n\033[%sm%s\033[0m\n' "$colour" "$label"
    for e in "$@"; do
      printf '  %s\n' "${e%%|*}"
      [ -n "${e#*|}" ] && printf '      \033[2m%s\033[0m\n' "$(echo "${e#*|}" | cut -c1-120)"
    done; }
  show "LINKED — something points at these. Do not archive without fixing the link." 33 "${linked[@]}"
  show "MENTIONED — named in prose only. Read the line, then decide." 36 "${mentioned[@]}"
  show "UNREFERENCED — nothing names them." 32 "${orphan[@]}"

  printf '\n\033[2mArchive what you have judged:\033[0m\n'
  printf '  mkdir -p archive/$(date +%%Y-%%m) && mv <file>... archive/$(date +%%Y-%%m)/\n'
}

case "${1:-}" in
  doctor)       shift; doctor "$@";;
  sweep)        shift; sweep "$@";;
  init-scope)   shift; init_scope "$@";;
  add-project)  shift; add_project "$@";;
  add-docs)     shift; add_docs "$@";;
  *) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2;;
esac
