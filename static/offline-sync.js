// static/offline-sync.js - VERSION ULTRA SÉCURISÉE
// Gestion de la synchronisation hors ligne - NON-BLOQUANTE

// ──────────────────────────────────────────────────────────────
// FONCTIONS DE BASE - SÉCURISÉES
// ──────────────────────────────────────────────────────────────

function showOfflineNotification(message, type = 'info') {
    try {
        document.querySelectorAll('.offline-notification').forEach(el => el.remove());
        
        const notif = document.createElement('div');
        notif.className = 'offline-notification';
        
        const colors = {
            success: '#28a745',
            error: '#dc3545',
            info: '#17a2b8',
            warning: '#ffc107'
        };
        
        const bgColor = colors[type] || colors.info;
        const textColor = type === 'warning' ? '#333' : 'white';
        
        notif.style.cssText = `
            position: fixed;
            bottom: 130px;
            left: 50%;
            transform: translateX(-50%);
            background: ${bgColor};
            color: ${textColor};
            padding: 14px 28px;
            border-radius: 12px;
            z-index: 10002;
            font-weight: bold;
            font-size: 15px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            max-width: 90%;
            text-align: center;
            min-width: 200px;
        `;
        notif.textContent = message;
        document.body.appendChild(notif);
        
        setTimeout(() => {
            notif.style.transition = 'opacity 0.5s';
            notif.style.opacity = '0';
            setTimeout(() => notif.remove(), 500);
        }, 4000);
    } catch (error) {
        console.log('Erreur notification:', error);
    }
}

async function updatePendingBadge() {
    try {
        let badge = document.getElementById('pendingBadge');
        if (!badge) {
            const div = document.createElement('div');
            div.id = 'pendingBadge';
            div.style.cssText = `
                position: fixed;
                bottom: 80px;
                right: 20px;
                background: #ffc107;
                color: #333;
                padding: 8px 14px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: bold;
                z-index: 1000;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                display: none;
                cursor: pointer;
                border: 1px solid #e0a800;
            `;
            div.title = "Cliquez pour synchroniser";
            div.onclick = syncAllData;
            document.body.appendChild(div);
            badge = div;
        }
        
        const count = await window.OfflineDB.countUnsyncedActions();
        if (count > 0) {
            badge.style.display = 'block';
            badge.textContent = `📤 ${count} action(s) en attente`;
        } else {
            badge.style.display = 'none';
        }
    } catch (error) {
        console.log('Erreur badge:', error);
    }
}

// ──────────────────────────────────────────────────────────────
// SYNCHRONISATION - SIMPLIFIÉE
// ──────────────────────────────────────────────────────────────

async function syncAllData() {
    if (!navigator.onLine) return;
    
    try {
        const types = ['ventes', 'entrees', 'pertes'];
        let totalSynced = 0;
        
        for (const type of types) {
            try {
                const actions = await window.OfflineDB.getUnsyncedActions(type);
                if (actions.length === 0) continue;
                
                console.log(`🔄 Synchronisation de ${actions.length} ${type}...`);
                
                for (const action of actions) {
                    try {
                        let endpoint = '';
                        if (type === 'ventes') endpoint = '/api/sync/sorties';
                        else if (type === 'entrees') endpoint = '/api/sync/entrees';
                        else if (type === 'pertes') endpoint = '/api/sync/pertes';
                        
                        const cleanData = { ...action };
                        delete cleanData.id;
                        delete cleanData.synced;
                        delete cleanData.date;
                        delete cleanData.offline_id;
                        delete cleanData._endpoint;
                        delete cleanData.created_at;
                        
                        const response = await fetch(endpoint, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(cleanData)
                        });
                        
                        if (response.ok) {
                            await window.OfflineDB.removeSyncedAction(type, action.id);
                            totalSynced++;
                            console.log(`✅ ${type} ${action.id} synchronisée`);
                        }
                    } catch (error) {
                        console.error(`Erreur synchro ${type}:`, error);
                    }
                }
            } catch (error) {
                console.error(`Erreur pour ${type}:`, error);
            }
        }
        
        if (totalSynced > 0) {
            showOfflineNotification(`✅ ${totalSynced} action(s) synchronisée(s) !`, 'success');
            await updatePendingBadge();
            setTimeout(() => location.reload(), 1500);
        }
    } catch (error) {
        console.error('Erreur syncAllData:', error);
    }
}

// ──────────────────────────────────────────────────────────────
// INTERCEPTION DES FORMULAIRES - ULTRA SIMPLIFIÉE
// ──────────────────────────────────────────────────────────────

