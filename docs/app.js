// Control TV: manda los botones al puente de la casa (bridge/server.py), que se los pasa a la TV por el protocolo
// oficial de control remoto de Android TV. La dirección del puente y el PIN se guardan solo en este celular.
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const state = { bridge: "", pin: "", status: null, poll: null, busyPair: false };

  // ---- almacenamiento local (puede fallar en modo privado) -------------------------------------------------
  function load() {
    try {
      state.bridge = localStorage.getItem("ctv.bridge") || "";
      state.pin = localStorage.getItem("ctv.pin") || "";
    } catch (e) { /* sin almacenamiento: se pide cada vez */ }
  }
  function save() {
    try {
      localStorage.setItem("ctv.bridge", state.bridge);
      localStorage.setItem("ctv.pin", state.pin);
    } catch (e) { /* idem */ }
  }

  // El código QR del puente abre la webapp con #bridge=https://IP:8443&pin=123456: nada que escribir. El fragmento
  // (#…) nunca se envía a ningún servidor, y se borra de la barra de direcciones en cuanto se lee.
  function fromHash() {
    const bridge = location.hash.match(/bridge=([^&]+)/);
    const pin = location.hash.match(/pin=(\d{6})(?:&|$)/);
    if (!bridge && !pin) return false;
    history.replaceState(null, "", location.pathname + location.search);
    const url = bridge ? normalize(decodeURIComponent(bridge[1])) : "";
    if (url) state.bridge = url;
    if (pin) state.pin = pin[1];
    save();
    return !!url;
  }

  function normalize(value) {
    let v = (value || "").trim().replace(/\/+$/, "");
    if (!v) return "";
    if (!/^https?:\/\//i.test(v)) v = "https://" + v;
    if (!/:\d+$/.test(v.replace(/^https?:\/\//i, ""))) v += ":8443";
    try {
      const u = new URL(v);
      return u.protocol + "//" + u.host;
    } catch (e) {
      return "";
    }
  }

  // ---- API del puente -----------------------------------------------------------------------------------
  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(path, body) {
    if (!state.bridge || !state.pin) throw new ApiError("Configura el puente y el PIN", 0);
    const opts = {
      method: body ? "POST" : "GET",
      headers: { "X-Pin": state.pin },
      cache: "no-store",
    };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) opts.signal = AbortSignal.timeout(8000);
    let res;
    try {
      res = await fetch(state.bridge + path, opts);
    } catch (e) {
      throw new ApiError("No hay conexión con el puente: ¿está encendida la PC, estás en el Wi-Fi de la casa " +
                         "e instalaste el certificado?", 0);
    }
    let data = {};
    try { data = await res.json(); } catch (e) { /* sin cuerpo */ }
    if (!res.ok) throw new ApiError(data.error || ("Error " + res.status), res.status);
    return data;
  }

  // ---- avisos y estado ----------------------------------------------------------------------------------
  let toastTimer = null;
  function toast(message) {
    const el = $("#toast");
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
  }

  function haptic() {
    if (navigator.vibrate) navigator.vibrate(8);
  }

  const APP_NAMES = {
    "com.netflix.ninja": "Netflix",
    "com.google.android.youtube.tv": "YouTube",
    "com.amazon.amazonvideo.livingroom": "Prime Video",
    "com.disney.disneyplus": "Disney+",
    "com.wbd.stream": "Max",
    "com.canela.ott.tv": "Canela.TV",
    "com.amco.clarovideo_atv": "Claro video",
    "com.mercadolibre.android.mplay_tv": "Mercado Play",
    "com.gathin.tvhome": "Inicio",
    "com.mediatek.wwtv.tvcenter": "TV / HDMI",
    "com.android.tv.settings": "Ajustes",
    "com.android.vending": "Play Store",
    "io.github.sumyabazar1.weather": "Clima",
  };

  function showStatus(kind, title, detail) {
    $("#statusDot").className = "dot " + (kind || "");
    $("#statusTitle").textContent = title;
    $("#statusDetail").textContent = detail;
  }

  function render(s) {
    state.status = s;
    if (!s) return;
    const off = s.connected && s.on === false;
    $("#powerBtn").classList.toggle("off", off);
    $("#powerLabel").textContent = off ? "Encender" : "Apagar";
    if (s.has_tv === false) {
      showStatus("warn", "Falta elegir la TV", "Toca aquí para buscarla en la red");
    } else if (s.paired === false) {
      showStatus("warn", "Falta emparejar", "Toca aquí para emparejar con la TV");
    } else if (!s.connected) {
      showStatus("warn", "TV sin conexión", "¿Está conectada al Wi-Fi?");
    } else {
      showStatus("ok", s.tv || "TV conectada", s.on === false ? "En espera" : "Conectada");
    }
    const vol = s.volume;
    $("#volLevel").textContent = vol ? (vol.muted ? "silencio" : String(vol.level)) : "";
    $("#muteBtn").classList.toggle("muted-on", !!(vol && vol.muted));
    const app = s.app ? (APP_NAMES[s.app] || s.app) : "";
    $("#nowPlaying").textContent = s.connected && app ? "En pantalla: " + app : "";
  }

  async function refresh() {
    if (!state.bridge || !state.pin) {
      showStatus("", "Control TV", "Toca aquí para configurar");
      return;
    }
    try {
      render(await api("/api/status"));
    } catch (e) {
      showStatus("bad", "Sin conexión", e.status === 401 ? "PIN incorrecto" : "Toca aquí para ver la configuración");
    }
  }

  function startPolling() {
    stopPolling();
    refresh();
    state.poll = setInterval(refresh, 4000);
  }
  function stopPolling() {
    if (state.poll) clearInterval(state.poll);
    state.poll = null;
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPolling(); else startPolling();
  });

  // ---- botones ------------------------------------------------------------------------------------------
  async function sendKey(key) {
    try {
      await api("/api/key", { key, action: "tap" });
      if (key === "POWER") setTimeout(refresh, 1500);      // el botón cambia a Encender/Apagar
    } catch (e) {
      if (key === "POWER" && e.status === 503) {
        toast("La TV está apagada por completo (sin red): enciéndela una vez con su control");
      } else {
        toast(e.message);
      }
      if (e.status === 0 || e.status === 401) stopHolds();
    }
  }

  const holds = new Set();
  function stopHolds() {
    holds.forEach((h) => h());
    holds.clear();
  }

  function bindKey(el) {
    const key = el.dataset.key;
    if (el.hasAttribute("data-hold")) {
      // Mantener presionado repite la tecla (flechas, volumen, adelantar/retroceder).
      let delay = null, repeat = null;
      const stop = () => {
        clearTimeout(delay); clearInterval(repeat);
        delay = repeat = null;
        el.classList.remove("pressed");
        holds.delete(stop);
      };
      el.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        if (el.setPointerCapture) { try { el.setPointerCapture(ev.pointerId); } catch (e) { /* ok */ } }
        stop();
        el.classList.add("pressed");
        haptic();
        sendKey(key);
        holds.add(stop);
        delay = setTimeout(() => { repeat = setInterval(() => sendKey(key), 130); }, 420);
      });
      ["pointerup", "pointercancel", "lostpointercapture"].forEach((t) => el.addEventListener(t, stop));
      el.addEventListener("contextmenu", (ev) => ev.preventDefault());
      el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") sendKey(key); });
    } else {
      el.addEventListener("click", () => { haptic(); sendKey(key); });
    }
  }

  document.querySelectorAll("[data-key]").forEach(bindKey);

  document.querySelectorAll("[data-app]").forEach((el) => {
    el.addEventListener("click", async () => {
      haptic();
      try {
        await api("/api/app", { app: el.dataset.app });
        toast("Abriendo " + el.textContent.trim());
      } catch (e) {
        toast(e.message);
      }
    });
  });

  // ---- teclado ------------------------------------------------------------------------------------------
  $("#openKeyboard").addEventListener("click", () => {
    const kb = $("#keyboard");
    kb.hidden = !kb.hidden;
    if (!kb.hidden) $("#textInput").focus();
  });
  $("#keyboard").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("#textInput");
    const text = input.value;
    if (!text) { sendKey("ENTER"); return; }
    try {
      await api("/api/text", { text });
      input.value = "";
    } catch (e) {
      toast(e.message);
    }
  });

  // ---- configuración y emparejamiento -------------------------------------------------------------------
  function certUrl() {
    try {
      const u = new URL(state.bridge || normalize($("#bridgeInput").value));
      return "http://" + u.hostname + ":8080/";
    } catch (e) {
      return "#";
    }
  }

  function settingsMsg(text, kind) {
    const el = $("#settingsMsg");
    el.textContent = text;
    el.className = "small " + (kind || "");
  }

  function openSettings() {
    $("#bridgeInput").value = state.bridge;
    $("#pinInput").value = state.pin;
    $("#certLink").href = certUrl();
    settingsMsg("");
    $("#pairBox").hidden = true;
    $("#tvList").hidden = true;
    $("#settings").hidden = false;
  }
  function closeSettings() { $("#settings").hidden = true; }

  $("#openSettings").addEventListener("click", openSettings);
  $("#status").addEventListener("click", openSettings);
  $("#closeSettings").addEventListener("click", closeSettings);
  $("#settings").addEventListener("click", (ev) => { if (ev.target.id === "settings") closeSettings(); });
  $("#bridgeInput").addEventListener("input", () => { $("#certLink").href = certUrl(); });

  $("#saveSettings").addEventListener("click", async () => {
    const bridge = normalize($("#bridgeInput").value);
    const pin = $("#pinInput").value.trim();
    if (!bridge) { settingsMsg("Escribe la dirección del puente, por ejemplo https://192.168.1.20:8443", "bad"); return; }
    if (!/^\d{6}$/.test(pin)) { settingsMsg("El PIN son 6 dígitos", "bad"); return; }
    state.bridge = bridge;
    state.pin = pin;
    save();
    $("#bridgeInput").value = bridge;
    $("#certLink").href = certUrl();
    settingsMsg("Probando…");
    try {
      const s = await api("/api/status");
      render(s);
      if (s.paired === false) settingsMsg("Conectado al puente. Falta emparejar con la TV (abajo).", "ok");
      else if (!s.connected) settingsMsg("Conectado al puente, pero la TV no responde. ¿Está encendida?", "bad");
      else settingsMsg("¡Listo! Conectado a " + (s.tv || "la TV") + ".", "ok");
      startPolling();
    } catch (e) {
      settingsMsg(e.message, "bad");
    }
  });

  // Pide el código a una TV que encontró la búsqueda.
  async function startPairing(host, name) {
    settingsMsg("Pidiendo el código a " + (name || "la TV") + "…");
    await api("/api/pair/start", host ? { host } : {});
    $("#tvList").hidden = true;
    $("#pairBox").hidden = false;
    $("#pairCode").value = "";
    $("#pairCode").focus();
    settingsMsg("Escribe el código que muestra la TV (sin tocar el control de la TV).", "ok");
  }

  $("#pairStart").addEventListener("click", async () => {
    if (state.busyPair) return;
    state.busyPair = true;
    $("#pairBox").hidden = true;
    const list = $("#tvList");
    list.hidden = true;
    settingsMsg("Buscando TVs en tu red…");
    try {
      const { tvs } = await api("/api/tvs");
      if (!tvs.length) {
        settingsMsg("No encontré ninguna TV. ¿Está encendida y en el mismo Wi-Fi que la PC?", "bad");
      } else if (tvs.length === 1) {
        await startPairing(tvs[0].host, tvs[0].name);
      } else {
        list.replaceChildren(...tvs.map((t) => {
          const b = document.createElement("button");
          const name = document.createElement("span");
          const ip = document.createElement("small");
          name.textContent = t.name;
          ip.textContent = t.host;
          b.append(name, ip);
          b.addEventListener("click", () => {
            startPairing(t.host, t.name).catch((e) => settingsMsg(e.message, "bad"));
          });
          return b;
        }));
        list.hidden = false;
        settingsMsg("Encontré " + tvs.length + " TVs: elige la tuya.", "ok");
      }
    } catch (e) {
      settingsMsg(e.message, "bad");
    } finally {
      state.busyPair = false;
    }
  });

  $("#pairFinish").addEventListener("click", async () => {
    const code = $("#pairCode").value.trim().toUpperCase();
    if (!/^[0-9A-F]{4,8}$/.test(code)) { settingsMsg("El código son letras y números, como A1B2C3", "bad"); return; }
    settingsMsg("Emparejando…");
    try {
      await api("/api/pair/finish", { code });
      $("#pairBox").hidden = true;
      settingsMsg("¡Emparejado!", "ok");
      startPolling();
    } catch (e) {
      settingsMsg(e.message, "bad");
    }
  });

  // ---- arranque -----------------------------------------------------------------------------------------
  load();
  const fromLink = fromHash();
  if (!state.bridge || !state.pin || fromLink) openSettings();
  // Desde el código QR ya vienen la dirección y el PIN: se prueba solo.
  if (fromLink && state.pin) $("#saveSettings").click();
  startPolling();

  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("sw.js").catch(() => { /* sin modo sin conexión */ });
  }
})();
