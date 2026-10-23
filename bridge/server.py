"""Puente de Control TV: recibe los botones de la webapp (GitHub Pages) y se los manda a la TV por el protocolo
oficial de control remoto de Android TV (el mismo que usa la app Google TV), con la librería androidtvremote2.

    .venv\\Scripts\\python.exe server.py                       busca la TV en la red y abre en la PC un código
                                                             QR para configurar el celular sin escribir nada
    .venv\\Scripts\\python.exe server.py --tv 192.168.1.50     solo para fijar la IP de la TV a mano

Escucha en https://<IP de esta PC>:8443 (la API, protegida con PIN y solo para el origen de la webapp) y en
http://<IP de esta PC>:8080 (solo una página de ayuda y el certificado de la CA local para instalar en el celular).
Configuración, PIN y certificados en bridge/data/ (fuera del repositorio). No usa ADB.
"""
import argparse
import asyncio
import hmac
import ipaddress
import json
import logging
import os
import pathlib
import secrets
import socket
import ssl
import time
import webbrowser

from aiohttp import web
from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
from cryptography import x509
from cryptography.hazmat.primitives import serialization

import certs

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CONFIG_PATH = os.path.join(DATA, "config.json")
CLIENT_CERT = os.path.join(DATA, "client.pem")
CLIENT_KEY = os.path.join(DATA, "client.key")
WEBAPP = "https://misaelzepeda.github.io/control-tv/"
DEFAULT_ORIGINS = ["https://misaelzepeda.github.io"]
HTTPS_PORT = 8443
HTTP_PORT = 8080
TEST_PORT = 8099
API_PORT, PAIR_PORT = 6466, 6467

# Solo estas teclas y apps; la webapp no puede mandar otra cosa a la TV.
KEYS = {
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER", "BACK", "HOME", "MENU", "SETTINGS",
    "TV_INPUT", "VOLUME_UP", "VOLUME_DOWN", "VOLUME_MUTE", "POWER", "MEDIA_PLAY_PAUSE", "MEDIA_REWIND",
    "MEDIA_FAST_FORWARD", "MEDIA_NEXT", "MEDIA_PREVIOUS", "INFO", "GUIDE", "CHANNEL_UP", "CHANNEL_DOWN", "DEL",
    "ENTER",
}
ACTIONS = {"tap": "SHORT", "down": "START_LONG", "up": "END_LONG"}
APPS = {
    "netflix": "com.netflix.ninja",
    "youtube": "com.google.android.youtube.tv",
    "prime": "com.amazon.amazonvideo.livingroom",
    "disney": "com.disney.disneyplus",
    "max": "com.wbd.stream",
    "canela": "com.canela.ott.tv",
    "claro": "com.amco.clarovideo_atv",
    "mercado": "com.mercadolibre.android.mplay_tv",
    "clima": "io.github.sumyabazar1.weather",
    "play": "com.android.vending",
}
LOCK_AFTER = 5          # PIN incorrecto esta cantidad de veces...
LOCK_SECONDS = 60       # ...y esa dirección espera este tiempo

log = logging.getLogger("control-tv")


# ---- configuración y red --------------------------------------------------------------------------------------

def load_config(args):
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    if args.tv:
        cfg["tv_host"] = args.tv
    if args.new_pin or "pin" not in cfg:
        cfg["pin"] = f"{secrets.randbelow(10 ** 6):06d}"
    cfg["origins"] = cfg.get("origins") or list(DEFAULT_ORIGINS)
    # Los orígenes de --allow-origin (pruebas) valen solo mientras corre el puente: las claves con "_" no se guardan.
    cfg["_test_origins"] = [o.rstrip("/") for o in args.allow_origin or []]
    save_config(cfg)
    return cfg


