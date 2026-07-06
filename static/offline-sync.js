// static/offline-sync.js
// Gestion de la synchronisation hors ligne - VERSION CORRIGÉE

// ──────────────────────────────────────────────────────────────
// SYNCHRONISATION AVEC LE SERVEUR
// ──────────────────────────────────────────────────────────────

async function syncAllData() {
    if (!navigator.onLine) {
        console.log('📡 Hors ligne - Synchronisation différée');
        return;
    }
    
    const types = ['ventes', 'entrees', 'pertes'];
    let totalSynced = 0;
    let errors = [];
    
    for (const type of types) {
        try {
            const actions = await window.OfflineDB.getUnsyncedActions(type);
            
            if (actions.length === 0) continue;
            
            console.log(`🔄 Synchronisation de ${actions.length} ${type}...`);
            
            for (const action of actions) {
                try {
                    // Déterminer l'endpoint
                    let endpoint = '';
                    if (type === 'ventes') endpoint = '/api/sync/sorties';
                    else if (type === 'entrees') endpoint = '/api/sync/entrees';
                    else if (type === 'pertes') endpoint = '/api/sync/pertes';
                    
                    // Nettoyer les données avant envoi
                    const cleanData = { ...action };
                    delete cleanData.id;
                    delete cleanData.synced;
                    delete cleanData.date;
                    delete cleanData.offline_id;
                    delete cleanData._endpoint;
                    
                    // Envoyer au serveur
                    const response = await fetch(endpoint, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify(cleanData)
                    });
                    
                    const result = await response.json();
                    
                    if (response.ok && result.success) {
                        await window.OfflineDB.removeSyncedAction(type, action.id);
                        totalSynced++;
                        console.log(`✅ ${type} ${action.id} synchronisée`);
                    } else {
                        const errorMsg = result.error || `Erreur ${response.status}`;
                        errors.push(`${type} #${action.id}: ${errorMsg}`);
                        console.error(`❌ Erreur synchro ${type} ${action.id}: ${errorMsg}`);
                    }
                } catch (error) {
                    errors.push(`${type} #${action.id}: ${error.message}`);
                    console.error(`❌ Erreur réseau pour ${type} ${action.id}: ${error.message}`);
                }
            }
        } catch (error) {
            console.error(`❌ Erreur pour ${type}: ${error.message}`);
        }
    }
    
    // Afficher le résultat
    if (totalSynced > 0) {
        showOfflineNotification(`✅ ${totalSynced} action(s) synchronisée(s) !`, 'success');
        // Mettre à jour les badges
        await updatePendingBadge();
        // Recharger la page après un court délai
        setTimeout(() => {
            location.reload();
        }, 2000);
    } else if (errors.length > 0) {
        showOfflineNotification(`⚠️ ${errors.length} erreur(s) de synchronisation`, 'error');
    } else {
        const totalPending = await window.OfflineDB.countUnsyncedActions();
        if (totalPending > 0) {
            showOfflineNotification(`📱 ${totalPending} action(s) en attente de synchronisation`, 'info');
        }
    }
}

// ──────────────────────────────────────────────────────────────
// INTERCEPTION DES FORMULAIRES - CORRIGÉE
// ──────────────────────────────────────────────────────────────

