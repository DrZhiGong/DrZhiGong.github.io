<#
.SYNOPSIS
Open the local website content editor.
.DESCRIPTION
Edit Notes, profile/research, and manuscript/patent entries in your browser.
Saves update source files and the local preview, but do not commit or publish.
Runs on this computer only. Keep this terminal open; Ctrl+C stops the editor.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PythonExecutable = '',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$siteRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\', '/')
$editorUrl = "http://127.0.0.1:$Port/editor/"
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
        throw 'Python was not found. Install Python 3.12 and requirements.txt, or pass -PythonExecutable.'
    }
    if (-not $PSCmdlet.ShouldProcess($siteRoot, "Build the website and open the local editor at $editorUrl using $pythonCommand")) {
        return
    }
    $editorArguments = @('scripts/editor_server.py', '--port', "$Port")
    if (-not $NoBrowser) { $editorArguments += '--open-browser' }
    Write-Host "Local content editor: $editorUrl"
    Write-Host 'Keep this terminal open. Press Ctrl+C to stop. Saving does not publish to GitHub.'
    & $pythonCommand @pythonPrefix @editorArguments
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 130) {
        throw "The editor stopped with exit code $LASTEXITCODE. If the port is in use, try -Port 8080."
    }
} finally {
    Pop-Location
}
