#Requires -Version 7.0
<#
    WtLedger — scope resolution, the append-only ledger, and git helpers.

    PowerShell port of wtledger.py. The FORMAT is the contract (see SPEC.md): a ledger line
    written here must parse under the Python reader and vice versa. Keys are sorted on write
    so two implementations produce comparable lines.
#>
Set-StrictMode -Version Latest

$script:ScopeMarker = '.workboard'
$script:Actions = @('create','pull','sync','merge','prune','revive','board','commit','denied')

# The workspace is the directory this module's repo is installed into, resolved once.
# Nothing above it is ever acted on or journalled.
$script:Workspace = $env:WORKTREE_GATE_WORKSPACE
if (-not $script:Workspace) {
    $script:Workspace = (Get-Location).Path
}

# A refusal, not a crash. PowerShell classes do NOT cross module boundaries -- a [GateError]
# defined here is invisible to the server script that imports this module, which turns every
# refusal into "Unable to find type [GateError]". So refusals are marked with a sentinel in
# the message instead, which survives any boundary.
$script:GateMark = 'GATE:'

function New-GateError {
    param([Parameter(Mandatory)][string]$Message)
    [System.Management.Automation.RuntimeException]::new("$($script:GateMark) $Message")
}

function Test-GateError {
    param([System.Exception]$Exception)
    $Exception.Message.StartsWith('GATE:')
}

function Get-GateMessage {
    param([System.Exception]$Exception)
    $Exception.Message -replace '^GATE:\s*', ''
}

function Get-Workspace { $script:Workspace }
function Set-Workspace { param([string]$Path) $script:Workspace = (Resolve-Path $Path).Path }

function Get-Now {
    # ISO 8601 with offset, seconds precision -- matches the Python writer exactly.
    (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
}

function Test-IsScope { param([string]$Path) Test-Path (Join-Path $Path $script:ScopeMarker) }

function Resolve-Scope {
    <#  Nearest enclosing directory holding .workboard, bounded at the workspace root.
        A path outside the workspace resolves to the workspace: the gate never acts above it. #>
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $script:Workspace }
    $p = [System.IO.Path]::GetFullPath($Path)
    $ws = $script:Workspace
    if (-not ($p -eq $ws -or $p.StartsWith($ws + [IO.Path]::DirectorySeparatorChar))) { return $ws }
    while ($p -ne $ws) {
        if (Test-IsScope $p) { return $p }
        $parent = Split-Path -Parent $p
        if ($parent -eq $p -or -not $parent) { break }
        $p = $parent
    }
    $ws
}

function Get-NestedScopes {
    <#  Immediate children that are scopes in their own right. They govern themselves and are
        skipped entirely -- repos, worktrees and journal. Callers must NAME what they skipped;
        a silently omitted sub-scope looks identical to one that does not exist. #>
    param([string]$Scope = $script:Workspace)
    Get-ChildItem $Scope -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne $script:ScopeMarker -and (Test-IsScope $_.FullName) } |
        ForEach-Object { $_.Name } | Sort-Object
}

