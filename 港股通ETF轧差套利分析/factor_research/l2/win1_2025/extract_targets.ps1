param([string]$Root='F:\港股通ETF轧差套利分析\win1_2025',[ValidatePattern('^$|^2025\d{4}$')][string]$OnlyDate='')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$OutputEncoding=[Console]::OutputEncoding
$utf8=New-Object System.Text.UTF8Encoding($false)
$seven='C:\Wind\Wind.NET.Client\WWT\bin\7za.exe'
$source='F:\BaiduNetdiskDownload\2025'
New-Item -ItemType Directory -Force -Path "$Root\manifests","$Root\logs","$Root\retained","$Root\stage" | Out-Null
$job=if($OnlyDate){"repair_$OnlyDate"}else{'worker'}
$lock=[IO.File]::Open("$Root\$job.lock",[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
function SaveJson($path,$obj){[IO.File]::WriteAllText($path,($obj|ConvertTo-Json -Depth 8),$utf8)}
$mapping=@{}
$symbols=Get-Content -LiteralPath "$Root\universe.json" -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($symbol in $symbols){$mapping[$symbol.Substring(0,6)]=$symbol}
Write-Output "TARGETS $($mapping.Count)"
$archives=@(Get-ChildItem -LiteralPath $source -Directory | ForEach-Object {Get-ChildItem -LiteralPath $_.FullName -File -Filter '*.7z'} | Where-Object {$_.BaseName -match '^2025\d{4}$'} | Sort-Object @{Expression={if($_.BaseName -ge '20250701'){0}else{1}}},Name)
if($OnlyDate){$archives=@($archives|Where-Object BaseName -eq $OnlyDate)}
$statusPath=if($OnlyDate){"$Root\status_$OnlyDate.json"}else{"$Root\status.json"}
SaveJson "$Root\snapshot_$job.json" @($archives | Select-Object FullName,Name,Length,LastWriteTimeUtc)
$ok=0;$failed=0;$skipped=0
try {
 foreach($archive in $archives){
  $day=$archive.BaseName;$manifest="$Root\manifests\$day.json"
  if(Test-Path -LiteralPath $manifest){$m=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json;if($m.status -eq 'extracted' -and $m.archive_bytes -eq $archive.Length -and $m.archive_mtime -eq $archive.LastWriteTimeUtc.ToString('o')){$skipped++;continue}}
  if(((Get-Date).ToUniversalTime()-$archive.LastWriteTimeUtc).TotalSeconds -lt 90){$skipped++;continue}
  try {
   SaveJson $statusPath @{state='listing';day=$day;completed=$ok;failed=$failed;skipped=$skipped;total=$archives.Count;updated=(Get-Date).ToUniversalTime().ToString('o')}
   # Directory listing only: no 7z test command and no separate CRC pass.
   $listing=@(& $seven l -slt -sccUTF-8 -- $archive.FullName 2>&1)
   if($LASTEXITCODE -ne 0){throw "7za listing failed: $LASTEXITCODE"}
   $chosen=New-Object 'System.Collections.Generic.List[object]'
   foreach($line in $listing){
    if([string]$line -match '^Path = (.+)$'){
     $member=$Matches[1]
     if($member -match '^(?:(\d{8})\\)?(\d{6})\.[^\\]+\\([^\\]+)$'){
      $prefix=$Matches[1];$code=$Matches[2];$filename=$Matches[3]
      if($mapping.ContainsKey($code)){
       if(($prefix -and $prefix -ne $day) -or $filename -in @('.','..') -or $filename.Contains(':')){throw "Unsafe or mismatched member: $member"}
       $chosen.Add(@{member=$member;symbol=$mapping[$code];filename=$filename})
      }
     }
    }
   }
   if($chosen.Count -eq 0){throw 'No target ETF members found'}
   $keyCounts=@{}
   foreach($entry in $chosen){$key="$($entry.symbol)\$($entry.filename)";if(!$keyCounts.ContainsKey($key)){$keyCounts[$key]=0};$keyCounts[$key]++}
   foreach($entry in $chosen){
    $key="$($entry.symbol)\$($entry.filename)";$entry.ambiguous_variant=($keyCounts[$key] -gt 1)
    $entry.relative=$key
    if($entry.ambiguous_variant){$vendorFolder=($entry.member -split '\\')[-2];$entry.relative="$($entry.symbol)\source_variants\$vendorFolder\$($entry.filename)"}
   }
   if(@($chosen|ForEach-Object {$_.relative}|Sort-Object -Unique).Count -ne $chosen.Count){throw 'Unresolved output collision'}
   $include="$Root\logs\${day}_include.txt";[IO.File]::WriteAllLines($include,[string[]]@($chosen|ForEach-Object {$_.member}),$utf8)
   $stage="$Root\stage\$day";New-Item -ItemType Directory -Force -Path $stage|Out-Null
   SaveJson $statusPath @{state='extracting';day=$day;files=$chosen.Count;completed=$ok;failed=$failed;skipped=$skipped;total=$archives.Count;updated=(Get-Date).ToUniversalTime().ToString('o')}
   Write-Output "EXTRACT $day $($chosen.Count) target files"
   & $seven x -y -bd -bb0 -scsUTF-8 -sccUTF-8 "-i@$include" "-o$stage" -- $archive.FullName > "$Root\logs\${day}_extract.log" 2>&1
   if($LASTEXITCODE -ne 0){throw "7za extraction failed: $LASTEXITCODE"}
   $now=Get-Item -LiteralPath $archive.FullName
   if($now.Length -ne $archive.Length -or $now.LastWriteTimeUtc -ne $archive.LastWriteTimeUtc){throw 'Source archive changed during extraction'}
   $files=New-Object 'System.Collections.Generic.List[object]'
   foreach($entry in $chosen){
    $src=Join-Path $stage $entry.member;$dst=Join-Path "$Root\retained\$day" $entry.relative;$dir=Split-Path -Parent $dst
    if(!(Test-Path -LiteralPath $src -PathType Leaf)){throw "Missing extracted member: $($entry.member)"}
    if(Test-Path -LiteralPath $dst){throw "Existing destination without completed matching manifest: $dst"}
    New-Item -ItemType Directory -Force -Path $dir|Out-Null
    Move-Item -LiteralPath $src -Destination $dst
    $files.Add(@{path=$dst;source_member=$entry.member;symbol=$entry.symbol;ambiguous_variant=$entry.ambiguous_variant;bytes=(Get-Item -LiteralPath $dst).Length})
   }
   SaveJson $manifest @{status='extracted';archive=$archive.FullName;archive_bytes=$archive.Length;archive_mtime=$archive.LastWriteTimeUtc.ToString('o');archive_deleted=$false;separate_integrity_test=$false;extract_exit_code=0;files=@($files.ToArray());completed_at=(Get-Date).ToUniversalTime().ToString('o')}
   $ok++;Write-Output "DONE $day $($files.Count) files"
  } catch {
   $failed++;$err=$_.Exception.Message
   SaveJson "$Root\logs\${day}_error.json" @{day=$day;error=$err;updated=(Get-Date).ToUniversalTime().ToString('o')}
   Write-Output "FAILED $day $err"
   # A general tool or disk failure should stop rather than repeat across all archives.
   if($ok -eq 0 -or (Get-PSDrive F).Free -lt 10GB){throw}
  }
 }
 SaveJson $statusPath @{state='finished';completed=$ok;failed=$failed;skipped=$skipped;total=$archives.Count;updated=(Get-Date).ToUniversalTime().ToString('o')}
 Write-Output "FINISHED completed=$ok failed=$failed skipped=$skipped"
} finally {$lock.Dispose()}