function setupOfflineForms() {
    try {
        console.log('🔧 Configuration des formulaires hors ligne...');
        
        // Trouver le formulaire de vente
        const form = document.getElementById('saleForm');
        if (!form) {
            console.log('ℹ️ Formulaire saleForm non trouvé (normal sur certaines pages)');
            return;
        }
        
        console.log('✅ Formulaire saleForm trouvé, configuration en cours...');
        
        // Éviter les doublons : supprimer l'ancien listener si présent
        const newForm = form.cloneNode(true);
        form.parentNode.replaceChild(newForm, form);
        
        // Ajouter l'écouteur d'événement
        newForm.addEventListener('submit', async function(e) {
            try {
                // Vérifier si on est hors ligne
                if (!navigator.onLine) {
                    e.preventDefault();
                    e.stopPropagation();
                    
                    console.log('📱 Vente hors ligne détectée');
                    
                    // Récupérer les données du formulaire
                    const formData = new FormData(this);
                    const data = {};
                    for (let [key, value] of formData.entries()) {
                        if (value && value.trim && value.trim() !== '') {
                            data[key] = value;
                        }
                    }
                    
                    // Vérifier les données
                    if (!data.produit_id || !data.quantite) {
                        showOfflineNotification('❌ Données manquantes', 'error');
                        return;
                    }
                    
                    // Ajouter les infos
                    data.employe_id = window.userId || 1;
                    data.date_sortie = new Date().toISOString();
                    data.client = data.client || '';
                    
                    // Sauvegarder
                    await window.OfflineDB.addOfflineAction('ventes', data, this.action);
                    showOfflineNotification('✅ Vente sauvegardée hors ligne', 'success');
                    
                    // Réinitialiser
                    this.reset();
                    const display = document.getElementById('selectedProductDisplay');
                    if (display) {
                        display.innerHTML = '<div style="opacity:0.7;">✅ Vente sauvegardée</div>';
                    }
                    document.querySelectorAll('.product-card').forEach(c => c.classList.remove('selected'));
                    await updatePendingBadge();
                }
            } catch (error) {
                console.error('Erreur vente hors ligne:', error);
                showOfflineNotification('❌ Erreur: ' + error.message, 'error');
            }
        });
        
        console.log('✅ Formulaire de vente configuré avec succès');
    } catch (error) {
        console.warn('⚠️ Erreur configuration (non-bloquante):', error.message);
    }
}

// ──────────────────────────────────────────────────────────────
// INDICATEUR DE CONNEXION
// ──────────────────────────────────────────────────────────────

function updateConnectionStatus() {
    try {
        let statusDiv = document.getElementById('connectionStatus');
        if (!statusDiv) {
            const div = document.createElement('div');
            div.id = 'connectionStatus';
            div.style.cssText = `
                position: fixed;
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                padding: 6px 16px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: bold;
                z-index: 1000;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                cursor: pointer;
                background: rgba(255,255,255,0.9);
                display: none;
            `;
            div.title = "Cliquez pour synchroniser";
            div.onclick = syncAllData;
            document.body.appendChild(div);
            statusDiv = div;
        }
        
        if (navigator.onLine) {
            statusDiv.textContent = '🟢 En ligne';
            statusDiv.style.background = '#d4edda';
            statusDiv.style.color = '#155724';
            statusDiv.style.display = 'block';
        } else {
            statusDiv.textContent = '🔴 Hors ligne';
            statusDiv.style.background = '#f8d7da';
            statusDiv.style.color = '#721c24';
            statusDiv.style.display = 'block';
        }
    } catch (error) {
        console.log('Erreur statut:', error);
    }
}

// ──────────────────────────────────────────────────────────────
// INITIALISATION - SÉCURISÉE
// ──────────────────────────────────────────────────────────────

async function initOfflineSync() {
    try {
        console.log('🔧 Initialisation du mode hors ligne...');
        
        // Vérifier que OfflineDB existe
        if (typeof window.OfflineDB === 'undefined') {
            console.warn('⚠️ OfflineDB non disponible');
            return;
        }
        
        // Ouvrir la base de données
        await window.OfflineDB.openDB();
        console.log('✅ Base de données hors ligne OK');
        
        // Configurer les formulaires
        setupOfflineForms();
        
        // Mettre à jour les indicateurs
        updateConnectionStatus();
        await updatePendingBadge();
        
        // Événements de connexion
        window.addEventListener('online', async () => {
            console.log('🌐 Connexion rétablie');
            updateConnectionStatus();
            await syncAllData();
            await updatePendingBadge();
        });
        
        window.addEventListener('offline', () => {
            console.log('📡 Mode hors ligne activé');
            updateConnectionStatus();
            showOfflineNotification('📡 Mode hors ligne activé', 'warning');
        });
        
        // Synchronisation initiale
        if (navigator.onLine) {
            console.log('🔄 Synchronisation initiale...');
            await syncAllData();
        } else {
            const count = await window.OfflineDB.countUnsyncedActions();
            if (count > 0) {
                showOfflineNotification(`📱 ${count} action(s) en attente de synchronisation`, 'info');
            } else {
                showOfflineNotification('📡 Mode hors ligne', 'warning');
            }
        }
        
        // Synchronisation périodique (toutes les 60 secondes)
        setInterval(async () => {
            if (navigator.onLine) {
                await syncAllData();
                await updatePendingBadge();
            }
        }, 60000);
        
        console.log('✅ Mode hors ligne initialisé avec succès');
    } catch (error) {
        // NE PAS BLOQUER L'APPLICATION
        console.warn('⚠️ Mode hors ligne désactivé:', error.message);
    }
}

// ──────────────────────────────────────────────────────────────
// EXPOSER LES FONCTIONS
// ──────────────────────────────────────────────────────────────

window.initOfflineSync = initOfflineSync;
window.syncAllData = syncAllData;
window.showOfflineNotification = showOfflineNotification;
window.updatePendingBadge = updatePendingBadge;
window.updateConnectionStatus = updateConnectionStatus;

console.log('📦 offline-sync.js chargé avec succès');

// Initialisation automatique après chargement
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(function() {
        try {
            if (typeof initOfflineSync === 'function') {
                initOfflineSync();
            }
        } catch (e) {
            console.warn('⚠️ Erreur auto-init offline:', e.message);
        }
    }, 1000);
});