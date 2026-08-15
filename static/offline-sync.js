// ============================================================
// offline-sync.js — Orchestration de la synchronisation hors ligne
// Appelé depuis base.html via initOfflineSync()
// ============================================================

let hitnaSyncing = false;

function hitnaShowBanner(message, kind) {
    let banner = document.getElementById('hitnaOfflineBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'hitnaOfflineBanner';
        banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:20000;' +
            'padding:10px 15px;text-align:center;font-weight:bold;font-size:13px;' +
            'transition:all 0.3s;';
        document.body.prepend(banner);
    }
    const colors = {
        offline: 'background:#ffc107;color:#333;',
        syncing: 'background:#17a2b8;color:white;',
        synced: 'background:#28a745;color:white;',
        pending: 'background:#fd7e14;color:white;'
    };
    banner.style.cssText += colors[kind] || colors.offline;
    banner.textContent = message;
    banner.style.display = 'block';
}

function hitnaHideBanner() {
    const banner = document.getElementById('hitnaOfflineBanner');
    if (banner) banner.style.display = 'none';
}

async function hitnaUpdateBannerState() {
    try {
        const count = await window.HitnaDB.queueCount();
        if (!navigator.onLine) {
            hitnaShowBanner(
                count > 0
                    ? `📡 Hors ligne — ${count} action(s) en attente de synchronisation`
                    : '📡 Mode hors ligne activé',
                'offline'
            );
        } else if (count > 0) {
            hitnaShowBanner(`⏳ ${count} action(s) en attente d'envoi...`, 'pending');
        } else {
            hitnaHideBanner();
        }
    } catch (e) {
        console.warn('⚠️ hitnaUpdateBannerState:', e.message);
    }
}

// ── ENREGISTRER UNE ACTION (à appeler depuis les pages vente/entrées/pertes) ──
// Retourne true si l'action a été mise en file d'attente hors ligne (le formulaire
// ne doit alors PAS être soumis normalement), false si en ligne (soumission normale).
async function hitnaQueueIfOffline(type, payload) {
    if (navigator.onLine) return false;
    try {
        await window.HitnaDB.queueAction(type, payload);
        await hitnaUpdateBannerState();
        return true;
    } catch (e) {
        console.error('❌ Erreur mise en file d\'attente:', e);
        alert('Erreur: impossible d\'enregistrer l\'action hors ligne sur cet appareil (' + e.message + ')');
        return false;
    }
}

// ── SYNCHRONISATION AU RETOUR DU RÉSEAU ─────────────────────────
async function hitnaSyncQueue() {
    if (hitnaSyncing || !navigator.onLine) return;
    hitnaSyncing = true;
    try {
        const queue = await window.HitnaDB.getQueue();
        if (queue.length === 0) {
            hitnaHideBanner();
            hitnaSyncing = false;
            return;
        }
        hitnaShowBanner(`🔄 Synchronisation de ${queue.length} action(s)...`, 'syncing');

        const response = await fetch('/api/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ actions: queue })
        });

        if (!response.ok) throw new Error('Réponse serveur invalide');
        const result = await response.json();

        let nbOk = 0;
        for (const r of (result.results || [])) {
            if (r.status === 'ok' || r.status === 'error_definitif') {
                // succès, ou erreur définitive (ex: produit supprimé) → on retire de la file
                // pour ne pas bloquer indéfiniment la synchronisation
                await window.HitnaDB.removeFromQueue(r.client_id);
                if (r.status === 'ok') nbOk++;
                if (r.status === 'error_definitif') {
                    console.warn('⚠️ Action abandonnée:', r.client_id, r.message);
                }
            }
            // si status === 'retry' (ex: pas encore reconnecté côté serveur), on la garde en file
        }

        const restant = await window.HitnaDB.queueCount();
        if (restant === 0) {
            hitnaShowBanner(`✅ ${nbOk} action(s) synchronisée(s) avec succès`, 'synced');
            setTimeout(hitnaHideBanner, 4000);
        } else {
            hitnaShowBanner(`⚠️ ${restant} action(s) encore en attente`, 'pending');
        }
    } catch (e) {
        console.warn('⚠️ Synchronisation impossible pour le moment:', e.message);
        await hitnaUpdateBannerState();
    } finally {
        hitnaSyncing = false;
    }
}

// ── CACHE DES PRODUITS POUR CONSULTATION HORS LIGNE ─────────────
async function hitnaRefreshProduitsCache() {
    if (!navigator.onLine) return;
    try {
        const res = await fetch('/api/produits');
        if (!res.ok) return;
        const data = await res.json();
        if (data && data.produits) {
            await window.HitnaDB.saveProduitsCache(data.produits);
        }
    } catch (e) {
        console.warn('⚠️ Cache produits non rafraîchi:', e.message);
    }
}

// ── INITIALISATION (appelée depuis base.html) ───────────────────
function initOfflineSync() {
    if (!window.HitnaDB) {
        console.warn('ℹ️ HitnaDB non chargé, mode hors ligne désactivé');
        return;
    }

    hitnaUpdateBannerState();
    hitnaRefreshProduitsCache();

    window.addEventListener('online', () => {
        console.log('🌐 Connexion rétablie');
        hitnaSyncQueue();
        hitnaRefreshProduitsCache();
    });

    window.addEventListener('offline', () => {
        console.log('📡 Connexion perdue — mode hors ligne activé');
        hitnaUpdateBannerState();
    });

    // Tentative de synchronisation périodique (au cas où le navigateur
    // ne déclenche pas fiablement l'événement 'online')
    setInterval(() => {
        if (navigator.onLine) hitnaSyncQueue();
    }, 20000);

    // Synchronisation immédiate si des actions sont déjà en attente au chargement
    if (navigator.onLine) hitnaSyncQueue();
}

window.hitnaQueueIfOffline = hitnaQueueIfOffline;
window.initOfflineSync = initOfflineSync;