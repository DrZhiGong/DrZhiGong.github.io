<#
.SYNOPSIS
Prepare this folder for the DrZhiGong.github.io repository.
.DESCRIPTION
Creates a local main branch if needed and adds the expected origin remote.
Does not create the GitHub repository, commit, push, or change an existing remote.
Use -WhatIf to inspect the plan without changing files or Git configuration.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('Https', 'Ssh')]
    [string]$Protocol = 'Https'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$siteRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\', '/')
$expectedHttps = 'https://github.com/DrZhiGong/DrZhiGong.github.io.git'
$expectedSsh = 'git@github.com:DrZhiGong/DrZhiGong.github.io.git'
$remoteUrl = if ($Protocol -eq 'Ssh') { $expectedSsh } else { $expectedHttps }

function Invoke-CheckedGit {
    param([string[]]$GitArguments)
    & git -C $siteRoot @GitArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git failed (exit $LASTEXITCODE): git $($GitArguments -join ' ')"
    }
}

function Test-ExpectedRemote {
    param([string]$Url)
    return $Url -match '^(https://github\.com/DrZhiGong/DrZhiGong\.github\.io(?:\.git)?|git@github\.com:DrZhiGong/DrZhiGong\.github\.io(?:\.git)?)$'
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is not installed or is not on PATH. Install Git for Windows and reopen PowerShell.'
}

# Windows PowerShell 5.1 treats redirected native stderr as an error record.
# A missing repository is expected here; inspect Git's exit code explicitly.
try {
    $ErrorActionPreference = 'Continue'
    $gitRootOutput = @(& git -C $siteRoot rev-parse --show-toplevel 2>$null)
    $isRepository = $LASTEXITCODE -eq 0
} finally {
    $ErrorActionPreference = 'Stop'
}
if ($isRepository) {
    $gitRoot = [System.IO.Path]::GetFullPath($gitRootOutput[0]).TrimEnd('\', '/')
    if (-not [string]::Equals($gitRoot, $siteRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "This website folder is inside another repository ($gitRoot). No changes made. Use a separate website checkout."
    }
    $branch = Invoke-CheckedGit -GitArguments @('symbolic-ref', '--quiet', '--short', 'HEAD')
    if ($branch -ne 'main') {
        throw "Expected branch 'main', found '$branch'. Switch to main yourself before running setup."
    }
} elseif (Test-Path -LiteralPath (Join-Path $siteRoot '.git')) {
    throw 'A .git entry exists but Git could not open it. Resolve that repository problem before setup.'
}

if ($isRepository) {
    $remoteNames = @(Invoke-CheckedGit -GitArguments @('remote'))
    if ($remoteNames -contains 'origin') {
        $fetchUrls = @(Invoke-CheckedGit -GitArguments @('remote', 'get-url', '--all', 'origin'))
        $pushUrls = @(Invoke-CheckedGit -GitArguments @('remote', 'get-url', '--push', '--all', 'origin'))
        foreach ($configuredUrl in @($fetchUrls + $pushUrls)) {
            if (-not (Test-ExpectedRemote -Url $configuredUrl)) {
                throw "Origin is not the expected website repository: $configuredUrl. No remote was changed."
            }
        }
        Write-Host 'The main branch and origin remote are already configured.'
        return
    }
}

if (-not $isRepository) {
    if ($PSCmdlet.ShouldProcess($siteRoot, 'Initialize a Git repository with branch main')) {
        Invoke-CheckedGit -GitArguments @('init', '--initial-branch=main')
    }
}
if ($PSCmdlet.ShouldProcess($remoteUrl, 'Add origin remote')) {
    Invoke-CheckedGit -GitArguments @('remote', 'add', 'origin', $remoteUrl)
}
Write-Host 'Next: create the empty public DrZhiGong.github.io repository on GitHub, then run scripts/update-site.ps1.'
