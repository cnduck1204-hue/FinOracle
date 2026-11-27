param([switch]$Refresh)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runArgs = @('-m','crawler','run','--config','config/szse.json','--data','data/szse')
if ($Refresh) { $runArgs += '--refresh' }
python @runArgs
if ($LASTEXITCODE -ne 0) { throw '采集存在未完成或隔离任务，请查看 report.json 和 state.db' }
foreach ($step in @('export','parse','verify')) {
    python -m crawler $step --data data/szse
    if ($LASTEXITCODE -ne 0) { throw "步骤失败：$step" }
}