def save_config(cfg):
    os.makedirs(DATA, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in cfg.items() if not k.startswith("_")}, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def lan_ip():
    """La IP de esta PC en la red de la casa (la de la ruta por defecto; no se envía ningún paquete)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def local_ips():
    ips = {lan_ip()}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    private = sorted(ip for ip in ips if ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback)
    first = lan_ip()
    return ([first] if first in private else []) + [ip for ip in private if ip != first] + ["127.0.0.1"]


async def port_open(host, port, timeout=0.8):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


async def scan(port):
    """Las direcciones de la red de esta PC (/24) que tienen ese puerto abierto: para encontrar la TV si cambió de IP."""
    net = ipaddress.ip_network(f"{lan_ip()}/24", strict=False)
    hosts = [str(h) for h in net.hosts() if str(h) != lan_ip()]
    found = []
    for i in range(0, len(hosts), 64):
        batch = hosts[i:i + 64]
        results = await asyncio.gather(*(port_open(h, port) for h in batch))
        found += [h for h, ok in zip(batch, results) if ok]
    return found


# ---- la TV ----------------------------------------------------------------------------------------------------

class Tv:
    def __init__(self, cfg):
        self.cfg = cfg
        self.remote = None
        self.available = False
        self.paired = None            # None: todavía no se sabe
        self.pairing_remote = None
        self.pairing_since = 0.0
        self.lock = asyncio.Lock()
        self.down_since = None
        self.found = []               # la última búsqueda de TVs en la red

    def _new_remote(self, host):
        remote = AndroidTVRemote("Control TV", CLIENT_CERT, CLIENT_KEY, host, enable_ime=True)
        remote.add_is_available_updated_callback(self._on_available)
        return remote

    def _on_available(self, ok):
        self.available = ok
        self.down_since = None if ok else (self.down_since or time.monotonic())
        log.info("TV %s", "disponible" if ok else "sin conexión")

    def _lost_auth(self):
        log.warning("La TV ya no reconoce este puente: hay que emparejar otra vez")
        self.paired = False
        self.available = False
        self.remote = None

    @property
    def connected(self):
        return self.remote is not None and self.available

    async def discover(self):
        """Las TVs con el servicio de control remoto en la red de esta PC, con el nombre que cada una da (no hace
        falta emparejar para saberlo: viene en su certificado)."""
        found = []
        for host in await scan(PAIR_PORT):
            name = host
            probe = AndroidTVRemote("Control TV", CLIENT_CERT, CLIENT_KEY, host)
            try:
                await probe.async_generate_cert_if_missing()
                name, _mac = await asyncio.wait_for(probe.async_get_name_and_mac(), 6)
            except Exception:                                   # sin nombre: se muestra la IP
                pass
            finally:
                probe.disconnect()
            found.append({"host": host, "name": name})
        self.found = found
        log.info("TVs en la red: %s", ", ".join(f"{t['name']} ({t['host']})" for t in found) or "ninguna")
        return found

    def use_tv(self, host):
        """Cambia a otra TV: hay que emparejarla."""
        if host != self.cfg.get("tv_host"):
            self.cfg["tv_host"] = host
            save_config(self.cfg)
            if self.remote:
                self.remote.disconnect()
                self.remote = None
            self.paired = False

    async def maintain(self):
        """Conecta al arrancar y vuelve a buscar la TV si lleva más de 2 minutos sin conexión (cambió de IP, etc.).
        Sin TV configurada, la busca en la red; si hay una sola, la toma."""
        if not self.cfg.get("tv_host"):
            try:
                found = await self.discover()
                if len(found) == 1:
                    self.use_tv(found[0]["host"])
            except Exception:
                log.exception("Error al buscar la TV")
        while True:
            try:
                # Un emparejamiento que nadie terminó (se cerró la página) no debe dejar al puente sin TV.
                if self.pairing_remote is not None and time.monotonic() - self.pairing_since > 180:
                    log.info("Emparejamiento sin terminar: se cancela")
                    self.pairing_remote.disconnect()
                    self.pairing_remote = None
                if self.pairing_remote is None and self.paired is not False:
                    stale = self.remote is not None and not self.available and self.down_since \
                        and time.monotonic() - self.down_since > 120
                    if stale:
                        self.remote.disconnect()
                        self.remote = None
                    if self.remote is None:
                        await self.try_connect()
            except Exception:                                   # el bucle nunca debe morir
                log.exception("Error al conectar con la TV")
            await asyncio.sleep(20)

    async def try_connect(self):
        async with self.lock:
            host = self.cfg.get("tv_host")
            candidates = [host] if host and await port_open(host, API_PORT) else []
            if not candidates:
                candidates = await scan(API_PORT)
            for h in candidates:
                remote = self._new_remote(h)
                await remote.async_generate_cert_if_missing()
                try:
                    await remote.async_connect()
                except InvalidAuth:
                    remote.disconnect()
                    if h == host:
                        self.paired = False
                        log.info("La TV %s no está emparejada con este puente", h)
                    continue
                except (CannotConnect, ConnectionClosed, OSError):
                    remote.disconnect()
                    continue
                self.remote = remote
                self.paired = True
                self._on_available(True)
                if h != host:
                    log.info("TV encontrada en %s", h)
                    self.cfg["tv_host"] = h
                    save_config(self.cfg)
                remote.keep_reconnecting(self._lost_auth)
                return True
            return False

    async def pair_start(self, chosen=None):
        async with self.lock:
            if chosen:
                # Solo una TV que la búsqueda encontró: la webapp no puede mandar al puente a otra dirección.
                if chosen not in [t["host"] for t in self.found] + [self.cfg.get("tv_host")]:
                    raise json_error(web.HTTPBadRequest, "Esa TV no apareció en la búsqueda; busca otra vez")
                self.use_tv(chosen)
            host = self.cfg.get("tv_host")
            if not host or not await port_open(host, PAIR_PORT):
                found = await self.discover()
                if not found:
                    raise json_error(web.HTTPServiceUnavailable, "No encuentro ninguna TV en la red")
                if len(found) > 1:
                    raise json_error(web.HTTPConflict, "Hay varias TVs en la red; búscalas y elige una")
                self.use_tv(found[0]["host"])
                host = found[0]["host"]
            if self.remote:
                self.remote.disconnect()
                self.remote = None
            if self.pairing_remote:
                self.pairing_remote.disconnect()
            remote = self._new_remote(host)
            await remote.async_generate_cert_if_missing()
            await remote.async_start_pairing()
            self.pairing_remote = remote
            self.pairing_since = time.monotonic()
            log.info("Emparejando: la TV muestra un código")

    async def pair_finish(self, code):
        remote = self.pairing_remote
        if remote is None:
            raise json_error(web.HTTPConflict, "Primero inicia el emparejamiento")
        try:
            await remote.async_finish_pairing(code)
        except InvalidAuth:
            raise json_error(web.HTTPForbidden, "Código incorrecto; vuelve a emparejar")
        except (ConnectionClosed, CannotConnect, OSError):
            # La TV cancela el emparejamiento si se toca el control mientras muestra el código.
            raise json_error(web.HTTPConflict, "La TV cerró el emparejamiento; vuelve a intentarlo sin tocar el control")
        finally:
            remote.disconnect()
            self.pairing_remote = None
        self.paired = True
        log.info("Emparejado")
        await self.try_connect()

    def ready(self):
        if not self.connected:
            raise json_error(web.HTTPServiceUnavailable, "La TV no está conectada")
        return self.remote

    def status(self):
        r = self.remote
        vol = r.volume_info if r else None
        info = r.device_info if r else None
        return {
            "paired": self.paired,
            "connected": self.connected,
            "pairing": self.pairing_remote is not None,
            "on": r.is_on if r else None,
            "app": r.current_app if r else None,
            "volume": dict(vol) if vol else None,
            "tv": f"{info['manufacturer']} {info['model']}".strip() if info else None,
            "has_tv": bool(self.cfg.get("tv_host")),
        }


# ---- HTTP -----------------------------------------------------------------------------------------------------

def json_error(cls, message):
    return cls(text=json.dumps({"error": message}), content_type="application/json")


def make_api(cfg, tv):
    failures = {}

    @web.middleware
    async def guard(request, handler):
        origin = request.headers.get("Origin", "")
        allowed = origin.rstrip("/") in cfg["origins"] + cfg["_test_origins"]
        try:
            if request.method == "OPTIONS":
                resp = web.Response(status=204)
            else:
                if request.path.startswith("/api/"):
                    who = request.remote or "?"
                    count, until = failures.get(who, (0, 0.0))
                    if until > time.monotonic():
                        raise json_error(web.HTTPTooManyRequests, "Demasiados intentos; espera un minuto")
                    if not hmac.compare_digest(request.headers.get("X-Pin", ""), cfg["pin"]):
                        count += 1
                        failures[who] = (0, time.monotonic() + LOCK_SECONDS) if count >= LOCK_AFTER else (count, 0.0)
                        raise json_error(web.HTTPUnauthorized, "PIN incorrecto")
                    failures.pop(who, None)
                resp = await handler(request)
        except web.HTTPException as e:
            resp = e
        if allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Pin"
            resp.headers["Access-Control-Max-Age"] = "600"
            # Chrome: permiso para que una página pública hable con un equipo de la red local.
            if request.headers.get("Access-Control-Request-Private-Network") == "true":
                resp.headers["Access-Control-Allow-Private-Network"] = "true"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def body(request):
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise json_error(web.HTTPBadRequest, "JSON inválido")
        if not isinstance(data, dict):
            raise json_error(web.HTTPBadRequest, "JSON inválido")
        return data

    async def root(request):
        return web.Response(text="Puente de Control TV funcionando. Abre la webapp: " + WEBAPP + "\n")

    async def status(request):
        return web.json_response(tv.status())

    async def key(request):
        data = await body(request)
        name, action = str(data.get("key", "")), str(data.get("action", "tap"))
        if name not in KEYS or action not in ACTIONS:
            raise json_error(web.HTTPBadRequest, "Tecla no permitida")
        try:
            tv.ready().send_key_command(name, ACTIONS[action])
        except ConnectionClosed:
            raise json_error(web.HTTPServiceUnavailable, "La TV no está conectada")
        return web.json_response({"ok": True})

    async def app(request):
        data = await body(request)
        package = APPS.get(str(data.get("app", "")))
        if not package:
            raise json_error(web.HTTPBadRequest, "App no permitida")
        try:
            tv.ready().send_launch_app_command(package)
        except ConnectionClosed:
            raise json_error(web.HTTPServiceUnavailable, "La TV no está conectada")
        return web.json_response({"ok": True})

    async def text(request):
        data = await body(request)
        value = str(data.get("text", ""))
        if not value or len(value) > 200:
            raise json_error(web.HTTPBadRequest, "Texto vacío o demasiado largo")
        try:
            tv.ready().send_text(value)
        except ConnectionClosed:
            raise json_error(web.HTTPServiceUnavailable, "La TV no está conectada")
        return web.json_response({"ok": True})

    async def tvs(request):
        found = await tv.discover()
        return web.json_response({"tvs": found, "current": cfg.get("tv_host")})

    async def pair_start(request):
        data = await body(request)
        chosen = str(data.get("host", "")).strip() or None
        if chosen:
            try:
                if not ipaddress.ip_address(chosen).is_private:
                    raise ValueError
            except ValueError:
                raise json_error(web.HTTPBadRequest, "Dirección de TV inválida")
        await tv.pair_start(chosen)
        return web.json_response({"ok": True})

    async def pair_finish(request):
        data = await body(request)
        code = str(data.get("code", "")).strip().upper()
        if not (4 <= len(code) <= 8) or not code.isalnum():
            raise json_error(web.HTTPBadRequest, "Código inválido")
        await tv.pair_finish(code)
        return web.json_response({"ok": True, "connected": tv.connected})

    async def preflight(request):
        return web.Response(status=204)     # guard la contesta antes; esto solo le da una ruta a OPTIONS

    api = web.Application(middlewares=[guard], client_max_size=4096)
    api.router.add_get("/", root)
    api.router.add_get("/api/status", status)
    api.router.add_post("/api/key", key)
    api.router.add_post("/api/app", app)
    api.router.add_post("/api/text", text)
    api.router.add_get("/api/tvs", tvs)
    api.router.add_post("/api/pair/start", pair_start)
    api.router.add_post("/api/pair/finish", pair_finish)
    api.router.add_route("OPTIONS", "/{tail:.*}", preflight)
    return api


HELP_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Control TV: certificado</title>
<style>body{{font-family:system-ui,sans-serif;background:#000;color:#eee;margin:0;padding:24px 16px;line-height:1.5}}
main{{max-width:560px;margin:auto}}a.btn{{display:block;text-align:center;padding:14px;border-radius:14px;
background:#fff;color:#000;text-decoration:none;font-weight:600;margin:16px 0}}h1{{font-weight:300}}
li{{margin:6px 0}}code{{background:#222;padding:2px 6px;border-radius:6px}}</style></head><body><main>
<h1>Control TV</h1>
<p>Para que tu celular hable con el puente de esta PC, instala una vez su certificado (solo sirve para
direcciones de tu casa, no para sitios de internet).</p>
<a class="btn" href="/control-tv-ca.crt">1. Descargar el certificado</a>
<p><b>iPhone:</b> Permitir la descarga → Ajustes → <i>Perfil descargado</i> → Instalar. Después Ajustes → General →
Información → <i>Ajustes de confianza de certificados</i> → activa <i>Control TV - CA local</i>.</p>
<p><b>Android:</b> Ajustes → Seguridad → Más ajustes → Cifrado y credenciales → Instalar un certificado →
Certificado de CA → elige el archivo descargado.</p>
<a class="btn" id="open" href="{webapp}#bridge={bridge}">2. Abrir el control</a>
<p id="pinNote">El control te pedirá el PIN que muestra el puente en la PC.</p>
<p>Si ya instalaste el certificado antes, toca directo <b>Abrir el control</b>.</p>
<script>
// El código QR de la PC trae el PIN en el fragmento (#pin=…), que nunca sale del celular: se lo pasamos al control.
var m = location.hash.match(/pin=(\\d{{6}})/);
if (m) {{
  document.getElementById("open").href += "&pin=" + m[1];
  document.getElementById("pinNote").textContent = "El PIN ya va incluido: no tienes que escribir nada.";
  history.replaceState(null, "", location.pathname);
}}
</script>
</main></body></html>"""

