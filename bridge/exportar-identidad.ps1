# Guarda la "identidad" del puente (su CA, el PIN y el emparejamiento con la TV) en un zip en el Escritorio,
# para pasar el puente a otro equipo -por ejemplo un celular Android con Termux- sin reinstalar el certificado en
# el iPhone ni volver a emparejar la TV.
#
# Contiene llaves privadas: cópialo al celular (a Descargas) y bórralo de la PC en cuanto lo copies. El instalador
# de Termux lo borra del celular al importarlo.
$ErrorActionPreference = 'Stop'
$data = Join-Path $PSScriptRoot 'data'
$names = 'config.json', 'ca.crt', 'ca.key', 'client.pem', 'client.key'
$files = foreach ($n in $names) {
    $p = Join-Path $data $n
    if (-not (Test-Path $p)) { throw "Falta ${p}: arranca el puente y empareja la TV antes de exportar" }
    $p
}
$out = Join-Path ([Environment]::GetFolderPath('Desktop')) 'control-tv-identidad.zip'
Compress-Archive -Path $files -DestinationPath $out -Force
Write-Host "Listo: $out"
Write-Host 'Copialo a la carpeta Descargas del celular y despues borralo de la PC.'
