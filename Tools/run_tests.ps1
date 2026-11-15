#Requires -Version 7.0
<#
.SYNOPSIS
    Run the UEAssetLens (UAL) test suite, regenerate reference docs, and build
    the MkDocs site in strict mode.

.DESCRIPTION
    Locates the repository root, prepares a local virtual environment
    (unless -SkipVenv is supplied), installs runtime and docs dependencies,
    regenerates the auto-generated reference pages, builds the documentation
    site with --strict, runs pytest, and finally runs the CLI self-test.
    Any failed step aborts the run with a non-zero exit code.

.PARAMETER SkipVenv
    Do not create or reuse a local .venv; use the interpreter on PATH (or the
    already-activated environment) instead. Dependency installation still runs.

.PARAMETER SkipDocs
    Skip docs dependency install, reference generation, and the strict site
    build. Useful when mkdocs-material is not available locally. CI never uses
    this switch.

.EXAMPLE
    pwsh -File Tools/run_tests.ps1

.EXAMPLE
    pwsh -File Tools/run_tests.ps1 -SkipVenv -SkipDocs
#>
[CmdletBinding()]
param(
    [switch]$SkipVenv,
    [switch]$SkipDocs
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
            (Test-Path -LiteralPath (Join-Path $dir 'mkdocs.yml')) -and
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
Write-Section '1/8  Locating repository root'
$repoRoot = Resolve-RepoRoot
Write-Step ('RepoRoot    : ' + $repoRoot)

$requirements = Join-Path $repoRoot 'requirements.txt'
$docsReqs     = Join-Path $repoRoot 'requirements-docs.txt'
$mkdocsYml    = Join-Path $repoRoot 'mkdocs.yml'
$generator    = Join-Path $repoRoot 'Tools/gen_docs.py'
$testDir      = Join-Path $repoRoot 'Server/tests'
$cliEntry     = Join-Path $repoRoot 'Server/ual_cli/ual_cli.py'
$bridgeEntry  = Join-Path $repoRoot 'Server/ual_mcp/engine_bridge.py'

foreach ($required in @($requirements, $testDir, $cliEntry, $bridgeEntry)) {
    if (-not (Test-Path -LiteralPath $required)) {
        Write-Host ('[FAIL] missing required path: ' + $required) -ForegroundColor Red
        exit 1
    }
}
if (-not $SkipDocs) {
    foreach ($required in @($docsReqs, $mkdocsYml, $generator)) {
        if (-not (Test-Path -LiteralPath $required)) {
            Write-Host ('[FAIL] missing docs path: ' + $required) -ForegroundColor Red
            exit 1
        }
    }
}

Push-Location -LiteralPath $repoRoot
$overallOk = $true
try {
    # -----------------------------------------------------------------------
    Write-Section '2/8  Preparing Python environment'
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
    Write-Section '3/8  Installing runtime dependencies'
    & $python -m pip install -r $requirements
    Assert-ExitCode -Code $LASTEXITCODE -What 'pip install -r requirements.txt'

    # -----------------------------------------------------------------------
    Write-Section '4/8  Installing documentation dependencies'
    if ($SkipDocs) {
        Write-Step '-SkipDocs: skipping MkDocs toolchain install.'
    }
    else {
        & $python -m pip install -r $docsReqs
        Assert-ExitCode -Code $LASTEXITCODE -What 'pip install -r requirements-docs.txt'
    }

    # -----------------------------------------------------------------------
    Write-Section '5/8  Generating API reference'
    if ($SkipDocs) {
        Write-Step '-SkipDocs: skipping reference generation.'
    }
    else {
        & $python $generator
        Assert-ExitCode -Code $LASTEXITCODE -What 'Tools/gen_docs.py'
        & $python $generator --check
        Assert-ExitCode -Code $LASTEXITCODE -What 'Tools/gen_docs.py --check'
    }

    # -----------------------------------------------------------------------
    Write-Section '6/8  Building documentation site (strict)'
    if ($SkipDocs) {
        Write-Step '-SkipDocs: skipping mkdocs build --strict.'
    }
    else {
        & $python -m mkdocs build --strict --site-dir _site
        Assert-ExitCode -Code $LASTEXITCODE -What 'mkdocs build --strict'
    }

    # -----------------------------------------------------------------------
    Write-Section '7/8  Running pytest (Server/tests)'
    & $python -m pytest $testDir -q
    Assert-ExitCode -Code $LASTEXITCODE -What 'python -m pytest Server/tests -q'

    # -----------------------------------------------------------------------
    Write-Section '8/8  Running CLI and engine-bridge self-tests'
    & $python $cliEntry self-test
    Assert-ExitCode -Code $LASTEXITCODE -What 'ual_cli.py self-test'
    & $python $bridgeEntry --self-test
    Assert-ExitCode -Code $LASTEXITCODE -What 'engine_bridge.py --self-test'
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
Write-Host 'Docs generation, strict site build, pytest and CLI self-test succeeded.' -ForegroundColor Green
exit 0
