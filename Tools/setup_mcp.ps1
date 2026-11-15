#Requires -Version 7.0
<#
.SYNOPSIS
    Generate or merge MCP client configuration for UEAssetLens (UAL).

.DESCRIPTION
    Adds a stdio MCP server entry that launches Server/ual_mcp/server.py
    into the Claude Code and Cursor user configuration files:
      - Claude Code : $env:USERPROFILE/.claude.json
      - Cursor      : $env:USERPROFILE/.cursor/mcp.json
    Existing mcpServers entries and all other top-level keys are preserved
    (merge, never wholesale overwrite). Before the first modification the
    original file is copied to <file>.bak. The operation is idempotent.
    Use -DryRun to preview all actions without writing anything.

.PARAMETER DryRun
    Print the planned actions and resulting JSON without creating,
    modifying, or backing up any file.

.EXAMPLE
    pwsh -File Tools/setup_mcp.ps1 -DryRun

.EXAMPLE
    pwsh -File Tools/setup_mcp.ps1
#>
[CmdletBinding()]
param(
    [switch]$DryRun
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
function Test-EntryEqual {
    param(
        $Existing,
        [System.Collections.IDictionary]$Desired
    )
    if ($Existing -isnot [System.Collections.IDictionary]) { return $false }
    if (-not $Existing.Contains('command')) { return $false }
    if (-not $Existing.Contains('args')) { return $false }
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
Write-Section 'UEAssetLens MCP client setup'

$repoRoot     = Resolve-RepoRoot
$serverScript = Convert-ToForwardSlash (Join-Path $repoRoot 'Server/ual_mcp/server.py')
$pythonExe    = Convert-ToForwardSlash (Resolve-PythonExe -RepoRoot $repoRoot)

Write-Step ('RepoRoot      : ' + (Convert-ToForwardSlash $repoRoot))
Write-Step ('Interpreter   : ' + $pythonExe)
Write-Step ('Server script : ' + $serverScript)
Write-Step ('Transport     : stdio (command + args; no SSE/HTTP url)')
if ($DryRun) {
    Write-Step 'Mode          : DryRun (no writes, no backups)'
}
else {
    Write-Step 'Mode          : Apply'
}

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

$claudeConfig = Join-Path $env:USERPROFILE '.claude.json'
$cursorDir    = Join-Path $env:USERPROFILE '.cursor'
$cursorConfig = Join-Path $cursorDir 'mcp.json'

Write-Host ''
Write-Host '  Merging configuration into:' -ForegroundColor White
Merge-McpConfig -Path $claudeConfig -Name $ServerName -Entry $entry -DryRunMode $DryRun
Merge-McpConfig -Path $cursorConfig -Name $ServerName -Entry $entry -DryRunMode $DryRun

Write-Section 'Next steps'
Write-Host '  1. Restart Claude Code and Cursor so they reload MCP configuration.'
Write-Host '  2. Confirm the ueassetlens server shows as connected in the client.'
Write-Host '  3. Build the index first: ual-cli build --project <UE project>'
if ($DryRun) {
    Write-Host ''
    Write-Host '  DryRun complete: no file was created, modified, or backed up.' -ForegroundColor Cyan
}
