param([switch]$Headless, [switch]$Streaming)

$ErrorActionPreference = 'Stop'
$state = if ($env:AVATAR_STATE_DIR) { $env:AVATAR_STATE_DIR } else { Join-Path $PSScriptRoot '.local' }
$root = Join-Path $state 'kit-data'
if (-not $env:A2F_INSTALL_DIR) { throw 'Set A2F_INSTALL_DIR to your separately installed Audio2Face 2023.2.' }
$app = $env:A2F_INSTALL_DIR
$exe = Join-Path $app 'kit\kit.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw 'Audio2Face has not finished extracting.'
}
if ($Headless -and $Streaming) {
    throw 'Streaming requires the full Audio2Face experience.'
}
if (Get-Process -Name kit -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
    throw 'This Audio2Face installation is already running.'
}
$profile = Join-Path $root 'user-profile'
$portable = Join-Path $root 'user-data'
$folders = @(
    'temp', 'config', 'user-data\data', 'user-data\cache', 'user-data\logs',
    'user-data\documents', 'user-data\program-data', 'user-data\cuda-cache',
    'user-data\pip-cache', 'user-data\python-cache', 'user-data\warp-cache',
    'user-profile\AppData\Local', 'user-profile\AppData\Roaming'
)
foreach ($folder in $folders) {
    New-Item -ItemType Directory -Path (Join-Path $root $folder) -Force | Out-Null
}

# Scope redirects to this launcher and its children, not to Windows globally.
$env:TEMP = Join-Path $root 'temp'
$env:TMP = $env:TEMP
$env:USERPROFILE = $profile
$env:HOME = $profile
$env:LOCALAPPDATA = Join-Path $profile 'AppData\Local'
$env:APPDATA = Join-Path $profile 'AppData\Roaming'
$env:OMNI_CONFIG_PATH = Join-Path $root 'config'
$env:CUDA_CACHE_PATH = Join-Path $portable 'cuda-cache'
$env:PIP_CACHE_DIR = Join-Path $portable 'pip-cache'
$env:PYTHONPYCACHEPREFIX = Join-Path $portable 'python-cache'
$env:WARP_CACHE_PATH = Join-Path $portable 'warp-cache'
$env:AVATAR_STATE_DIR = $state

$experience = if ($Headless) { 'audio2face_headless.kit' } else { 'audio2face.kit' }
$arguments = @(
    (Join-Path $app "apps\$experience"),
    '--portable', '--portable-root', $portable,
    "--/log/file=$portable/logs/audio2face.log",
    "--/app/userConfigPath=$portable/data/user.config.json",
    "--/app/tokens/app_documents=$portable/documents",
    "--/app/tokens/shared_documents=$portable/documents",
    "--/app/tokens/omni_documents=$portable/documents",
    "--/app/tokens/app_program_data=$portable/program-data",
    "--/app/tokens/shared_program_data=$portable/program-data",
    '--/renderer/multiGpu/enabled=false',
    '--/app/renderer/resolution/width=1280',
    '--/app/renderer/resolution/height=720',
    '--/exts/omni.services.transport.server.http/host=127.0.0.1',
    '--/exts/omni.services.transport.server.http/port=8011',
    '--/exts/omni.services.transport.server.http/allow_port_range=false'
)
if ($Streaming) {
    $arguments += @('--exec', (Join-Path $PSScriptRoot 'audio2face-stream.py'))
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$quotedArguments = @($arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
$process = Start-Process -FilePath $exe -ArgumentList $quotedArguments -WorkingDirectory $app -WindowStyle Hidden -RedirectStandardOutput "$portable\logs\stdout-$stamp.log" -RedirectStandardError "$portable\logs\stderr-$stamp.log" -PassThru
$process | Select-Object Id, ProcessName, Path
