<#
.SYNOPSIS
Exercise the Git helpers in isolated local repositories, without network access.
.DESCRIPTION
Creates disposable fixtures under .local/git-script-tests-<id>. It never initializes
the real website repository. Push and Python calls are simulated; all repository
checks, staging, and commits use real Git. Fixtures are left for inspection.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$testRoot = Join-Path $sourceRoot ('.local/git-script-tests-' + [Guid]::NewGuid().ToString('N'))
$gitExecutable = (Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$script:assertionCount = 0
$wrapperRoot = Join-Path $testRoot 'command-wrappers'
[void][System.IO.Directory]::CreateDirectory($wrapperRoot)
$savedPath = $env:PATH
$savedGitCeiling = $env:GIT_CEILING_DIRECTORIES
# These fixtures live inside the real checkout; do not discover its Git metadata.
$env:GIT_CEILING_DIRECTORIES = $testRoot
$env:WEBSITE_TEST_REAL_GIT = $gitExecutable
$env:WEBSITE_TEST_PUSH_LOG = Join-Path $testRoot 'push.log'
$env:WEBSITE_TEST_PYTHON_LOG = Join-Path $testRoot 'python.log'
$env:WEBSITE_TEST_PUSH_EXIT = '0'
$env:WEBSITE_TEST_BUILD_FAIL = '0'
$env:WEBSITE_TEST_GUARD_FAIL = ''
$fakePythonPath = Join-Path $wrapperRoot 'test-python.cmd'
Set-Content -LiteralPath (Join-Path $wrapperRoot 'git.cmd') -Encoding ascii -Value @'
@echo off
if /I "%~3"=="push" goto mockpush
"%WEBSITE_TEST_REAL_GIT%" %*
exit /b %ERRORLEVEL%
:mockpush
echo push>>"%WEBSITE_TEST_PUSH_LOG%"
exit /b %WEBSITE_TEST_PUSH_EXIT%
'@
Set-Content -LiteralPath $fakePythonPath -Encoding ascii -Value @'
@echo off
echo python>>"%WEBSITE_TEST_PYTHON_LOG%"
if "%~1"=="scripts/check_public_source.py" if "%~2"=="%WEBSITE_TEST_GUARD_FAIL%" exit /b 8
if not "%~1"=="scripts/build_site.py" exit /b 0
if "%WEBSITE_TEST_BUILD_FAIL%"=="1" exit /b 9
if not exist "_site" mkdir "_site"
echo Test site>"_site\index.html"
exit /b 0
'@
$env:PATH = $wrapperRoot + [System.IO.Path]::PathSeparator + $savedPath

function Get-PushCount {
    if (Test-Path -LiteralPath $env:WEBSITE_TEST_PUSH_LOG) {
        return @(Get-Content -LiteralPath $env:WEBSITE_TEST_PUSH_LOG).Count
    }
    return 0
}

function Assert-True {
    param([bool]$Condition, [string]$Reason)
    if (-not $Condition) { throw "Assertion failed: $Reason" }
    $script:assertionCount += 1
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Pattern)
    $caught = $null
    try { & $Action } catch { $caught = $_.Exception.Message }
    Assert-True -Condition ($null -ne $caught -and $caught -match $Pattern) -Reason "Expected error matching '$Pattern'; got '$caught'"
}

function New-Fixture {
    param([string]$Name)
    $fixtureRoot = Join-Path $testRoot $Name
    [void][System.IO.Directory]::CreateDirectory((Join-Path $fixtureRoot 'scripts'))
    foreach ($scriptName in @('setup-github.ps1', 'update-site.ps1', 'check_public_source.py')) {
        Copy-Item -LiteralPath (Join-Path $sourceRoot "scripts/$scriptName") -Destination (Join-Path $fixtureRoot "scripts/$scriptName")
    }
    Copy-Item -LiteralPath (Join-Path $sourceRoot '.gitignore') -Destination (Join-Path $fixtureRoot '.gitignore')
    Set-Content -LiteralPath (Join-Path $fixtureRoot 'README.md') -Value '# Test fixture'
    return $fixtureRoot
}

try {
$fixture = New-Fixture -Name 'normal'
$setupScript = Join-Path $fixture 'scripts/setup-github.ps1'
$updateScript = Join-Path $fixture 'scripts/update-site.ps1'
& $setupScript -WhatIf
Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $fixture '.git'))) -Reason 'Setup WhatIf must not initialize Git.'
& $setupScript
git -C $fixture config user.name 'Website Script Test'
git -C $fixture config user.email 'website-test@example.invalid'
Assert-True -Condition ((git -C $fixture branch --show-current) -eq 'main') -Reason 'Setup must create main.'
Assert-True -Condition ((git -C $fixture remote get-url origin) -eq 'https://github.com/DrZhiGong/DrZhiGong.github.io.git') -Reason 'Setup must add the expected origin.'
& $setupScript

