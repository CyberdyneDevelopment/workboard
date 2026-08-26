#Requires -Version 7.0
<#
.SYNOPSIS
    worktree-gate MCP server (stdio JSON-RPC).

.DESCRIPTION
    PowerShell port of server.py. Same tools, same refusals, same ledger format -- see SPEC.md,
    which is the contract. A ledger line written here must parse under the Python reader.

    TWO POWERSHELL TRAPS THIS FILE WORKS AROUND
      * ConvertTo-Json defaults to -Depth 2 and SILENTLY truncates deeper objects. A nested
        inputSchema becomes the string "System.Collections.Hashtable" and the client sees a
        malformed tool. Every serialisation here passes an explicit depth.
      * Any stray stdout corrupts the JSON-RPC stream. Diagnostics go to [Console]::Error and
        every protocol write goes through one Write-Rpc path.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Here = Split-Path -Parent $PSCommandPath
Import-Module (Join-Path $Here 'WtLedger.psm1') -Force

if ($env:WORKTREE_GATE_WORKSPACE) { Set-Workspace $env:WORKTREE_GATE_WORKSPACE }
else { Set-Workspace (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $Here))) }

# ----------------------------------------------------------------- helpers

function Write-Rpc([object]$Msg) {
    [Console]::Out.WriteLine(($Msg | ConvertTo-Json -Depth 24 -Compress))
    [Console]::Out.Flush()
}
function Trace([string]$m) { [Console]::Error.WriteLine("worktree-gate: $m") }

function Get-ScopeArg([hashtable]$a) {
    if ($a.ContainsKey('scope') -and $a.scope) {
        $p = if ([IO.Path]::IsPathRooted($a.scope)) { $a.scope } else { Join-Path (Get-Workspace) $a.scope }
        return Resolve-Scope $p
    }
    if ($a.ContainsKey('repo') -and $a.repo) { return Resolve-Scope (Get-RepoPath $a.repo) }
    Get-Workspace
}

function Get-BoardPath([string]$Scope) { Join-Path $Scope '.workboard/items.json' }

function Resolve-Task {
    <#  A free-text task field decays into "misc" within a week, and then the ledger says a
        worktree existed but not what it was for. Where a board exists the task must name a
        real item. An issue key is accepted without lookup -- the tracker is a different
        system this cannot reach. #>
    param([string]$Scope, [string]$Task)
    $t = $Task.Trim()
    $board = Get-BoardPath $Scope
    $isKey = $t -match '^[A-Z][A-Z0-9]*-\d+$'
    if (-not (Test-Path $board)) { return @{ task = $t; board = $null; verified = $isKey } }
    try { $data = Get-Content $board -Raw | ConvertFrom-Json -AsHashtable }
    catch { throw (New-GateError "$board is not valid JSON: $($_.Exception.Message)") }
    $ids = foreach ($g in $data.groups) { foreach ($i in $g.items) { $i.id } }
    if ($ids -contains $t -or $isKey) { return @{ task = $t; board = $board; verified = $true } }
    $open = foreach ($g in $data.groups) {
        foreach ($i in $g.items) { if ($i.status -ne 'done') { $i.id } } }
    $rel = [IO.Path]::GetRelativePath((Get-Workspace), $board)
    $list = (($open | Sort-Object) | Select-Object -First 14) -join ', '
    throw (New-GateError ("task '$t' is not an item on $rel and is not an issue key like NVS-87.`n" +
                          "Open items: $list`n" +
                          "File the work on the board first, or pass the issue key."))
}

$script:Mutating = @(
    'worktree_create','worktree_pull','worktree_sync','worktree_merge','worktree_prune',
    'worktree_adopt','worktree_commit','doc_review_resolve','expert_revive',
    'item_subscribe','item_unsubscribe','item_update','notification_relayed')

function Assert-Who([string]$Name, [hashtable]$a) {
    <#  Attribution is a PRECONDITION. Write-LedgerEntry enforces it too, but it runs after the
        operation: a blank description there once cut a worktree and then refused to record it,
        producing exactly the unattributed worktree this server exists to prevent. #>
    if ($script:Mutating -notcontains $Name) { return }
    foreach ($f in @('session','description','task')) {
        if (-not ($a.ContainsKey($f) -and "$($a[$f])".Trim())) {
            throw (New-GateError "$f is required before $Name will touch anything — say who is acting, why, and which task this belongs to.")
        }
    }
    $scope = Get-ScopeArg $a
    $r = Resolve-Task $scope $a.task
    $a['_task'] = $r.task; $a['_verified'] = $r.verified; $a['_scope'] = $scope
    $a['_board'] = if ($r.board) { [IO.Path]::GetRelativePath((Get-Workspace), $r.board) } else { $null }
}

