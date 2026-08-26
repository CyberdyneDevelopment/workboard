#Requires -Version 7.0
<#
.SYNOPSIS
    PreToolUse / PostToolUse gate: worktree lifecycle operations go through the MCP server,
    not raw shell git.

.DESCRIPTION
    PowerShell port of gate.py. Same contract, same ledger format -- see SPEC.md.

    PreToolUse on a shell tool  : denies the gated git verbs and records the attempt.
    PostToolUse on EnterWorktree: RECORDS what appeared, never denies.

    Exit 0 with deny JSON on stdout -> blocked.
    Exit 1                          -> no decision; other hooks decide as normal.

    Why record rather than deny on EnterWorktree: it is a first-class tool, and blocking it
    teaches people to route around the gate -- which is how the gate came to govern the exit
    and not the entrance in the first place.

.NOTES
    Startup cost is the reason to think before installing this as a PreToolUse hook.
    Measured on this estate: pwsh ~560 ms per invocation against ~21 ms for the Python
    equivalent, and that is bare interpreter startup -- the gate logic adds almost nothing.
    On a box that also has Python, prefer the Python hook even in a PowerShell environment;
    they are separate processes and share only the ledger. See README.md.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Here      = Split-Path -Parent $PSCommandPath
$Workspace = Split-Path -Parent (Split-Path -Parent $Here)

# Shell words that wrap another command; look past them for the real verb.
$Wrappers = @('sudo','env','nohup','timeout','nice','ionice','command','exec','stdbuf','xargs','time')
# git global flags that take a value, so the subcommand is one further along.
$Valued   = @('-C','-c','--git-dir','--work-tree','--namespace','--exec-path')

# subcommand -> (trigger args | $null for "always gated"), suggested tool
$Gated = @{
    'worktree' = @{ Triggers = @('add','remove','move','prune','lock','unlock','repair')
                    Tool = 'worktree_create' }
    'merge'    = @{ Triggers = $null; Tool = 'worktree_merge' }
    'pull'     = @{ Triggers = $null; Tool = 'worktree_pull' }
    'branch'   = @{ Triggers = @('-d','-D','--delete','-m','-M','--move')
                    Tool = 'worktree_prune' }
}

function Remove-HeredocBodies([string]$Command) {
    # Why: a heredoc payload is data, not commands. A runbook that contains a worktree command
    # on its own line would otherwise be blocked -- and the agent brief literally contains one.
    $lines = $Command -split "`n"
    $out = [System.Collections.Generic.List[string]]::new()
    $i = 0
    while ($i -lt $lines.Count) {
        $line = $lines[$i]; $out.Add($line); $i++
        $m = [regex]::Match($line, "<<-?\s*(['`"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
        if (-not $m.Success) { continue }
        $delim = $m.Groups[2].Value
        while ($i -lt $lines.Count -and $lines[$i].Trim() -ne $delim) { $i++ }
        $i++   # drop the terminator too
    }
    ($out -join "`n")
}

function Get-RealVerb([string[]]$Tokens) {
    # Peel env assignments and wrapper commands; return tokens starting at the real command.
    $i = 0
    while ($i -lt $Tokens.Count) {
        $t = $Tokens[$i]
        if ($t -notmatch '^-' -and $t -match '^[A-Za-z_][A-Za-z0-9_]*=') { $i++; continue }
        $base = Split-Path -Leaf $t
        if ($Wrappers -contains $t -or $Wrappers -contains $base) {
            $i++
            while ($i -lt $Tokens.Count -and ($Tokens[$i] -match '^-' -or $Tokens[$i] -match '^\d+$')) { $i++ }
            continue
        }
        return $Tokens[$i..($Tokens.Count - 1)]
    }
    @()
}

function Get-GatedCall([string]$Segment) {
    $tokens = Get-RealVerb ($Segment -split '\s+' | Where-Object { $_ })
    if ($tokens.Count -eq 0) { return $null }
    if ((Split-Path -Leaf ($tokens[0].Trim("'", '"'))) -ne 'git') { return $null }

    $i = 1
    while ($i -lt $tokens.Count -and $tokens[$i] -match '^-') {
        if ($Valued -contains $tokens[$i]) { $i += 2 } else { $i++ }
    }
    if ($i -ge $tokens.Count) { return $null }
    $sub = $tokens[$i]
    if (-not $Gated.ContainsKey($sub)) { return $null }

    $spec = $Gated[$sub]
    if ($null -eq $spec.Triggers) { return @{ Sub = $sub; Tool = $spec.Tool } }
    $rest = if ($i + 1 -lt $tokens.Count) { $tokens[($i + 1)..($tokens.Count - 1)] } else { @() }
    $hit  = $rest | Where-Object { $spec.Triggers -contains $_ }
    if ($hit) { return @{ Sub = "$sub $($hit -join ' ')"; Tool = $spec.Tool } }
    $null
}

function Add-LedgerEntry([hashtable]$Entry, [string]$Scope) {
    $dir = Join-Path $Scope '.worktree-gate'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    # Sorted keys so both implementations emit comparable lines (SPEC.md §2).
    $ordered = [ordered]@{}
    foreach ($k in ($Entry.Keys | Sort-Object)) { $ordered[$k] = $Entry[$k] }
    # Depth MUST be explicit: ConvertTo-Json defaults to 2 and silently truncates.
    Add-Content -Path (Join-Path $dir 'ledger.jsonl') -Encoding utf8 `
        -Value ($ordered | ConvertTo-Json -Depth 12 -Compress)
}

function Resolve-Scope([string]$Path) {
    # Nearest enclosing directory holding .workboard, bounded at the workspace root.
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Workspace }
    $p = [System.IO.Path]::GetFullPath($Path)
    if (-not ($p -eq $Workspace -or $p.StartsWith($Workspace + [IO.Path]::DirectorySeparatorChar))) {
        return $Workspace
    }
    while ($p -ne $Workspace) {
        if (Test-Path (Join-Path $p '.workboard')) { return $p }
        $parent = Split-Path -Parent $p
        if ($parent -eq $p) { break }
        $p = $parent
    }
    $Workspace
}

# ----------------------------------------------------------------------------- main

$raw = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($raw)) { exit 1 }
try { $payload = $raw | ConvertFrom-Json -AsHashtable } catch { exit 1 }

$hookEvent = if ($payload.ContainsKey('hook_event_name')) { $payload.hook_event_name } else { '' }
$toolName  = if ($payload.ContainsKey('tool_name'))       { $payload.tool_name }       else { '' }
$cwd       = if ($payload.ContainsKey('cwd'))             { $payload.cwd }             else { '' }
$session   = if ($payload.ContainsKey('session_id'))      { $payload.session_id }      else { 'unknown' }

if ($hookEvent -eq 'PostToolUse') {
    # Record-only path. Reconciliation lives in the MCP server; this is the trigger.
    try {
        & (Join-Path $Here 'Start-WorktreeGateServer.ps1') -Call 'worktree_adopt' `
            -Arguments (@{ session = $session
                           description = "$toolName recorded by the gate, not routed through it"
                           confirm = $true } | ConvertTo-Json -Depth 6 -Compress) | Out-Null
    } catch { [Console]::Error.WriteLine("worktree-gate: could not record: $_") }
    exit 0
}

