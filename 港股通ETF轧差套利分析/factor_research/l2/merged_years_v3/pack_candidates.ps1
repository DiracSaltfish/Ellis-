$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$root='F:\港股通ETF轧差套利分析\win1_2025'
$out="$root\model_transfer\candidates_2025.zip"
Set-Location -LiteralPath "$root\retained"
& 'C:\Wind\Wind.NET.Client\WWT\bin\7za.exe' a -tzip -mx=1 -mmt=4 -bd -bb0 -scsUTF-8 -sccUTF-8 $out "@$root\model_transfer\transfer_list.txt"
if($LASTEXITCODE -ne 0){throw "pack failed $LASTEXITCODE"}
Get-Item -LiteralPath $out|Select-Object FullName,Length|ConvertTo-Json
