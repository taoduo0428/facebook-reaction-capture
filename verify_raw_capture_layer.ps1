$ErrorActionPreference = 'Stop'
$files = @('run_facebook_capture.ps1', 'open_facebook_raw_capture.ps1')
foreach ($file in $files) {
    $errors = $null
    $tokens = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot $file),
        [ref]$tokens,
        [ref]$errors
    ) | Out-Null
    if ($errors.Count -gt 0) {
        Write-Output "$file FAIL"
        $errors | ForEach-Object { Write-Output $_.Message }
        exit 1
    }
    Write-Output "$file OK"
}

$python = Join-Path $env:SystemRoot 'py.exe'
if (-not (Test-Path $python)) { $python = 'py.exe' }
& $python -m unittest tests.test_facebook_raw_capture
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'tests.test_facebook_raw_capture OK'
