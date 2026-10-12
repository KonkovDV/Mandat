# Демо без установки в систему: виртуальное окружение в .venv, отчёты в var\demo.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONUTF8 = "1"
if (-not (Test-Path ".venv")) { py -3 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --quiet -e .
& .\.venv\Scripts\mandat.exe demo
& .\.venv\Scripts\mandat.exe monitor examples\events\takeover.json
Write-Host "Откройте var\demo\01_ok.html, var\demo\10_old_director_poa.html и var\demo\05_director_changed.html. Сценарий: docs\DEMO.md"