function Get-KnownRepos {
    <#  Git repos this scope owns, including documentation projects one level under docs/.
        A documentation project is a repository like any other and gets worktrees like any
        other; moving it under docs/ so the router can enumerate it must not remove it from
        the gate's reach. #>
    param([string]$Scope = $script:Workspace)
    $skip = @(Get-NestedScopes $Scope)
    $repos = Get-ChildItem $Scope -Directory -ErrorAction SilentlyContinue |
        Where-Object { $skip -notcontains $_.Name -and (Test-Path (Join-Path $_.FullName '.git')) } |
        ForEach-Object { $_.Name }
    $docs = Join-Path $Scope 'docs'
    if (Test-Path $docs) {
        $repos += Get-ChildItem $docs -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName '.git') } |
            ForEach-Object { "docs/$($_.Name)" }
    }
    # Dedupe by REAL path. A documentation project may be a symlink into a checkout that also
    # sits at the scope root -- the right way to enumerate one without moving a live session
    # out from under it. Counted twice, one repo could take two worktrees under two names.
    $seen = @{}; $out = [System.Collections.Generic.List[string]]::new()
    foreach ($r in $repos) {
        # Resolve-Path does NOT follow a symlink -- it returns the link's own path, so a
        # symlinked docs project never matched its target and the dedupe silently did nothing.
        # ResolvedTarget is the resolved destination, or $null when the item is not a link.
        $item = Get-Item (Join-Path $Scope $r) -Force
        $real = if ($item.ResolvedTarget) { $item.ResolvedTarget } else { $item.FullName }
        if ($seen.ContainsKey($real)) {
            # Prefer the shallower name: the real location, not the alias.
            if (($r -split '/').Count -lt (($seen[$real]) -split '/').Count) {
                $out[$out.IndexOf($seen[$real])] = $r
                $seen[$real] = $r
            }
            continue
        }
        $seen[$real] = $r
        $out.Add($r)
    }
    # Ordinal sort, NOT Sort-Object's culture-aware default: Python's sorted() is ordinal, so
    # culture-aware sorting silently interleaves FrameworkMap/RoslynMcp among lowercase names
    # and the two implementations disagree about order for an identical set.
    [string[]]$arr = $out.ToArray()
    [Array]::Sort($arr, [StringComparer]::Ordinal)
    $arr
}

function Get-RepoPath {
    param([string]$Repo, [string]$Scope = $script:Workspace)
    $p = Join-Path $Scope $Repo
    if (-not (Test-Path (Join-Path $p '.git'))) {
        $alt = Join-Path $Scope "docs/$Repo"       # accept a bare docs-project name
        if (Test-Path (Join-Path $alt '.git')) { return $alt }
    }
    $p
}

function Get-WorktreeDir { param([string]$Scope = $script:Workspace) Join-Path $Scope '.worktrees' }

function Invoke-Git {
    <#  Run git and return stdout. Throws GateError on failure unless -NoThrow.
        Never uses the shell: arguments are passed as an array so a path with a space or a
        branch name with a quote cannot become two arguments. #>
    param([string[]]$GitArgs, [string]$Cwd, [switch]$NoThrow)
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = 'git'
    foreach ($a in $GitArgs) { [void]$psi.ArgumentList.Add($a) }
    $psi.WorkingDirectory = $Cwd
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $proc = [System.Diagnostics.Process]::Start($psi)
    $out = $proc.StandardOutput.ReadToEnd()
    $err = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0 -and -not $NoThrow) {
        throw (New-GateError "git $($GitArgs -join ' ') failed in ${Cwd}: $($err.Trim())")
    }
    $out.TrimEnd("`r", "`n")
}

function Test-IsClean {
    <#  Tracked changes only. `git worktree add` does not care about untracked files, and
        refusing on one trains people to route around the gate. #>
    param([string]$Path)
    [string]::IsNullOrWhiteSpace((Invoke-Git @('status','--porcelain','--untracked-files=no') $Path -NoThrow))
}

function Get-CurrentBranch {
    param([string]$Path)
    (Invoke-Git @('rev-parse','--abbrev-ref','HEAD') $Path -NoThrow).Trim()
}

function Get-LedgerPath {
    param([string]$Scope = $script:Workspace)
    Join-Path $Scope '.worktree-gate/ledger.jsonl'
}

function Read-Ledger {
    <#  A malformed line is an ERROR naming the line number, never a skipped record. A silently
        dropped line is a worker who believes it reported and is invisible. #>
    param([string]$Scope = $script:Workspace)
    $path = Get-LedgerPath $Scope
    if (-not (Test-Path $path)) { return @() }
    $n = 0; $rows = @()
    foreach ($line in [System.IO.File]::ReadLines($path)) {
        $n++
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -AsHashtable) }
        catch { throw (New-GateError "$path line ${n}: $($_.Exception.Message)") }
    }
    $rows
}

