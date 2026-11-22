# TonieTausch – Installation / Update für Windows 10/11
# Aufruf aus dem Repo-Ordner:
#   powershell -ExecutionPolicy Bypass -File install\setup-windows.ps1
$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$Docs = [Environment]::GetFolderPath('MyDocuments')
$Desk = [Environment]::GetFolderPath('Desktop')
$Root = Join-Path $Docs 'TonieTausch-Daten'

function Say($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Fail($t) { Write-Host "`n[FEHLER] $t" -ForegroundColor Red; Read-Host 'Enter zum Beenden'; exit 1 }

if (-not (Test-Path (Join-Path $Repo 'app\server.py'))) { Fail 'app\server.py nicht gefunden. Bitte aus dem Repo-Ordner starten.' }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
Start-Transcript -Path (Join-Path $Root 'installation.log') -Force | Out-Null

try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/api/beenden' -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null; Start-Sleep 1 } catch {}

# Python 3.12
$py = $null
$cand = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (Test-Path $cand) { $py = $cand }
if (-not $py) { try { $py = (& py -3.12 -c "import sys;print(sys.executable)" 2>$null) } catch {} }
if (-not $py) {
  Say 'Python 3.12 installieren'
  winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements
  if (Test-Path $cand) { $py = $cand } else { Fail 'Python konnte nicht installiert werden. Bitte von python.org installieren und erneut starten.' }
}

Say 'Python-Umgebung und TonieToolbox'
$venvPy = Join-Path $Root 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  & $py -m venv (Join-Path $Root 'venv')
  if ($LASTEXITCODE -ne 0) { Fail 'Python-Umgebung konnte nicht erstellt werden.' }
}
& $venvPy -m pip install --quiet --upgrade pip tonietoolbox pillow
if ($LASTEXITCODE -ne 0) { Fail 'Paket-Installation fehlgeschlagen.' }

Say 'Programm kopieren'
$appDir = Join-Path $Root 'app'
if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
Copy-Item -Recurse (Join-Path $Repo 'app') $appDir

Say 'Icon und Verknuepfung auf dem Desktop'
$ico = Join-Path $appDir 'TonieTausch.ico'
& $venvPy (Join-Path $appDir 'icon.py') $ico
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $Desk 'TonieTausch.lnk'))
$lnk.TargetPath = Join-Path $Root 'venv\Scripts\pythonw.exe'
$lnk.Arguments = '"' + (Join-Path $appDir 'start.py') + '"'
$lnk.WorkingDirectory = $Root
if (Test-Path $ico) { $lnk.IconLocation = $ico }
$lnk.Save()

Say 'Fertig! Doppelklick auf TonieTausch auf dem Desktop.'
Write-Host "Daten: $Root"
Stop-Transcript | Out-Null
Read-Host 'Enter zum Beenden'
