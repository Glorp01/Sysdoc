# Run on an isolated CI runner: exercises installation, upgrade, and uninstallation.
param([Parameter(Mandatory=$true)][string]$Version)
$ErrorActionPreference = "Stop"
$installerPath = (Resolve-Path -LiteralPath "$PSScriptRoot/../dist/release/Sysdoc-Setup-x64.exe").Path
if (-not $env:RUNNER_TEMP) { throw "This installation test is intended for a disposable GitHub Actions runner." }
$testRoot = Join-Path $env:RUNNER_TEMP "sysdoc-installer-smoke"
$installPath = Join-Path $testRoot "Installed App"
$configPath = Join-Path $env:USERPROFILE ".sysdoc/config.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $configPath) | Out-Null
if (Test-Path -LiteralPath $configPath) { throw "Refusing to overwrite an existing user configuration." }
$config = '{"gemini_api_key":"ci-test-placeholder"}'
Set-Content -LiteralPath $configPath -Value $config
foreach ($pass in 1..2) {
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', '/TASKS=', ('/DIR="' + $installPath + '"'), ('/LOG="' + $testRoot + "-$pass.log" + '"'))
    if ($pass -eq 2) {
        $arguments += '/UPDATE'
        # Verify that an upgrade really replaces the previous installed binary.
        Set-Content -LiteralPath (Join-Path $installPath 'sysdoc.exe') -Value 'old binary placeholder'
    }
    $process = Start-Process -FilePath $installerPath -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "Installer pass $pass failed: $($process.ExitCode)" }
    $actual = & (Join-Path $installPath 'sysdoc.exe') --version
    if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne "sysdoc $Version") { throw "Installed version mismatch: $actual" }
    & (Join-Path $installPath 'sysdoc.exe') scan storage
    if ($LASTEXITCODE -ne 0) { throw "Installed CLI scan failed" }
    # Each provider's SDK must load in the frozen assistant. The placeholder key is never sent anywhere.
    foreach ($provider in @(@('claude', 'ANTHROPIC_API_KEY'), @('gpt', 'OPENAI_API_KEY'), @('gemini', 'GEMINI_API_KEY'))) {
        Set-Item -Path "Env:$($provider[1])" -Value 'ci-placeholder'
        '/exit' | & (Join-Path $installPath 'sysdoc.exe') --provider $provider[0]
        $code = $LASTEXITCODE
        Remove-Item -Path "Env:$($provider[1])"
        if ($code -ne 0) { throw "Installed assistant failed to start with $($provider[0])" }
    }
    if ((Get-Content -LiteralPath $configPath -Raw).Trim() -ne $config) { throw "Update changed the saved configuration" }
    if (-not (Test-Path -LiteralPath (Join-Path $installPath 'sysdoc-gui.exe'))) { throw "Desktop executable missing" }
    $report = Join-Path $testRoot "desktop-$pass.txt"
    $desktop = Start-Process -FilePath (Join-Path $installPath 'sysdoc-gui.exe') -ArgumentList '--smoke-test', ('"' + $report + '"') -WindowStyle Hidden -PassThru -Wait
    Get-Content -LiteralPath $report
    if ($desktop.ExitCode -ne 0) { throw "Installed desktop smoke test failed" }
}
$uninstaller = Start-Process -FilePath (Join-Path $installPath 'unins000.exe') -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -WindowStyle Hidden -Wait -PassThru
if ($uninstaller.ExitCode -ne 0) { throw "Uninstall failed" }
if ((Get-Content -LiteralPath $configPath -Raw).Trim() -ne $config) { throw "Uninstall removed the saved configuration" }
Write-Output "Install, upgrade, desktop startup, CLI scan, settings preservation, and uninstall passed."
