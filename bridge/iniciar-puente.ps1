# Arranca el puente de Control TV. La primera vez crea su entorno de Python e instala las librerías.
#
#   Clic derecho -> "Ejecutar con PowerShell", o desde PowerShell en esta carpeta:
#   .\iniciar-puente.ps1                      normal
#   .\iniciar-puente.ps1 --tv 192.168.1.50    la primera vez, o si la TV cambió de IP
#   .\iniciar-puente.ps1 --new-pin            genera un PIN nuevo
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Primera vez: preparando el entorno de Python...'
    python -m venv .venv
    & $py -m pip install --disable-pip-version-check -q -r requirements.txt
}
& $py -u server.py @args
