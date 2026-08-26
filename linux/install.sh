#!/usr/bin/env bash
# Install the workboard tools into a workspace. Idempotent; prints what it changed.
#
# Usage:  ./install.sh /path/to/workspace [--extension]
#
# The gate and board are copied/symlinked into the workspace. Registering the MCP server
# and the PreToolUse hook touches YOUR config, so this prints those two snippets rather
# than editing them behind your back.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${1:?usage: ./install.sh /path/to/workspace [--extension]}"
WS="$(cd "$WS" && pwd)"

ln -sfn "$HERE/worktree-gate" "$WS/.worktree-gate"
echo "linked  $WS/.worktree-gate -> $HERE/worktree-gate"

mkdir -p "$WS/.workboard"
ln -sfn "$HERE/board/build.py" "$WS/.workboard/build.py"
echo "linked  $WS/.workboard/build.py"
if [ ! -f "$WS/.workboard/items.json" ]; then
  cp "$HERE/../shared/board/items.example.json" "$WS/.workboard/items.json"
  echo "created $WS/.workboard/items.json (from the example — edit it)"
fi
[ -f "$WS/.workboard/notes.jsonl" ] || { : > "$WS/.workboard/notes.jsonl"; echo "created $WS/.workboard/notes.jsonl"; }

if [ "${2:-}" = "--extension" ]; then
  EXT="${HOME}/.vscode-server/extensions"; [ -d "$EXT" ] || EXT="${HOME}/.vscode/extensions"
  mkdir -p "$EXT"
  ln -sfn "$HERE/vscode-extension" "$EXT/cyberdine.workboard-0.1.0"
  echo "linked  $EXT/cyberdine.workboard-0.1.0  (Developer: Reload Window)"
fi

cat <<SNIP

Two things this does NOT change for you — they live in your own config:

1) MCP server — add to $WS/.mcp.json
   { "mcpServers": { "worktree-gate": { "type": "stdio", "command": "python3",
       "args": ["$WS/.worktree-gate/server.py"], "env": {} } } }

2) Gate hook — add to ~/.claude/settings.json under hooks.PreToolUse (FIRST, so a deny
   wins before anything expensive runs):
   { "matcher": "Bash", "hooks": [ { "type": "command",
       "command": "python3 $WS/.worktree-gate/gate.py", "timeout": 5,
       "statusMessage": "Checking worktree gate..." } ] }

Then:  python3 $WS/.workboard/build.py
SNIP
