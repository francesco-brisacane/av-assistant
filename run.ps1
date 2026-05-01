#Requires -Version 5.1
<#
.SYNOPSIS
    Avvia AV Assistant in locale: gestisce venv, dipendenze e lancia Streamlit.
.DESCRIPTION
    - Crea il virtual environment .\venv se non esiste
    - Lo attiva nella sessione corrente se non gia' attivo
    - Installa/aggiorna le dipendenze da requirements.txt se necessario
    - Avvia "streamlit run app.py"
#>

param(
    [switch]$ForceInstall  # Forza il reinstall delle dipendenze
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvPath      = Join-Path $PSScriptRoot "venv"
$venvPython    = Join-Path $venvPath "Scripts\python.exe"
$venvActivate  = Join-Path $venvPath "Scripts\Activate.ps1"
$requirements  = Join-Path $PSScriptRoot "requirements.txt"
$installMarker = Join-Path $venvPath ".requirements.hash"

# 1. Crea venv se mancante
if (-not (Test-Path $venvPython)) {
    Write-Host "[run] venv non trovato, creazione in $venvPath ..." -ForegroundColor Yellow
    python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) { throw "Creazione venv fallita. Verifica che 'python' sia nel PATH." }
}

# 2. Attiva venv se non gia' attivo
$alreadyActive = $env:VIRTUAL_ENV -and ((Resolve-Path $env:VIRTUAL_ENV).Path -eq (Resolve-Path $venvPath).Path)
if (-not $alreadyActive) {
    Write-Host "[run] Attivazione venv ..." -ForegroundColor Cyan
    . $venvActivate
} else {
    Write-Host "[run] venv gia' attivo." -ForegroundColor DarkGray
}

# 3. Installa/aggiorna dipendenze se requirements.txt e' cambiato (o se -ForceInstall)
$needInstall = $ForceInstall.IsPresent
if (-not $needInstall) {
    if (-not (Test-Path $installMarker)) {
        $needInstall = $true
    } else {
        $currentHash = (Get-FileHash $requirements -Algorithm SHA256).Hash
        $savedHash   = Get-Content $installMarker -Raw -ErrorAction SilentlyContinue
        if ($currentHash.Trim() -ne $savedHash.Trim()) { $needInstall = $true }
    }
}

if ($needInstall) {
    Write-Host "[run] Installazione/aggiornamento dipendenze ..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Upgrade pip fallito." }
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "pip install fallito." }
    (Get-FileHash $requirements -Algorithm SHA256).Hash | Set-Content $installMarker -Encoding utf8
    Write-Host "[run] Dipendenze aggiornate." -ForegroundColor Green
} else {
    Write-Host "[run] Dipendenze gia' aggiornate (requirements.txt invariato)." -ForegroundColor DarkGray
}

# 4. Avvia Streamlit
Write-Host "[run] Avvio Streamlit ..." -ForegroundColor Green
& $venvPython -m streamlit run app.py