# ----------------------------------------------------------------- worktree ops

function Op-WorktreeCreate([hashtable]$a) {
    $scope = Get-ScopeArg $a
    $repo = $a.repo; $name = $a.worktree
    $main = Get-RepoPath $repo $scope
    if (-not (Test-Path (Join-Path $main '.git'))) {
        throw (New-GateError "$repo is not a git repository in this scope. Known: $((Get-KnownRepos $scope) -join ', ')")
    }
    if (-not (Test-IsClean $main)) {
        throw (New-GateError "$repo main checkout has uncommitted tracked changes. Stop and ask — never stash or discard someone else's in-progress work.")
    }
    $dir = Get-WorktreeDir $scope
    $path = Join-Path $dir $name
    if (Test-Path $path) { throw (New-GateError "$path already exists. Prune it first, or pick another name.") }
    $base = Get-CurrentBranch $main
    $branch = if ($a.ContainsKey('branch') -and $a.branch) { $a.branch } else { "feature/$name" }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    # LOCAL HEAD, never origin/*: unpushed local commits are real work, and branching from
    # origin silently drops them and rebuilds on a stale tree.
    Invoke-Git @('worktree','add',$path,'-b',$branch,'HEAD') $main | Out-Null
    Write-LedgerEntry -Action create -Session $a.session -Description $a.description `
        -Repo $repo -Worktree $name -Scope $scope -Extra @{
            branch = $branch; base = $base; path = $path
            task = $a['_task']; board = $a['_board']; taskVerified = $a['_verified']; ok = $true } | Out-Null
    $untracked = (Invoke-Git @('status','--porcelain') $path -NoThrow) -split "`n" | Where-Object { $_ -match '^\?\?' }
    $msg = "Created $path on $branch, cut from $repo's local $base."
    if ($untracked) { $msg += "`n  ($($untracked.Count) untracked file(s) present — not a blocker, noted.)" }
    $msg
}

function Op-WorktreeStatus([hashtable]$a) {
    $scope = Get-ScopeArg $a
    $live = Get-LiveWorktrees $scope
    $ledger = Read-Ledger $scope
    $known = @{}
    foreach ($r in $ledger) {
        if ($r.action -eq 'create' -and $r.worktree) { $known["$($r.repo)/$($r.worktree)"] = $r }
    }
    $lines = @(); $unattributed = @()
    foreach ($w in $live) {
        $k = "$($w.Repo)/$($w.Name)"
        if ($known.ContainsKey($k)) {
            $r = $known[$k]
            $t = if ($r.ContainsKey('task') -and $r.task) { " task=$($r.task)" } else { '' }
            $lines += "  $k  [$($w.Branch)]  $($r.session) — $($r.description)$t"
        } else { $unattributed += "  $k  [$($w.Branch)]  UNATTRIBUTED — no create record" }
    }
    $nested = Get-NestedScopes $scope
    $out = @("$($live.Count) live worktree(s) in $([IO.Path]::GetRelativePath((Get-Workspace), $scope)):")
    # Unattributed first: they are the ones needing a decision.
    $out += $unattributed; $out += $lines
    if ($nested) { $out += "", "Sub-scopes excluded (they govern themselves): $($nested -join ', ')" }
    $out -join "`n"
}

function Op-WorktreeHistory([hashtable]$a) {
    $scope = Get-ScopeArg $a
    $rows = Read-Ledger $scope
    if ($a.ContainsKey('repo') -and $a.repo) { $rows = $rows | Where-Object { $_.repo -eq $a.repo } }
    if ($a.ContainsKey('worktree') -and $a.worktree) { $rows = $rows | Where-Object { $_.worktree -eq $a.worktree } }
    $n = if ($a.ContainsKey('limit') -and $a.limit) { [int]$a.limit } else { 30 }
    $rows = $rows | Select-Object -Last $n
    if (-not $rows) { return 'No matching ledger entries.' }
    (($rows | ForEach-Object {
        "  $($_.ts.Substring(0,16))  $($_.action.PadRight(7))  $($_.repo)/$($_.worktree)  $($_.session) — $($_.description)"
    }) -join "`n")
}

# ----------------------------------------------------------------- tool table

