#Requires -Version 7.0
<#
.SYNOPSIS
    Set up and check a workboard/registrar environment.

.DESCRIPTION
    PowerShell port of registrar.sh. Same files, same layout, same checks.

      Registrar.ps1 doctor      [-Scope <path>]
      Registrar.ps1 init-scope  -Path <path> [-WithDocs]
      Registrar.ps1 add-project -Repo <path> [-Docs <name>] [-Agent <name>] [-NoDocs]
                                [-Tracker workboard|youtrack|none]
      Registrar.ps1 add-docs    -Name <name> [-Scope <path>] [-Agent <name>] [-Covers <repo>]

    It never edits your Claude Code settings. Registration snippets are printed for you to
    paste -- a tool that silently rewrites your configuration is worse than one that asks.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory)]
    [ValidateSet('doctor', 'init-scope', 'add-project', 'add-docs')]
    [string]$Command,

    [string]$Scope = $PWD,
    [string]$Path,
    [string]$Repo,
    [string]$Name,
    [string]$Docs,
    [string]$Agent,
    [string]$Covers,
    [ValidateSet('workboard', 'youtrack', 'none')][string]$Tracker = 'workboard',
    [switch]$NoDocs,
    [switch]$WithDocs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $PSCommandPath

$script:Ok = 0; $script:Warn = 0; $script:Bad = 0
function Say  { param($m) Write-Host "  $m" }
function Pass { param($m) Write-Host "  ✓ $m" -ForegroundColor Green; $script:Ok++ }
function Flag { param($m) Write-Host "  ! $m" -ForegroundColor Yellow; $script:Warn++ }
function Fail { param($m) Write-Host "  ✗ $m" -ForegroundColor Red;   $script:Bad++ }
function Section { param($m) Write-Host ""; Write-Host $m -ForegroundColor White }

function Invoke-Doctor {
    param([string]$ScopePath)
    $s = (Resolve-Path $ScopePath).Path
    Write-Host "registrar doctor  $s"

    Section 'Scope'
    if (Test-Path "$s/.workboard") { Pass '.workboard/ present — this directory is a scope' }
    else { Fail ".workboard/ missing — not a scope. Run: Registrar.ps1 init-scope -Path $s" }
    if (Test-Path "$s/.workboard/items.json")  { Pass 'items.json present' }  else { Fail 'items.json missing' }
    if (Test-Path "$s/.workboard/notes.jsonl") { Pass 'notes.jsonl present' } else { Flag 'notes.jsonl missing (created on first note)' }
    if (Test-Path "$s/.worktrees")             { Pass '.worktrees/ present' } else { Flag '.worktrees/ missing (created on first worktree)' }

    Section 'Gate'
    if (Test-Path "$s/.worktree-gate/server.py") {
        Pass 'gate present at .worktree-gate/'
        $item = Get-Item "$s/.worktree-gate" -Force
        if ($item.LinkType) { Pass 'linked to a repo checkout (stays canonical)' }
        else { Flag 'gate is a COPY, not a link — it will drift from the repo' }
    } else { Fail '.worktree-gate/ missing' }
    if (Test-Path "$s/.worktree-gate/ledger.jsonl") {
        $n = (Get-Content "$s/.worktree-gate/ledger.jsonl").Count
        Pass "ledger.jsonl present ($n entries)"
    } else { Flag 'ledger.jsonl empty/absent — nothing recorded yet' }

    Section 'Hook and MCP registration'
    $settings = Join-Path $HOME '.claude/settings.json'
    if ((Test-Path $settings) -and (Select-String -Path $settings -Pattern 'worktree-gate' -Quiet)) {
        Pass 'PreToolUse hook registered'
        if (Select-String -Path $settings -Pattern 'PostToolUse' -Quiet) {
            Pass 'PostToolUse hook registered (entrance attribution)'
        } else { Flag 'PostToolUse hook NOT registered — worktrees made by EnterWorktree go unattributed' }
    } else { Fail 'gate hook not registered in ~/.claude/settings.json' }

    $mcpFound = @("$s/.mcp.json", (Join-Path $HOME '.claude.json')) |
        Where-Object { (Test-Path $_) -and (Select-String -Path $_ -Pattern 'worktree-gate' -Quiet) }
    if ($mcpFound) { Pass 'MCP server registered' }
    else { Flag 'MCP server not registered — sessions must use the CLI form' }

    Section 'Sub-scopes (excluded from this scope)'
    $nested = Get-ChildItem $s -Directory | Where-Object { Test-Path "$($_.FullName)/.workboard" }
    if ($nested) { $nested | ForEach-Object { Say "$($_.Name) — governs itself" } } else { Say '(none)' }
    $nestedNames = @($nested | ForEach-Object { $_.Name })

    Section 'Projects'
    $repos = Get-ChildItem $s -Directory |
             Where-Object { (Test-Path "$($_.FullName)/.git") -and ($nestedNames -notcontains $_.Name) }
    $cfg = @($repos | Where-Object { Test-Path "$($_.FullName)/.registrar.json" }).Count
    $cmd = @($repos | Where-Object { Test-Path "$($_.FullName)/CLAUDE.md" }).Count
    Say "$($repos.Count) repositories in this scope"
    if ($cfg -eq $repos.Count) { Pass 'all have .registrar.json' }
    else { Fail "$($repos.Count - $cfg) of $($repos.Count) missing .registrar.json — the gate cannot tell if docs are required" }
    if ($cmd -eq $repos.Count) { Pass 'all have CLAUDE.md' }
    else { Flag "$($repos.Count - $cmd) of $($repos.Count) missing CLAUDE.md" }

    Section 'Documentation'
    if (Test-Path "$s/docs") {
        Pass 'docs/ present'
        if (Test-Path "$s/docs/CLAUDE.md")     { Pass 'docs/CLAUDE.md (router instructions)' } else { Fail 'docs/CLAUDE.md missing — nothing tells the router how to route' }
        if (Test-Path "$s/docs/_routes.json")  { Pass '_routes.json' } else { Flag '_routes.json missing — no precedence or fallback' }
        $dp = Get-ChildItem "$s/docs" -Directory
        $dc = @($dp | Where-Object { Test-Path "$($_.FullName)/coverage.json" }).Count
        $dm = @($dp | Where-Object { Test-Path "$($_.FullName)/CLAUDE.md" }).Count
        Say "$($dp.Count) documentation project(s)"
        if ($dc -eq $dp.Count) { Pass 'all declare coverage.json' } else { Fail "$($dp.Count - $dc) of $($dp.Count) missing coverage.json — unroutable" }
        if ($dm -eq $dp.Count) { Pass 'all have CLAUDE.md' }        else { Fail "$($dp.Count - $dm) of $($dp.Count) missing CLAUDE.md — the registrar would invent a process" }
    } else { Fail 'docs/ missing — documentation projects are not enumerable' }

    Write-Host ''
    Write-Host "$($script:Ok) ok · $($script:Warn) warn · $($script:Bad) wrong"
    if ($script:Bad -gt 0) { exit 1 }
}

