// Guarda la página para que abra al instante y sin internet. Las llamadas al puente (otro origen) no pasan por aquí.
const CACHE = "control-tv-v2";
const SHELL = ["./", "index.html", "style.css", "app.js", "manifest.webmanifest", "icon.svg",
  "icon-192.png", "icon-512.png", "apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  // Primero la red (así llegan las actualizaciones); sin red, lo guardado.
  event.respondWith(fetch(event.request)
    .then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(event.request, copy));
      return res;
    })
    .catch(() => caches.match(event.request).then((hit) => hit || caches.match("./"))));
});
