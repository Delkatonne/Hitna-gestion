const CACHE_NAME = 'hitna-v1';
const STATIC_CACHE = 'hitna-static-v1';
const DYNAMIC_CACHE = 'hitna-dynamic-v1';

// Fichiers statiques à mettre en cache
const urlsToCache = [
  '/',
  '/login',
  '/offline',          // ⚠️ Page hors ligne
  '/static/style.css',
  '/static/manifest.json',
  '/static/images/logo.jpg'
];

// Installation
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => {
        console.log('📦 Cache des fichiers statiques');
        return cache.addAll(urlsToCache);
      })
      .then(() => self.skipWaiting())
  );
});

// Activation - nettoyer les anciens caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== STATIC_CACHE && cache !== DYNAMIC_CACHE) {
            console.log('🗑️ Suppression du cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Stratégie : Network First (sauf pour les pages)
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  
  // Ne pas interférer avec l'API
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin/')) {
    // Pour les requêtes API, tenter le réseau d'abord
    event.respondWith(
      fetch(event.request)
        .catch(() => {
          // Si hors ligne, retourner une réponse JSON d'erreur
          return new Response(JSON.stringify({
            error: 'Hors ligne',
            offline: true
          }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          });
        })
    );
    return;
  }
  
  // Pour les pages HTML : Network First avec fallback
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          // Mettre en cache la page
          const responseClone = response.clone();
          caches.open(DYNAMIC_CACHE).then(cache => {
            cache.put(event.request, responseClone);
          });
          return response;
        })
        .catch(() => {
          // Retourner la page en cache ou une page hors ligne
          return caches.match(event.request)
            .then(cached => cached || caches.match('/offline'));
        })
    );
    return;
  }
  
  // Pour les ressources statiques : Cache First
  event.respondWith(
    caches.match(event.request)
      .then(cached => {
        if (cached) {
          // Mettre à jour le cache en arrière-plan
          fetch(event.request)
            .then(response => {
              caches.open(STATIC_CACHE).then(cache => {
                cache.put(event.request, response);
              });
            })
            .catch(() => {});
          return cached;
        }
        
        return fetch(event.request)
          .then(response => {
            const responseClone = response.clone();
            caches.open(STATIC_CACHE).then(cache => {
              cache.put(event.request, responseClone);
            });
            return response;
          })
          .catch(() => {
            // Si la ressource est une image, retourner une image par défaut
            if (event.request.url.match(/\.(jpg|jpeg|png|gif|svg)$/)) {
              return caches.match('/static/images/logo.jpg');
            }
          });
      })
  );
});