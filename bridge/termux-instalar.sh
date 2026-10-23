#!/data/data/com.termux/files/usr/bin/bash
# Instala el puente de Control TV en un celular Android, con Termux (de F-Droid, no de Play Store).
#
#   curl -fsSL https://raw.githubusercontent.com/MisaelZepeda/control-tv/main/bridge/termux-instalar.sh | bash
#
# 1. Instala Python y lo necesario y descarga el puente en ~/control-tv.
# 2. Si en Descargas está control-tv-identidad.zip (exportado desde la PC con exportar-identidad.ps1), lo
#    importa y lo borra: el celular usa la misma CA (el iPhone no instala nada otra vez), el mismo PIN y el
#    mismo emparejamiento con la TV.
# 3. Deja el puente arrancando solo al encender el celular (con la app Termux:Boot) y lo arranca ahora.
set -e

echo
echo "== Control TV: instalando el puente =="
pkg update -y
pkg install -y python python-cryptography git

cd "$HOME"
if [ -d control-tv/.git ]; then
    git -C control-tv pull --ff-only
else
    git clone https://github.com/MisaelZepeda/control-tv
fi
cd control-tv/bridge

# El entorno usa la librería de cifrado de Termux (ya compilada) y aiohttp en Python puro: nada que compilar.
[ -d .venv ] || python -m venv --system-site-packages .venv
export AIOHTTP_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 FROZENLIST_NO_EXTENSIONS=1 \
       PROPCACHE_NO_EXTENSIONS=1
./.venv/bin/pip install --disable-pip-version-check -q -r requirements.txt

# La identidad de la PC, si se copió a Descargas.
[ -d "$HOME/storage" ] || { echo "Permite a Termux ver tus archivos (para leer Descargas)..."; termux-setup-storage; sleep 6; }
ZIP=""
for d in "$HOME/storage/downloads" /sdcard/Download; do
    [ -f "$d/control-tv-identidad.zip" ] && ZIP="$d/control-tv-identidad.zip" && break
done
if [ -n "$ZIP" ]; then
    mkdir -p data
    ./.venv/bin/python - "$ZIP" <<'PY'
import os, sys, zipfile
allowed = {"config.json", "ca.crt", "ca.key", "client.pem", "client.key"}
with zipfile.ZipFile(sys.argv[1]) as z:
    for name in z.namelist():
        base = os.path.basename(name)
        if base in allowed:
            with z.open(name) as src, open(os.path.join("data", base), "wb") as dst:
                dst.write(src.read())
            os.chmod(os.path.join("data", base), 0o600)
            print("  importado:", base)
PY
    rm -f "$ZIP"
    echo "Identidad de la PC importada (y borrada de Descargas)."
else
    echo "No hay control-tv-identidad.zip en Descargas: el puente empieza de cero (nuevo PIN, nuevo certificado"
    echo "para el iPhone y hay que emparejar la TV desde el control)."
fi

# Arranque automático al encender el celular (necesita la app Termux:Boot, ábrela una vez).
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/control-tv" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd "$HOME/control-tv/bridge"
exec ./.venv/bin/python -u server.py --no-browser >> data/puente.log 2>&1
BOOT
chmod +x "$HOME/.termux/boot/control-tv"

echo
echo "== Listo. Arrancando el puente (se abre el código QR en el navegador del celular) =="
echo "   Para detenerlo: Ctrl+C. Para que no se duerma: deja el celular cargando y quita a Termux"
echo "   la optimización de batería (Ajustes > Apps > Termux > Batería > Sin restricciones)."
termux-wake-lock
exec ./.venv/bin/python -u server.py
