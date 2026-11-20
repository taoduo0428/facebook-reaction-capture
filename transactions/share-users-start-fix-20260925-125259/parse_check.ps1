$ErrorActionPreference = "Stop"
$files = @("run_facebook_capture.ps1", "explore_facebook_readonly.ps1", "open_facebook_post_readonly.ps1")
foreach ($f in $files) {
  $errors = $null
  $tokens = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $f), [ref]$tokens, [ref]$errors)
  Write-Output "$f parse_errors=$(@($errors).Count)"
  if (@($errors).Count -ne 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }
}
