$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$root='F:\港股通ETF轧差套利分析\win1_2025'
$symbols=Get-Content -LiteralPath "$root\universe.json" -Raw -Encoding UTF8|ConvertFrom-Json
$map=@{};foreach($s in $symbols){$map[$s.Substring(0,6)]=$s}
$lines=@(& 'C:\Wind\Wind.NET.Client\WWT\bin\7za.exe' l -slt -sccUTF-8 'F:\BaiduNetdiskDownload\2025\202507\20250731.7z')
$items=@(foreach($line in $lines){if($line -match '^Path = ((?:(\d{8})\\)?(\d{6})\.[^\\]+\\([^\\]+))$'){if($map.ContainsKey($Matches[3])){[pscustomobject]@{member=$Matches[1];key=$map[$Matches[3]]+'\'+$Matches[4]}}}})
$items|Group-Object key|Where-Object Count -gt 1|ForEach-Object {$_.Group}|ConvertTo-Json -Depth 4
