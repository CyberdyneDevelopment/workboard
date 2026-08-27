<#
.SYNOPSIS
    Build a portable, self-contained workboard page.

.DESCRIPTION
    PowerShell port of build-artifact.py. Reads the same items.json and notes.jsonl, fills the
    same template.html, and emits byte-comparable output — so a board built on Windows and one
    built on Linux are the same page.

    Two things differ from the full build script, and both matter for moving the board:
      * no <!doctype>/<html>/<head> wrapper. The Artifact host supplies that; a page that
        brings its own renders nested and broken.
      * no shell-out to git. An artifact cannot run git wherever it lands, so worktrees come
        from an optional .workboard/worktrees-snapshot.json and are labelled as a snapshot
        rather than pretended to be live.

.PARAMETER Board
    Folder holding .workboard\items.json and .workboard\notes.jsonl. Defaults to the current
    directory.

.PARAMETER Out
    Output file. Defaults to <Board>\workboard-artifact.html.

.PARAMETER Stamp
    Build stamp shown in the footer. Defaults to the current date and time.

.EXAMPLE
    .\Build-WorkboardArtifact.ps1 -Board C:\work\DevSession

.NOTES
    Windows PowerShell 5.1 and PowerShell 7+. No modules required.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string] $Board = '.',
    [string] $Out,
    [string] $Stamp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Template {
    # The template is language-neutral and lives in shared/, but this script has moved
    # between layouts. Try the places it legitimately sits, then say what was tried.
    $candidates = @(
        (Join-Path $Here 'template.html'),
        (Join-Path $Here '..\..\shared\board\template.html'),
        (Join-Path $Here '..\shared\board\template.html')
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c -PathType Leaf) { return (Resolve-Path -LiteralPath $c).Path }
    }
    throw ("cannot find template.html. Tried:`n  " + ($candidates -join "`n  "))
}
$SevLabel = @{ live = 'live bug'; high = 'high'; medium = 'medium'; low = 'low';
    design = 'decision'; done = 'shipped' }

function ConvertTo-HtmlText([object] $Value) {
    if ($null -eq $Value) { return '' }
    $s = [string] $Value
    $s = $s.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;')
    # &#x27; not &#39;: matches Python's html.escape byte for byte, so the two
    # implementations can be diffed against each other as a regression test.
    $s = $s.Replace('"', '&quot;').Replace("'", '&#x27;')
    return $s
}

# PSCustomObject has no soft property access; this keeps the renderer readable.
function Get-Prop($Object, [string] $Name, $Default = $null) {
    if ($null -eq $Object) { return $Default }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p -or $null -eq $p.Value) { return $Default }
    return $p.Value
}

function Get-Items($Data) {
    $out = @()
    foreach ($g in (Get-Prop $Data 'groups' @())) {
        foreach ($i in (Get-Prop $g 'items' @())) { $out += $i }
    }
    return , $out
}

# ---------------------------------------------------------------------------- load
$Board = (Resolve-Path -LiteralPath $Board).Path
$wb = Join-Path $Board '.workboard'
if (-not (Test-Path -LiteralPath $wb)) { throw "no .workboard\ in $Board" }

$Data = Get-Content -LiteralPath (Join-Path $wb 'items.json') -Raw -Encoding UTF8 |
    ConvertFrom-Json

$Notes = @{}
$notesPath = Join-Path $wb 'notes.jsonl'
if (Test-Path -LiteralPath $notesPath) {
    $lineNo = 0
    foreach ($line in (Get-Content -LiteralPath $notesPath -Encoding UTF8)) {
        $lineNo++
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $row = $line | ConvertFrom-Json }
        catch {
            # Loud on purpose: a silently dropped note is a worker who believes it reported
            # and is invisible. Name the line instead of hiding it.
            throw "notes.jsonl line ${lineNo} is not valid JSON: $($_.Exception.Message)"
        }
        $key = Get-Prop $row 'item' '?'
        if (-not $Notes.ContainsKey($key)) { $Notes[$key] = @() }
        $Notes[$key] += $row
    }
}

