# 功能：一键生成密钥、配对、注册用户登录自启并验证真实服务器心跳。
# 参数：Bundle默认仓库根Pairing.json；Python自动选择已有解释器，不重复安装依赖。
param([string]$Bundle='', [string]$Python='', [switch]$Server)
$ErrorActionPreference='Stop'
$OutputEncoding=New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding=$OutputEncoding
$root=Split-Path -Parent $PSScriptRoot
if (!$Python) {
    foreach($candidate in @($env:SYNLOVE_PYTHON,(Join-Path $root '.venv\Scripts\python.exe'))){
        if($candidate -and (Test-Path -LiteralPath $candidate)){$Python=$candidate;break}
    }
    if(!$Python){$Python=(Get-Command python.exe -ErrorAction Stop).Source}
}
$pythonw=Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
if(!(Test-Path -LiteralPath $pythonw)){throw '未找到pythonw，请指定完整Python环境'}
& $Python -X utf8 (Join-Path $PSScriptRoot 'CheckEnvironment.py')
if($LASTEXITCODE -ne 0){throw '已有Python缺少cryptography或websocket-client，请按requirements准备环境'}
$role=if($Server){'server'}else{'client'}
$name=if($Server){'SynLoveTerminalServer'}else{'SynLoveTerminalClient'}
$config=Join-Path $root $(if($Server){'config\ServerConfig.json'}else{'config\TerminalConfig.json'})
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach($folder in @((Join-Path $root 'config'),(Join-Path $root 'privateKeys'))){
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    & icacls.exe $folder /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if($LASTEXITCODE -ne 0){throw '私有目录权限设置失败'}
    foreach($file in Get-ChildItem -LiteralPath $folder -File -Recurse){
        & icacls.exe $file.FullName /inheritance:r /grant:r "*${sid}:(F)" '*S-1-5-18:(F)' | Out-Null
        if($LASTEXITCODE -ne 0){throw '私有文件权限设置失败'}
    }
}
if(!$Server -and !(Test-Path -LiteralPath $config)){
    if(!$Bundle){$Bundle=Join-Path $root 'Pairing.json'}
    & $Python -X utf8 (Join-Path $PSScriptRoot 'ProvisionTerminal.py') --bundle $Bundle
    if($LASTEXITCODE -ne 0){throw '登记失败；未启动终端服务'}
}
if(!(Test-Path -LiteralPath $config)){throw '私有配置不存在'}
& icacls.exe $config /inheritance:r /grant:r "*${sid}:(F)" '*S-1-5-18:(F)' | Out-Null
if($LASTEXITCODE -ne 0){throw '私有配置权限设置失败'}
$action=New-ScheduledTaskAction -Execute $pythonw -Argument ('-X utf8 "'+(Join-Path $PSScriptRoot 'RunTerminal.py')+'" --role '+$role+' --config "'+$config+'"') -WorkingDirectory $root
$principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $sid) -Force | Out-Null
Start-ScheduledTask -TaskName $name
if(!$Server){
    $cfg=Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json
    $statusPath=Join-Path $cfg.runtimeDir 'status.json'
    $ready=$false
    for($i=0;$i -lt 35;$i++){
        if(Test-Path -LiteralPath $statusPath){
            $status=Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if($status.connected -and ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$status.checkedAt) -lt 15){$ready=$true;break}
        }
        Start-Sleep -Seconds 1
    }
    if(!$ready){throw '任务已启动，但尚未确认服务器心跳；请检查runtime状态，勿重复配对'}
}else{
    $cfg=Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json
    $ready=$false
    for($i=0;$i -lt 35;$i++){
        try{
            $health=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$cfg.adminPort+'/v1/health') -Headers @{Authorization=('Bearer '+$cfg.adminToken)} -TimeoutSec 2
            if($health.protocol -eq 'synlove-terminal-v1' -and $health.relayConnected){$ready=$true;break}
        }catch{}
        Start-Sleep -Seconds 1
    }
    if(!$ready){throw '服务器管理器任务已启动，但尚未确认VPS连接；请检查runtime诊断'}
}
Write-Output '终端组件已安装；身份与登录自启已配置。'
