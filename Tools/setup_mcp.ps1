#Requires -Version 7.0
<#
.SYNOPSIS
    Generate or merge MCP client configuration for UEAssetLens (UAL).

.DESCRIPTION
    Writes a single "ueassetlens" entry into the Claude Code and Cursor user
    configuration files:
      - Claude Code : <ConfigRoot>/.claude.json
      - Cursor      : <ConfigRoot>/.cursor/mcp.json
    The default -Transport Http adds a "type": "http" entry pointing at
    http://localhost:8000/mcp, the single loopback entry served by the UE6
    official MCP plugin (Experimental + NoRedist, no authentication). Only
    loopback hosts (localhost / 127.0.0.1) over plain http are accepted; this
    script never starts, exposes, or authenticates a server.

    -Transport Stdio is the explicit fallback: it keeps the command + args
    entry that launches Server/ual_mcp/server.py with a Python interpreter,
    for hosts where the official MCP plugin is unavailable.

    Existing mcpServers entries and all other top-level keys are preserved
    (merge, never wholesale overwrite). Before the first modification the
    original file is copied to <file>.bak. The operation is idempotent.
    Use -DryRun to preview all actions without writing anything.

.PARAMETER Transport
    'Http' (default) writes an ordered { type = 'http'; url = ... } entry.
    'Stdio' writes the fallback { command; args } entry.

.PARAMETER HttpUrl
    HTTP endpoint for -Transport Http. Default: http://localhost:8000/mcp.
    Scheme must be http and host must be exactly localhost or 127.0.0.1.

.PARAMETER ConfigRoot
    Root that holds .claude.json and .cursor/mcp.json. Defaults to
    $env:USERPROFILE; a temp directory can be supplied for -DryRun tests.

.PARAMETER DryRun
    Print the planned actions and resulting JSON without creating,
    modifying, or backing up any file.

.EXAMPLE
    pwsh -File Tools/setup_mcp.ps1 -DryRun -ConfigRoot $env:TEMP/ual-cfg

.EXAMPLE
    pwsh -File Tools/setup_mcp.ps1 -Transport Stdio -DryRun

.EXAMPLE
    pwsh -File Tools/setup_mcp.ps1
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [ValidateSet('Http', 'Stdio')]
    [string]$Transport = 'Http',
    [string]$HttpUrl = 'http://localhost:8000/mcp',
    [string]$ConfigRoot = $env:USERPROFILE
)

$ErrorActionPreference = 'Stop'

$ServerName = 'ueassetlens'

function Write-Section {
    param([string]$Title)
    $line = '=' * 72
    Write-Host ''
    Write-Host $line -ForegroundColor Cyan
    Write-Host ('  ' + $Title) -ForegroundColor Cyan
    Write-Host $line -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Message)
    Write-Host ('[cfg]  ' + $Message) -ForegroundColor Gray
}

function Convert-ToForwardSlash {
    param([string]$Path)
    return ($Path -replace '\\', '/')
}

function Assert-HttpLoopbackUrl {
    param([string]$Url)

    $parsed = $null
    if (-not [System.Uri]::TryCreate($Url, [System.UriKind]::Absolute, [ref]$parsed)) {
        throw "Invalid -HttpUrl '$Url': expected an absolute http:// URL."
    }
    if ($parsed.Scheme -ne 'http') {
        throw "Invalid -HttpUrl '$Url': scheme must be 'http' (loopback-only, no auth)."
    }
    if ($parsed.Host -notin @('localhost', '127.0.0.1')) {
        throw "Invalid -HttpUrl '$Url': host must be exactly 'localhost' or '127.0.0.1'. Non-loopback addresses are rejected: the UE6 official MCP server is Experimental + NoRedist, loopback-only and has no authentication."
    }
    if (-not [string]::IsNullOrEmpty($parsed.UserInfo) -or
        -not [string]::IsNullOrEmpty($parsed.Query) -or
        -not [string]::IsNullOrEmpty($parsed.Fragment)) {
        throw "Invalid -HttpUrl '$Url': userinfo, query and fragment are not allowed (no auth or extra routing)."
    }
}

function Resolve-RepoRoot {
    $dir = $PSScriptRoot
    for ($i = 0; $i -lt 6; $i++) {
        if (Test-Path -LiteralPath (Join-Path $dir 'Server/ual_mcp/server.py')) {
            return (Resolve-Path -LiteralPath $dir).Path
        }
        $parent = Split-Path -Parent $dir
        if ([string]::IsNullOrEmpty($parent) -or ($parent -eq $dir)) { break }
        $dir = $parent
    }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}

