param(
  [Parameter(Mandatory = $true)][string]$SfxPath,
  [Parameter(Mandatory = $true)][string]$AppPath,
  [Parameter(Mandatory = $true)][string]$IconPath
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

foreach ($path in @($SfxPath, $AppPath, $IconPath)) {
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Required file not found: $path"
  }
}

function Get-IconHash([string]$Path) {
  $icon = [System.Drawing.Icon]::ExtractAssociatedIcon((Resolve-Path -LiteralPath $Path).Path)
  if ($null -eq $icon) {
    throw "No associated icon found: $Path"
  }
  $stream = [System.IO.MemoryStream]::new()
  try {
    $icon.Save($stream)
    $stream.Position = 0
    return (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash
  } finally {
    $stream.Dispose()
    $icon.Dispose()
  }
}

$sfxHash = Get-IconHash $SfxPath
$appHash = Get-IconHash $AppPath
if ($sfxHash -ne $appHash) {
  throw "Portable SFX icon does not match app icon: SFX=$sfxHash App=$appHash"
}

Write-Output "SFX icon SHA256: $sfxHash"
Write-Output "App icon SHA256: $appHash"
