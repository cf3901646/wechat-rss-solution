# ==============================================================================
# Windows 任务计划程序卸载脚本
# ==============================================================================

param (
    [string]$TaskName = "WechatRssSyncTask"
)

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已成功移除定时任务: $TaskName" -ForegroundColor Green
} catch {
    Write-Host "移除失败或任务不存在: $_" -ForegroundColor Yellow
}