function setupOfflineForms() {
    // ── Formulaire de vente (employé et admin) ──
    const saleForms = document.querySelectorAll('#saleForm, form[action*="/vente"], form[action*="/admin/ventes"]');
    saleForms.forEach(form => {
        form.addEventListener('submit', async function(e) {
            if (!navigator.onLine) {
                e.preventDefault();
                e.stopPropagation();
                
                const formData = new FormData(this);
                const data = {};
                for (let [key, value] of formData.entries()) {
                    if (value && value.trim && value.trim() !== '') {
                        data[key] = value;
                    }
                }
                
                // Ajouter les informations nécessaires
                data.employe_id = window.userId || 1;
                data.date_sortie = new Date().toISOString();
                
                // S'assurer que le client est défini
                if (!data.client) data.client = '';
                
                try {
                    await window.OfflineDB.addOfflineAction('ventes', data, this.action);
                    showOfflineNotification('📱 Vente sauvegardée hors ligne ✅', 'success');
                    
                    // Réinitialiser le formulaire
                    this.reset();
                    
                    // Réinitialiser l'affichage du produit sélectionné
                    const display = document.getElementById('selectedProductDisplay');
                    if (display) {
                        display.innerHTML = '<div style="opacity: 0.7; color: #28a745;">✅ Vente sauvegardée hors ligne</div>';
                    }
                    
                    // Désélectionner les produits
                    document.querySelectorAll('.product-card').forEach(c => c.classList.remove('selected'));
                    
                    // Mettre à jour le badge
                    await updatePendingBadge();
                } catch (error) {
                    showOfflineNotification('❌ Erreur lors de la sauvegarde: ' + error.message, 'error');
                }
            }
        });
    });
    
    // ── Formulaire d'entrée de stock ──
    const entreForms = document.querySelectorAll('form[action*="/entrees/ajouter"]');
    entreForms.forEach(form => {
        form.addEventListener('submit', async function(e) {
            if (!navigator.onLine) {
                e.preventDefault();
                e.stopPropagation();
                
                const formData = new FormData(this);
                const data = {};
                for (let [key, value] of formData.entries()) {
                    if (value && value.trim && value.trim() !== '') {
                        data[key] = value;
                    }
                }
                
                data.employe_id = window.userId || 1;
                data.date_entree = new Date().toISOString();
                
                try {
                    await window.OfflineDB.addOfflineAction('entrees', data, this.action);
                    showOfflineNotification('📥 Entrée sauvegardée hors ligne ✅', 'success');
                    
                    this.reset();
                    
                    const display = document.getElementById('selectedProductDisplay');
                    if (display) display.style.display = 'none';
                    
                    // Réinitialiser la sélection
                    document.getElementById('selectedProductId').value = '';
                    document.getElementById('submitBtn').disabled = true;
                    
                    await updatePendingBadge();
                } catch (error) {
                    showOfflineNotification('❌ Erreur: ' + error.message, 'error');
                }
            }
        });
    });
    
    // ── Formulaire des pertes ──
    const pertesForms = document.querySelectorAll('.form-perte, form[action*="/pertes/ajouter"]');
    pertesForms.forEach(form => {
        form.addEventListener('submit', async function(e) {
            if (!navigator.onLine) {
                e.preventDefault();
                e.stopPropagation();
                
                const formData = new FormData(this);
                const data = {};
                for (let [key, value] of formData.entries()) {
                    if (value && value.trim && value.trim() !== '') {
                        data[key] = value;
                    }
                }
                
                data.employe_id = window.userId || 1;
                data.date_perte = new Date().toISOString();
                
                try {
                    await window.OfflineDB.addOfflineAction('pertes', data, this.action);
                    showOfflineNotification('⚠️ Perte sauvegardée hors ligne ✅', 'success');
                    this.reset();
                    await updatePendingBadge();
                } catch (error) {
                    showOfflineNotification('❌ Erreur: ' + error.message, 'error');
                }
            }
        });
    });
}

// ──────────────────────────────────────────────────────────────
// INDICATEUR DE CONNEXION - AMÉLIORÉ
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
            padding: 8px 14px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            z-index: 1000;
            transition: all 0.3s;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            cursor: pointer;
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
        statusDiv.style.border = '1px solid #28a745';
    } else {
        statusDiv.textContent = '🔴 Hors ligne';
        statusDiv.style.background = '#f8d7da';
        statusDiv.style.color = '#721c24';
        statusDiv.style.border = '1px solid #dc3545';
    }
}

async function updatePendingBadge() {
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
    
    try {
        const count = await window.OfflineDB.countUnsyncedActions();
        if (count > 0) {
            badge.style.display = 'block';
            badge.textContent = `📤 ${count} action(s) en attente`;
        } else {
            badge.style.display = 'none';
        }
    } catch (error) {
        console.error('Erreur lors du comptage des actions:', error);
        badge.style.display = 'none';
    }
}

