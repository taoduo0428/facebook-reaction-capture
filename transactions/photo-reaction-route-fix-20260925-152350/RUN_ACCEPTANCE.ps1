param([Parameter(Mandatory=$true)][string]$Label)
$ErrorActionPreference='Stop'
$base='http://127.0.0.1:8765'
$payload=@{
  post_url='https://www.facebook.com/100064751857206/posts/1598693948965652'
  collect=@('reactions','shares')
  save_artifacts=@('safe_capture','progress')
  reaction_scroll_limit=1
} | ConvertTo-Json -Depth 5
$submitted=Invoke-RestMethod -Method Post -Uri "$base/v1/jobs" -ContentType 'application/json; charset=utf-8' -Body $payload
$jobId=[string]$submitted.job_id
Write-Output "label=$Label"
Write-Output "job_id=$jobId"
Write-Output "payload=$payload"
$deadline=(Get-Date).AddMinutes(12)
do {
  Start-Sleep -Seconds 2
  $job=Invoke-RestMethod -Method Get -Uri "$base/v1/jobs/$jobId"
  Write-Output ("status={0} reaction_users={1} reaction_pages={2} share_users={3} share_pages={4}" -f $job.status,$job.progress.reaction_users,$job.progress.reaction_pages,$job.progress.share_users,$job.progress.share_pages)
  if((Get-Date) -gt $deadline){ throw "timeout waiting for $jobId" }
} while($job.status -in @('queued','running','cancelling'))
$job | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot "$Label-final-job.json")
Write-Output ("final_status={0}" -f $job.status)
Write-Output ("run_dir={0}" -f $job.capture_run_dir)
if($job.status -eq 'completed'){
  $result=Invoke-RestMethod -Method Get -Uri "$base/v1/jobs/$jobId/result"
  $result | ConvertTo-Json -Depth 30 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot "$Label-result.json")
  Write-Output ("completion_scope={0} reaction_users={1} share_users={2}" -f $result.completion_scope,$result.reaction_users.Count,$result.share_users.Count)
  exit 0
}
Write-Error ([string]$job.error)
exit 1