function Resolve-PythonExe {
    param([string]$RepoRoot)
    $venvWin = Join-Path $RepoRoot '.venv/Scripts/python.exe'
    $venvNix = Join-Path $RepoRoot '.venv/bin/python'
    if (Test-Path -LiteralPath $venvWin) { return (Resolve-Path -LiteralPath $venvWin).Path }
    if (Test-Path -LiteralPath $venvNix) { return (Resolve-Path -LiteralPath $venvNix).Path }
    foreach ($name in @('python', 'python3', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $cmd) { return $cmd.Source }
    }
    throw 'Python interpreter not found. Run Tools/run_tests.ps1 first (it creates .venv) or add python to PATH.'
}

# Compare field values instead of serialized JSON, so key ordering of the
# parsed config (Hashtable vs OrderedHashtable) cannot break idempotency.
# Http entries are matched on type + url; the stdio fallback on command + args.
# A mixed-shape entry is treated as stale so switching transports removes the
# other shape's keys instead of leaving a half-configured server.
function Test-EntryEqual {
    param(
        $Existing,
        [System.Collections.IDictionary]$Desired
    )
    if ($Existing -isnot [System.Collections.IDictionary]) { return $false }

    if ($Desired.Contains('type') -or $Desired.Contains('url')) {
        if ($Existing.Contains('command') -or $Existing.Contains('args')) { return $false }
        if (-not $Existing.Contains('type') -or -not $Existing.Contains('url')) { return $false }
        if ([string]$Existing['type'] -ne [string]$Desired['type']) { return $false }
        if ([string]$Existing['url'] -ne [string]$Desired['url']) { return $false }
        return $true
    }

    if ($Existing.Contains('type') -or $Existing.Contains('url')) { return $false }
    if (-not $Existing.Contains('command') -or -not $Existing.Contains('args')) { return $false }
    if ([string]$Existing['command'] -ne [string]$Desired['command']) { return $false }
    $existingArgs = (@($Existing['args']) -join '|')
    $desiredArgs  = (@($Desired['args']) -join '|')
    return ($existingArgs -eq $desiredArgs)
}

function Merge-McpConfig {
    param(
        [string]$Path,
        [string]$Name,
        [System.Collections.IDictionary]$Entry,
        [bool]$DryRunMode
    )

    Write-Host ''
    Write-Host ('  Target : ' + (Convert-ToForwardSlash $Path)) -ForegroundColor White

    # Load existing config, tolerating missing, empty, or invalid files.
    $cfg = [ordered]@{}
    $exists = Test-Path -LiteralPath $Path
    if ($exists) {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding utf8
        if (-not [string]::IsNullOrWhiteSpace($raw)) {
            try {
                $parsed = ConvertFrom-Json -AsHashtable -InputObject $raw
                if ($null -ne $parsed -and $parsed -is [System.Collections.IDictionary]) {
                    $cfg = $parsed
                }
            }
            catch {
                Write-Host '  WARN   : existing file is not valid JSON; it will be backed up and rebuilt.' -ForegroundColor Yellow
            }
        }
    }

    # Ensure mcpServers is a dictionary; keep every other top-level key.
    $servers = $null
    if ($cfg.Contains('mcpServers') -and ($cfg['mcpServers'] -is [System.Collections.IDictionary])) {
        $servers = $cfg['mcpServers']
    }
    else {
        $servers = [ordered]@{}
        $cfg['mcpServers'] = $servers
    }

    # Idempotency: skip when the desired entry already matches exactly.
    $changed = $true
    if ($servers.Contains($Name) -and (Test-EntryEqual -Existing $servers[$Name] -Desired $Entry)) {
        $changed = $false
    }

    if (-not $changed) {
        Write-Host ('  Result : already up to date (' + $Name + ' identical); no write.') -ForegroundColor Green
        return
    }

    $servers[$Name] = $Entry

    if ($DryRunMode) {
        Write-Host '  Result : [DryRun] would merge this entry (nothing written):' -ForegroundColor Cyan
        Write-Host (ConvertTo-Json -InputObject $Entry -Depth 10)
        if ($exists) {
            Write-Host ('  Backup : [DryRun] would create ' + (Convert-ToForwardSlash $Path) + '.bak') -ForegroundColor Cyan
        }
        return
    }

    # Back up the original exactly once, preserving the very first version.
    if ($exists) {
        $bak = $Path + '.bak'
        if (-not (Test-Path -LiteralPath $bak)) {
            Copy-Item -LiteralPath $Path -Destination $bak -Force
            Write-Host ('  Backup : ' + (Convert-ToForwardSlash $bak)) -ForegroundColor Yellow
        }
        else {
            Write-Host ('  Backup : ' + (Convert-ToForwardSlash $bak) + ' already exists, kept as-is') -ForegroundColor Gray
        }
    }

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $json = ConvertTo-Json -InputObject $cfg -Depth 40
    Set-Content -LiteralPath $Path -Value $json -Encoding utf8
    Write-Host ('  Result : merged ' + $Name + ' into mcpServers (existing entries preserved).') -ForegroundColor Green
}