if ($toolName -notin @('Bash','Shell','PowerShell')) { exit 1 }
$command = if ($payload.tool_input -and $payload.tool_input.ContainsKey('command')) { $payload.tool_input.command } else { '' }
if ([string]::IsNullOrWhiteSpace($command)) { exit 1 }

# The gate never acts above its own workspace. Anything outside keeps normal git.
if (-not ($cwd.StartsWith($Workspace) -or $command.Contains($Workspace))) { exit 1 }

foreach ($segment in ((Remove-HeredocBodies $command) -split '&&|\|\||[;|\n]')) {
    if ([string]::IsNullOrWhiteSpace($segment)) { continue }
    $hit = Get-GatedCall $segment
    if (-not $hit) { continue }

    $scope = Resolve-Scope $cwd
    try {
        Add-LedgerEntry @{
            ts          = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
            action      = 'denied'
            session     = $session
            description = "raw shell ``git $($hit.Sub)`` blocked by gate"
            repo        = $null; worktree = $null; ok = $false
            scope       = [IO.Path]::GetRelativePath($Workspace, $scope)
            command     = $command.Substring(0, [Math]::Min(400, $command.Length))
            cwd         = $cwd; suggested = $hit.Tool
        } $scope
    } catch { [Console]::Error.WriteLine("worktree-gate: could not record denial: $_") }

    $reason = @"
Blocked: worktree lifecycle operations go through the worktree-gate MCP server.

  $($segment.Trim())

Use ``mcp__worktree-gate__$($hit.Tool)`` instead. It requires ``session`` and ``description``,
does the same git work, and records who did it and why in
.worktree-gate/ledger.jsonl -- which is what makes worktree ownership observed
rather than asserted.

If the MCP server is not in your tool list, use the recorded CLI instead:

  pwsh -File $Here/Start-WorktreeGateServer.ps1 -Call $($hit.Tool) -Arguments '{"session":"<you>","description":"<why>"}'

Read-only git (status, log, diff, worktree list, branch --show-current) is not gated.
"@
    [Console]::Out.WriteLine((@{
        hookSpecificOutput = @{
            hookEventName            = 'PreToolUse'
            permissionDecision       = 'deny'
            permissionDecisionReason = $reason
        }
    } | ConvertTo-Json -Depth 8 -Compress))
    exit 0
}

exit 1