function New-DocsRoot {
    param([string]$ScopePath)
    $d = Join-Path $ScopePath 'docs'
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    $routes = Join-Path $d '_routes.json'
    if (-not (Test-Path $routes)) {
        @{ fallback = $null; unrouted = '_unrouted.jsonl'
           note = 'fallback null means an unmatched change is queued in _unrouted.jsonl rather than sent to a default project.'
        } | ConvertTo-Json -Depth 6 | Set-Content $routes -Encoding utf8
    }
    $cm = Join-Path $d 'CLAUDE.md'
    if (-not (Test-Path $cm)) {
        @'
# CLAUDE.md — documentation router

You select which documentation project owns a change. You do not write documentation.

## How to route
1. Read every `*/coverage.json` in this directory. A project with no `coverage.json` is
   unroutable — name it in your answer, every time, until it has one.
2. Match the report's `repo` and changed paths against each `covers` entry, then remove
   anything matching that project's `ignores`.
3. More than one match: highest `precedence` wins. Record which projects matched.
4. No match: append to `_unrouted.jsonl`. Do not send it to a default project unless
   `_routes.json` names one. A subsystem no document covers is a finding.
5. Write your decision into the review record. An unrecorded route cannot be audited.

## What you never do
Open the source, edit a documentation page, or decide whether a claim is still true.
'@ | Set-Content $cm -Encoding utf8
    }
    Say 'created docs/CLAUDE.md and docs/_routes.json'
}

function Invoke-InitScope {
    param([string]$TargetPath, [switch]$Docs)
    New-Item -ItemType Directory -Path $TargetPath -Force | Out-Null
    $p = (Resolve-Path $TargetPath).Path
    New-Item -ItemType Directory -Path "$p/.workboard", "$p/.worktrees" -Force | Out-Null

    if (-not (Test-Path "$p/.workboard/items.json")) {
        Copy-Item "$Here/../shared/board/items.example.json" "$p/.workboard/items.json"
        Say 'created .workboard/items.json from the example — edit it'
    }
    if (-not (Test-Path "$p/.workboard/notes.jsonl")) { New-Item -ItemType File -Path "$p/.workboard/notes.jsonl" | Out-Null }
    if ($Docs) { New-DocsRoot -ScopePath $p }

    Write-Host ""
    Write-Host "Scope ready at $p"
    Write-Host ""
    Write-Host "Register the hook — ~/.claude/settings.json, hooks.PreToolUse (FIRST):"
    Write-Host "  { `"matcher`": `"Bash`", `"hooks`": [ { `"type`": `"command`","
    Write-Host "      `"command`": `"pwsh -NoProfile -NonInteractive -File $p/.worktree-gate/Invoke-WorktreeGate.ps1`", `"timeout`": 10 } ] }"
    Write-Host ""
    Write-Host "Then: Registrar.ps1 doctor -Scope $p"
}

