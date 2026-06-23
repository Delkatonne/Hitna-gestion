const DB_NAME = 'hitna_db';
const DB_VERSION = 1;

let db = null;

function openDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
            db = request.result;
            resolve(db);
        };
        
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            
            if (!db.objectStoreNames.contains('produits')) {
                db.createObjectStore('produits', { keyPath: 'id' });
            }
            if (!db.objectStoreNames.contains('sorties')) {
                db.createObjectStore('sorties', { keyPath: 'id' });
            }
            if (!db.objectStoreNames.contains('entrees')) {
                db.createObjectStore('entrees', { keyPath: 'id' });
            }
        };
    });
}

async function saveData(storeName, data) {
    await openDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction([storeName], 'readwrite');
        const store = transaction.objectStore(storeName);
        const request = store.put(data);
        request.onsuccess = () => resolve(true);
        request.onerror = () => reject(request.error);
    });
}

async function getAllData(storeName) {
    await openDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction([storeName], 'readonly');
        const store = transaction.objectStore(storeName);
        const request = store.getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

async function syncWithServer() {
    // Récupérer les données non synchronisées
    const unsynced = await getAllData('produits');
    
    for (const item of unsynced) {
        if (!item.synced) {
            try {
                await fetch('/api/sync/produits', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(item)
                });
                item.synced = true;
                await saveData('produits', item);
            } catch (e) {
                console.log('Hors ligne, sync plus tard');
            }
        }
    }
}

// Synchroniser quand la connexion revient
window.addEventListener('online', () => syncWithServer());