function Write-LedgerEntry {
    <#  Append one record. Attribution is validated here too, but callers MUST validate before
        running git: this function is reached after the operation, and a blank description here
        once cut a worktree and then refused to record it -- producing exactly the unattributed
        worktree the gate exists to prevent. #>
    param(
        [Parameter(Mandatory)][string]$Action,
        [Parameter(Mandatory)][string]$Session,
        [Parameter(Mandatory)][string]$Description,
        [string]$Repo, [string]$Worktree,
        [string]$Scope = $script:Workspace,
        [hashtable]$Extra = @{}
    )
    if ($script:Actions -notcontains $Action) {
        throw (New-GateError "unknown ledger action '$Action'. Known: $($script:Actions -join ', ')")
    }
    foreach ($pair in @(@('session', $Session), @('description', $Description))) {
        if ([string]::IsNullOrWhiteSpace($pair[1])) {
            throw (New-GateError "$($pair[0]) is required on every ledger entry.")
        }
    }
    $entry = @{
        ts = Get-Now; action = $Action; session = $Session; description = $Description
        repo = $Repo; worktree = $Worktree
        scope = [IO.Path]::GetRelativePath($script:Workspace, $Scope)
    }
    foreach ($k in $Extra.Keys) { $entry[$k] = $Extra[$k] }

    $ordered = [ordered]@{}
    foreach ($k in ($entry.Keys | Sort-Object)) { $ordered[$k] = $entry[$k] }
    $dir = Split-Path -Parent (Get-LedgerPath $Scope)
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    # Depth MUST be explicit: ConvertTo-Json defaults to 2 and silently truncates.
    Add-Content -Path (Get-LedgerPath $Scope) -Encoding utf8 `
        -Value ($ordered | ConvertTo-Json -Depth 12 -Compress)
    $entry
}

function Get-LiveWorktrees {
    <#  Every worktree of every repo this scope owns. Ownership follows the REPO, not the path:
        a worktree under <repo>/.worktrees counts the same as one under <workspace>/.worktrees.
        A repo's own main checkout is never listed. #>
    param([string]$Scope = $script:Workspace)
    $out = @()
    foreach ($repo in Get-KnownRepos $Scope) {
        $main = Get-RepoPath $repo $Scope
        $txt = Invoke-Git @('worktree','list','--porcelain') $main -NoThrow
        if (-not $txt) { continue }
        $cur = @{}
        foreach ($line in ($txt -split "`n")) {
            if ($line -match '^worktree (.+)$')  { $cur = @{ path = $Matches[1] } }
            elseif ($line -match '^branch (.+)$'){ $cur.branch = ($Matches[1] -replace '^refs/heads/','') }
            elseif ([string]::IsNullOrWhiteSpace($line) -and $cur.Count) {
                if ($cur.path -and ([IO.Path]::GetFullPath($cur.path) -ne [IO.Path]::GetFullPath($main))) {
                    $out += [pscustomobject]@{
                        Repo = $repo; Name = Split-Path -Leaf $cur.path
                        Branch = $(if ($cur.ContainsKey('branch')) { $cur.branch } else { '(detached)' })
                        Path = $cur.path }
                }
                $cur = @{}
            }
        }
        if ($cur.Count -and $cur.path -and ([IO.Path]::GetFullPath($cur.path) -ne [IO.Path]::GetFullPath($main))) {
            $out += [pscustomobject]@{
                Repo = $repo; Name = Split-Path -Leaf $cur.path
                Branch = $(if ($cur.ContainsKey('branch')) { $cur.branch } else { '(detached)' })
                Path = $cur.path }
        }
    }
    $out
}

function Find-Worktree {
    param([string]$Repo, [string]$Name, [string]$Scope = $script:Workspace)
    Get-LiveWorktrees $Scope | Where-Object { $_.Repo -eq $Repo -and $_.Name -eq $Name } | Select-Object -First 1
}

Export-ModuleMember -Function New-GateError, Test-GateError, Get-GateMessage, Get-Workspace, Set-Workspace, Get-Now, Test-IsScope, Resolve-Scope,
    Get-NestedScopes, Get-KnownRepos, Get-RepoPath, Get-WorktreeDir, Invoke-Git, Test-IsClean,
    Get-CurrentBranch, Get-LedgerPath, Read-Ledger, Write-LedgerEntry, Get-LiveWorktrees, Find-Worktree