function Invoke-AddProject {
    param([string]$RepoPath, [string]$DocsProject, [string]$AgentName,
          [switch]$SkipDocs, [string]$TrackerKind)
    $r = (Resolve-Path $RepoPath).Path
    if (-not (Test-Path "$r/.git")) { throw "$r is not a git repository" }

    $trackerBlock = switch ($TrackerKind) {
        'workboard' { @{ kind = 'workboard'; path = '../.workboard/items.json' } }
        'youtrack'  { @{ kind = 'youtrack'; keyPattern = '^[A-Z][A-Z0-9]*-[0-9]+$' } }
        'none'      { @{ kind = 'none' } }
    }
    $docBlock = if ($SkipDocs) { @{ required = $false } }
                elseif ($DocsProject) {
                    $h = @{ required = $true; project = "docs/$DocsProject"; model = 'sonnet' }
                    if ($AgentName) { $h.agent = $AgentName }; $h
                } else { @{ required = $true; model = 'sonnet' } }

    # Depth MUST be explicit: ConvertTo-Json defaults to 2 and silently truncates.
    @{ project = (Split-Path -Leaf $r); claudeMd = 'CLAUDE.md'
       tracker = $trackerBlock; documentation = $docBlock } |
        ConvertTo-Json -Depth 12 | Set-Content "$r/.registrar.json" -Encoding utf8
    Say "wrote $r/.registrar.json"
    if (-not (Test-Path "$r/CLAUDE.md")) { Flag "$(Split-Path -Leaf $r) has no CLAUDE.md — a session here has no project rules" }
}

function Invoke-AddDocs {
    param([string]$DocName, [string]$ScopePath, [string]$AgentName, [string]$CoversRepo)
    $s = (Resolve-Path $ScopePath).Path
    $d = Join-Path $s "docs/$DocName"
    New-Item -ItemType Directory -Path "$d/content" -Force | Out-Null
    if (-not (Test-Path "$s/docs/CLAUDE.md")) { New-DocsRoot -ScopePath $s }

    $covers = if ($CoversRepo) { @(@{ repo = $CoversRepo; paths = @('**') }) } else { @() }
    @{ project = $DocName
       agent = $(if ($AgentName) { $AgentName } else { "$DocName-maintainer" })
       model = 'sonnet'; covers = $covers
       ignores = @('**/*.Tests/**', '**/obj/**', '**/bin/**')
       answers = @(); precedence = 10 } |
        ConvertTo-Json -Depth 12 | Set-Content "$d/coverage.json" -Encoding utf8

    if (-not (Test-Path "$d/CLAUDE.md")) {
        @"
# CLAUDE.md — $DocName

# YOU ARE THE REGISTRAR OF THIS CORPUS, NOT A RESEARCHER

You record what changed and which documents it affects. You do not verify claims against
source, and you do not edit pages to match a diff. A page changes when someone re-verifies
it against source, cites it, and re-stamps it.

## Where the index lives
- ``MEMORY.md`` — the index, one line per document, loaded at session start.
- ``doc_<slug>.md`` — one per document: pages, what it answers, stamp, what is known wrong.
- ``reference_corpus_roster.md`` — every slug, its stamp, its status.
- ``content/<slug>.json`` + ``content/<slug>.meta.json`` — pages and their stamps.

## Inbound protocol — "I just updated…"
1. Pin the change: repo, branch, commit, what moved. Ask for the sha if it is missing.
2. Answer from the index, in the reply: which documents and pages, by name.
3. Write ``change_<yyyymmdd>_<topic>.md`` — reporter, ref, what moved, affected documents,
   and the precise claim that is now wrong. Quote the sentence.
4. Mark each affected ``doc_<slug>.md`` ``Status: suspect`` with a one-line why.
5. If nothing covers it, write ``gap_<topic>.md`` and say so.

## What you never do
Open the source to confirm a report, re-stamp a meta.json, or edit a page to match.
"@ | Set-Content "$d/CLAUDE.md" -Encoding utf8
    }
    Say "created $d with coverage.json and CLAUDE.md"
    Say 'edit coverage.json: fill in covers[] and answers[] before the router can use it'
}

switch ($Command) {
    'doctor'      { Invoke-Doctor -ScopePath $Scope }
    'init-scope'  { Invoke-InitScope -TargetPath $(if ($Path) { $Path } else { $Scope }) -Docs:$WithDocs }
    'add-project' { Invoke-AddProject -RepoPath $Repo -DocsProject $Docs -AgentName $Agent -SkipDocs:$NoDocs -TrackerKind $Tracker }
    'add-docs'    { Invoke-AddDocs -DocName $Name -ScopePath $Scope -AgentName $Agent -CoversRepo $Covers }
}
