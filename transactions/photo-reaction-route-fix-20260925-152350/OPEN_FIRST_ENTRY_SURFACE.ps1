$ErrorActionPreference='Stop'
$health=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health'
if($health.worker -ne 'idle'){ throw "worker busy: $($health.workers[0].current_job_id)" }
$serial='emulator-5554'
function Dump-Ui([string]$name){
  $remote="/sdcard/$name"
  $local=Join-Path $PSScriptRoot $name
  adb -s $serial shell uiautomator dump $remote | Out-Null
  adb -s $serial pull $remote $local | Out-Null
  [xml](Get-Content -Raw -Encoding UTF8 $local)
}
function Get-Label($node){ (($node.GetAttribute('text')+' '+$node.GetAttribute('content-desc')).Trim()) }
function Tap-Node($node){
  $m=[regex]::Match($node.GetAttribute('bounds'),'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')
  if(-not $m.Success){throw 'node bounds unavailable'}
  $x=[int](([int]$m.Groups[1].Value+[int]$m.Groups[3].Value)/2)
  $y=[int](([int]$m.Groups[2].Value+[int]$m.Groups[4].Value)/2)
  adb -s $serial shell input tap $x $y | Out-Null
}
$x=Dump-Ui 'first-before.xml'
$photos=@($x.SelectNodes('//node') | Where-Object {
  $_.GetAttribute('package') -eq 'com.facebook.katana' -and $_.GetAttribute('clickable') -eq 'true' -and
  $_.GetAttribute('class') -eq 'android.widget.ImageView' -and (Get-Label $_) -match '^(?:Photo|照片|相片)$'
})
if($photos.Count -ne 1){throw "expected one clickable target photo, found $($photos.Count)"}
Tap-Node $photos[0]
Start-Sleep -Seconds 3
$x=Dump-Ui 'first-viewer.xml'
$comments=@($x.SelectNodes('//node') | Where-Object {
  $_.GetAttribute('package') -eq 'com.facebook.katana' -and $_.GetAttribute('clickable') -eq 'true' -and
  $_.GetAttribute('class') -eq 'android.widget.Button' -and (Get-Label $_) -match '^(?:Comment|Comments|评论|評論)(?:\s+\1)?$'
})
if($comments.Count -ne 1){throw "expected one viewer Comment button, found $($comments.Count)"}
Tap-Node $comments[0]
Start-Sleep -Seconds 3
$x=Dump-Ui 'first-entry-surface.xml'
$top=@($x.SelectNodes('//node') | ForEach-Object {
  $label=Get-Label $_
  if($_.GetAttribute('clickable') -eq 'true' -and $label){[pscustomobject]@{label=$label;class=$_.GetAttribute('class');bounds=$_.GetAttribute('bounds')}}
} | Where-Object {$_.label -match '^[0-9]|分享|share|reaction|心情'})
$top | ConvertTo-Json -Depth 4