& $updateScript -WhatIf
Assert-True -Condition (-not (Test-Path -LiteralPath $env:WEBSITE_TEST_PYTHON_LOG)) -Reason 'Update WhatIf must not invoke Python.'
Assert-True -Condition ((@(git -C $fixture ls-files)).Count -eq 0) -Reason 'Update WhatIf must not stage files.'
Assert-True -Condition ((Get-PushCount) -eq 0) -Reason 'Update WhatIf must not push.'

Set-Content -LiteralPath (Join-Path $fixture 'Zhi Gong_CV_Rice.pdf') -Value 'PRIVATE CV TEST'
Set-Content -LiteralPath (Join-Path $fixture 'private-notes.txt') -Value 'PRIVATE NOTES TEST'
[void][System.IO.Directory]::CreateDirectory((Join-Path $fixture 'editor'))
Set-Content -LiteralPath (Join-Path $fixture 'editor/index.html') -Value '<h1>Local editor</h1>'
Set-Content -LiteralPath (Join-Path $fixture 'Edit Website.cmd') -Value '@echo Editor launcher'
Set-Content -LiteralPath (Join-Path $fixture 'scripts/editor_server.py') -Value '# Local editor server'
Set-Content -LiteralPath (Join-Path $fixture 'scripts/edit-site.ps1') -Value '# Local editor launcher'
[void][System.IO.Directory]::CreateDirectory((Join-Path $fixture '.local/drafts'))
Set-Content -LiteralPath (Join-Path $fixture '.local/drafts/private.md') -Value 'PRIVATE DRAFT TEST'
[void][System.IO.Directory]::CreateDirectory((Join-Path $fixture '.local/editor-backups'))
Set-Content -LiteralPath (Join-Path $fixture '.local/editor-backups/old-note.md') -Value 'LOCAL BACKUP TEST'
& $updateScript -SkipSync -NoPush -PythonExecutable $fakePythonPath -Message 'Initial fixture website'
$tracked = @(git -C $fixture ls-files)
Assert-True -Condition ($tracked -contains 'README.md') -Reason 'Website source must be committed.'
Assert-True -Condition ($tracked -notcontains 'editor/index.html') -Reason 'Local editor source must stay off GitHub.'
Assert-True -Condition ($tracked -notcontains 'Edit Website.cmd') -Reason 'Double-click editor launcher must stay local.'
Assert-True -Condition ($tracked -notcontains 'scripts/editor_server.py') -Reason 'Local editor backend must not be staged with public scripts.'
Assert-True -Condition ($tracked -notcontains 'scripts/edit-site.ps1') -Reason 'Local editor PowerShell launcher must not be staged.'
Assert-True -Condition ($tracked -notcontains '.local/drafts/private.md') -Reason 'Private drafts must never be included.'
Assert-True -Condition (Test-Path -LiteralPath (Join-Path $fixture 'Edit Website.cmd')) -Reason 'Ignored tools must remain on disk.'
Assert-True -Condition ($tracked -notcontains '.local/editor-backups/old-note.md') -Reason 'Local content backups must stay out of Git.'
Assert-True -Condition ($tracked -notcontains 'Zhi Gong_CV_Rice.pdf') -Reason 'Raw CV must stay out of Git.'
Assert-True -Condition ($tracked -notcontains 'private-notes.txt') -Reason 'Unlisted personal files must stay out of Git.'
Assert-True -Condition ($tracked -notcontains '_site/index.html') -Reason 'Generated site must stay out of Git.'
Assert-True -Condition ((Get-PushCount) -eq 0) -Reason 'NoPush must not push.'

