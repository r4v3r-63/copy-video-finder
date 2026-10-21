# Официальный переносимый Deno для JavaScript-проверок YouTube в yt-dlp.
param([string]$Destination = "app/deno")
$ErrorActionPreference = "Stop"
$release = Invoke-RestMethod "https://api.github.com/repos/denoland/deno/releases/latest"
$asset = $release.assets | Where-Object { $_.name -eq "deno-x86_64-pc-windows-msvc.zip" } | Select-Object -First 1
if (-not $asset -or $asset.digest -notmatch '^sha256:([a-f0-9]{64})$') {
    throw "Официальный выпуск Deno не содержит проверяемой контрольной суммы."
}
$expected = $asset.digest.Split(":")[1]
if ($asset.browser_download_url -notlike 'https://github.com/denoland/deno/releases/download/*') {
    throw "Неожиданный адрес официального архива Deno."
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory $temporary | Out-Null
try {
    $zip = Join-Path $temporary "deno.zip"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $expected) {
        throw "Контрольная сумма Deno не совпала."
    }
    Expand-Archive $zip -DestinationPath "$temporary/unpacked"
    New-Item -ItemType Directory -Force $Destination | Out-Null
    Copy-Item "$temporary/unpacked/deno.exe" $Destination
    Invoke-WebRequest "https://raw.githubusercontent.com/denoland/deno/$($release.tag_name)/LICENSE.md" -OutFile "$Destination/LICENSE.md"
    & "$Destination/deno.exe" --version
    if ($LASTEXITCODE -ne 0) { throw "Deno не запускается." }
} finally {
    Remove-Item $temporary -Recurse -Force
}
