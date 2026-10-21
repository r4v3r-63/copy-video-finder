$ErrorActionPreference = 'Stop'
$config = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CONFIG_BASE64__')) | ConvertFrom-Json
$exeName = 'CopyVideoFinder.exe'

function Write-Log([string]$message) {
    Add-Content -LiteralPath $config.log -Value ((Get-Date -Format o) + ' ' + $message) -Encoding UTF8
}

function Write-State([string]$state, [string]$message = '') {
    $value = @{state=$state; message=$message; version=$config.version; installer_pid=$PID}
    $temporary = $config.result + '.tmp'
    $value | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $config.result -Force
}

try {
    Write-Log 'Установщик запущен.'
    $source = Join-Path $config.new $exeName
    $destination = Join-Path $config.app $exeName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Не найден новый EXE.' }
    if (Test-Path -LiteralPath (Join-Path $config.new 'Данные')) { throw 'В обновлении обнаружена папка Данные.' }
    Write-State 'waiting'
    Set-Content -LiteralPath $config.ready -Value "$PID" -Encoding UTF8
    # Ожидание реального процесса, без разбора локализованного tasklist.
    $parent = Get-Process -Id $config.pid -ErrorAction SilentlyContinue
    if ($parent -and -not $parent.WaitForExit(120000)) { throw 'Программа не завершилась за две минуты.' }
    Write-State 'copying'
    Write-Log 'Программа закрылась. Копирование файлов.'
    # /IS /IT заменяют также файлы с совпадающими размером и временем. Данные не удаляются.
    $output = & "$env:SystemRoot\System32\robocopy.exe" $config.new $config.app /E /IS /IT /R:10 /W:1 /NP /NFL /NDL /NJH /NJS 2>&1
    $code = $LASTEXITCODE
    if ($output) { $output | Out-File -LiteralPath $config.log -Encoding UTF8 -Append }
    if ($code -ge 8) { throw "Не удалось скопировать файлы, код robocopy: $code." }
    $stream = $null
    $algorithm = $null
    try {
        $stream = [IO.File]::OpenRead($destination)
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $actual = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($stream) { $stream.Dispose() }
        if ($algorithm) { $algorithm.Dispose() }
    }
    if ($actual -ne $config.sha256) { throw 'Новый EXE не установлен: контрольная сумма не совпала.' }
    Write-State 'installed'
    Write-Log 'Готово: новый EXE установлен, SHA-256 проверена.'
    $options = @{FilePath=$destination; WorkingDirectory=$config.app; PassThru=$true}
    if ($config.restart_args.Count -gt 0) { $options.ArgumentList = $config.restart_args }
    $started = Start-Process @options
    Start-Sleep -Milliseconds 500
    $started.Refresh()
    if ($started.HasExited) {
        $started.WaitForExit()
        if ($started.ExitCode -ne 0) { throw 'Новая программа завершилась с ошибкой запуска.' }
    }
} catch {
    $message = $_.Exception.Message
    Write-Log ("Ошибка: " + $message)
    Write-State 'failed' $message
    if ($config.show_errors) {
        try {
            Add-Type -AssemblyName PresentationFramework
            [System.Windows.MessageBox]::Show(
                "Обновление не установлено. $message`nПодробности: $($config.log)", 'Обновление Copy.ru') | Out-Null
        } catch { }
    }
    exit 1
}