$Trees = @()
$snap = Join-Path $wb 'worktrees-snapshot.json'
if (Test-Path -LiteralPath $snap) {
    $Trees = @(Get-Content -LiteralPath $snap -Raw -Encoding UTF8 | ConvertFrom-Json)
}

if (-not $Stamp) { $Stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm') }
if (-not $Out) { $Out = Join-Path $Board 'workboard-artifact.html' }

# ---------------------------------------------------------------------------- derive
$AllItems = Get-Items $Data
$Questions = @(Get-Prop $Data 'questions' @())

$Awaiting = @{}
foreach ($q in $Questions) {
    foreach ($b in (Get-Prop $q 'blocks' @())) { $Awaiting[$b] = $true }
}
function Test-Awaiting([string] $Id) { return $Awaiting.ContainsKey($Id) }

$noteCount = 0
foreach ($k in $Notes.Keys) { $noteCount += $Notes[$k].Count }

function New-Tally([string] $Label, [int] $Value, [string] $Class = '') {
    "<div class=`"tally $Class`"><b>$Value</b><span>$(ConvertTo-HtmlText $Label)</span></div>"
}

$openN = @($AllItems | Where-Object { (Get-Prop $_ 'status' 'open') -ne 'done' }).Count
$liveN = @($AllItems | Where-Object {
        (Get-Prop $_ 'sev' '') -eq 'live' -and (Get-Prop $_ 'status' 'open') -ne 'done' }).Count
$blockN = @($AllItems | Where-Object { (Get-Prop $_ 'status' 'open') -eq 'blocked' }).Count
$doneN = @($AllItems | Where-Object { (Get-Prop $_ 'status' 'open') -eq 'done' }).Count

$Tallies = (New-Tally 'open' $openN) + (New-Tally 'live' $liveN 'is-live') +
    (New-Tally 'blocked' $blockN) + (New-Tally 'awaiting you' $Awaiting.Count 'is-you') +
    (New-Tally 'questions' $Questions.Count) + (New-Tally 'notes' $noteCount) +
    (New-Tally 'shipped' $doneN)

# ---------------------------------------------------------------------------- questions
$qParts = @()
$n = 0
foreach ($q in $Questions) {
    $n++
    $rec = Get-Prop $q 'rec' ''
    $opts = ''
    foreach ($o in (Get-Prop $q 'options' @())) {
        $cls = if ($rec -and $o.StartsWith($rec)) { 'is-rec' } else { '' }
        $opts += "<li class=`"$cls`">$(ConvertTo-HtmlText $o)</li>"
    }
    $meta = ''
    if ($rec) { $meta += "<dt>i'd do</dt><dd>$(ConvertTo-HtmlText $rec)</dd>" }
    $blocks = @(Get-Prop $q 'blocks' @())
    if ($blocks.Count) {
        $links = ($blocks | ForEach-Object {
                "<a class=`"n`" href=`"#$(ConvertTo-HtmlText $_)`">$(ConvertTo-HtmlText $_)</a>" }) -join ', '
        $meta += "<dt>unblocks</dt><dd>$links</dd>"
    }
    $who = Get-Prop $q 'who' ''
    if ($who) { $meta += "<dt>who acts</dt><dd>$(ConvertTo-HtmlText $who)</dd>" }
    $metaHtml = if ($meta) { "<dl class=`"meta`">$meta</dl>" } else { '' }
    $qParts += "<article class=`"q`"><h3>$n. $(ConvertTo-HtmlText (Get-Prop $q 'q' ''))</h3>" +
        "<p class=`"why`">$(ConvertTo-HtmlText (Get-Prop $q 'why' ''))</p>" +
        "<ul class=`"opts`">$opts</ul>$metaHtml</article>"
}
$QuestionsHtml = ''
if ($qParts.Count) {
    $QuestionsHtml = "<section id=`"questions`"><h2 class=`"grp`">For you to answer " +
        "<span class=`"chip chip-you`">$($qParts.Count)</span></h2>" +
        "<p class=`"blurb`">Nothing here is blocked on work. Each one is a decision only you " +
        "can make, with what I would do and why.</p>" + ($qParts -join '')
    $QuestionsHtml += '</section>'
}

# ---------------------------------------------------------------------------- items
function New-FileBlock($Item, [string] $Key, [string] $Label) {
    $list = @(Get-Prop $Item $Key @())
    if (-not $list.Count) { return '' }
    $id = ConvertTo-HtmlText (Get-Prop $Item 'id' '')
    $lis = ($list | ForEach-Object { "<li>$(ConvertTo-HtmlText $_)</li>" }) -join ''
    return "<details data-k=`"$id-$Key`"><summary>$Label <b>$($list.Count)</b></summary>" +
        "<ul>$lis</ul></details>"
}

function New-Item($Item) {
    $id = Get-Prop $Item 'id' ''
    $sev = Get-Prop $Item 'sev' 'medium'
    $status = Get-Prop $Item 'status' 'open'
    $sevText = if ($SevLabel.ContainsKey($sev)) { $SevLabel[$sev] } else { $sev }
    $chips = "<span class=`"chip`">$(ConvertTo-HtmlText $sevText)</span>" +
        "<span class=`"chip`">$(ConvertTo-HtmlText $status)</span>"
    if (Test-Awaiting $id) { $chips += '<span class="chip chip-you">awaiting you</span>' }

    $ev = ''
    foreach ($e in (Get-Prop $Item 'evidence' @())) { $ev += "<li>$(ConvertTo-HtmlText $e)</li>" }
    $evHtml = if ($ev) { "<ul class=`"ev`">$ev</ul>" } else { '' }

    $meta = ''
    foreach ($k in @('fix', 'repo', 'owner')) {
        $v = Get-Prop $Item $k ''
        if ($v) { $meta += "<dt>$k</dt><dd>$(ConvertTo-HtmlText $v)</dd>" }
    }
    $metaHtml = if ($meta) { "<dl class=`"meta`">$meta</dl>" } else { '' }

    $pf = ''
    if (Get-Prop $Item 'projectFirst' $false) {
        $pf = '<p class="project-first">Project the impact BEFORE opening files ' +
            [char]0x2014 + ' list every file you expect to create, delete or change, post it ' +
            'as a note, then work.</p>'
    }

    $log = @()
    if ($Notes.ContainsKey($id)) { $log = $Notes[$id] }
    $entries = ''
    foreach ($e in $log) {
        $where = Get-Prop $e 'worktree' (Get-Prop $e 'session' '')
        $ts = Get-Prop $e 'ts' ''
        $kind = Get-Prop $e 'kind' ''
        $dot = ' ' + [char]0x00B7 + ' '
        $by = "<span class=`"byline`">$(ConvertTo-HtmlText (Get-Prop $e 'who' '?'))"
        if ($where) { $by += "$dot$(ConvertTo-HtmlText $where)" }
        if ($ts) { $by += "$dot$(ConvertTo-HtmlText $ts)" }
        if ($kind) { $by += "$dot$(ConvertTo-HtmlText $kind)" }
        $by += '</span>'
        $entries += "<li>$by$(ConvertTo-HtmlText (Get-Prop $e 'note' ''))</li>"
    }
    $logHtml = if ($log.Count) { "<ol>$entries</ol>" } else { '<p class="empty">No notes yet.</p>' }

    $idE = ConvertTo-HtmlText $id
    return "<article class=`"item sev-$(ConvertTo-HtmlText $sev) st-$(ConvertTo-HtmlText $status)`" " +
        "id=`"$idE`"><a class=`"slug`" href=`"#$idE`">#$idE</a>" +
        "<h3>$(ConvertTo-HtmlText (Get-Prop $Item 'title' ''))<span class=`"chips`">$chips</span></h3>" +
        "<p class=`"why`">$(ConvertTo-HtmlText (Get-Prop $Item 'why' ''))</p>" +
        $evHtml + $metaHtml + $pf +
        (New-FileBlock $Item 'expected' 'expected files') +
        (New-FileBlock $Item 'actual' 'actual changes') +
        "<details class=`"log`" data-k=`"$idE-notes`"><summary>notes <b>$($log.Count)</b></summary>" +
        $logHtml + '</details></article>'
}

$Body = ''
foreach ($g in (Get-Prop $Data 'groups' @())) {
    $Body += "<h2 class=`"grp`">$(ConvertTo-HtmlText (Get-Prop $g 'name' ''))</h2>"
    $blurb = Get-Prop $g 'blurb' ''
    if ($blurb) { $Body += "<p class=`"blurb`">$(ConvertTo-HtmlText $blurb)</p>" }
    $rows = @(Get-Prop $g 'items' @()) | Sort-Object `
        @{ Expression = { if (Test-Awaiting (Get-Prop $_ 'id' '')) { 0 } else { 1 } } }, `
        @{ Expression = { if ((Get-Prop $_ 'status' 'open') -eq 'done') { 1 } else { 0 } } }
    foreach ($i in $rows) { $Body += New-Item $i }
}

# ---------------------------------------------------------------------------- map
$Edges = @()
foreach ($i in $AllItems) {
    $links = Get-Prop $i 'links' $null
    if ($null -eq $links) { continue }
    $id = Get-Prop $i 'id' ''
    foreach ($t in (Get-Prop $links 'gates' @())) { $Edges += , @($id, $t, 'gates') }
    foreach ($t in (Get-Prop $links 'related' @())) {
        $dup = $false
        foreach ($e in $Edges) { if ($e[0] -eq $t -and $e[1] -eq $id -and $e[2] -eq 'with') { $dup = $true } }
        if (-not $dup) { $Edges += , @($id, $t, 'with') }
    }
}
$Adj = @{}
foreach ($e in $Edges) {
    foreach ($pair in @(@($e[0], $e[1]), @($e[1], $e[0]))) {
        if (-not $Adj.ContainsKey($pair[0])) { $Adj[$pair[0]] = @{} }
        $Adj[$pair[0]][$pair[1]] = $true
    }
}
$MapHtml = ''
if ($Edges.Count) {
    $seen = @{}; $clusters = @()
    foreach ($node in ($Adj.Keys | Sort-Object)) {
        if ($seen.ContainsKey($node)) { continue }
        $stack = [System.Collections.Stack]::new(); $stack.Push($node); $comp = @()
        while ($stack.Count) {
            $cur = $stack.Pop()
            if ($seen.ContainsKey($cur)) { continue }
            $seen[$cur] = $true; $comp += $cur
            foreach ($nb in $Adj[$cur].Keys) { $stack.Push($nb) }
        }
        $clusters += , ($comp | Sort-Object)
    }
    # explicit tiebreak so this matches build-artifact.py byte for byte
    $clusters = $clusters | Sort-Object @{ Expression = { - $_.Count } }, @{ Expression = { $_[0] } }
    $MapHtml = '<aside class="map"><h2 class="grp">How they connect</h2>' +
        '<p class="legend"><b>gates</b> must land first ' + [char]0x00B7 +
        ' <b>with</b> same problem, no order</p>'
    foreach ($comp in $clusters) {
        $hub = $comp | Sort-Object @{ Expression = { - $Adj[$_].Count } }, @{ Expression = { $_ } } |
            Select-Object -First 1
        $MapHtml += "<div class=`"cluster`"><h3>$(ConvertTo-HtmlText ($hub -replace '-', ' '))</h3>"
        foreach ($e in $Edges) {
            if ($comp -notcontains $e[0]) { continue }
            $MapHtml += "<div class=`"edge`"><a class=`"n`" href=`"#$(ConvertTo-HtmlText $e[0])`">" +
                "$(ConvertTo-HtmlText $e[0])</a><span class=`"rel`">$(ConvertTo-HtmlText $e[2])</span>" +
                "<a class=`"n`" href=`"#$(ConvertTo-HtmlText $e[1])`">$(ConvertTo-HtmlText $e[1])</a></div>"
        }
        $MapHtml += '</div>'
    }
    $MapHtml += '</aside>'
}

# ---------------------------------------------------------------------------- panels
$Panels = ''
$Sessions = @(Get-Prop $Data 'sessions' @())
if ($Sessions.Count) {
    $rows = ''
    foreach ($s in $Sessions) {
        $ref = Get-Prop $s 'ref' ''
        $refHtml = if ($ref -and $ref -ne '—') { " [$(ConvertTo-HtmlText $ref)]" } else { '' }
        $rows += "<tr><td>$(ConvertTo-HtmlText (Get-Prop $s 'name' ''))$refHtml</td>" +
            "<td>$(ConvertTo-HtmlText (Get-Prop $s 'role' ''))</td>" +
            "<td>$(ConvertTo-HtmlText (Get-Prop $s 'holds' ''))</td></tr>"
    }
    $Panels += "<details class=`"panel`" data-k=`"sessions`"><summary>Sessions " +
        "<b>$($Sessions.Count)</b></summary><table><tr><th>session</th><th>role</th>" +
        "<th>holds</th></tr>$rows</table></details>"
}
if ($Trees.Count) {
    $rows = ''
    foreach ($t in $Trees) {
        $owner = Get-Prop $t 'owner' ''
        if (-not $owner) { $owner = '—' }
        $rows += "<tr><td>$(ConvertTo-HtmlText (Get-Prop $t 'repo' ''))</td>" +
            "<td>$(ConvertTo-HtmlText (Get-Prop $t 'name' ''))</td>" +
            "<td>$(ConvertTo-HtmlText (Get-Prop $t 'branch' '-'))</td>" +
            "<td>$(ConvertTo-HtmlText $owner)</td></tr>"
    }
    $Panels += "<details class=`"panel`" data-k=`"worktrees`"><summary>Worktrees " +
        "<b>$($Trees.Count)</b> " + [char]0x2014 + " snapshot, not live</summary><table>" +
        "<tr><th>repo</th><th>worktree</th><th>branch</th><th>owner</th></tr>$rows</table></details>"
}

# ---------------------------------------------------------------------------- emit
$sep = ' ' + [char]0x00B7 + ' '
$Footer = "$($AllItems.Count) items$($sep)$noteCount notes$($sep)$($Questions.Count) " +
    "open questions$($sep)built $(ConvertTo-HtmlText $Stamp)<br>" +
    'Regenerate and republish to the same URL to update in place. ' +
    'Worktrees are a snapshot: an artifact cannot run git.'

$tpl = Get-Content -LiteralPath (Find-Template) -Raw -Encoding UTF8
$title = ConvertTo-HtmlText (Get-Prop $Data 'title' 'Workboard')
$subtitle = ConvertTo-HtmlText (Get-Prop $Data 'subtitle' `
        'What is open, the evidence, who holds it, and what they have found.')

$tpl = $tpl.Replace('{{TITLE}}', $title).Replace('{{SUBTITLE}}', $subtitle)
$tpl = $tpl.Replace('{{TALLIES}}', $Tallies).Replace('{{PANELS}}', $Panels)
$tpl = $tpl.Replace('{{QUESTIONS}}', $QuestionsHtml).Replace('{{BODY}}', $Body)
$tpl = $tpl.Replace('{{MAP}}', $MapHtml).Replace('{{FOOTER}}', $Footer)

[System.IO.File]::WriteAllText($Out, $tpl, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "wrote $Out"
Write-Host ("  {0} items - {1} notes - {2} questions - {3} worktrees" -f `
        $AllItems.Count, $noteCount, $Questions.Count, $Trees.Count)
