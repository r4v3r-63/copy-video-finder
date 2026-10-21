# Переносимый официальный WebView2: без установки и изменений реестра.
param([string]$Destination = "app/webview2")
$ErrorActionPreference = "Stop"
$catalog = @()
try { $catalog = Invoke-RestMethod "https://edgeupdates.microsoft.com/api/products?view=enterprise" }
catch { Write-Host "Каталог WebView2 недоступен; проверяем официальную страницу загрузки." }
$urls = @($catalog | Where-Object Product -eq "WebView2" | ForEach-Object {
    $_.Releases | Where-Object Architecture -eq "x64" | ForEach-Object {
        $_.Artifacts | ForEach-Object { $_.Location }
    }
} | Where-Object { $_ -match 'Microsoft\.WebView2\.FixedVersionRuntime\.[\d.]+\.x64\.cab' })
if (-not $urls) {
    $page = (Invoke-WebRequest "https://developer.microsoft.com/en-us/microsoft-edge/webview2/").Content
    $page = [System.Net.WebUtility]::HtmlDecode($page.Replace('\/', '/').Replace('\u002F', '/'))
    $pattern = 'https://msedge\.sf\.dl\.delivery\.mp\.microsoft\.com/filestreamingservice/files/[^"<>\s]+/Microsoft\.WebView2\.FixedVersionRuntime\.[\d.]+\.x64\.cab'
    $urls = @([regex]::Matches($page, $pattern) | ForEach-Object { $_.Value })
}
$url = $urls | Sort-Object -Unique | Sort-Object {
    [version]([regex]::Match($_, 'FixedVersionRuntime\.([\d.]+)\.x64').Groups[1].Value)
} -Descending | Select-Object -First 1
if (-not $url -or ([uri]$url).Scheme -ne "https" -or ([uri]$url).Host -ne "msedge.sf.dl.delivery.mp.microsoft.com") {
    throw "Не найдена официальная переносимая сборка WebView2 x64."
}
$temporary = Join-Path $env:RUNNER_TEMP ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Force $temporary | Out-Null
try {
    $cab = Join-Path $temporary "runtime.cab"
    Invoke-WebRequest $url -OutFile $cab
    $unpacked = Join-Path $temporary "unpacked"
    New-Item -ItemType Directory $unpacked | Out-Null
    & expand.exe '-F:*' $cab $unpacked | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Не удалось распаковать WebView2." }
    $exe = Get-ChildItem $unpacked -Recurse -Filter msedgewebview2.exe | Select-Object -First 1
    if (-not $exe) { throw "В архиве нет WebView2." }
    $signature = Get-AuthenticodeSignature $exe.FullName
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
        throw "Не подтверждена подпись Microsoft у WebView2."
    }
    New-Item -ItemType Directory -Force $Destination | Out-Null
    Copy-Item (Join-Path $exe.Directory.FullName '*') $Destination -Recurse -Force
    Write-Host "Переносимый WebView2: $($exe.VersionInfo.ProductVersion), подпись Microsoft проверена."
} finally {
    Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
