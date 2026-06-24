// ──────────────────────────────────────────────────────────────
// CACHE DES DONNÉES API
// ──────────────────────────────────────────────────────────────
const API_CACHE = 'hitna-api-v1';
const API_URLS = [
    '/api/produits',
    '/api/stock_bas',
    '/api/notifications'
];

// Mettre en cache les données API
async function cacheApiData() {
    try {
        for (const url of API_URLS) {
            const response = await fetch(url);
            if (response.ok) {
                const data = await response.json();
                const cache = await caches.open(API_CACHE);
                const responseToCache = new Response(JSON.stringify(data), {
                    headers: { 'Content-Type': 'application/json' }
                });
                await cache.put(url, responseToCache);
                console.log(`✅ API mise en cache: ${url}`);
            }
        }
    } catch (error) {
        console.log('⚠️ Impossible de mettre en cache les API (hors ligne)');
    }
}

// Récupérer les données depuis le cache
async function getCachedData(url) {
    try {
        const cache = await caches.open(API_CACHE);
        const cached = await cache.match(url);
        if (cached) {
            return await cached.json();
        }
        return null;
    } catch (error) {
        console.error('Erreur lors de la récupération du cache:', error);
        return null;
    }
}

// Intercepter les requêtes API
function setupApiCache() {
    const originalFetch = window.fetch;
    
    window.fetch = async function(...args) {
        const [url, options = {}] = args;
        
        if (typeof url === 'string' && url.includes('/api/')) {
            try {
                // Essayer le réseau d'abord
                const response = await originalFetch(...args);
                if (response.ok) {
                    // Mettre en cache
                    const clone = response.clone();
                    const data = await clone.json();
                    const cache = await caches.open(API_CACHE);
                    const responseToCache = new Response(JSON.stringify(data), {
                        headers: { 'Content-Type': 'application/json' }
                    });
                    await cache.put(url, responseToCache);
                    return response;
                }
            } catch (error) {
                // Hors ligne : essayer le cache
                console.log('📡 Hors ligne, récupération depuis le cache:', url);
                const cached = await getCachedData(url);
                if (cached) {
                    return new Response(JSON.stringify(cached), {
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
            }
        }
        
        return originalFetch(...args);
    };
}

// Initialiser le cache API au chargement
document.addEventListener('DOMContentLoaded', () => {
    if (navigator.onLine) {
        cacheApiData();
    }
    
    setupApiCache();
});

// Exporter
window.cacheApiData = cacheApiData;
window.getCachedData = getCachedData;