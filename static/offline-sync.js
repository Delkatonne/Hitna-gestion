// ──────────────────────────────────────────────────────────────
// BASE DE DONNÉES INDEXEDDB POUR LA FILE D'ATTENTE HORS LIGNE
// ──────────────────────────────────────────────────────────────
const DB_NAME = 'hitna_offline';
const DB_VERSION = 1;
const STORE_NAME = 'pending_actions';

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
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                const store = db.createObjectStore(STORE_NAME, { 
                    keyPath: 'id', 
                    autoIncrement: true 
                });
                store.createIndex('type', 'type', { unique: false });
                store.createIndex('timestamp', 'timestamp', { unique: false });
            }
        };
    });
}

// Ajouter une action en file d'attente
async function queueAction(type, endpoint, data) {
    await openDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction([STORE_NAME], 'readwrite');
        const store = transaction.objectStore(STORE_NAME);
        
        const action = {
            type: type,
            endpoint: endpoint,
            data: data,
            timestamp: new Date().toISOString(),
            synced: false,
            attempts: 0
        };
        
        const request = store.add(action);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

// Récupérer toutes les actions en attente
async function getPendingActions() {
    await openDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction([STORE_NAME], 'readonly');
        const store = transaction.objectStore(STORE_NAME);
        const request = store.getAll();
        
        request.onsuccess = () => {
            const actions = request.result.filter(a => !a.synced);
            resolve(actions);
        };
        request.onerror = () => reject(request.error);
    });
}

// Supprimer une action une fois synchronisée
async function removeSyncedAction(id) {
    await openDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction([STORE_NAME], 'readwrite');
        const store = transaction.objectStore(STORE_NAME);
        const request = store.delete(id);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
    });
}

// Mettre à jour une action (incrémenter les tentatives)
async function updateAction(id, data) {
    await openDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction([STORE_NAME], 'readwrite');
        const store = transaction.objectStore(STORE_NAME);
        const request = store.get(id);
        
        request.onsuccess = () => {
            const action = request.result;
            if (action) {
                Object.assign(action, data);
                const updateRequest = store.put(action);
                updateRequest.onsuccess = () => resolve();
                updateRequest.onerror = () => reject(updateRequest.error);
            } else {
                resolve();
            }
        };
        request.onerror = () => reject(request.error);
    });
}

// ──────────────────────────────────────────────────────────────
// SYNCHRONISATION AVEC LE SERVEUR
// ──────────────────────────────────────────────────────────────
async function syncWithServer() {
    const actions = await getPendingActions();
    
    if (actions.length === 0) {
        console.log('✅ Aucune action en attente de synchronisation');
        return;
    }
    
    console.log(`🔄 Synchronisation de ${actions.length} action(s)...`);
    showOfflineNotification(`🔄 Synchronisation de ${actions.length} action(s)...`);
    
    let syncedCount = 0;
    
    for (const action of actions) {
        try {
            // Incrémenter les tentatives
            action.attempts = (action.attempts || 0) + 1;
            await updateAction(action.id, { attempts: action.attempts });
            
            const response = await fetch(action.endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(action.data)
            });
            
            if (response.ok) {
                await removeSyncedAction(action.id);
                syncedCount++;
                console.log(`✅ ${action.type} ${action.id} synchronisée`);
            } else {
                console.error(`❌ Erreur synchro ${action.id}: ${response.status}`);
                
                if (action.attempts > 5) {
                    console.warn(`⚠️ Action ${action.id} abandonnée après 5 tentatives`);
                    await updateAction(action.id, { synced: true });
                }
            }
        } catch (error) {
            console.error(`❌ Erreur réseau pour ${action.id}: ${error.message}`);
        }
    }
    
    if (syncedCount > 0) {
        showOfflineNotification(`✅ ${syncedCount} action(s) synchronisée(s) !`);
    }
}

// ──────────────────────────────────────────────────────────────
// NOTIFICATION VISUELLE
// ──────────────────────────────────────────────────────────────
function showOfflineNotification(message, type = 'info') {
    const existing = document.querySelector('.offline-notification');
    if (existing) existing.remove();
    
    const notif = document.createElement('div');
    notif.className = 'offline-notification';
    notif.style.cssText = `
        position: fixed;
        bottom: 80px;
        left: 50%;
        transform: translateX(-50%);
        background: ${type === 'error' ? '#dc3545' : type === 'success' ? '#28a745' : '#17a2b8'};
        color: white;
        padding: 12px 24px;
        border-radius: 10px;
        z-index: 10002;
        font-weight: bold;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3);
        max-width: 90%;
        text-align: center;
        animation: slideUp 0.3s ease-out;
    `;
    notif.textContent = message;
    document.body.appendChild(notif);
    
    setTimeout(() => {
        notif.style.transition = 'opacity 0.5s';
        notif.style.opacity = '0';
        setTimeout(() => notif.remove(), 500);
    }, 4000);
}

