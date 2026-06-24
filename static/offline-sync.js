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
            synced: false
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
                console.log(`✅ Action ${action.id} synchronisée`);
            } else {
                console.error(`❌ Erreur lors de la synchro de l'action ${action.id}`);
            }
        } catch (error) {
            console.error(`❌ Erreur réseau: ${error.message}`);
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
        bottom: 20px;
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
    // Intercepter le formulaire de vente
    const saleForm = document.querySelector('#saleForm');
    if (saleForm) {
        saleForm.addEventListener('submit', async function(e) {
            // Vérifier si on est hors ligne
            if (!navigator.onLine) {
                e.preventDefault();
                
                const formData = new FormData(this);
                const data = {};
                for (let [key, value] of formData.entries()) {
                    data[key] = value;
                }
                
                await queueAction('vente', this.action, data);
                showOfflineNotification('📱 Vente sauvegardée hors ligne. Synchronisation automatique au retour de la connexion.', 'info');
                
                // Réinitialiser le formulaire
                this.reset();
                
                // Mettre à jour l'affichage
                const display = document.getElementById('selectedProductDisplay');
                if (display) {
                    display.innerHTML = '<div style="opacity: 0.7;">Vente sauvegardée hors ligne ✅</div>';
                }
            }
        });
    }
    
    // Intercepter le formulaire d'entrée de stock
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
                showOfflineNotification('📥 Entrée sauvegardée hors ligne. Synchronisation automatique au retour de la connexion.', 'info');
                
                // Réinitialiser le formulaire
                this.reset();
                document.getElementById('selectedProductDisplay').style.display = 'none';
            }
        });
    }
}

// ──────────────────────────────────────────────────────────────
// INITIALISATION
// ──────────────────────────────────────────────────────────────
function initOfflineSync() {
    // Synchroniser quand la connexion revient
    window.addEventListener('online', () => {
        showOfflineNotification('🌐 Connexion rétablie, synchronisation...', 'info');
        syncWithServer();
    });
    
    // Synchroniser au chargement de la page
    document.addEventListener('DOMContentLoaded', () => {
        setupOfflineForms();
        if (navigator.onLine) {
            syncWithServer();
        }
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