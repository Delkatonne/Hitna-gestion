// static/offline-cache.js
// Cache des données API pour le mode hors ligne

const API_CACHE_NAME = 'hitna-api-cache-v1';
const API_URLS_TO_CACHE = [
    '/api/produits',
    '/api/stock_bas',
    '/api/notifications'
];

// Mettre en cache les données API
async function cacheApiData() {
    if (!navigator.onLine) return;
    
    try {
        const cache = await caches.open(API_CACHE_NAME);
        
        for (const url of API_URLS_TO_CACHE) {
            try {
                const response = await fetch(url);
                if (response.ok) {
                    const data = await response.clone().json();
                    const cachedResponse = new Response(JSON.stringify(data), {
                        headers: {
                            'Content-Type': 'application/json',
                            'Cache-Control': 'max-age=3600'
                        }
                    });
                    await cache.put(url, cachedResponse);
                    console.log(`✅ API mise en cache: ${url}`);
                }
            } catch (error) {
                console.log(`⚠️ Impossible de mettre en cache ${url}:`, error);
            }
        }
    } catch (error) {
        console.log('⚠️ Erreur de mise en cache des API:', error);
    }
}

// Récupérer les données depuis le cache
async function getCachedData(url) {
    try {
        const cache = await caches.open(API_CACHE_NAME);
        const cached = await cache.match(url);
        if (cached) {
            return await cached.json();
        }
        return null;
    } catch (error) {
        console.error('Erreur de récupération du cache:', error);
        return null;
    }
}

// Intercepter les requêtes API (fetch override amélioré)
function setupApiInterception() {
    const originalFetch = window.fetch;
    
    window.fetch = async function(...args) {
        const [url, options = {}] = args;
        const urlStr = typeof url === 'string' ? url : url.url;
        
        // Intercepter uniquement les API GET
        if (typeof urlStr === 'string' && 
            urlStr.includes('/api/') && 
            (!options.method || options.method === 'GET')) {
            
            try {
                // Essayer le réseau d'abord
                const response = await originalFetch(...args);
                if (response.ok) {
                    // Mettre en cache
                    const clone = response.clone();
                    const data = await clone.json();
                    const cache = await caches.open(API_CACHE_NAME);
                    const cachedResponse = new Response(JSON.stringify(data), {
                        headers: {
                            'Content-Type': 'application/json',
                            'Cache-Control': 'max-age=3600'
                        }
                    });
                    await cache.put(urlStr, cachedResponse);
                    return response;
                }
            } catch (error) {
                // Hors ligne : essayer le cache
                console.log('📡 Hors ligne, récupération depuis le cache:', urlStr);
                const cached = await getCachedData(urlStr);
                if (cached) {
                    return new Response(JSON.stringify(cached), {
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                // Retourner une réponse d'erreur
                return new Response(JSON.stringify({
                    error: 'Hors ligne',
                    offline: true,
                    data: []
                }), {
                    status: 503,
                    headers: { 'Content-Type': 'application/json' }
                });
            }
        }
        
        return originalFetch(...args);
    };
}

// Initialiser le cache API
document.addEventListener('DOMContentLoaded', () => {
    setupApiInterception();
    if (navigator.onLine) {
        cacheApiData();
    }
});

window.cacheApiData = cacheApiData;
window.getCachedData = getCachedData;