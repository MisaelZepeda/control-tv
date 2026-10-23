# Control TV

Control remoto web para una TV con **Android TV / Google TV**, sin la app Google Home y sin ADB.

```
Celular (webapp en GitHub Pages)  --https + PIN-->  Puente en una PC de la casa  --protocolo oficial-->  TV
```

- **Webapp** (`docs/`): flechas, OK, Atrás, Inicio, Menú, volumen, silencio, reproducción, encendido, Entradas,
  Ajustes, accesos directos a apps y un teclado para escribir en la TV. Se instala en el celular como app.
  Publicada en https://misaelzepeda.github.io/control-tv/
- **Puente** (`bridge/`): un programa pequeño en Python que habla con la TV por el **protocolo de control remoto
  de Android TV** (puertos 6466/6467, el mismo de la app Google TV), con la librería
  [androidtvremote2](https://github.com/tronikos/androidtvremote2). Un navegador no puede hablar ese protocolo
  (conexiones TLS con certificados de cliente), por eso hace falta el puente.

## Por qué un puente y un certificado

La webapp vive en `https://…github.io`; el navegador solo la deja hablar con el puente si el puente también usa
https con un certificado en el que el celular confíe. El puente crea su propia **autoridad de certificados (CA)
local** y el celular la instala una vez. Esa CA tiene **restricción de nombres**: solo puede firmar certificados
para direcciones privadas (`10.x`, `172.16-31.x`, `192.168.x`, `127.x`) y `localhost`, así que no sirve para
hacerse pasar por ningún sitio de internet. Su llave nunca sale de la PC (`bridge/data/`, fuera del repositorio).

## Puesta en marcha

Necesitas: la TV y la PC en la misma red, Python 3.10 o más nuevo en la PC, y el servicio de control remoto de
la TV activo (viene con Android TV; es el que usa la app Google TV).

No hace falta saber ninguna IP: el puente busca la TV en la red y le da al celular su propia dirección con un
código QR.

1. **Arranca el puente**: doble clic en `bridge\iniciar-puente.cmd` (deja abierta la ventana negra). La primera
   vez instala las librerías. Busca la TV en la red y **abre en la PC una página con un código QR**.

2. **Firewall de Windows**: la primera vez pregunta si Python puede usar la red. Permítelo en **redes privadas**
   y marca tu Wi-Fi de casa como red privada (Configuración → Red e Internet → Wi-Fi → tu red → Red privada).

3. **Escanea el código QR** con la cámara del celular (en el mismo Wi-Fi). Abre la página del puente con la
   dirección y el PIN ya incluidos.

4. **Certificado en el celular** (una vez), desde esa página:
   - **iPhone:** permite la descarga → Ajustes → *Perfil descargado* → Instalar. Luego Ajustes → General →
     Información → *Ajustes de confianza de certificados* → activa **Control TV - CA local**.
   - **Android:** Ajustes → Seguridad → Cifrado y credenciales → Instalar un certificado → Certificado de CA.

5. **Abre el control** con el botón de esa misma página: se configura solo. Para tenerlo como app: en Safari,
   Compartir → *Agregar a inicio*; en Chrome, menú → *Instalar app*.

6. **Empareja con la TV** (una vez): Configuración → **Buscar TV y emparejar**. Si hay varias TVs, elige la tuya
   por su nombre; la TV muestra un código y lo escribes. **No toques el control de la TV mientras el código está
   en pantalla**: la TV cancela el emparejamiento.

**Encender y apagar**: el botón rojo **Apagar** de arriba pone la TV en espera; en espera se vuelve verde y dice
**Encender**. Para encender desde el celular, la TV tiene que seguir conectada a la red en espera (la mayoría de
las Android TV lo hacen para Chromecast). Si la TV se apagó por completo (desconectada de la luz), enciéndela una
vez con su control.

## Seguridad

- La API del puente pide **PIN** en cada orden y bloquea una dirección un minuto tras 5 PIN incorrectos.
- Solo acepta peticiones del navegador desde `https://misaelzepeda.github.io` (CORS).
- Solo deja pasar una lista fija de teclas y de apps; nada más llega a la TV.
- El repositorio no contiene IPs, PIN, nombres de red, llaves ni certificados: todo eso vive en `bridge/data/`
  (en `.gitignore`) y en el almacenamiento local del celular.
- No usa ADB: la depuración de la TV puede quedarse apagada.
- Para dejar de usarlo: borra `bridge/data/`, quita la CA del celular y en la TV Ajustes → Apps → *Android TV
  Remote Service* → Borrar datos (olvida el emparejamiento).

## Apps de los accesos directos

Netflix, YouTube, Prime Video, Disney+, Max, Canela.TV, Claro video y Mercado Play (por nombre de paquete, en
`bridge/server.py` → `APPS`, y los botones en `docs/index.html`). Para agregar una, añádela en ambos lados.

## Problemas comunes

| Síntoma | Qué hacer |
|---|---|
| "No hay conexión con el puente" | ¿Está corriendo el puente? ¿El celular está en el mismo Wi-Fi? ¿Instalaste y confiaste el certificado? ¿El firewall permite Python? |
| "PIN incorrecto" | Revisa el PIN que muestra el puente al arrancar. Tras 5 intentos espera un minuto. |
| "Falta emparejar" o "Falta elegir la TV" | Configuración → *Buscar TV y emparejar*. |
| "La TV cerró el emparejamiento" | Se tocó el control de la TV mientras mostraba el código; vuelve a intentarlo. |
| "TV sin conexión" | La TV está apagada del todo o cambió de red; el puente la vuelve a buscar sola en un par de minutos. |
| La PC cambió de IP | El puente renueva su certificado solo; actualiza la dirección en la Configuración de la webapp. |
