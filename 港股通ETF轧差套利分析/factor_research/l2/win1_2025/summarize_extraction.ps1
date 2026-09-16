$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$root='F:\港股通ETF轧差套利分析\win1_2025'
$utf8=New-Object System.Text.UTF8Encoding($false)
$snapshot=Get-Content -LiteralPath "$root\snapshot.json" -Raw -Encoding UTF8|ConvertFrom-Json
$symbols=Get-Content -LiteralPath "$root\universe.json" -Raw -Encoding UTF8|ConvertFrom-Json
$allowed=@{};foreach($s in $symbols){$allowed[$s]=$true}
$done=New-Object 'System.Collections.Generic.List[object]';$pending=New-Object 'System.Collections.Generic.List[string]'
$filesCount=0;$bytes=[long]0;$variants=0;$invalid=0
foreach($archive in $snapshot){
 $day=$archive.Name.Substring(0,8);$path="$root\manifests\$day.json"
 if(!(Test-Path -LiteralPath $path)){$pending.Add($day);continue}
 $m=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json
 if($m.status -ne 'extracted'){$pending.Add($day);continue}
 foreach($f in $m.files){if(!$allowed.ContainsKey($f.symbol)){$invalid++};$filesCount++;$bytes+=$f.bytes;if($f.ambiguous_variant){$variants++}}
 $done.Add(@{day=$day;files=$m.files.Count;symbols=@($m.files.symbol|Sort-Object -Unique).Count;completed_at=$m.completed_at})
}
$result=@{snapshot_days=$snapshot.Count;completed_days=$done.Count;pending_days=$pending.Count;pending=@($pending.ToArray());files=$filesCount;bytes=$bytes;ambiguous_variant_files=$variants;non_target_manifest_files=$invalid;completion=@($done.ToArray());updated=(Get-Date).ToUniversalTime().ToString('o');output="$root\retained";archive_integrity_test_run=$false;source_archives_deleted=$false}
$json=$result|ConvertTo-Json -Depth 6
[IO.File]::WriteAllText("$root\summary.json",$json,$utf8)
$result.Remove('completion');$result.Remove('pending');$result|ConvertTo-Json -Depth 4
