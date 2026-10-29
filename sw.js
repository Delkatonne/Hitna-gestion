// ============================================================
// SERVICE WORKER - HITNA Gestion
// Stratégie :
//  - App shell (CSS/JS/logo) : cache-first
//  - Pages HTML (GET) : network-first, fallback cache, puis /offline
//  - Requêtes API GET (ex: /api/produits) : network-first + cache
//  - Requêtes POST/PUT/DELETE : jamais interceptées (gérées par offline-sync.js
//    via la file d'attente IndexedDB, pas par le cache HTTP)
// ============================================================

const CACHE_VERSION = 'hitna-v6';   // v6 : purge des anciennes pages mises en cache par erreur (page de réveil Render, redirections vers /login)
// Jamais mises en cache : pages de connexion et réponses techniques
const NE_PAS_METTRE_EN_CACHE = ['/login', '/logout', '/healthz', '/erreur-temporaire'];
// On ne garde en cache que les vraies réponses réussies du site (pas la page « Service waking up » de Render,
// pas les erreurs 5xx, pas les redirections vers /login).
function reponseCacheable(res) {
  return !!res && res.ok && res.type === 'basic' && !res.redirected;
}
// À la déconnexion, on efface les pages privées gardées en cache (téléphone partagé)
function viderPagesPrivees() {
  return caches.open(CACHE_VERSION).then((cache) =>
    cache.keys().then((requetes) =>
      Promise.all(requetes.filter((r) => {
        const chemin = new URL(r.url).pathname;
        return !chemin.startsWith('/static/') && chemin !== '/offline';
      }).map((r) => cache.delete(r)))
    )
  );
}
const APP_SHELL = [
  '/static/style.css',
  '/static/db.js',
  '/static/offline-sync.js',
  '/static/paiement-multiple.js',
  '/static/manifest.json',
  '/offline',
  '/vente',
  '/admin/entrees',
  '/admin/pertes',
  '/admin/ventes',
  '/dashboard'
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

  if (url.pathname === '/logout') {
    event.waitUntil(viderPagesPrivees());
    return;
  }

  // Pages HTML : network-first (toujours la version la plus fraîche si en ligne),
  // fallback sur le cache, puis sur la page /offline en dernier recours.
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (reponseCacheable(res) && !NE_PAS_METTRE_EN_CACHE.includes(url.pathname)) {
            const resClone = res.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(req, resClone));
          }
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
            if (reponseCacheable(res)) {
              const resClone = res.clone();
              caches.open(CACHE_VERSION).then((cache) => cache.put(req, resClone));
            }
            return res;
          })
          .catch(() => cached);
        return cached || fetchPromise;
      })
    );
  }
});