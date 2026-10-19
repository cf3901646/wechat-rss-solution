# ==============================================================================
# Windows 任务计划程序一键注册脚本
# 作用：设置 Windows 每天定时或每隔几小时自动在后台静默运行同步脚本
# ==============================================================================

param (
    [string]$Time = "02:00",         # 每天执行时间（默认凌晨 2 点）
    [int]$IntervalHours = 0,          # 如果大于 0 则按间隔小时循环执行（如 4 小时）
    [string]$TaskName = "WechatRssSyncTask"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Get-Item $ScriptDir).Parent.FullName
$PythonScript = Join-Path $ProjectRoot "scripts\rss_sync.py"
$ConfigFile = Join-Path $ProjectRoot "config\config.yaml"

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  正在注册 Windows 定时任务: $TaskName" -ForegroundColor Green
Write-Host "====================================================" -ForegroundColor Cyan

if (-not (Test-Path $ConfigFile)) {
    Write-Host "[错误] 配置文件不存在: $ConfigFile" -ForegroundColor Red
    Write-Host "       请先从 config\config.example.yaml 复制一份并填入你的 RSS 源地址。" -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# 检测 Python 解释器路径
#
# 必须优先用项目自带的 .venv：pyyaml / requests / beautifulsoup4 都只装在虚拟环境里，
# 用系统 PATH 上的 python.exe 运行会直接 ModuleNotFoundError。
# 又因为定时任务走 pythonw.exe（无控制台窗口），这个错误不会有任何可见提示，
# 只能靠 logs\sync.log 才能发现，所以这里必须选对解释器。
#
# 优先级：.venv\pythonw.exe > .venv\python.exe > 系统 pythonw.exe > 系统 python.exe
# ---------------------------------------------------------------------------
$VenvPythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

$ExecProgram = $null
$UsingVenv = $false

if (Test-Path $VenvPythonw) {
    $ExecProgram = $VenvPythonw
    $UsingVenv = $true
} elseif (Test-Path $VenvPython) {
    $ExecProgram = $VenvPython
    $UsingVenv = $true
} else {
    $PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $PythonExe) {
        Write-Host "[错误] 既没找到 $VenvPython，系统 PATH 上也没有 python.exe。" -ForegroundColor Red
        Write-Host "       请先在项目根目录创建虚拟环境并安装依赖：" -ForegroundColor Yellow
        Write-Host "       python -m venv .venv" -ForegroundColor Yellow
        Write-Host "       .venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Yellow
        exit 1
    }
    # 在解释器所在目录里找同级的 pythonw.exe，而不是对整条路径做字符串替换
    $CandidatePythonw = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
    $ExecProgram = if (Test-Path $CandidatePythonw) { $CandidatePythonw } else { $PythonExe }

    Write-Host "[警告] 未找到项目虚拟环境 .venv，将使用系统 Python：$ExecProgram" -ForegroundColor Yellow
    Write-Host "       请确认该解释器已安装 requirements.txt 中的依赖，否则定时任务会静默失败。" -ForegroundColor Yellow
}

if ($UsingVenv) {
    Write-Host "已选用项目虚拟环境解释器（依赖完整）" -ForegroundColor Green
}

Write-Host "Python 执行路径: $ExecProgram" -ForegroundColor Gray
Write-Host "脚本执行路径: $PythonScript" -ForegroundColor Gray

# 依赖自检：直接试导入三个必需的第三方库，避免注册一个注定跑不起来的任务
$DepCheckExe = if ($UsingVenv -and (Test-Path $VenvPython)) { $VenvPython } else { $ExecProgram }
& $DepCheckExe -c "import yaml, requests, bs4" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 解释器 $DepCheckExe 缺少必需依赖 (pyyaml / requests / beautifulsoup4)。" -ForegroundColor Red
    Write-Host "       请先执行: `"$DepCheckExe`" -m pip install -r `"$(Join-Path $ProjectRoot 'requirements.txt')`"" -ForegroundColor Yellow
    exit 1
}
Write-Host "依赖自检通过 (pyyaml / requests / beautifulsoup4)" -ForegroundColor Green

# 定义操作 (Action)
$Action = New-ScheduledTaskAction -Execute $ExecProgram -Argument "`"$PythonScript`" --config `"$ConfigFile`"" -WorkingDirectory $ProjectRoot

# 定义触发器 (Trigger)
if ($IntervalHours -gt 0) {
    Write-Host "触发规则: 每天每隔 $IntervalHours 小时自动执行一次" -ForegroundColor Yellow
    $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours $IntervalHours)
} else {
    Write-Host "触发规则: 每天 $Time 自动执行一次" -ForegroundColor Yellow
    $Trigger = New-ScheduledTaskTrigger -Daily -At $Time
}

# 任务设置（允许随时唤醒、后台执行、断电后补充运行）
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# 注册任务
try {
    # 如果已存在先注销旧任务
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "微信公众号 RSS 自动拉取与 Markdown 知识库同步"
    
    Write-Host ""
    Write-Host "🎉 任务计划注册成功！" -ForegroundColor Green
    Write-Host "任务名称: $TaskName" -ForegroundColor Cyan
    Write-Host "你可以打开 Windows【任务计划程序】随时查看执行历史或手动右键运行测试。" -ForegroundColor White
    Write-Host ""
    Write-Host "静默运行时没有控制台输出，排查请看日志文件：" -ForegroundColor White
    Write-Host "  $(Join-Path $ProjectRoot 'logs\sync.log')" -ForegroundColor Cyan
} catch {
    Write-Host "[错误] 注册任务计划失败: $_" -ForegroundColor Red
    Write-Host "提示：请尝试以【管理员身份运行 PowerShell】重新执行此脚本。" -ForegroundColor Yellow
}
