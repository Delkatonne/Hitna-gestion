// ============================================================
// db.js — Couche IndexedDB pour HITNA Gestion
// Deux "object stores" :
//   - sync_queue : actions écrites hors ligne, en attente d'envoi au serveur
//   - produits_cache : dernière copie connue des produits (lecture hors ligne)
// ============================================================

const HITNA_DB_NAME = 'hitna_offline';
// Passé de 1 à 2 : sans ce changement, les appareils qui avaient déjà créé la
// base avec un ancien schéma (avant l'ajout de "produits_cache") restent
// bloqués pour toujours — onupgradeneeded ne se redéclenche que si le numéro
// de version augmente. C'était la cause de l'erreur en boucle "One of the
// specified object stores was not found" vue dans la console.
const HITNA_DB_VERSION = 2;

function hitnaOpenDB() {
    return new Promise((resolve, reject) => {
        if (!('indexedDB' in window)) {
            reject(new Error('IndexedDB non supporté sur ce navigateur'));
            return;
        }
        const request = indexedDB.open(HITNA_DB_NAME, HITNA_DB_VERSION);

        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains('sync_queue')) {
                const store = db.createObjectStore('sync_queue', { keyPath: 'client_id' });
                store.createIndex('created_at', 'created_at', { unique: false });
            }
            if (!db.objectStoreNames.contains('produits_cache')) {
                db.createObjectStore('produits_cache', { keyPath: 'id' });
            }
        };

        request.onsuccess = (event) => resolve(event.target.result);
        request.onerror = (event) => reject(event.target.error);
    });
}

function hitnaGenerateClientId() {
    return 'off_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
}

// ── FILE D'ATTENTE DES ACTIONS ────────────────────────────────
async function hitnaQueueAction(type, payload) {
    const db = await hitnaOpenDB();
    const action = {
        client_id: hitnaGenerateClientId(),
        type: type, // 'vente' | 'entree' | 'perte'
        payload: payload,
        created_at: new Date().toISOString(),
        status: 'pending'
    };
    return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readwrite');
        tx.objectStore('sync_queue').add(action);
        tx.oncomplete = () => resolve(action);
        tx.onerror = () => reject(tx.error);
    });
}

async function hitnaGetQueue() {
    const db = await hitnaOpenDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readonly');
        const req = tx.objectStore('sync_queue').getAll();
        req.onsuccess = () => resolve(req.result.sort((a, b) => a.created_at.localeCompare(b.created_at)));
        req.onerror = () => reject(req.error);
    });
}

async function hitnaRemoveFromQueue(clientId) {
    const db = await hitnaOpenDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readwrite');
        tx.objectStore('sync_queue').delete(clientId);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

async function hitnaQueueCount() {
    const items = await hitnaGetQueue();
    return items.length;
}

// ── CACHE LOCAL DES PRODUITS (lecture hors ligne) ───────────────
async function hitnaSaveProduitsCache(produits) {
    const db = await hitnaOpenDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction('produits_cache', 'readwrite');
        const store = tx.objectStore('produits_cache');
        store.clear();
        produits.forEach((p) => store.put(p));
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

async function hitnaGetProduitsCache() {
    const db = await hitnaOpenDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction('produits_cache', 'readonly');
        const req = tx.objectStore('produits_cache').getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

window.HitnaDB = {
    queueAction: hitnaQueueAction,
    getQueue: hitnaGetQueue,
    removeFromQueue: hitnaRemoveFromQueue,
    queueCount: hitnaQueueCount,
    saveProduitsCache: hitnaSaveProduitsCache,
    getProduitsCache: hitnaGetProduitsCache
};