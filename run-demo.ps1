# Демо без установки в систему: виртуальное окружение в .venv, отчёты в var\demo.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONUTF8 = "1"
if (-not (Test-Path ".venv")) { py -3 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --quiet --require-hashes -r requirements\pip.lock
& .\.venv\Scripts\python.exe -m pip install --quiet --require-hashes -r requirements\runtime.lock -r requirements\build.lock
& .\.venv\Scripts\python.exe -m pip install --quiet --no-deps --no-build-isolation -e .
& .\.venv\Scripts\mandat.exe demo
Write-Host "Подготовка не меняет статусы. Живые шаги отмены и захвата — в docs\DEMO.md, во время показа."
Write-Host "Откройте var\demo\01_ok.html, var\demo\10_old_director_poa.html и var\demo\05_director_changed.html."
