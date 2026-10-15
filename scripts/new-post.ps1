<#
.SYNOPSIS
Create an unpublished Markdown blog draft.
.EXAMPLE
.\scripts\new-post.ps1 'A research note'
.EXAMPLE
.\scripts\new-post.ps1 'A research note' -Slug 'research-note' -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$Title,
    [string]$Slug = '',
    [string]$PythonExecutable = ''
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$siteRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\', '/')
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
        throw 'Python was not found. Install Python 3.12, or pass -PythonExecutable.'
    }
    $postArguments = @('scripts/new_post.py', $Title)
    if ($Slug) { $postArguments += @('--slug', $Slug) }
    if ($PSCmdlet.ShouldProcess("content/posts ($Title)", "Create a draft using $pythonCommand")) {
        & $pythonCommand @pythonPrefix @postArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Draft creation failed (exit $LASTEXITCODE). Existing posts were not overwritten."
        }
    }
} finally {
    Pop-Location
}
