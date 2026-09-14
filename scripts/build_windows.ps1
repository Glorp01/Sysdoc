param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$repoPath = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repoPath
try {
    $version = & $Python -c "from sysdoc import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) { throw "Could not read app version" }
    foreach ($entry in @(@("sysdoc", "packaging/cli_entry.py", "--console"))) {
        & $Python -m PyInstaller --noconfirm --clean --onefile $entry[2] --name $entry[0] --paths . --collect-all google.genai --copy-metadata google-genai --distpath dist/windows --workpath build/pyinstaller --specpath build $entry[1]
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $($entry[0])" }
    }
    & $Python -m build --no-isolation --outdir dist/release
    if ($LASTEXITCODE -ne 0) { throw "Python package build failed" }
    Copy-Item -LiteralPath dist/windows/sysdoc.exe -Destination dist/release/Sysdoc-Terminal-x64.exe -Force
    $hashes = Get-ChildItem -LiteralPath dist/release -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | ForEach-Object {
        "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower())  $($_.Name)"
    }
    $hashes | Set-Content -LiteralPath dist/release/SHA256SUMS.txt -Encoding ascii
} finally {
    Pop-Location
}
