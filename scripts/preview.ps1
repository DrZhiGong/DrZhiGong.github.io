<#
.SYNOPSIS
Build and preview the website locally using the saved research snapshot.
.DESCRIPTION
Does not refresh ORCID, change Git, or publish anything. Runs the server in this
terminal, bound to this computer only. Open the printed URL and press Ctrl+C to stop.
.EXAMPLE
.\scripts\preview.ps1
.EXAMPLE
.\scripts\preview.ps1 -Port 8080 -PythonExecutable .\.venv\Scripts\python.exe
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PythonExecutable = '',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$siteRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\', '/')
$previewUrl = "http://127.0.0.1:$Port"
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
    if (-not $PSCmdlet.ShouldProcess($siteRoot, "Build the saved snapshot and serve locally at $previewUrl/ using $pythonCommand")) {
        return
    }
    & $pythonCommand @pythonPrefix 'scripts/build_site.py' '--base-url' $previewUrl
    if ($LASTEXITCODE -ne 0) {
        throw "The site build failed (exit $LASTEXITCODE). The preview server was not started."
    }
    Write-Host "Open $previewUrl/ in your browser. Press Ctrl+C in this terminal to stop."
    & $pythonCommand @pythonPrefix '-m' 'http.server' "$Port" '--bind' '127.0.0.1' '--directory' '_site'
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 130) {
        throw "The preview server stopped with exit code $LASTEXITCODE. If the port is in use, try -Port 8080."
    }
} finally {
    Pop-Location
}
