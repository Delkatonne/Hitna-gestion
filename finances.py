# ══════════════════════════════════════════════════════════════════════════
# finances.py — module séparé d'app.py (même principe que ajouts.py)
#   • API JSON du dashboard et des statistiques  (/api/dashboard, /api/stats)
#   • Dépenses (achat de produits, renouvellement de stock, transport...)
#   • Trésorerie interne (comptes, entrées/sorties, virements entre comptes)
# L'affichage est assuré par React (static/js/page-*.js) ; ce module ne fait
# que servir les pages-coquilles et les données.
# Chargé par app.py avec :  import finances
# ══════════════════════════════════════════════════════════════════════════
import uuid
from datetime import datetime, date, timedelta

from flask import request, session, jsonify, render_template, redirect

from app import (app, q1, qall, get_db, release_db, clear_cache,
                 boutique_active, boutique_filtre_sql)

CATEGORIES_DEPENSES = [
    'Achat de nouveaux produits',
    'Renouvellement de stock',
    'Transport et livraison',
    'Emballages et fournitures',
    'Équipement et matériel',
    'Réparation et entretien',
    'Publicité et communication',
    'Autre',
]
MODES_PAIEMENT_DEPENSE = ['Espèces', 'MTN MobileMoney', 'Moov MoovMoney',
                          'Celtiis CeltiisCash', 'Virement bancaire', 'Chèque']
TYPES_COMPTE = ['Caisse', 'Banque', 'Mobile Money', 'Autre']

# Ventes courantes + ventes archivées, vues comme une seule table (les ventes
# passent dans archive_ventes lors de l'archivage hebdomadaire).
_VENTES_U = """
    SELECT s.date_sortie::timestamp AS d, s.total AS total, s.quantite AS qte,
           COALESCE(p.nom, 'Produit supprimé') AS produit,
           COALESCE(s.mode_paiement, 'Espèces') AS mode,
           s.boutique_id AS boutique_id, s.groupe_vente AS groupe,
           COALESCE(s.client, '') AS client, COALESCE(u.nom, '') AS vendeur
    FROM sorties s
    LEFT JOIN produits p ON p.id = s.produit_id
    LEFT JOIN users u ON u.id = s.employe_id
    UNION ALL
    SELECT a.date_vente::timestamp, a.total, a.quantite,
           COALESCE(a.produit_nom, ''), COALESCE(a.mode_paiement, 'Espèces'),
           a.boutique_id, a.groupe_vente, COALESCE(a.client, ''),
           COALESCE(a.employe_nom, '')
    FROM archive_ventes a
"""
_ACHATS_U = """
    SELECT date_entree::timestamp AS d, total, boutique_id FROM entrees
    UNION ALL
    SELECT date_entree::timestamp, total, boutique_id FROM archive_entrees
"""
_PERTES_U = """
    SELECT date_perte::timestamp AS d, total, boutique_id FROM pertes
    UNION ALL
    SELECT date_perte::timestamp, total, boutique_id FROM archive_pertes
"""

NOMS_MOIS_COURTS = ['Janv.', 'Févr.', 'Mars', 'Avr.', 'Mai', 'Juin',
                    'Juil.', 'Août', 'Sept.', 'Oct.', 'Nov.', 'Déc.']


# ──────────────────────────────────────────────────────────────
# OUTILS
# ──────────────────────────────────────────────────────────────
def _admin():
    return session.get('role') == 'admin'


def _non_autorise():
    return jsonify({'erreur': 'Accès réservé à l’administrateur'}), 401


def _err(message, code=400):
    return jsonify({'erreur': message}), code


