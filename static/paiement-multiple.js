// ============================================================
// Paiement en plusieurs modes (ex : espèces + MoMo) — pages de vente
// Utilise les éléments : #pmToggle, #pmSplit, #pmRows, #pmAdd, #pmReste, #paiementsJson
// et le menu « mode de paiement » classique (#modePaiement ou #adminModePaiement).
// Les pages appellent : hitnaPaiement.setTotal(total), .valider(), .valeur()
// ============================================================
(function () {
    const MODES = [
        ['Espèces', '💵 Espèces'],
        ['MTN MobileMoney', '📱 MTN MobileMoney'],
        ['Moov MoovMoney', '📱 Moov MoovMoney'],
        ['Celtiis CeltiisCash', '📱 Celtiis CeltiisCash']
    ];
    let total = 0;
    let actif = false;
    const $ = id => document.getElementById(id);
    const fmt = n => new Intl.NumberFormat('fr-FR').format(n);

    function rows() { return Array.from(document.querySelectorAll('#pmRows .pm-row')); }
    function lire(row) {
        return { mode: row.querySelector('select').value, montant: Math.max(0, Math.round(parseFloat(row.querySelector('input').value) || 0)) };
    }
    function lignes() { return rows().map(lire); }
    function somme() { return lignes().reduce((s, l) => s + l.montant, 0); }
    function valeur() { return actif ? lignes().filter(l => l.montant > 0) : []; }

    function majAffichage() {
        const reste = total - somme();
        const el = $('pmReste');
        if (!el) return;
        if (reste === 0) { el.textContent = '✅ Total réparti : ' + fmt(total) + ' FCFA'; el.style.color = '#198754'; }
        else if (reste > 0) { el.textContent = 'Reste à répartir : ' + fmt(reste) + ' FCFA'; el.style.color = '#dc3545'; }
        else { el.textContent = 'Trop perçu : ' + fmt(-reste) + ' FCFA de plus que le total'; el.style.color = '#dc3545'; }
        const h = $('paiementsJson');
        if (h) h.value = actif ? JSON.stringify(valeur()) : '';
    }

    // Avec exactement 2 modes, l'autre montant se complète tout seul (cas courant : espèces + MoMo)
    function completerAutre(source) {
        const r = rows();
        if (r.length !== 2) return;
        const autre = r[0] === source ? r[1] : r[0];
        const saisi = lire(source).montant;
        autre.querySelector('input').value = Math.max(0, total - saisi);
    }

    function ajouterLigne(modeInitial) {
        const conteneur = $('pmRows');
        const row = document.createElement('div');
        row.className = 'pm-row';
        row.style.cssText = 'display:flex; gap:8px; margin-bottom:8px; align-items:center;';

        const sel = document.createElement('select');
        sel.style.cssText = 'flex:2; padding:10px; border:2px solid #e0e0e0; border-radius:8px; min-width:0;';
        MODES.forEach(([val, lib]) => { const o = document.createElement('option'); o.value = val; o.textContent = lib; sel.appendChild(o); });
        sel.value = modeInitial;
        sel.addEventListener('change', majAffichage);

        const inp = document.createElement('input');
        inp.type = 'number'; inp.min = '0'; inp.step = '1'; inp.inputMode = 'numeric'; inp.placeholder = 'Montant FCFA';
        inp.style.cssText = 'flex:2; padding:10px; border:2px solid #e0e0e0; border-radius:8px; min-width:0;';
        inp.addEventListener('input', () => { completerAutre(row); majAffichage(); });

        const del = document.createElement('button');
        del.type = 'button'; del.textContent = '✕';
        del.style.cssText = 'background:#dc3545; padding:8px 12px;';
        del.addEventListener('click', () => { if (rows().length > 2) { row.remove(); majBoutons(); majAffichage(); } });

        row.append(sel, inp, del);
        conteneur.appendChild(row);
        majBoutons();
        return row;
    }
    function majBoutons() {
        const n = rows().length;
        rows().forEach(r => { r.querySelector('button').style.visibility = n > 2 ? 'visible' : 'hidden'; });
        const add = $('pmAdd'); if (add) add.style.display = n >= MODES.length ? 'none' : '';
    }

    function activer(oui) {
        actif = oui;
        const simple = $('modePaiement') || $('adminModePaiement');
        if (simple) simple.style.display = oui ? 'none' : '';
        $('pmSplit').style.display = oui ? 'block' : 'none';
        if (oui && rows().length === 0) {
            const choisi = simple ? simple.value : 'Espèces';
            const second = MODES.map(m => m[0]).find(m => m !== choisi);
            ajouterLigne(choisi);
            ajouterLigne(second);
        }
        majAffichage();
    }

    function setTotal(t) {
        total = Math.max(0, Math.round(Number(t) || 0));
        if (actif) {
            const r = rows();
            if (r.length === 2) {
                const a = lire(r[0]).montant;
                if (a > 0) r[1].querySelector('input').value = Math.max(0, total - a);
            }
            majAffichage();
        }
    }

    function valider() {
        if (!actif) return true;
        const parts = lignes().filter(l => l.montant > 0);
        const s = somme();
        if (parts.length === 0 || s !== total) {
            alert('Le total des paiements (' + fmt(s) + ' FCFA) doit être égal au total de la vente (' + fmt(total) + ' FCFA).');
            return false;
        }
        majAffichage();
        return true;
    }

    document.addEventListener('DOMContentLoaded', function () {
        const t = $('pmToggle');
        if (!t) return;
        t.addEventListener('change', () => activer(t.checked));
        const add = $('pmAdd');
        if (add) add.addEventListener('click', () => {
            const utilises = lignes().map(l => l.mode);
            const libre = MODES.map(m => m[0]).find(m => !utilises.includes(m));
            if (libre) { ajouterLigne(libre); majAffichage(); }
        });
    });

    window.hitnaPaiement = { setTotal: setTotal, valider: valider, valeur: valeur };
})();
