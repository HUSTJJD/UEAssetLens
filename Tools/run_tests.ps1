#Requires -Version 7.0
<#
.SYNOPSIS
    Run the UEAssetLens (UAL) test suite and the CLI self-test.

.DESCRIPTION
    Locates the repository root, prepares a local virtual environment
    (unless -SkipVenv is supplied), installs dependencies from
    requirements.txt, runs pytest against Server/tests, and finally runs
    the CLI self-test. Any failed step aborts the run with a non-zero exit
    code. Output is grouped into labelled sections.

.PARAMETER SkipVenv
    Do not create or reuse a local .venv; use the interpreter on PATH (or
    the already-activated environment) instead. Dependency installation
    still runs so that the chosen environment is complete.

.EXAMPLE
    pwsh -File Tools/run_tests.ps1

.EXAMPLE
    pwsh -File Tools/run_tests.ps1 -SkipVenv
#>
[CmdletBinding()]
param(
    [switch]$SkipVenv
)

$ErrorActionPreference = 'Stop'

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
    Write-Host ('[step] ' + $Message) -ForegroundColor Gray
}

function Assert-ExitCode {
    param(
        [int]$Code,
        [string]$What
    )
    if ($Code -ne 0) {
        throw ($What + ' failed with exit code ' + $Code)
    }
    Write-Host ('[ok]   ' + $What + ' passed.') -ForegroundColor Green
}

function Get-BasePython {
    foreach ($name in @('python', 'python3', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $cmd) { return $cmd.Source }
    }
    throw 'Python 3.10+ not found on PATH. Install Python or add it to PATH.'
}

function Resolve-RepoRoot {
    $dir = $PSScriptRoot
    for ($i = 0; $i -lt 6; $i++) {
        if ((Test-Path -LiteralPath (Join-Path $dir 'requirements.txt')) -and
            (Test-Path -LiteralPath (Join-Path $dir 'Server'))) {
            return (Resolve-Path -LiteralPath $dir).Path
        }
        $parent = Split-Path -Parent $dir
        if ([string]::IsNullOrEmpty($parent) -or ($parent -eq $dir)) { break }
        $dir = $parent
    }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}

# ---------------------------------------------------------------------------
Write-Section '1/5  Locating repository root'
$repoRoot = Resolve-RepoRoot
Write-Step ('RepoRoot    : ' + $repoRoot)

$requirements = Join-Path $repoRoot 'requirements.txt'
$testDir      = Join-Path $repoRoot 'Server/tests'
$cliEntry     = Join-Path $repoRoot 'Server/ual_cli/ual_cli.py'

if (-not (Test-Path -LiteralPath $requirements)) {
    Write-Host ('[FAIL] requirements.txt not found at ' + $requirements) -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $testDir)) {
    Write-Host ('[FAIL] Server/tests not found at ' + $testDir) -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $cliEntry)) {
    Write-Host ('[FAIL] ual_cli.py not found at ' + $cliEntry) -ForegroundColor Red
    exit 1
}

Push-Location -LiteralPath $repoRoot
$overallOk = $true
try {
    # -----------------------------------------------------------------------
    Write-Section '2/5  Preparing Python environment'
    if ($SkipVenv) {
        Write-Step '-SkipVenv: using interpreter from PATH / active environment.'
        $python = Get-BasePython
    }
    else {
        $venvDir = Join-Path $repoRoot '.venv'
        if (-not (Test-Path -LiteralPath $venvDir)) {
            Write-Step ('Creating venv at ' + $venvDir)
            $base = Get-BasePython
            & $base -m venv $venvDir
            Assert-ExitCode -Code $LASTEXITCODE -What 'python -m venv .venv'
        }
        else {
            Write-Step ('Reusing existing venv at ' + $venvDir)
        }
        if ($IsWindows) {
            $venvPython = Join-Path $venvDir 'Scripts/python.exe'
        }
        else {
            $venvPython = Join-Path $venvDir 'bin/python'
        }
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw ('venv python not found at ' + $venvPython)
        }
        $python = $venvPython
    }
    Write-Step ('Interpreter : ' + $python)

    # -----------------------------------------------------------------------
    Write-Section '3/5  Installing dependencies'
    & $python -m pip install -r $requirements
    Assert-ExitCode -Code $LASTEXITCODE -What 'pip install -r requirements.txt'

    # -----------------------------------------------------------------------
    Write-Section '4/5  Running pytest (Server/tests)'
    & $python -m pytest $testDir -q
    Assert-ExitCode -Code $LASTEXITCODE -What 'python -m pytest Server/tests -q'

    # -----------------------------------------------------------------------
    Write-Section '5/5  Running CLI self-test'
    & $python $cliEntry self-test
    Assert-ExitCode -Code $LASTEXITCODE -What 'ual_cli.py self-test'
}
catch {
    Write-Host ''
    Write-Host ('[FAIL] ' + $_.Exception.Message) -ForegroundColor Red
    $overallOk = $false
}
finally {
    Pop-Location
}

Write-Host ''
if (-not $overallOk) {
    Write-Section 'RESULT: FAILED'
    Write-Host 'One or more checks failed; exiting with a non-zero code.' -ForegroundColor Red
    exit 1
}
Write-Section 'RESULT: ALL CHECKS PASSED'
Write-Host 'pytest and the CLI self-test both completed successfully.' -ForegroundColor Green
exit 0