def _n(valeur):
    """Nombre sûr pour le JSON (Decimal/None -> float)."""
    try:
        return round(float(valeur or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _date_ou(texte, defaut):
    """Texte 'aaaa-mm-jj' -> date ; sinon la valeur par défaut."""
    try:
        return datetime.strptime(str(texte)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return defaut


def _variation(actuel, precedent):
    if not precedent:
        return None
    return round((actuel - precedent) / precedent * 100, 1)


def _mois_cle(d):
    return d.strftime('%Y-%m')


def _mois_precedent(d):
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def _liste_mois(nb, fin=None):
    """Les nb derniers mois (clés 'aaaa-mm'), du plus ancien au plus récent."""
    cur = (fin or date.today()).replace(day=1)
    cles = []
    for _ in range(nb):
        cles.append(_mois_cle(cur))
        cur = _mois_precedent(cur)
    return list(reversed(cles))


def _libelle_mois(cle):
    a, m = cle.split('-')
    return f"{NOMS_MOIS_COURTS[int(m) - 1]} {a[2:]}"


def _transaction(fn):
    """Exécute fn(cur) dans une seule transaction (tout ou rien)."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        resultat = fn(cur)
        conn.commit()
        cur.close()
        clear_cache()
        return resultat
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn:
            release_db(conn)


# ──────────────────────────────────────────────────────────────
# TABLES
# ──────────────────────────────────────────────────────────────
def init_db_finances():
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS comptes_tresorerie (
            id SERIAL PRIMARY KEY,
            nom TEXT NOT NULL,
            type TEXT DEFAULT 'Caisse',
            solde_initial NUMERIC(14,2) DEFAULT 0,
            boutique_id INTEGER,
            actif INTEGER DEFAULT 1,
            date_creation TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS depenses (
            id SERIAL PRIMARY KEY,
            date_depense TEXT,
            categorie TEXT,
            motif TEXT,
            montant NUMERIC(14,2),
            fournisseur TEXT DEFAULT '',
            mode_paiement TEXT DEFAULT 'Espèces',
            compte_id INTEGER,
            reference TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            boutique_id INTEGER,
            employe_id INTEGER,
            date_creation TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS mouvements_tresorerie (
            id SERIAL PRIMARY KEY,
            compte_id INTEGER,
            date_mouvement TEXT,
            type TEXT,
            montant NUMERIC(14,2),
            libelle TEXT,
            source TEXT DEFAULT 'manuel',
            reference_id INTEGER,
            transfert_id TEXT,
            boutique_id INTEGER,
            employe_id INTEGER,
            date_creation TEXT)''')
        conn.commit()
        c.close()
    except Exception as e:
        print(f"Erreur init_db_finances: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            release_db(conn)


# ──────────────────────────────────────────────────────────────
# PAGES (coquilles React)
# ──────────────────────────────────────────────────────────────
@app.route('/admin/depenses')
def page_depenses():
    if not _admin():
        return redirect('/login')
    return render_template('admin_depenses.html')


@app.route('/admin/tresorerie')
def page_tresorerie():
    if not _admin():
        return redirect('/login')
    return render_template('admin_tresorerie.html')


# ──────────────────────────────────────────────────────────────
# API — DASHBOARD
# ──────────────────────────────────────────────────────────────
def _soldes_comptes():
    """Liste des comptes actifs (dans le périmètre boutique) avec leur solde."""
    bq, bp = boutique_filtre_sql('c.boutique_id')
    lignes = qall(f'''
        SELECT c.id, c.nom, c.type, c.solde_initial, c.boutique_id,
               COALESCE(b.nom, ''),
               COALESCE(SUM(CASE WHEN m.type = 'entree' THEN m.montant ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN m.type = 'sortie' THEN m.montant ELSE 0 END), 0)
        FROM comptes_tresorerie c
        LEFT JOIN mouvements_tresorerie m ON m.compte_id = c.id
        LEFT JOIN boutiques b ON b.id = c.boutique_id
        WHERE COALESCE(c.actif, 1) = 1{bq}
        GROUP BY c.id, c.nom, c.type, c.solde_initial, c.boutique_id, b.nom
        ORDER BY c.nom''', bp)
    comptes = []
    for r in lignes:
        initial, entrees, sorties = _n(r[3]), _n(r[6]), _n(r[7])
        comptes.append({
            'id': r[0], 'nom': r[1], 'type': r[2] or 'Caisse',
            'solde_initial': initial, 'boutique_id': r[4], 'boutique': r[5],
            'entrees': entrees, 'sorties': sorties,
            'solde': round(initial + entrees - sorties, 2),
        })
    return comptes


@app.route('/api/dashboard')
def api_dashboard():
    if not _admin():
        return _non_autorise()
    try:
        bq, bp = boutique_filtre_sql('boutique_id')
        aujourdhui = date.today()
        demain = aujourdhui + timedelta(days=1)
        hier = aujourdhui - timedelta(days=1)
        debut_mois = aujourdhui.replace(day=1)
        debut_mois_prec = _mois_precedent(aujourdhui)
        meme_periode_fin = min(debut_mois_prec + timedelta(days=aujourdhui.day), debut_mois)
        debut_14 = aujourdhui - timedelta(days=13)
        debut_30 = aujourdhui - timedelta(days=29)
        iso = lambda d: d.isoformat()

        def ventes(debut, fin_exclue):
            r = q1(f'''SELECT COALESCE(SUM(total), 0), COUNT(DISTINCT groupe)
                       FROM ({_VENTES_U}) v
                       WHERE v.d >= ?::date AND v.d < ?::date{bq}''',
                   (iso(debut), iso(fin_exclue)) + bp)
            return (_n(r[0]), int(r[1] or 0)) if r else (0.0, 0)

        def somme(sql_from, debut, fin_exclue, colonne_date='d', colonne_montant='total'):
            r = q1(f'''SELECT COALESCE(SUM({colonne_montant}), 0) FROM {sql_from}
                       WHERE {colonne_date}::date >= ?::date AND {colonne_date}::date < ?::date{bq}''',
                   (iso(debut), iso(fin_exclue)) + bp)
            return _n(r[0]) if r else 0.0

        ca_jour, nb_jour = ventes(aujourdhui, demain)
        ca_hier, _ = ventes(hier, aujourdhui)
        ca_mois, nb_mois = ventes(debut_mois, demain)
        ca_mois_prec, _ = ventes(debut_mois_prec, meme_periode_fin)

        achats_mois = somme(f'({_ACHATS_U}) a', debut_mois, demain)
        pertes_mois = somme(f'({_PERTES_U}) p', debut_mois, demain)
        charges_mois = somme('charges', debut_mois, demain, 'date_charge', 'montant')
        depenses_mois = somme('depenses', debut_mois, demain, 'date_depense', 'montant')
        benefice_net = ca_mois - achats_mois - charges_mois

        comptes = _soldes_comptes()
        tresorerie_totale = round(sum(c['solde'] for c in comptes), 2)

        bq_c, bp_c = boutique_filtre_sql('c.boutique_id')
        credits = q1(f'''
            SELECT COALESCE(SUM(l.t), 0) - COALESCE(SUM(p.t), 0), COUNT(*)
            FROM credits c
            LEFT JOIN (SELECT credit_id, SUM(total) t FROM credits_lignes GROUP BY credit_id) l ON l.credit_id = c.id
            LEFT JOIN (SELECT credit_id, SUM(montant) t FROM credits_paiements GROUP BY credit_id) p ON p.credit_id = c.id
            WHERE c.statut = 'en_cours'{bq_c}''', bp_c)
        credits_en_cours = _n(credits[0]) if credits else 0.0
        nb_credits = int(credits[1] or 0) if credits else 0

        bq_p, bp_p = boutique_filtre_sql('boutique_id')
        stock = q1(f'''SELECT COUNT(*), COALESCE(SUM(stock * prix), 0),
                              COALESCE(SUM(CASE WHEN stock <= stock_min THEN 1 ELSE 0 END), 0),
                              COALESCE(SUM(CASE WHEN stock <= 0 THEN 1 ELSE 0 END), 0)
                       FROM produits WHERE COALESCE(actif, 1) = 1{bq_p}''', bp_p) or (0, 0, 0, 0)
        stock_bas = qall(f'''SELECT nom, stock, stock_min FROM produits
                             WHERE COALESCE(actif, 1) = 1 AND stock <= stock_min{bq_p}
                             ORDER BY stock ASC LIMIT 8''', bp_p)

        # Ventes des 14 derniers jours (jours sans vente = 0)
        rows = qall(f'''SELECT v.d::date, COALESCE(SUM(total), 0), COUNT(DISTINCT groupe)
                        FROM ({_VENTES_U}) v WHERE v.d >= ?::date{bq}
                        GROUP BY 1 ORDER BY 1''', (iso(debut_14),) + bp)
        par_jour = {str(r[0]): (_n(r[1]), int(r[2] or 0)) for r in rows}
        serie_14 = []
        for i in range(14):
            j = debut_14 + timedelta(days=i)
            t, nb = par_jour.get(iso(j), (0.0, 0))
            serie_14.append({'date': iso(j), 'total': t, 'nb': nb})

        rows = qall(f'''SELECT EXTRACT(HOUR FROM v.d)::int, COALESCE(SUM(total), 0)
                        FROM ({_VENTES_U}) v
                        WHERE v.d >= ?::date AND v.d < ?::date{bq}
                        GROUP BY 1 ORDER BY 1''', (iso(aujourdhui), iso(demain)) + bp)
        par_heure = {int(r[0]): _n(r[1]) for r in rows}
        serie_heures = [{'heure': h, 'total': par_heure.get(h, 0.0)} for h in range(24)]

        top = qall(f'''SELECT produit, COALESCE(SUM(qte), 0), COALESCE(SUM(total), 0)
                       FROM ({_VENTES_U}) v WHERE v.d >= ?::date{bq}
                       GROUP BY produit ORDER BY 3 DESC LIMIT 6''', (iso(debut_30),) + bp)
        modes = qall(f'''SELECT mode, COALESCE(SUM(total), 0)
                         FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                         GROUP BY mode ORDER BY 2 DESC''', (iso(debut_mois), iso(demain)) + bp)
        vendeurs = qall(f'''SELECT vendeur, COUNT(DISTINCT groupe), COALESCE(SUM(total), 0)
                            FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                            GROUP BY vendeur ORDER BY 3 DESC LIMIT 6''', (iso(aujourdhui), iso(demain)) + bp)
        dernieres = qall(f'''SELECT groupe, MAX(d), COALESCE(SUM(total), 0), MAX(client), MAX(vendeur), MAX(mode)
                             FROM ({_VENTES_U}) v
                             WHERE v.d >= ?::date AND groupe IS NOT NULL{bq}
                             GROUP BY groupe ORDER BY MAX(d) DESC LIMIT 8''',
                         (iso(aujourdhui - timedelta(days=6)),) + bp)
        dep_cat = qall(f'''SELECT COALESCE(categorie, 'Autre'), COALESCE(SUM(montant), 0)
                           FROM depenses WHERE date_depense::date >= ?::date AND date_depense::date < ?::date{bq}
                           GROUP BY 1 ORDER BY 2 DESC''', (iso(debut_mois), iso(demain)) + bp)

        nb_cmd = q1("SELECT COUNT(*) FROM commandes WHERE statut = 'nouvelle'")
        nb_msg = q1("SELECT COUNT(*) FROM messages_contact WHERE statut = 'non_lu'")

        return jsonify({
            'date': iso(aujourdhui),
            'kpis': {
                'ventes_jour': ca_jour, 'nb_ventes_jour': nb_jour,
                'variation_jour': _variation(ca_jour, ca_hier),
                'ventes_mois': ca_mois, 'nb_ventes_mois': nb_mois,
                'variation_mois': _variation(ca_mois, ca_mois_prec),
                'achats_mois': achats_mois, 'charges_mois': charges_mois,
                'depenses_mois': depenses_mois, 'pertes_mois': pertes_mois,
                'benefice_net_mois': round(benefice_net, 2),
                'tresorerie_totale': tresorerie_totale,
                'credits_en_cours': credits_en_cours, 'nb_credits': nb_credits,
                'nb_produits': int(stock[0] or 0), 'valeur_stock': _n(stock[1]),
                'nb_stock_bas': int(stock[2] or 0), 'nb_ruptures': int(stock[3] or 0),
                'commandes_nouvelles': int(nb_cmd[0]) if nb_cmd else 0,
                'messages_non_lus': int(nb_msg[0]) if nb_msg else 0,
            },
            'ventes_14_jours': serie_14,
            'ventes_par_heure': serie_heures,
            'top_produits': [{'nom': r[0], 'quantite': _n(r[1]), 'total': _n(r[2])} for r in top],
            'modes_paiement': [{'mode': r[0], 'total': _n(r[1])} for r in modes],
            'vendeurs': [{'nom': r[0] or '-', 'nb': int(r[1] or 0), 'total': _n(r[2])} for r in vendeurs],
            'dernieres_ventes': [{'groupe': r[0], 'date': str(r[1]), 'total': _n(r[2]),
                                  'client': r[3] or '', 'vendeur': r[4] or '', 'mode': r[5] or ''}
                                 for r in dernieres],
            'stock_bas': [{'nom': r[0], 'stock': _n(r[1]), 'stock_min': _n(r[2])} for r in stock_bas],
            'depenses_par_categorie': [{'categorie': r[0], 'total': _n(r[1])} for r in dep_cat],
            'comptes': comptes,
        })
    except Exception as e:
        print(f"Erreur api_dashboard: {e}")
        return _err('Impossible de charger le tableau de bord', 500)


# ──────────────────────────────────────────────────────────────
# API — STATISTIQUES
# ──────────────────────────────────────────────────────────────
@app.route('/api/stats')
def api_stats():
    if not _admin():
        return _non_autorise()
    try:
        jours = request.args.get('jours', '30')
        jours = int(jours) if jours in ('7', '30', '90', '365') else 30
        bq, bp = boutique_filtre_sql('boutique_id')
        aujourdhui = date.today()
        demain = aujourdhui + timedelta(days=1)
        debut = aujourdhui - timedelta(days=jours - 1)
        debut_prec = debut - timedelta(days=jours)
        iso = lambda d: d.isoformat()

        def ventes(d1, d2):
            r = q1(f'''SELECT COALESCE(SUM(total), 0), COUNT(DISTINCT groupe), COALESCE(SUM(qte), 0)
                       FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}''',
                   (iso(d1), iso(d2)) + bp)
            return (_n(r[0]), int(r[1] or 0), _n(r[2])) if r else (0.0, 0, 0.0)

        def somme(sql_from, colonne_date, colonne_montant):
            r = q1(f'''SELECT COALESCE(SUM({colonne_montant}), 0) FROM {sql_from}
                       WHERE {colonne_date}::date >= ?::date AND {colonne_date}::date < ?::date{bq}''',
                   (iso(debut), iso(demain)) + bp)
            return _n(r[0]) if r else 0.0

        ca, nb_ventes, nb_articles = ventes(debut, demain)
        ca_prec, nb_prec, _ = ventes(debut_prec, debut)
        achats = somme(f'({_ACHATS_U}) a', 'd', 'total')
        pertes = somme(f'({_PERTES_U}) p', 'd', 'total')
        charges = somme('charges', 'date_charge', 'montant')
        depenses = somme('depenses', 'date_depense', 'montant')

        # Évolution : par jour jusqu'à 90 jours, par mois au-delà
        if jours <= 90:
            granularite = 'jour'
            rows = qall(f'''SELECT v.d::date, COALESCE(SUM(total), 0), COUNT(DISTINCT groupe)
                            FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                            GROUP BY 1 ORDER BY 1''', (iso(debut), iso(demain)) + bp)
            par = {str(r[0]): (_n(r[1]), int(r[2] or 0)) for r in rows}
            serie = []
            for i in range(jours):
                j = debut + timedelta(days=i)
                t, nb = par.get(iso(j), (0.0, 0))
                serie.append({'cle': iso(j), 'total': t, 'nb': nb})
        else:
            granularite = 'mois'
            cles = _liste_mois(12)
            rows = qall(f'''SELECT to_char(v.d, 'YYYY-MM'), COALESCE(SUM(total), 0), COUNT(DISTINCT groupe)
                            FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                            GROUP BY 1 ORDER BY 1''', (cles[0] + '-01', iso(demain)) + bp)
            par = {r[0]: (_n(r[1]), int(r[2] or 0)) for r in rows}
            serie = [{'cle': c, 'total': par.get(c, (0.0, 0))[0], 'nb': par.get(c, (0.0, 0))[1]} for c in cles]

        top = qall(f'''SELECT produit, COALESCE(SUM(qte), 0), COALESCE(SUM(total), 0)
                       FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                       GROUP BY produit ORDER BY 3 DESC LIMIT 10''', (iso(debut), iso(demain)) + bp)
        modes = qall(f'''SELECT mode, COALESCE(SUM(total), 0)
                         FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                         GROUP BY mode ORDER BY 2 DESC''', (iso(debut), iso(demain)) + bp)
        vendeurs = qall(f'''SELECT vendeur, COUNT(DISTINCT groupe), COALESCE(SUM(total), 0)
                            FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                            GROUP BY vendeur ORDER BY 3 DESC LIMIT 8''', (iso(debut), iso(demain)) + bp)
        semaine = qall(f'''SELECT EXTRACT(DOW FROM v.d)::int, COALESCE(SUM(total), 0)
                           FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                           GROUP BY 1''', (iso(debut), iso(demain)) + bp)
        par_dow = {int(r[0]): _n(r[1]) for r in semaine}
        # DOW PostgreSQL : 0 = dimanche -> on affiche du lundi au dimanche
        jours_semaine = [{'jour': nom, 'total': par_dow.get(idx, 0.0)}
                         for nom, idx in (('Lun', 1), ('Mar', 2), ('Mer', 3), ('Jeu', 4),
                                          ('Ven', 5), ('Sam', 6), ('Dim', 0))]
        heures = qall(f'''SELECT EXTRACT(HOUR FROM v.d)::int, COALESCE(SUM(total), 0)
                          FROM ({_VENTES_U}) v WHERE v.d >= ?::date AND v.d < ?::date{bq}
                          GROUP BY 1''', (iso(debut), iso(demain)) + bp)
        par_h = {int(r[0]): _n(r[1]) for r in heures}
        par_heure = [{'heure': h, 'total': par_h.get(h, 0.0)} for h in range(24)]

        bq_p, bp_p = boutique_filtre_sql('p.boutique_id')
        dormants = qall(f'''
            SELECT p.nom, p.stock, p.stock * p.prix
            FROM produits p
            WHERE COALESCE(p.actif, 1) = 1 AND p.stock > 0
              AND NOT EXISTS (SELECT 1 FROM sorties s WHERE s.produit_id = p.id AND s.date_sortie::date >= ?::date)
              AND NOT EXISTS (SELECT 1 FROM archive_ventes a WHERE a.produit_id = p.id AND a.date_vente::date >= ?::date){bq_p}
            ORDER BY 3 DESC LIMIT 8''', (iso(debut), iso(debut)) + bp_p)

        dep_cat = qall(f'''SELECT COALESCE(categorie, 'Autre'), COALESCE(SUM(montant), 0)
                           FROM depenses WHERE date_depense::date >= ?::date AND date_depense::date < ?::date{bq}
                           GROUP BY 1 ORDER BY 2 DESC''', (iso(debut), iso(demain)) + bp)
        chg_cat = qall(f'''SELECT COALESCE(categorie, 'Autre'), COALESCE(SUM(montant), 0)
                           FROM charges WHERE date_charge::date >= ?::date AND date_charge::date < ?::date{bq}
                           GROUP BY 1 ORDER BY 2 DESC''', (iso(debut), iso(demain)) + bp)

        # Résultat mensuel sur 12 mois
        cles12 = _liste_mois(12)
        debut12 = cles12[0] + '-01'

        def par_mois(sql, params):
            return {r[0]: _n(r[1]) for r in qall(sql, params)}

        m_ventes = par_mois(f'''SELECT to_char(v.d, 'YYYY-MM'), COALESCE(SUM(total), 0)
                                FROM ({_VENTES_U}) v WHERE v.d >= ?::date{bq} GROUP BY 1''', (debut12,) + bp)
        m_achats = par_mois(f'''SELECT to_char(a.d, 'YYYY-MM'), COALESCE(SUM(total), 0)
                                FROM ({_ACHATS_U}) a WHERE a.d >= ?::date{bq} GROUP BY 1''', (debut12,) + bp)
        m_charges = par_mois(f'''SELECT to_char(date_charge::date, 'YYYY-MM'), COALESCE(SUM(montant), 0)
                                 FROM charges WHERE date_charge::date >= ?::date{bq} GROUP BY 1''', (debut12,) + bp)
        m_dep = par_mois(f'''SELECT to_char(date_depense::date, 'YYYY-MM'), COALESCE(SUM(montant), 0)
                             FROM depenses WHERE date_depense::date >= ?::date{bq} GROUP BY 1''', (debut12,) + bp)
        mensuel = []
        for cle in cles12:
            v, a, c, d = m_ventes.get(cle, 0.0), m_achats.get(cle, 0.0), m_charges.get(cle, 0.0), m_dep.get(cle, 0.0)
            mensuel.append({'cle': cle, 'libelle': _libelle_mois(cle), 'ventes': v, 'achats': a,
                            'charges': c, 'depenses': d, 'resultat': round(v - a - c, 2)})

        return jsonify({
            'jours': jours, 'debut': iso(debut), 'fin': iso(aujourdhui), 'granularite': granularite,
            'kpis': {
                'ca': ca, 'ca_precedent': ca_prec, 'variation_ca': _variation(ca, ca_prec),
                'nb_ventes': nb_ventes, 'variation_nb': _variation(nb_ventes, nb_prec),
                'panier_moyen': round(ca / nb_ventes) if nb_ventes else 0,
                'nb_articles': nb_articles,
                'achats': achats, 'charges': charges, 'depenses': depenses, 'pertes': pertes,
                'marge_brute': round(ca - achats, 2), 'resultat': round(ca - achats - charges, 2),
            },
            'serie': serie,
            'top_produits': [{'nom': r[0], 'quantite': _n(r[1]), 'total': _n(r[2])} for r in top],
            'modes_paiement': [{'mode': r[0], 'total': _n(r[1])} for r in modes],
            'vendeurs': [{'nom': r[0] or '-', 'nb': int(r[1] or 0), 'total': _n(r[2])} for r in vendeurs],
            'jours_semaine': jours_semaine,
            'par_heure': par_heure,
            'produits_dormants': [{'nom': r[0], 'stock': _n(r[1]), 'valeur': _n(r[2])} for r in dormants],
            'depenses_par_categorie': [{'categorie': r[0], 'total': _n(r[1])} for r in dep_cat],
            'charges_par_categorie': [{'categorie': r[0], 'total': _n(r[1])} for r in chg_cat],
            'mensuel': mensuel,
        })
    except Exception as e:
        print(f"Erreur api_stats: {e}")
        return _err('Impossible de charger les statistiques', 500)


# ──────────────────────────────────────────────────────────────
# API — DÉPENSES
# ──────────────────────────────────────────────────────────────
def _compte_valide(compte_id, boutique_id):
    """Retourne (id, nom) du compte s'il existe, est actif et appartient à la boutique."""
    if not compte_id:
        return None
    r = q1("SELECT id, nom, boutique_id FROM comptes_tresorerie WHERE id = ? AND COALESCE(actif, 1) = 1",
           (compte_id,))
    if not r:
        return None
    if boutique_id is not None and r[2] is not None and r[2] != boutique_id:
        return None
    return r


def _solde_compte(compte_id):
    r = q1('''SELECT c.solde_initial
                     + COALESCE(SUM(CASE WHEN m.type = 'entree' THEN m.montant ELSE 0 END), 0)
                     - COALESCE(SUM(CASE WHEN m.type = 'sortie' THEN m.montant ELSE 0 END), 0)
              FROM comptes_tresorerie c
              LEFT JOIN mouvements_tresorerie m ON m.compte_id = c.id
              WHERE c.id = ? GROUP BY c.id, c.solde_initial''', (compte_id,))
    return _n(r[0]) if r else 0.0


@app.route('/api/depenses')
def api_depenses():
    if not _admin():
        return _non_autorise()
    try:
        aujourdhui = date.today()
        debut = _date_ou(request.args.get('debut'), aujourdhui.replace(day=1))
        fin = _date_ou(request.args.get('fin'), aujourdhui)
        categorie = (request.args.get('categorie') or '').strip()
        recherche = (request.args.get('q') or '').strip()

        bq, bp = boutique_filtre_sql('d.boutique_id')
        conditions = "d.date_depense::date BETWEEN ?::date AND ?::date" + bq
        params = (debut.isoformat(), fin.isoformat()) + bp
        if categorie:
            conditions += " AND d.categorie = ?"
            params += (categorie,)
        if recherche:
            conditions += " AND (d.motif ILIKE ? OR COALESCE(d.fournisseur, '') ILIKE ? OR COALESCE(d.reference, '') ILIKE ?)"
            params += (f'%{recherche}%',) * 3

        lignes = qall(f'''
            SELECT d.id, d.date_depense, d.categorie, d.motif, d.montant, d.fournisseur,
                   d.mode_paiement, d.compte_id, COALESCE(c.nom, ''), d.reference, d.notes,
                   COALESCE(b.nom, ''), COALESCE(u.nom, '')
            FROM depenses d
            LEFT JOIN comptes_tresorerie c ON c.id = d.compte_id
            LEFT JOIN boutiques b ON b.id = d.boutique_id
            LEFT JOIN users u ON u.id = d.employe_id
            WHERE {conditions}
            ORDER BY d.date_depense DESC, d.id DESC LIMIT 500''', params)
        items = [{
            'id': r[0], 'date': r[1], 'categorie': r[2], 'motif': r[3], 'montant': _n(r[4]),
            'fournisseur': r[5] or '', 'mode_paiement': r[6] or '', 'compte_id': r[7],
            'compte': r[8], 'reference': r[9] or '', 'notes': r[10] or '',
            'boutique': r[11], 'auteur': r[12],
        } for r in lignes]

        par_cat = qall(f'''SELECT COALESCE(d.categorie, 'Autre'), COALESCE(SUM(d.montant), 0), COUNT(*)
                           FROM depenses d WHERE {conditions}
                           GROUP BY 1 ORDER BY 2 DESC''', params)

        # Tendance des 6 derniers mois (hors filtres de période)
        cles = _liste_mois(6)
        bq2, bp2 = boutique_filtre_sql('boutique_id')
        rows = qall(f'''SELECT to_char(date_depense::date, 'YYYY-MM'), COALESCE(SUM(montant), 0)
                        FROM depenses WHERE date_depense::date >= ?::date{bq2} GROUP BY 1''',
                    (cles[0] + '-01',) + bp2)
        par_mois = {r[0]: _n(r[1]) for r in rows}

        return jsonify({
            'debut': debut.isoformat(), 'fin': fin.isoformat(),
            'items': items,
            'total': round(sum(i['montant'] for i in items), 2),
            'nb': len(items),
            'par_categorie': [{'categorie': r[0], 'total': _n(r[1]), 'nb': int(r[2])} for r in par_cat],
            'par_mois': [{'cle': c, 'libelle': _libelle_mois(c), 'total': par_mois.get(c, 0.0)} for c in cles],
            'categories': CATEGORIES_DEPENSES,
            'modes_paiement': MODES_PAIEMENT_DEPENSE,
            'comptes': _soldes_comptes(),
            'boutique_active': boutique_active() is not None,
        })
    except Exception as e:
        print(f"Erreur api_depenses: {e}")
        return _err('Impossible de charger les dépenses', 500)


@app.route('/api/depenses', methods=['POST'])
def api_depenses_creer():
    if not _admin():
        return _non_autorise()
    try:
        data = request.get_json(silent=True) or {}
        categorie = (data.get('categorie') or 'Autre').strip()
        if categorie not in CATEGORIES_DEPENSES:
            categorie = 'Autre'
        motif = (data.get('motif') or '').strip()
        if not motif:
            return _err('Le motif de la dépense est obligatoire')
        try:
            montant = round(float(data.get('montant') or 0), 2)
        except (TypeError, ValueError):
            montant = 0
        if montant <= 0:
            return _err('Le montant doit être supérieur à 0')
        jour = _date_ou(data.get('date'), date.today())
        bid = boutique_active()
        if bid is None:
            return _err('Choisissez d’abord une boutique active (menu en haut de page) avant d’enregistrer une dépense')
        mode = (data.get('mode_paiement') or 'Espèces').strip()
        fournisseur = (data.get('fournisseur') or '').strip()
        reference = (data.get('reference') or '').strip()
        notes = (data.get('notes') or '').strip()

        compte = None
        compte_id = data.get('compte_id') or None
        if compte_id:
            compte = _compte_valide(int(compte_id), bid)
            if not compte:
                return _err('Compte de trésorerie introuvable pour cette boutique')

        maintenant = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_id = session.get('user_id', 1)
        avertissement = None
        if compte and _solde_compte(compte[0]) - montant < 0:
            avertissement = f"Le solde du compte « {compte[1]} » devient négatif."

        def ecrire(cur):
            cur.execute('''INSERT INTO depenses (date_depense, categorie, motif, montant, fournisseur,
                                mode_paiement, compte_id, reference, notes, boutique_id, employe_id, date_creation)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                        (jour.isoformat(), categorie, motif, montant, fournisseur, mode,
                         compte[0] if compte else None, reference, notes, bid, user_id, maintenant))
            dep_id = cur.fetchone()[0]
            if compte:
                cur.execute('''INSERT INTO mouvements_tresorerie (compte_id, date_mouvement, type, montant,
                                    libelle, source, reference_id, boutique_id, employe_id, date_creation)
                               VALUES (%s,%s,'sortie',%s,%s,'depense',%s,%s,%s,%s)''',
                            (compte[0], jour.isoformat(), montant, f"{categorie} : {motif}",
                             dep_id, bid, user_id, maintenant))
            return dep_id

        dep_id = _transaction(ecrire)
        return jsonify({'ok': True, 'id': dep_id, 'avertissement': avertissement}), 201
    except Exception as e:
        print(f"Erreur api_depenses_creer: {e}")
        return _err('Erreur lors de l’enregistrement de la dépense', 500)


@app.route('/api/depenses/<int:dep_id>', methods=['DELETE'])
def api_depenses_supprimer(dep_id):
    if not _admin():
        return _non_autorise()
    try:
        if not q1("SELECT id FROM depenses WHERE id = ?", (dep_id,)):
            return _err('Dépense introuvable', 404)

        def effacer(cur):
            cur.execute("DELETE FROM mouvements_tresorerie WHERE source = 'depense' AND reference_id = %s", (dep_id,))
            cur.execute("DELETE FROM depenses WHERE id = %s", (dep_id,))

        _transaction(effacer)
        return jsonify({'ok': True})
    except Exception as e:
        print(f"Erreur api_depenses_supprimer: {e}")
        return _err('Erreur lors de la suppression', 500)


# ──────────────────────────────────────────────────────────────
# API — TRÉSORERIE
# ──────────────────────────────────────────────────────────────
@app.route('/api/tresorerie')
def api_tresorerie():
    if not _admin():
        return _non_autorise()
    try:
        aujourdhui = date.today()
        debut = _date_ou(request.args.get('debut'), aujourdhui.replace(day=1))
        fin = _date_ou(request.args.get('fin'), aujourdhui)
        compte_filtre = request.args.get('compte_id', type=int)
        type_filtre = request.args.get('type', '')

        comptes = _soldes_comptes()
        bq, bp = boutique_filtre_sql('c.boutique_id')
        conditions = "COALESCE(c.actif, 1) = 1 AND m.date_mouvement::date BETWEEN ?::date AND ?::date" + bq
        params = (debut.isoformat(), fin.isoformat()) + bp
        if compte_filtre:
            conditions += " AND m.compte_id = ?"
            params += (compte_filtre,)
        if type_filtre in ('entree', 'sortie'):
            conditions += " AND m.type = ?"
            params += (type_filtre,)

        lignes = qall(f'''
            SELECT m.id, m.date_mouvement, m.type, m.montant, m.libelle, m.source,
                   m.transfert_id, m.compte_id, c.nom, COALESCE(u.nom, '')
            FROM mouvements_tresorerie m
            JOIN comptes_tresorerie c ON c.id = m.compte_id
            LEFT JOIN users u ON u.id = m.employe_id
            WHERE {conditions}
            ORDER BY m.date_mouvement DESC, m.id DESC LIMIT 300''', params)
        mouvements = [{
            'id': r[0], 'date': r[1], 'type': r[2], 'montant': _n(r[3]), 'libelle': r[4] or '',
            'source': r[5] or 'manuel', 'transfert': bool(r[6]), 'compte_id': r[7],
            'compte': r[8], 'auteur': r[9],
        } for r in lignes]

        # Totaux de la période : les virements entre comptes ne sont ni un revenu ni une dépense
        totaux = q1(f'''
            SELECT COALESCE(SUM(CASE WHEN m.type = 'entree' AND COALESCE(m.source, '') <> 'transfert' THEN m.montant ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN m.type = 'sortie' AND COALESCE(m.source, '') <> 'transfert' THEN m.montant ELSE 0 END), 0)
            FROM mouvements_tresorerie m JOIN comptes_tresorerie c ON c.id = m.compte_id
            WHERE {conditions}''', params) or (0, 0)

        flux = qall(f'''
            SELECT m.date_mouvement::date,
                   COALESCE(SUM(CASE WHEN m.type = 'entree' THEN m.montant ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN m.type = 'sortie' THEN m.montant ELSE 0 END), 0)
            FROM mouvements_tresorerie m JOIN comptes_tresorerie c ON c.id = m.compte_id
            WHERE {conditions} AND COALESCE(m.source, '') <> 'transfert'
            GROUP BY 1 ORDER BY 1''', params)

        # Encaissements de ventes par mode de paiement, pour rapprocher avec les comptes
        bqv, bpv = boutique_filtre_sql('boutique_id')
        encaissements = qall(f'''SELECT mode, COALESCE(SUM(total), 0)
                                 FROM ({_VENTES_U}) v
                                 WHERE v.d >= ?::date AND v.d < ?::date{bqv}
                                 GROUP BY mode ORDER BY 2 DESC''',
                             (debut.isoformat(), (fin + timedelta(days=1)).isoformat()) + bpv)

        return jsonify({
            'debut': debut.isoformat(), 'fin': fin.isoformat(),
            'comptes': comptes,
            'total_tresorerie': round(sum(c['solde'] for c in comptes), 2),
            'entrees_periode': _n(totaux[0]), 'sorties_periode': _n(totaux[1]),
            'solde_periode': round(_n(totaux[0]) - _n(totaux[1]), 2),
            'mouvements': mouvements,
            'flux': [{'date': str(r[0]), 'entrees': _n(r[1]), 'sorties': _n(r[2])} for r in flux],
            'encaissements_ventes': [{'mode': r[0], 'total': _n(r[1])} for r in encaissements],
            'types_compte': TYPES_COMPTE,
            'boutique_active': boutique_active() is not None,
        })
    except Exception as e:
        print(f"Erreur api_tresorerie: {e}")
        return _err('Impossible de charger la trésorerie', 500)


@app.route('/api/tresorerie/comptes', methods=['POST'])
def api_tresorerie_compte_creer():
    if not _admin():
        return _non_autorise()
    try:
        data = request.get_json(silent=True) or {}
        nom = (data.get('nom') or '').strip()
        if not nom:
            return _err('Le nom du compte est obligatoire')
        type_compte = data.get('type') if data.get('type') in TYPES_COMPTE else 'Caisse'
        try:
            solde_initial = round(float(data.get('solde_initial') or 0), 2)
        except (TypeError, ValueError):
            solde_initial = 0
        if solde_initial < 0:
            return _err('Le solde initial ne peut pas être négatif')
        bid = boutique_active()
        if bid is None:
            return _err('Choisissez d’abord une boutique active (menu en haut de page) avant de créer un compte')
        if q1("SELECT id FROM comptes_tresorerie WHERE LOWER(nom) = LOWER(?) AND boutique_id = ? AND COALESCE(actif, 1) = 1",
              (nom, bid)):
            return _err('Un compte portant ce nom existe déjà dans cette boutique')

        def ecrire(cur):
            cur.execute('''INSERT INTO comptes_tresorerie (nom, type, solde_initial, boutique_id, actif, date_creation)
                           VALUES (%s,%s,%s,%s,1,%s) RETURNING id''',
                        (nom, type_compte, solde_initial, bid, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            return cur.fetchone()[0]

        return jsonify({'ok': True, 'id': _transaction(ecrire)}), 201
    except Exception as e:
        print(f"Erreur api_tresorerie_compte_creer: {e}")
        return _err('Erreur lors de la création du compte', 500)


@app.route('/api/tresorerie/comptes/<int:compte_id>/archiver', methods=['POST'])
def api_tresorerie_compte_archiver(compte_id):
    if not _admin():
        return _non_autorise()
    try:
        if not q1("SELECT id FROM comptes_tresorerie WHERE id = ?", (compte_id,)):
            return _err('Compte introuvable', 404)

        def ecrire(cur):
            cur.execute("UPDATE comptes_tresorerie SET actif = 0 WHERE id = %s", (compte_id,))

        _transaction(ecrire)
        return jsonify({'ok': True})
    except Exception as e:
        print(f"Erreur api_tresorerie_compte_archiver: {e}")
        return _err('Erreur lors de l’archivage du compte', 500)


@app.route('/api/tresorerie/mouvements', methods=['POST'])
def api_tresorerie_mouvement_creer():
    if not _admin():
        return _non_autorise()
    try:
        data = request.get_json(silent=True) or {}
        type_mvt = data.get('type')
        if type_mvt not in ('entree', 'sortie'):
            return _err('Type de mouvement invalide')
        libelle = (data.get('libelle') or '').strip()
        if not libelle:
            return _err('Le libellé est obligatoire')
        try:
            montant = round(float(data.get('montant') or 0), 2)
        except (TypeError, ValueError):
            montant = 0
        if montant <= 0:
            return _err('Le montant doit être supérieur à 0')
        compte = _compte_valide(int(data.get('compte_id') or 0), boutique_active())
        if not compte:
            return _err('Compte introuvable')
        jour = _date_ou(data.get('date'), date.today())
        avertissement = None
        if type_mvt == 'sortie' and _solde_compte(compte[0]) - montant < 0:
            avertissement = f"Le solde du compte « {compte[1]} » devient négatif."

        def ecrire(cur):
            cur.execute('''INSERT INTO mouvements_tresorerie (compte_id, date_mouvement, type, montant, libelle,
                                source, boutique_id, employe_id, date_creation)
                           VALUES (%s,%s,%s,%s,%s,'manuel',%s,%s,%s) RETURNING id''',
                        (compte[0], jour.isoformat(), type_mvt, montant, libelle, compte[2],
                         session.get('user_id', 1), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            return cur.fetchone()[0]

        return jsonify({'ok': True, 'id': _transaction(ecrire), 'avertissement': avertissement}), 201
    except Exception as e:
        print(f"Erreur api_tresorerie_mouvement_creer: {e}")
        return _err('Erreur lors de l’enregistrement du mouvement', 500)


@app.route('/api/tresorerie/transfert', methods=['POST'])
def api_tresorerie_transfert():
    if not _admin():
        return _non_autorise()
    try:
        data = request.get_json(silent=True) or {}
        try:
            montant = round(float(data.get('montant') or 0), 2)
        except (TypeError, ValueError):
            montant = 0
        if montant <= 0:
            return _err('Le montant doit être supérieur à 0')
        source = _compte_valide(int(data.get('compte_source') or 0), None)
        dest = _compte_valide(int(data.get('compte_dest') or 0), None)
        if not source or not dest:
            return _err('Choisissez deux comptes valides')
        if source[0] == dest[0]:
            return _err('Les deux comptes doivent être différents')
        jour = _date_ou(data.get('date'), date.today())
        note = (data.get('libelle') or '').strip()
        avertissement = None
        if _solde_compte(source[0]) - montant < 0:
            avertissement = f"Le solde du compte « {source[1]} » devient négatif."
        lien = uuid.uuid4().hex
        maintenant = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_id = session.get('user_id', 1)

        def ecrire(cur):
            for compte, sens, texte in ((source, 'sortie', f"Virement vers {dest[1]}"),
                                        (dest, 'entree', f"Virement depuis {source[1]}")):
                cur.execute('''INSERT INTO mouvements_tresorerie (compte_id, date_mouvement, type, montant, libelle,
                                    source, transfert_id, boutique_id, employe_id, date_creation)
                               VALUES (%s,%s,%s,%s,%s,'transfert',%s,%s,%s,%s)''',
                            (compte[0], jour.isoformat(), sens, montant,
                             texte + (f" — {note}" if note else ''), lien, compte[2], user_id, maintenant))

        _transaction(ecrire)
        return jsonify({'ok': True, 'avertissement': avertissement}), 201
    except Exception as e:
        print(f"Erreur api_tresorerie_transfert: {e}")
        return _err('Erreur lors du virement', 500)


@app.route('/api/tresorerie/mouvements/<int:mvt_id>', methods=['DELETE'])
def api_tresorerie_mouvement_supprimer(mvt_id):
    if not _admin():
        return _non_autorise()
    try:
        m = q1("SELECT id, source, transfert_id FROM mouvements_tresorerie WHERE id = ?", (mvt_id,))
        if not m:
            return _err('Mouvement introuvable', 404)
        if m[1] == 'depense':
            return _err('Ce mouvement vient d’une dépense : supprimez la dépense depuis la page Dépenses')

        def effacer(cur):
            if m[2]:
                cur.execute("DELETE FROM mouvements_tresorerie WHERE transfert_id = %s", (m[2],))
            else:
                cur.execute("DELETE FROM mouvements_tresorerie WHERE id = %s", (mvt_id,))

        _transaction(effacer)
        return jsonify({'ok': True})
    except Exception as e:
        print(f"Erreur api_tresorerie_mouvement_supprimer: {e}")
        return _err('Erreur lors de la suppression', 500)


init_db_finances()