// ──────────────────────────────────────────────────────────────
// INTERCEPTION DES FORMULAIRES POUR HORS LIGNE
// ──────────────────────────────────────────────────────────────
function setupOfflineForms() {
    // ── Formulaire de vente ──
    const saleForm = document.querySelector('#saleForm');
    if (saleForm) {
        saleForm.addEventListener('submit', async function(e) {
            if (!navigator.onLine) {
                e.preventDefault();
                
                const formData = new FormData(this);
                const data = {};
                for (let [key, value] of formData.entries()) {
                    data[key] = value;
                }
                
                await queueAction('vente', this.action, data);
                showOfflineNotification('📱 Vente sauvegardée hors ligne ✅');
                
                this.reset();
                const display = document.getElementById('selectedProductDisplay');
                if (display) {
                    display.innerHTML = '<div style="opacity: 0.7;">✅ Vente sauvegardée hors ligne</div>';
                }
            }
        });
    }
    
    // ── Formulaire d'entrée de stock ──
    const entreForm = document.querySelector('#entreeForm');
    if (entreForm) {
        entreForm.addEventListener('submit', async function(e) {
            if (!navigator.onLine) {
                e.preventDefault();
                
                const formData = new FormData(this);
                const data = {};
                for (let [key, value] of formData.entries()) {
                    data[key] = value;
                }
                
                await queueAction('entree', this.action, data);
                showOfflineNotification('📥 Entrée sauvegardée hors ligne ✅');
                
                this.reset();
                const display = document.getElementById('selectedProductDisplay');
                if (display) {
                    display.style.display = 'none';
                }
            }
        });
    }
}

// ──────────────────────────────────────────────────────────────
// INDIQUER L'ÉTAT DE LA CONNEXION
// ──────────────────────────────────────────────────────────────
function updateConnectionStatus() {
    let statusDiv = document.getElementById('connectionStatus');
    if (!statusDiv) {
        const div = document.createElement('div');
        div.id = 'connectionStatus';
        div.style.cssText = `
            position: fixed;
            top: 70px;
            right: 10px;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            z-index: 1000;
            transition: all 0.3s;
        `;
        document.body.appendChild(div);
        statusDiv = div;
    }
    
    if (navigator.onLine) {
        statusDiv.textContent = '🟢 En ligne';
        statusDiv.style.background = '#d4edda';
        statusDiv.style.color = '#155724';
    } else {
        statusDiv.textContent = '🔴 Hors ligne';
        statusDiv.style.background = '#f8d7da';
        statusDiv.style.color = '#721c24';
    }
}

// ──────────────────────────────────────────────────────────────
// INITIALISATION
// ──────────────────────────────────────────────────────────────
function initOfflineSync() {
    // Synchroniser quand la connexion revient
    window.addEventListener('online', () => {
        updateConnectionStatus();
        showOfflineNotification('🌐 Connexion rétablie, synchronisation...', 'info');
        syncWithServer();
    });
    
    window.addEventListener('offline', () => {
        updateConnectionStatus();
        showOfflineNotification('📡 Connexion perdue, mode hors ligne activé', 'error');
    });
    
    // Synchroniser au chargement de la page
    document.addEventListener('DOMContentLoaded', () => {
        setupOfflineForms();
        updateConnectionStatus();
        
        getPendingActions().then(actions => {
            if (actions.length > 0 && navigator.onLine) {
                syncWithServer();
            } else if (actions.length > 0) {
                showOfflineNotification(`📱 ${actions.length} action(s) en attente de synchronisation`, 'info');
            }
        });
    });
    
    // Synchronisation périodique (toutes les 2 minutes)
    setInterval(() => {
        if (navigator.onLine) {
            syncWithServer();
        }
    }, 120000);
}

// Exporter pour usage
window.queueAction = queueAction;
window.syncWithServer = syncWithServer;
window.initOfflineSync = initOfflineSync;
window.setupOfflineForms = setupOfflineForms;