function New-Tool([string]$Name, [string]$Desc, [hashtable]$Props, [string[]]$Required, [bool]$Mutates = $true) {
    $req = if ($Mutates) { @('session','description','task') + $Required } else { $Required }
    $p = @{}
    foreach ($k in $Props.Keys) { $p[$k] = $Props[$k] }
    if ($Mutates) {
        $p['session'] = @{ type = 'string'; description = 'Who is acting. Required.' }
        $p['description'] = @{ type = 'string'; description = 'Why, in one sentence. Required.' }
        $p['task'] = @{ type = 'string'; description = 'Board item id or issue key. Required.' }
    }
    @{ name = $Name; description = $Desc
       inputSchema = @{ type = 'object'; properties = $p; required = $req } }
}

$script:Tools = @(
    (New-Tool 'worktree_create' 'Cut a worktree from the main checkout local HEAD, never origin/*. Recorded with who and why.' `
        @{ repo = @{ type='string' }; worktree = @{ type='string' }; branch = @{ type='string' }; scope = @{ type='string' } } `
        @('repo','worktree')),
    (New-Tool 'worktree_status' 'Live worktrees with provenance; unattributed first. Names excluded sub-scopes.' `
        @{ scope = @{ type='string' } } @() $false),
    (New-Tool 'worktree_history' 'Ledger entries, most recent last.' `
        @{ repo = @{ type='string' }; worktree = @{ type='string' }; limit = @{ type='integer' }; scope = @{ type='string' } } @() $false)
)

$script:Ops = @{
    worktree_create  = ${function:Op-WorktreeCreate}
    worktree_status  = ${function:Op-WorktreeStatus}
    worktree_history = ${function:Op-WorktreeHistory}
}

# ----------------------------------------------------------------- jsonrpc loop

if ($args.Count -ge 1 -and $args[0] -eq 'call') {
    # CLI escape hatch: same validation, same ledger, for when the MCP server is not in a
    # session's tool list.
    $name = $args[1]
    $a = if ($args.Count -ge 3) { $args[2] | ConvertFrom-Json -AsHashtable } else { @{} }
    try {
        if (-not $script:Ops.ContainsKey($name)) { throw (New-GateError "unknown tool '$name'") }
        Assert-Who $name $a
        Write-Host (& $script:Ops[$name] $a)
    } catch {
        if (Test-GateError $_.Exception) { Write-Host "REFUSED: $(Get-GateMessage $_.Exception)" }
        else { Write-Host "$($_.Exception.GetType().Name): $($_.Exception.Message)" }
        exit 1
    }
    exit 0
}

while ($null -ne ($line = [Console]::In.ReadLine())) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    try { $msg = $line | ConvertFrom-Json -AsHashtable } catch { Trace "bad json: $_"; continue }
    $id = if ($msg.ContainsKey('id')) { $msg.id } else { $null }
    switch ($msg.method) {
        'initialize' {
            Write-Rpc @{ jsonrpc='2.0'; id=$id; result=@{
                protocolVersion = '2024-11-05'
                capabilities = @{ tools = @{} }
                serverInfo = @{ name='worktree-gate'; version='0.2.0-pwsh' } } }
        }
        'notifications/initialized' { }
        'tools/list' { Write-Rpc @{ jsonrpc='2.0'; id=$id; result=@{ tools=$script:Tools } } }
        'tools/call' {
            $p = $msg.params; $name = $p.name
            $a = if ($p.ContainsKey('arguments') -and $p.arguments) { $p.arguments } else { @{} }
            try {
                if (-not $script:Ops.ContainsKey($name)) { throw (New-GateError "unknown tool '$name'") }
                Assert-Who $name $a
                $text = & $script:Ops[$name] $a
                $isErr = $false
            } catch {
                # A refusal is expected and carries its own message. Anything else is a defect
                # and is recorded, never swallowed -- the same split the Python server makes.
                if (Test-GateError $_.Exception) {
                    $text = "REFUSED: $(Get-GateMessage $_.Exception)"
                } else {
                    $text = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
                    try {
                        Write-LedgerEntry -Action denied -Session ("$($a.session)" ? "$($a.session)" : 'unknown') `
                            -Description "$name raised $($_.Exception.GetType().Name)" `
                            -Scope (Get-ScopeArg $a) -Extra @{ tool = $name; ok = $false
                                                               error = "$($_.Exception.Message)" } | Out-Null
                    } catch { Trace "could not record: $_" }
                }
                $isErr = $true
            }
            Write-Rpc @{ jsonrpc='2.0'; id=$id; result=@{
                content=@(@{ type='text'; text="$text" }); isError=$isErr } }
        }
        default {
            if ($null -ne $id) {
                Write-Rpc @{ jsonrpc='2.0'; id=$id; error=@{ code=-32601; message="no method $($msg.method)" } }
            }
        }
    }
}
