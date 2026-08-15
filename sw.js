// ============================================================
// SERVICE WORKER - HITNA Gestion
// Stratégie :
//  - App shell (CSS/JS/logo) : cache-first
//  - Pages HTML (GET) : network-first, fallback cache, puis /offline
//  - Requêtes API GET (ex: /api/produits) : network-first + cache
//  - Requêtes POST/PUT/DELETE : jamais interceptées (gérées par offline-sync.js
//    via la file d'attente IndexedDB, pas par le cache HTTP)
// ============================================================

const CACHE_VERSION = 'hitna-v1';
const APP_SHELL = [
  '/static/style.css',
  '/static/db.js',
  '/static/offline-sync.js',
  '/static/manifest.json',
  '/offline'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => {
      return Promise.all(
        APP_SHELL.map((url) => cache.add(url).catch(() => {}))
      );
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Ne jamais intercepter les écritures : elles sont gérées par la file
  // d'attente hors ligne (IndexedDB) côté client, pas par le cache HTTP.
  if (req.method !== 'GET') {
    return;
  }

  const url = new URL(req.url);

  // Pages HTML : network-first (toujours la version la plus fraîche si en ligne),
  // fallback sur le cache, puis sur la page /offline en dernier recours.
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const resClone = res.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(req, resClone));
          return res;
        })
        .catch(() =>
          caches.match(req).then((cached) => cached || caches.match('/offline'))
        )
    );
    return;
  }

  // Assets statiques et API GET : cache-first avec mise à jour en arrière-plan
  if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/api/')) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const fetchPromise = fetch(req)
          .then((res) => {
            const resClone = res.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(req, resClone));
            return res;
          })
          .catch(() => cached);
        return cached || fetchPromise;
      })
    );
  }
});