// ──────────────────────────────────────────────────────────────
// NOTIFICATION VISUELLE - AMÉLIORÉE
// ──────────────────────────────────────────────────────────────

function showOfflineNotification(message, type = 'info') {
    // Supprimer les notifications existantes
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
        animation: slideUp 0.3s ease-out;
        border: 1px solid rgba(255,255,255,0.2);
        min-width: 200px;
    `;
    notif.textContent = message;
    document.body.appendChild(notif);
    
    // Ajouter l'animation CSS si elle n'existe pas
    if (!document.getElementById('offlineAnimationStyle')) {
        const style = document.createElement('style');
        style.id = 'offlineAnimationStyle';
        style.textContent = `
            @keyframes slideUp {
                from { transform: translateX(-50%) translateY(100%); opacity: 0; }
                to { transform: translateX(-50%) translateY(0); opacity: 1; }
            }
            .offline-notification {
                animation: slideUp 0.4s ease-out;
            }
        `;
        document.head.appendChild(style);
    }
    
    // Auto-fermeture
    setTimeout(() => {
        notif.style.transition = 'opacity 0.5s, transform 0.5s';
        notif.style.opacity = '0';
        notif.style.transform = 'translateX(-50%) translateY(20px)';
        setTimeout(() => notif.remove(), 500);
    }, 5000);
    
    // Fermeture au clic
    notif.onclick = () => {
        notif.style.transition = 'opacity 0.3s';
        notif.style.opacity = '0';
        setTimeout(() => notif.remove(), 300);
    };
}

// ──────────────────────────────────────────────────────────────
// INITIALISATION - CORRIGÉE
// ──────────────────────────────────────────────────────────────

async function initOfflineSync() {
    try {
        // Ouvrir la base de données
        await window.OfflineDB.openDB();
        console.log('✅ Base de données hors ligne ouverte');
        
        // Configurer les formulaires
        setupOfflineForms();
        console.log('✅ Formulaires configurés pour le mode hors ligne');
        
        // Mettre à jour le statut de connexion
        updateConnectionStatus();
        await updatePendingBadge();
        console.log('✅ Interface hors ligne mise à jour');
        
        // Synchroniser quand la connexion revient
        window.addEventListener('online', async () => {
            console.log('🌐 Connexion rétablie');
            updateConnectionStatus();
            showOfflineNotification('🌐 Connexion rétablie, synchronisation...', 'info');
            await syncAllData();
            await updatePendingBadge();
            // Recharger les données
            if (typeof loadData === 'function') {
                loadData();
            }
        });
        
        window.addEventListener('offline', () => {
            console.log('📡 Mode hors ligne activé');
            updateConnectionStatus();
            showOfflineNotification('📡 Mode hors ligne activé', 'warning');
        });
        
        // Synchronisation au chargement
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
        
        // Synchronisation périodique (toutes les 30 secondes)
        setInterval(async () => {
            if (navigator.onLine) {
                await syncAllData();
                await updatePendingBadge();
            }
        }, 30000);
        
        console.log('✅ Mode hors ligne initialisé avec succès');
    } catch (error) {
        console.error('❌ Erreur lors de l\'initialisation hors ligne:', error);
        showOfflineNotification('⚠️ Erreur d\'initialisation du mode hors ligne', 'error');
    }
}

// ──────────────────────────────────────────────────────────────
// EXPOSER LES FONCTIONS
// ──────────────────────────────────────────────────────────────

window.initOfflineSync = initOfflineSync;
window.syncAllData = syncAllData;
window.setupOfflineForms = setupOfflineForms;
window.showOfflineNotification = showOfflineNotification;
window.updatePendingBadge = updatePendingBadge;
window.updateConnectionStatus = updateConnectionStatus;

// Initialisation automatique
document.addEventListener('DOMContentLoaded', function() {
    // Attendre que la DB soit prête
    setTimeout(() => {
        if (typeof initOfflineSync === 'function') {
            initOfflineSync();
        }
    }, 500);
});