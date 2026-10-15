<#
.SYNOPSIS
Refresh ORCID, build, commit website source files, and push main.
.DESCRIPTION
Runs only in this site's own main-branch repository with the expected origin.
Stops on sync/build/commit/push failures. Never pulls, rebases, or force-pushes.
Only the explicit website paths listed below can be staged by this script.
Use -WhatIf for a no-write preview, or -NoPush to make a local commit only.
.EXAMPLE
.\scripts\update-site.ps1 -Message 'Add a research update'
.EXAMPLE
.\scripts\update-site.ps1 -SkipSync -NoPush -PythonExecutable .\.venv\Scripts\python.exe
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Message = '',
    [string]$PythonExecutable = '',
    [switch]$SkipSync,
    [switch]$NoPush
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$siteRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\', '/')
$siteUrl = 'https://drzhigong.github.io'
$allowedPaths = @(
    'data', 'assets', 'content', 'templates', 'scripts', 'tests', 'editor', '.github',
    'README.md', 'DEPLOYMENT.md', 'requirements.txt', '.gitignore', '.nojekyll', 'Edit Website.cmd'
)

function Invoke-CheckedGit {
    param([string[]]$GitArguments)
    & git -C $siteRoot @GitArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git failed (exit $LASTEXITCODE): git $($GitArguments -join ' ')"
    }
}

function Test-AllowedPath {
    param([string]$Path)
    foreach ($allowedPath in $allowedPaths) {
        if ($Path -eq $allowedPath -or $Path.StartsWith("$allowedPath/", [StringComparison]::Ordinal)) {
            return $true
        }
    }
    return $false
}

function Invoke-CheckedPython {
    param([string[]]$PythonArguments)
    & $pythonCommand @pythonPrefix @PythonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python failed (exit $LASTEXITCODE): $($PythonArguments -join ' '). Nothing was pushed."
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is not installed or is not on PATH.'
}
try {
    $ErrorActionPreference = 'Continue'
    $gitRootOutput = @(& git -C $siteRoot rev-parse --show-toplevel 2>$null)
    $isRepository = $LASTEXITCODE -eq 0
} finally {
    $ErrorActionPreference = 'Stop'
}
if (-not $isRepository) {
    throw 'This folder is not a Git repository yet. First run .\scripts\setup-github.ps1.'
}
$gitRoot = [System.IO.Path]::GetFullPath($gitRootOutput[0]).TrimEnd('\', '/')
if (-not [string]::Equals($gitRoot, $siteRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to modify the parent repository: $gitRoot. Set up a separate website checkout."
}
$branch = Invoke-CheckedGit -GitArguments @('symbolic-ref', '--quiet', '--short', 'HEAD')
if ($branch -ne 'main') {
    throw "Expected branch 'main', found '$branch'. Switch to main before updating the website."
}
$fetchUrls = @(Invoke-CheckedGit -GitArguments @('remote', 'get-url', '--all', 'origin'))
$pushUrls = @(Invoke-CheckedGit -GitArguments @('remote', 'get-url', '--push', '--all', 'origin'))
foreach ($configuredUrl in @($fetchUrls + $pushUrls)) {
    if ($configuredUrl -notmatch '^(https://github\.com/DrZhiGong/DrZhiGong\.github\.io(?:\.git)?|git@github\.com:DrZhiGong/DrZhiGong\.github\.io(?:\.git)?)$') {
        throw "Unexpected origin URL: $configuredUrl. No files were changed or pushed."
    }
}

# Do not accidentally commit changes the user staged outside the site allowlist.
$alreadyStaged = @(Invoke-CheckedGit -GitArguments @('-c', 'core.quotepath=false', 'diff', '--cached', '--name-only'))
foreach ($stagedPath in $alreadyStaged) {
    if (-not (Test-AllowedPath -Path $stagedPath)) {
        throw "A file outside the website allowlist is already staged: $stagedPath. Unstage it yourself before running this script."
    }
}

if ($WhatIfPreference) {
    if (-not $SkipSync) { Write-Host 'What if: refresh data/publications.json from the public ORCID API.' }
    Write-Host "What if: build _site/ for $siteUrl."
    Write-Host "What if: stage only these website paths: $($allowedPaths -join ', ')."
    Write-Host 'What if: commit staged changes if present.'
    if (-not $NoPush) { Write-Host 'What if: push main to the verified origin (including any existing local commits).' }
    return
}

$pythonPrefix = @()
$bundledPython = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if ($PythonExecutable) {
    $pythonCommand = $PythonExecutable
} elseif (Test-Path -LiteralPath (Join-Path $siteRoot '.venv/Scripts/python.exe')) {
    $pythonCommand = Join-Path $siteRoot '.venv/Scripts/python.exe'
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = 'py'
    $pythonPrefix = @('-3')
} elseif (Test-Path -LiteralPath $bundledPython) {
    $pythonCommand = $bundledPython
} else {
    $pythonCommand = 'python'
}

Push-Location -LiteralPath $siteRoot
try {
    if (-not (Get-Command $pythonCommand -ErrorAction SilentlyContinue)) {
        throw 'Python was not found. Install Python 3.12 and the dependencies in requirements.txt, or pass -PythonExecutable.'
    }
    if (-not $PSCmdlet.ShouldProcess($siteRoot, 'Refresh/build the site, stage source changes, and create a commit if needed')) {
        return
    }
    if (-not $SkipSync) {
        Invoke-CheckedPython -PythonArguments @('scripts/sync_orcid.py')
    } else {
        Write-Host 'ORCID refresh skipped; building with the saved publications snapshot.'
    }
    Invoke-CheckedPython -PythonArguments @('scripts/build_site.py', '--base-url', $siteUrl)

    # Include deleted tracked paths as well as files/directories that still exist.
    $pathsToStage = @()
    foreach ($allowedPath in $allowedPaths) {
        $trackedPaths = @(Invoke-CheckedGit -GitArguments @('ls-files', '--', $allowedPath))
        if ((Test-Path -LiteralPath (Join-Path $siteRoot $allowedPath)) -or $trackedPaths.Count -gt 0) {
            $pathsToStage += $allowedPath
        }
    }
    if ($pathsToStage.Count -gt 0) {
        Invoke-CheckedGit -GitArguments (@('add', '--all', '--') + $pathsToStage)
    }
    & git -C $siteRoot diff --cached --quiet --exit-code
    $diffExitCode = $LASTEXITCODE
    if ($diffExitCode -eq 1) {
        if ([string]::IsNullOrWhiteSpace($Message)) {
            $Message = 'Update website ' + (Get-Date -Format 'yyyy-MM-dd HH:mm')
        }
        Invoke-CheckedGit -GitArguments @('commit', '-m', $Message)
    } elseif ($diffExitCode -eq 0) {
        Write-Host 'No new website source changes to commit.'
    } else {
        throw "Could not inspect staged changes (Git exit $diffExitCode). Nothing was pushed."
    }

    if ($NoPush) {
        Write-Host 'Local update complete. Push skipped because -NoPush was specified.'
    } elseif ($PSCmdlet.ShouldProcess('origin/main', 'Push main to GitHub')) {
        # Always attempt a normal push: an earlier failed push may have left local commits ahead.
        Invoke-CheckedGit -GitArguments @('push', '--set-upstream', 'origin', 'main')
        Write-Host "Pushed successfully. Follow the Pages workflow on GitHub; the site is $siteUrl/."
    }
} finally {
    Pop-Location
}
