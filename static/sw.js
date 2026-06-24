const CACHE_NAME = 'hitna-v2';
const STATIC_CACHE = 'hitna-static-v2';
const DYNAMIC_CACHE = 'hitna-dynamic-v2';

// Fichiers statiques à mettre en cache
const urlsToCache = [
  '/',
  '/login',
  '/offline',
  '/static/style.css',
  '/static/manifest.json',
  '/static/images/logo.jpg'
];

// Installation
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => {
        console.log('📦 Installation du cache...');
        return cache.addAll(urlsToCache);
      })
      .then(() => {
        console.log('✅ Cache installé avec succès !');
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
          if (cache !== STATIC_CACHE && cache !== DYNAMIC_CACHE) {
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
  
  // Pour les pages HTML
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
          return caches.match('/offline');
        })
    );
    return;
  }
  
  // Pour les ressources statiques
  event.respondWith(
    caches.match(event.request)
      .then(cached => {
        if (cached) {
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