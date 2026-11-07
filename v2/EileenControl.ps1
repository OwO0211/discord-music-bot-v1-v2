param(
    [ValidateSet('menu', 'start', 'stop', 'restart', 'status')]
    [string]$Action = 'menu'
)

$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$PidFile = Join-Path $ProjectDir 'eileen.pid'
$LogFile = Join-Path $ProjectDir 'logs\bot.log'
$TaskName = 'Eileen'

function Get-EileenProcessIds {
    $ids = @()
    if (Test-Path -LiteralPath $PidFile) {
        $raw = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue).Trim()
        if ($raw -match '^\d+$') {
            $process = Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue
            if ($process -and $process.ProcessName -like 'python*') {
                $ids += [int]$raw
            }
        }
    }

    if ($ids.Count -eq 0) {
        try {
            $processes = Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $_.Name -like 'python*' -and
                    $_.CommandLine -like '*discordbot-v2*bot.py*'
                }
            foreach ($process in $processes) {
                $ids += [int]$process.ProcessId
            }
        }
        catch {
        }
    }

    return @($ids | Sort-Object -Unique)
}

function Test-EileenRunning {
    return ((Get-EileenProcessIds).Count -gt 0)
}

function Start-Eileen {
    if (Test-EileenRunning) {
        Write-Host 'Eileen 已經在運行中。' -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir '.env'))) {
        Write-Host '找不到 .env，請先設定 DISCORD_BOT_TOKEN。' -ForegroundColor Red
        return
    }
    Write-Host '正在啟動 Eileen...' -ForegroundColor Cyan
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Start-ScheduledTask -TaskName $TaskName
        }
        else {
            Start-Process -FilePath $Python -ArgumentList 'bot.py' `
                -WorkingDirectory $ProjectDir -WindowStyle Hidden
        }
    }
    catch {
        Write-Host "啟動失敗：$($_.Exception.Message)" -ForegroundColor Red
        return
    }
    Start-Sleep -Seconds 5
    if (-not (Test-EileenRunning)) {
        Write-Host '工作排程未啟動，改用直接啟動...' -ForegroundColor Yellow
        Start-Process -FilePath $Python -ArgumentList 'bot.py' `
            -WorkingDirectory $ProjectDir -WindowStyle Hidden
        Start-Sleep -Seconds 5
    }
    if (Test-EileenRunning) {
        Write-Host 'Eileen 已啟動。' -ForegroundColor Green
    }
    else {
        Write-Host '啟動中，請稍等幾秒；若仍離線請看 logs\bot.log。' -ForegroundColor Yellow
    }
}

function Stop-Eileen {
    $ids = Get-EileenProcessIds
    if ($ids.Count -eq 0) {
        Write-Host 'Eileen 目前沒有在運行。' -ForegroundColor Yellow
        return
    }
    foreach ($processId in $ids) {
        Write-Host "正在停止 Eileen (PID $processId)..."
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    if (Test-EileenRunning) {
        Write-Host '停止失敗，請稍後再試。' -ForegroundColor Red
    }
    else {
        Write-Host 'Eileen 已停止。' -ForegroundColor Green
    }
}

function Wait-Menu {
    Write-Host ''
    Read-Host '按 Enter 返回選單' | Out-Null
}

function Show-Menu {
    while ($true) {
        Clear-Host
        Write-Host '==========================================' -ForegroundColor DarkCyan
        Write-Host '             Eileen 控制台' -ForegroundColor Cyan
        Write-Host '==========================================' -ForegroundColor DarkCyan
        if (Test-EileenRunning) {
            Write-Host ' 目前狀態：' -NoNewline
            Write-Host '正在運行' -ForegroundColor Green
        }
        else {
            Write-Host ' 目前狀態：' -NoNewline
            Write-Host '未運行' -ForegroundColor Red
        }
        Write-Host ''
        Write-Host '  1. 啟動 Eileen'
        Write-Host '  2. 停止 Eileen'
        Write-Host '  3. 重新啟動'
        Write-Host '  4. 查看狀態'
        Write-Host '  5. 打開日誌'
        Write-Host '  0. 離開'
        Write-Host ''
        $choice = Read-Host '請輸入選項後按 Enter'

        switch ($choice) {
            '1' { Start-Eileen; Wait-Menu }
            '2' { Stop-Eileen; Wait-Menu }
            '3' { Stop-Eileen; Start-Sleep -Seconds 1; Start-Eileen; Wait-Menu }
            '4' {
                if (Test-EileenRunning) {
                    Write-Host 'Eileen 正在運行。' -ForegroundColor Green
                }
                else {
                    Write-Host 'Eileen 目前沒有在運行。' -ForegroundColor Red
                }
                Write-Host "日誌：$LogFile"
                Wait-Menu
            }
            '5' { Start-Process notepad.exe -ArgumentList $LogFile; Wait-Menu }
            '0' { return }
            default { }
        }
    }
}

switch ($Action) {
    'start' { Start-Eileen }
    'stop' { Stop-Eileen }
    'restart' { Stop-Eileen; Start-Sleep -Seconds 1; Start-Eileen }
    'status' {
        if (Test-EileenRunning) { Write-Output 'RUNNING' }
        else { Write-Output 'STOPPED' }
    }
    default { Show-Menu }
}
