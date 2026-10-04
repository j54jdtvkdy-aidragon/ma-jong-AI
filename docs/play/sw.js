
const CACHE = "mj-trainer-e3e55f218d3b";
const FILES = ["./boot.js", "./bundle/py.zip", "./icons/apple-touch-icon.png", "./icons/icon-192.png", "./icons/icon-512.png", "./icons/icon.svg", "./index.html", "./manifest.webmanifest", "./pyodide/pyodide-lock.json", "./pyodide/pyodide.asm.mjs", "./pyodide/pyodide.asm.wasm", "./pyodide/pyodide.js", "./pyodide/python_stdlib.zip", "./"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  e.respondWith(caches.match(e.request, {ignoreSearch: true}).then(r => r || fetch(e.request)));
});