START_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Control TV: escanea con el celular</title>
<style>body{{font-family:system-ui,sans-serif;background:#000;color:#eee;margin:0;display:grid;place-items:center;
min-height:100vh;text-align:center}}.qr{{background:#fff;padding:22px;border-radius:24px;display:inline-block}}
h1{{font-weight:300;margin:0 0 6px}}p{{color:#aaa;max-width:460px;margin:10px auto;line-height:1.5}}
code{{color:#fff}}</style></head><body><main>
<h1>Control TV</h1><p>Escanea este código con la cámara del celular (conectado al Wi-Fi de la casa).</p>
<div class="qr">{qr}</div>
<p>Abre la página del certificado y del control, con la dirección y el PIN ya incluidos.<br>
PIN: <code>{pin}</code> · Puente: <code>{bridge}</code></p>
<p>Deja abierta la ventana negra del puente mientras uses el control.</p></main></body></html>"""


def write_start_page(setup_url, bridge_url, pin):
    """La página con el código QR que se abre en la PC al arrancar el puente (queda en bridge/data/)."""
    import segno
    qr = segno.make(setup_url, error="m").svg_inline(scale=8, dark="#000", light="#fff")
    path = os.path.join(DATA, "inicio.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(START_PAGE.format(qr=qr, pin=pin, bridge=bridge_url))
    return path


def make_help(bridge_url):
    async def page(request):
        return web.Response(text=HELP_PAGE.format(webapp=WEBAPP, bridge=bridge_url), content_type="text/html")

    async def ca(request):
        with open(os.path.join(DATA, "ca.crt"), "rb") as f:
            der = x509.load_pem_x509_certificate(f.read()).public_bytes(serialization.Encoding.DER)
        return web.Response(body=der, content_type="application/x-x509-ca-cert",
                            headers={"Content-Disposition": 'attachment; filename="control-tv-ca.crt"'})

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/control-tv-ca.crt", ca)
    return app


async def main(args):
    cfg = load_config(args)
    os.makedirs(DATA, exist_ok=True)
    tv = Tv(cfg)
    asyncio.get_running_loop().create_task(tv.maintain())
    ip = lan_ip()
    runners = []

    if args.test_http_local:
        # Solo para probar la webapp en esta PC: http en 127.0.0.1, sin certificado. Nunca hacia la red.
        runner = web.AppRunner(make_api(cfg, tv))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", TEST_PORT).start()
        runners.append(runner)
        bridge_url = f"http://127.0.0.1:{TEST_PORT}"
    else:
        certs.ensure_ca(DATA)
        cert_path, key_path = certs.ensure_server_cert(DATA, local_ips())
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(cert_path, key_path)
        bridge_url = f"https://{ip}:{HTTPS_PORT}"
        for app, port, tls in ((make_api(cfg, tv), HTTPS_PORT, ctx), (make_help(bridge_url), HTTP_PORT, None)):
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", port, ssl_context=tls).start()
            runners.append(runner)

    print("\n  Control TV: puente listo")
    print(f"  Webapp:       {WEBAPP}#bridge={bridge_url}")
    print(f"  PIN:          {cfg['pin']}")
    if not args.test_http_local:
        setup_url = f"http://{ip}:{HTTP_PORT}/#pin={cfg['pin']}"
        print(f"  Celular:      http://{ip}:{HTTP_PORT}/   (o escanea el código QR que se abrió en la PC)")
        try:
            page = write_start_page(setup_url, bridge_url, cfg["pin"])
            if not args.no_browser:
                webbrowser.open(pathlib.Path(page).as_uri())
        except Exception:
            log.exception("No se pudo crear la página con el código QR")
    print(f"  TV:           {cfg.get('tv_host') or 'se buscará en la red'}")
    print("  Ctrl+C para detener.\n", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for runner in runners:
            await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Puente entre la webapp Control TV y la TV.")
    parser.add_argument("--tv", help="IP de la TV (se guarda para las siguientes veces)")
    parser.add_argument("--new-pin", action="store_true", help="genera un PIN nuevo")
    parser.add_argument("--allow-origin", action="append", help="otro origen permitido para la webapp (pruebas)")
    parser.add_argument("--test-http-local", action="store_true", help="solo pruebas: http en 127.0.0.1:8099")
    parser.add_argument("--no-browser", action="store_true", help="no abrir la página del código QR al arrancar")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("androidtvremote2").setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