& $updateScript -SkipSync -PythonExecutable $fakePythonPath
Assert-True -Condition ((Get-PushCount) -eq 1) -Reason 'No new diff must still permit pushing previously created commits.'
$headBeforeFailure = git -C $fixture rev-parse HEAD
Set-Content -LiteralPath (Join-Path $fixture 'README.md') -Value '# Changed fixture'
$env:WEBSITE_TEST_BUILD_FAIL = '1'
Assert-Throws -Action { & $updateScript -SkipSync -PythonExecutable $fakePythonPath } -Pattern 'Python failed'
Assert-True -Condition ((git -C $fixture rev-parse HEAD) -eq $headBeforeFailure) -Reason 'Build failure must not create a commit.'
Assert-True -Condition ((Get-PushCount) -eq 1) -Reason 'Build failure must not push.'
Assert-True -Condition ((@(git -C $fixture diff --cached --name-only)).Count -eq 0) -Reason 'Build failure must not stage files.'
$env:WEBSITE_TEST_BUILD_FAIL = '0'

foreach ($guardPhase in @('--worktree', '--staged')) {
    $env:WEBSITE_TEST_GUARD_FAIL = $guardPhase
    Assert-Throws -Action { & $updateScript -SkipSync -PythonExecutable $fakePythonPath } -Pattern 'Python failed.*check_public_source'
    Assert-True -Condition ((git -C $fixture rev-parse HEAD) -eq $headBeforeFailure) -Reason 'Privacy check failure must not commit.'
    Assert-True -Condition ((Get-PushCount) -eq 1) -Reason 'Privacy check failure must not push.'
}
$env:WEBSITE_TEST_GUARD_FAIL = ''
git -C $fixture restore --staged -- README.md

git -C $fixture add --force -- editor/index.html
Assert-Throws -Action { & $updateScript -SkipSync -NoPush -PythonExecutable $fakePythonPath } -Pattern 'outside the website allowlist'
git -C $fixture rm --cached -- editor/index.html
git -C $fixture add --force -- scripts/editor_server.py
Assert-Throws -Action { & $updateScript -SkipSync -NoPush -PythonExecutable $fakePythonPath } -Pattern 'outside the website allowlist'
git -C $fixture rm --cached -- scripts/editor_server.py

git -C $fixture add -- private-notes.txt
Assert-Throws -Action { & $updateScript -SkipSync -NoPush -PythonExecutable $fakePythonPath } -Pattern 'outside the website allowlist'
git -C $fixture restore --staged -- private-notes.txt

$env:WEBSITE_TEST_PUSH_EXIT = '12'
Assert-Throws -Action { & $updateScript -SkipSync -PythonExecutable $fakePythonPath -Message 'Update fixture' } -Pattern 'Git failed.*push'
Assert-True -Condition ((Get-PushCount) -eq 2) -Reason 'Push failures must be visible to the caller.'
$env:WEBSITE_TEST_PUSH_EXIT = '0'

git -C $fixture remote set-url origin 'https://github.com/example/unrelated.git'
Assert-Throws -Action { & $setupScript } -Pattern 'not the expected website repository'
Assert-Throws -Action { & $updateScript -WhatIf } -Pattern 'Unexpected origin URL'
git -C $fixture remote set-url origin 'https://github.com/DrZhiGong/DrZhiGong.github.io.git'
git -C $fixture remote set-url --push origin 'https://github.com/example/unrelated.git'
Assert-Throws -Action { & $updateScript -WhatIf } -Pattern 'Unexpected origin URL'

$nested = Join-Path $fixture 'nested-site'
[void][System.IO.Directory]::CreateDirectory((Join-Path $nested 'scripts'))
Copy-Item -LiteralPath (Join-Path $sourceRoot 'scripts/setup-github.ps1') -Destination (Join-Path $nested 'scripts/setup-github.ps1')
Copy-Item -LiteralPath (Join-Path $sourceRoot 'scripts/update-site.ps1') -Destination (Join-Path $nested 'scripts/update-site.ps1')
Assert-Throws -Action { & (Join-Path $nested 'scripts/setup-github.ps1') -WhatIf } -Pattern 'inside another repository'
Assert-Throws -Action { & (Join-Path $nested 'scripts/update-site.ps1') -WhatIf } -Pattern 'parent repository'

Write-Host "Passed $script:assertionCount assertions. Fixtures retained at: $testRoot"
} finally {
    $env:PATH = $savedPath
    $env:GIT_CEILING_DIRECTORIES = $savedGitCeiling
    foreach ($testEnvironmentVariable in @('WEBSITE_TEST_REAL_GIT', 'WEBSITE_TEST_PUSH_LOG', 'WEBSITE_TEST_PYTHON_LOG', 'WEBSITE_TEST_PUSH_EXIT', 'WEBSITE_TEST_BUILD_FAIL', 'WEBSITE_TEST_GUARD_FAIL')) {
        [Environment]::SetEnvironmentVariable($testEnvironmentVariable, $null, 'Process')
    }
}
