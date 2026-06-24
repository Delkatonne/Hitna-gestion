const CACHE_NAME = 'hitna-v3';
const STATIC_CACHE = 'hitna-static-v3';
const DYNAMIC_CACHE = 'hitna-dynamic-v3';
const API_CACHE = 'hitna-api-v3';

// Fichiers statiques à mettre en cache
const urlsToCache = [
  '/',
  '/login',
  '/offline',
  '/static/style.css',
  '/static/manifest.json',
  '/static/images/logo.jpg'
];

// URLs API à mettre en cache
const API_URLS = [
  '/api/produits',
  '/api/stock_bas',
  '/api/notifications'
];

// Installation
self.addEventListener('install', event => {
  event.waitUntil(
    Promise.all([
      // Cache des fichiers statiques
      caches.open(STATIC_CACHE).then(cache => {
        console.log('📦 Installation du cache statique...');
        return cache.addAll(urlsToCache);
      }),
      // Cache des API
      caches.open(API_CACHE).then(cache => {
        console.log('📦 Installation du cache API...');
        return cache.addAll(API_URLS);
      })
    ])
    .then(() => {
      console.log('✅ Tous les caches installés avec succès !');
      return self.skipWaiting();
    })
    .catch(error => {
      console.error('❌ Erreur d\'installation:', error);
    })
  );
});

// Activation
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== STATIC_CACHE && cache !== DYNAMIC_CACHE && cache !== API_CACHE) {
            console.log('🗑️ Suppression du cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => {
      console.log('✅ Activation terminée');
      return self.clients.claim();
    })
  );
});

// Interception des requêtes
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  
  // ── GESTION DES API ──
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          // Mettre en cache la réponse
          const clone = response.clone();
          caches.open(API_CACHE).then(cache => {
            cache.put(event.request, clone);
          });
          return response;
        })
        .catch(() => {
          // Hors ligne : servir le cache
          return caches.match(event.request)
            .then(cached => {
              if (cached) {
                console.log('📡 API servie depuis le cache:', url.pathname);
                return cached;
              }
              // Si pas en cache, retourner une erreur
              return new Response(JSON.stringify({
                error: 'Hors ligne',
                offline: true
              }), {
                status: 503,
                headers: { 'Content-Type': 'application/json' }
              });
            });
        })
    );
    return;
  }
  
  // ── PAGES HTML ──
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const responseClone = response.clone();
          caches.open(DYNAMIC_CACHE).then(cache => {
            cache.put(event.request, responseClone);
          });
          return response;
        })
        .catch(() => {
          return caches.match(event.request)
            .then(cached => cached || caches.match('/offline'));
        })
    );
    return;
  }
  
  // ── RESSOURCES STATIQUES ──
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
            if (event.request.url.match(/\.(jpg|jpeg|png|gif|svg)$/)) {
              return caches.match('/static/images/logo.jpg');
            }
          });
      })
  );
});