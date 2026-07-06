// static/db.js - VERSION CORRIGÉE
// Base de données IndexedDB pour le mode hors ligne

const DB_NAME = 'hitna_offline_db';
const DB_VERSION = 3; // Version incrémentée

let db = null;
let dbReady = false;

function openDB() {
    return new Promise((resolve, reject) => {
        if (db && dbReady) {
            resolve(db);
            return;
        }
        
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        
        request.onerror = () => {
            console.error('Erreur d\'ouverture de la DB:', request.error);
            reject(request.error);
        };
        
        request.onsuccess = () => {
            db = request.result;
            dbReady = true;
            
            db.onerror = (event) => {
                console.error('Erreur DB:', event.target.error);
            };
            
            resolve(db);
        };
        
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            
            // Supprimer les anciennes stores si elles existent (pour reconstruction propre)
            if (db.objectStoreNames.contains('ventes_offline')) {
                db.deleteObjectStore('ventes_offline');
            }
            if (db.objectStoreNames.contains('entrees_offline')) {
                db.deleteObjectStore('entrees_offline');
            }
            if (db.objectStoreNames.contains('pertes_offline')) {
                db.deleteObjectStore('pertes_offline');
            }
            if (db.objectStoreNames.contains('produits_cache')) {
                db.deleteObjectStore('produits_cache');
            }
            
            // Table des ventes hors ligne
            const ventesStore = db.createObjectStore('ventes_offline', { 
                keyPath: 'id', 
                autoIncrement: true 
            });
            ventesStore.createIndex('synced', 'synced');
            ventesStore.createIndex('date', 'date');
            ventesStore.createIndex('offline_id', 'offline_id', { unique: true });
            ventesStore.createIndex('created_at', 'created_at');
            
            // Table des entrées hors ligne
            const entreesStore = db.createObjectStore('entrees_offline', { 
                keyPath: 'id', 
                autoIncrement: true 
            });
            entreesStore.createIndex('synced', 'synced');
            entreesStore.createIndex('date', 'date');
            entreesStore.createIndex('offline_id', 'offline_id', { unique: true });
            entreesStore.createIndex('created_at', 'created_at');
            
            // Table des pertes hors ligne
            const pertesStore = db.createObjectStore('pertes_offline', { 
                keyPath: 'id', 
                autoIncrement: true 
            });
            pertesStore.createIndex('synced', 'synced');
            pertesStore.createIndex('date', 'date');
            pertesStore.createIndex('offline_id', 'offline_id', { unique: true });
            pertesStore.createIndex('created_at', 'created_at');
            
            // Table des produits en cache
            const produitsStore = db.createObjectStore('produits_cache', { 
                keyPath: 'id' 
            });
            produitsStore.createIndex('nom', 'nom');
            
            console.log('✅ Structure de la base de données créée');
        };
    });
}

// Ajouter une action hors ligne
async function addOfflineAction(type, data, endpoint) {
    await openDB();
    
    return new Promise((resolve, reject) => {
        const storeName = type + '_offline';
        
        // Vérifier que la store existe
        if (!db.objectStoreNames.contains(storeName)) {
            reject(new Error(`La table ${storeName} n'existe pas`));
            return;
        }
        
        const transaction = db.transaction([storeName], 'readwrite');
        const store = transaction.objectStore(storeName);
        
        const action = {
            ...data,
            synced: false,
            created_at: new Date().toISOString(),
            offline_id: Date.now() + '_' + Math.random().toString(36).substr(2, 8),
            _endpoint: endpoint || ''
        };
        
        const request = store.add(action);
        
        request.onsuccess = () => {
            console.log(`✅ Action ${type} ajoutée avec ID: ${request.result}`);
            resolve(request.result);
        };
        
        request.onerror = () => {
            console.error(`❌ Erreur ajout ${type}:`, request.error);
            reject(request.error);
        };
        
        transaction.onerror = () => {
            reject(transaction.error);
        };
    });
}

// Récupérer les actions non synchronisées
async function getUnsyncedActions(type) {
    await openDB();
    
    return new Promise((resolve, reject) => {
        const storeName = type + '_offline';
        
        if (!db.objectStoreNames.contains(storeName)) {
            resolve([]);
            return;
        }
        
        const transaction = db.transaction([storeName], 'readonly');
        const store = transaction.objectStore(storeName);
        const index = store.index('synced');
        
        const request = index.getAll(false);
        
        request.onsuccess = () => {
            resolve(request.result || []);
        };
        
        request.onerror = () => {
            console.error('Erreur récupération actions:', request.error);
            reject(request.error);
        };
    });
}

// Marquer une action comme synchronisée
async function markAsSynced(type, id) {
    await openDB();
    
    return new Promise((resolve, reject) => {
        const storeName = type + '_offline';
        
        if (!db.objectStoreNames.contains(storeName)) {
            resolve();
            return;
        }
        
        const transaction = db.transaction([storeName], 'readwrite');
        const store = transaction.objectStore(storeName);
        
        const request = store.get(id);
        
        request.onsuccess = () => {
            const data = request.result;
            if (data) {
                data.synced = true;
                data.synced_at = new Date().toISOString();
                const updateRequest = store.put(data);
                updateRequest.onsuccess = () => resolve();
                updateRequest.onerror = () => reject(updateRequest.error);
            } else {
                resolve();
            }
        };
        
        request.onerror = () => reject(request.error);
    });
}

// Supprimer une action
async function removeSyncedAction(type, id) {
    await openDB();
    
    return new Promise((resolve, reject) => {
        const storeName = type + '_offline';
        
        if (!db.objectStoreNames.contains(storeName)) {
            resolve();
            return;
        }
        
        const transaction = db.transaction([storeName], 'readwrite');
        const store = transaction.objectStore(storeName);
        
        const request = store.delete(id);
        
        request.onsuccess = () => {
            console.log(`✅ Action ${type} #${id} supprimée`);
            resolve();
        };
        
        request.onerror = () => reject(request.error);
    });
}

// Compter les actions non synchronisées
async function countUnsyncedActions() {
    await openDB();
    
    const types = ['ventes', 'entrees', 'pertes'];
    let total = 0;
    
    for (const type of types) {
        try {
            const actions = await getUnsyncedActions(type);
            total += actions.length;
        } catch (error) {
            console.error(`Erreur comptage ${type}:`, error);
        }
    }
    return total;
}

// Exporter les fonctions
window.OfflineDB = {
    openDB,
    addOfflineAction,
    getUnsyncedActions,
    markAsSynced,
    removeSyncedAction,
    countUnsyncedActions
};