# ---------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($ConfigRoot)) {
    throw '-ConfigRoot must be a non-empty path.'
}
if ($Transport -eq 'Http') {
    Assert-HttpLoopbackUrl -Url $HttpUrl
}

Write-Section 'UEAssetLens MCP client setup'

$repoRoot = Resolve-RepoRoot
Write-Step ('RepoRoot      : ' + (Convert-ToForwardSlash $repoRoot))
Write-Step ('ConfigRoot    : ' + (Convert-ToForwardSlash $ConfigRoot))

if ($Transport -eq 'Http') {
    # No command/args in the HTTP entry: the client connects to the single
    # loopback endpoint, it does not launch an extra Python process.
    $entry = [ordered]@{
        type = 'http'
        url  = $HttpUrl
    }
    Write-Step ('Transport     : http (default; single loopback MCP entry)')
    Write-Step ('Endpoint      : ' + $HttpUrl)
    Write-Step 'Server        : UE6 official MCP plugin, Experimental + NoRedist, loopback-only, no authentication'
    Write-Step 'Note          : only the client entry is written; this script does not start or expose a server'
}
else {
    $serverScript = Convert-ToForwardSlash (Join-Path $repoRoot 'Server/ual_mcp/server.py')
    $pythonExe    = Convert-ToForwardSlash (Resolve-PythonExe -RepoRoot $repoRoot)
    Write-Step ('Interpreter   : ' + $pythonExe)
    Write-Step ('Server script : ' + $serverScript)
    Write-Step 'Transport     : stdio (explicit fallback: Python MCP server when the official UE6 MCP plugin is unavailable)'
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'Server/ual_mcp/server.py'))) {
        Write-Host ''
        Write-Host '[warn] Server/ual_mcp/server.py is not present yet; config is still generated for the expected path.' -ForegroundColor Yellow
    }
    # stdio is implied by the command+args shape; no explicit transport key is
    # written, so the entry stays compatible with strict MCP config schemas.
    $entry = [ordered]@{
        command = $pythonExe
        args    = @($serverScript)
    }
}

if ($DryRun) {
    Write-Step 'Mode          : DryRun (no writes, no backups)'
}
else {
    Write-Step 'Mode          : Apply'
}

$claudeConfig = Join-Path $ConfigRoot '.claude.json'
$cursorDir    = Join-Path $ConfigRoot '.cursor'
$cursorConfig = Join-Path $cursorDir 'mcp.json'

Write-Host ''
Write-Host '  Merging configuration into:' -ForegroundColor White
Merge-McpConfig -Path $claudeConfig -Name $ServerName -Entry $entry -DryRunMode $DryRun
Merge-McpConfig -Path $cursorConfig -Name $ServerName -Entry $entry -DryRunMode $DryRun

Write-Section 'Next steps'
Write-Host '  1. Restart Claude Code and Cursor so they reload MCP configuration.'
Write-Host '  2. Confirm the ueassetlens server shows as connected in the client.'
Write-Host '  3. Build the index first: ual-cli build --project <UE project>'
if ($Transport -eq 'Http') {
    Write-Host ('  4. HTTP mode expects the UE6 official MCP plugin to serve ' + $HttpUrl + '.')
    Write-Host '     It is Experimental + NoRedist, loopback-only and unauthenticated; never bind it to an external address.'
    Write-Host '     Use -Transport Stdio for the explicit fallback when that plugin is unavailable.'
}
else {
    Write-Host '  4. stdio is the explicit fallback path; http://localhost:8000/mcp remains the default single entry.'
}
if ($DryRun) {
    Write-Host ''
    Write-Host '  DryRun complete: no file was created, modified, or backed up.' -ForegroundColor Cyan
}
