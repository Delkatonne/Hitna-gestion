# ══════════════════════════════════════════════════════════════════════════
# ajouts.py — nouvelles fonctionnalités HITNA (module séparé d'app.py)
#   • Évènements du Business Plan (foire, promotion...)
#   • Crédits clients
#   • Arrivages (marchandises reçues pas encore dans le stock)
#   • Clients : ajout manuel + envoi de messages WhatsApp
# Les routes gardent les mêmes noms d'endpoint que si elles étaient dans app.py
# (donc request.endpoint dans base.html continue de fonctionner).
# Chargé par app.py avec :  import ajouts   (voir INSTALLATION.md)
# ══════════════════════════════════════════════════════════════════════════
import os
import json
from datetime import datetime
from urllib.parse import quote
from flask import request, redirect, session, flash, render_template

from app import (app, q1, qall, exe, get_db, release_db,
                 boutique_active, boutique_filtre_sql,
                 format_qte, format_prix, verifier_alertes_stock,
                 trouver_ou_creer_client, _traiter_entree,
                 produit_nom_existe, message_doublon_produit)

MODES_PAIEMENT = ['Espèces', 'MTN MobileMoney', 'Moov MoovMoney', 'Celtiis CeltiisCash']
TYPES_EVENEMENT = ['foire', 'promotion', 'autre']
# Indicatif ajouté aux numéros locaux pour les liens WhatsApp (modifiable via variable d'env.)
WHATSAPP_INDICATIF = os.environ.get('WHATSAPP_INDICATIF', '229')


# ──────────────────────────────────────────────────────────────
# WHATSAPP — nettoyage des numéros + lien wa.me prérempli
# ──────────────────────────────────────────────────────────────
def normaliser_whatsapp(numero):
    """Retourne le numéro au format international sans '+' (ex: 2290167198531),
    ou '' s'il est inutilisable. On garde le '0' initial des numéros locaux à
    10 chiffres (format 01XXXXXXXX) : il fait partie du numéro."""
    brut = (numero or '').strip()
    chiffres = ''.join(ch for ch in brut if ch.isdigit())
    if not chiffres:
        return ''
    if brut.startswith('+'):
        return chiffres
    if chiffres.startswith('00'):
        return chiffres[2:]
    if chiffres.startswith(WHATSAPP_INDICATIF) and len(chiffres) >= len(WHATSAPP_INDICATIF) + 8:
        return chiffres
    return WHATSAPP_INDICATIF + chiffres

def lien_whatsapp(numero, message=''):
    n = normaliser_whatsapp(numero)
    if not n:
        return ''
    return f"https://wa.me/{n}" + (f"?text={quote(message)}" if message else '')

app.jinja_env.globals['lien_whatsapp'] = lien_whatsapp
app.jinja_env.filters['prix'] = format_prix


# ──────────────────────────────────────────────────────────────
# MIGRATIONS — nouvelles tables (sans toucher à init_db existante)
# ──────────────────────────────────────────────────────────────
def init_db_ajouts():
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS evenements (
            id SERIAL PRIMARY KEY,
            nom TEXT NOT NULL,
            type_evenement TEXT DEFAULT 'autre',
            date_debut TEXT,
            date_fin TEXT,
            boutique_id INTEGER REFERENCES boutiques(id),
            notes TEXT DEFAULT '',
            statut TEXT DEFAULT 'prevu',
            employe_id INTEGER,
            date_creation TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS credits (
            id SERIAL PRIMARY KEY,
            client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            client_nom TEXT NOT NULL,
            telephone TEXT,
            boutique_id INTEGER REFERENCES boutiques(id),
            date_credit TEXT,
            date_limite TEXT,
            notes TEXT DEFAULT '',
            statut TEXT DEFAULT 'en_cours',
            employe_id INTEGER,
            date_creation TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS credits_lignes (
            id SERIAL PRIMARY KEY,
            credit_id INTEGER REFERENCES credits(id) ON DELETE CASCADE,
            produit_id INTEGER REFERENCES produits(id) ON DELETE SET NULL,
            produit_nom TEXT,
            quantite NUMERIC(10,3),
            prix_unitaire INTEGER,
            total INTEGER)''')

        c.execute('''CREATE TABLE IF NOT EXISTS credits_paiements (
            id SERIAL PRIMARY KEY,
            credit_id INTEGER REFERENCES credits(id) ON DELETE CASCADE,
            montant INTEGER,
            mode_paiement TEXT DEFAULT 'Espèces',
            date_paiement TEXT,
            notes TEXT DEFAULT '',
            employe_id INTEGER)''')

        c.execute('''CREATE TABLE IF NOT EXISTS arrivages (
            id SERIAL PRIMARY KEY,
            boutique_id INTEGER REFERENCES boutiques(id),
            fournisseur TEXT DEFAULT '',
            date_arrivage TEXT,
            notes TEXT DEFAULT '',
            statut TEXT DEFAULT 'en_attente',
            employe_id INTEGER,
            date_creation TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS arrivages_lignes (
            id SERIAL PRIMARY KEY,
            arrivage_id INTEGER REFERENCES arrivages(id) ON DELETE CASCADE,
            produit_id INTEGER REFERENCES produits(id) ON DELETE SET NULL,
            produit_nom TEXT NOT NULL,
            quantite_recue NUMERIC(10,3),
            quantite_integree NUMERIC(10,3) DEFAULT 0,
            prix_achat INTEGER)''')

        # Clients : prénom (le nom et l'adresse existent déjà ; le numéro WhatsApp
        # est le champ « telephone », déjà unique et déjà utilisé partout).
        c.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS prenom TEXT DEFAULT ''")
        conn.commit()

        # Même précaution que pour les autres tables (réplication logique Postgres)
        for t in ['evenements', 'credits', 'credits_lignes', 'credits_paiements',
                  'arrivages', 'arrivages_lignes']:
            try:
                c.execute(f"ALTER TABLE {t} REPLICA IDENTITY FULL")
                conn.commit()
            except Exception as e:
                print(f"⚠️ REPLICA IDENTITY sur {t}: {e}")
                conn.rollback()

        # Unicité des noms de produit par boutique, garantie aussi au niveau de la base.
        # Si des doublons existent déjà, l'index ne peut pas être créé : la protection du
        # code reste active, et /admin/produits/doublons permet de les nettoyer
        # (l'index sera créé automatiquement au redémarrage suivant, une fois nettoyé).
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_produits_nom_boutique ON produits (boutique_id, LOWER(TRIM(nom)))")
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"⚠️ Index d'unicité des noms de produit non créé (doublons existants ?) — voir /admin/produits/doublons : {e}")
        c.close()
        print("✅ Tables évènements / crédits / arrivages prêtes")
    except Exception as e:
        print(f"❌ Erreur init_db_ajouts: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            release_db(conn)


# ══════════════════════════════════════════════════════════════
# 1) ÉVÈNEMENTS (foire, promotion...) — rattachés au Business Plan
#    L'admin saisit lui-même le nom ; le type sert juste à classer.
# ══════════════════════════════════════════════════════════════
@app.route('/admin/business-plan/evenements/ajouter', methods=['POST'])
def ajouter_evenement():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '').strip()
        type_evt = request.form.get('type_evenement', 'autre')
        if type_evt not in TYPES_EVENEMENT:
            type_evt = 'autre'
        date_debut = request.form.get('date_debut') or None
        date_fin = request.form.get('date_fin') or None
        boutique_id = request.form.get('boutique_id') or None
        boutique_id = int(boutique_id) if boutique_id else None   # vide = toutes les boutiques
        notes = request.form.get('notes', '').strip()
        if not nom or not date_debut:
            flash('❌ Le nom et la date de début de l\'évènement sont obligatoires')
            return redirect('/admin/business-plan')
        if date_fin and date_fin < date_debut:
            flash('❌ La date de fin ne peut pas être avant la date de début')
            return redirect('/admin/business-plan')
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        exe('''INSERT INTO evenements (nom, type_evenement, date_debut, date_fin, boutique_id, notes, statut, employe_id, date_creation)
               VALUES (?,?,?,?,?,?,'prevu',?,?)''',
            (nom, type_evt, date_debut, date_fin, boutique_id, notes, session.get('user_id', 1), now))
        flash(f'✅ Évènement programmé : {nom}')
    except Exception as e:
        print(f"❌ Erreur ajouter_evenement: {e}")
        flash('❌ Erreur lors de l\'ajout de l\'évènement')
    return redirect('/admin/business-plan')

@app.route('/admin/business-plan/evenements/basculer/<int:id>', methods=['POST'])
def basculer_evenement(id):
    """Marque l'évènement comme terminé, ou le remet « à venir »."""
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        e = q1("SELECT statut FROM evenements WHERE id=?", (id,))
        if e:
            exe("UPDATE evenements SET statut=? WHERE id=?", ('prevu' if e[0] == 'termine' else 'termine', id))
    except Exception as e:
        print(f"❌ Erreur basculer_evenement: {e}")
        flash('❌ Erreur lors de la mise à jour')
    return redirect('/admin/business-plan')

@app.route('/admin/business-plan/evenements/supprimer/<int:id>')
def supprimer_evenement(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        e = q1("SELECT nom FROM evenements WHERE id=?", (id,))
        if e:
            exe("DELETE FROM evenements WHERE id=?", (id,))
            flash(f'🗑️ Évènement "{e[0]}" supprimé')
    except Exception as e:
        print(f"❌ Erreur supprimer_evenement: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/business-plan')


# ══════════════════════════════════════════════════════════════
# 2) CRÉDITS — ventes à crédit pour certains clients
#    - le stock est retiré à la création du crédit
#    - l'argent n'entre en compta qu'au fil des versements
# ══════════════════════════════════════════════════════════════
def _totaux_credit(credit_id):
    total = q1("SELECT COALESCE(SUM(total),0) FROM credits_lignes WHERE credit_id=?", (credit_id,))
    paye = q1("SELECT COALESCE(SUM(montant),0) FROM credits_paiements WHERE credit_id=?", (credit_id,))
    return (total[0] if total else 0), (paye[0] if paye else 0)

@app.route('/admin/credits')
def admin_credits():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        filtre = request.args.get('statut', 'en_cours')
        if filtre not in ('en_cours', 'solde', 'annule', 'tous'):
            filtre = 'en_cours'
        today_iso = datetime.now().strftime('%Y-%m-%d')

        where_bq, params_bq = boutique_filtre_sql('c.boutique_id')
        sql = '''SELECT c.id, c.client_nom, c.telephone, COALESCE(b.nom,''), c.date_credit,
                        c.date_limite, c.statut, c.notes,
                        COALESCE((SELECT SUM(total) FROM credits_lignes WHERE credit_id=c.id),0),
                        COALESCE((SELECT SUM(montant) FROM credits_paiements WHERE credit_id=c.id),0)
                 FROM credits c LEFT JOIN boutiques b ON c.boutique_id=b.id WHERE 1=1'''
        params = []
        if filtre != 'tous':
            sql += " AND c.statut = ?"
            params.append(filtre)
        sql += where_bq
        params += list(params_bq)
        sql += " ORDER BY c.date_limite ASC NULLS LAST, c.id DESC LIMIT 300"
        rows = qall(sql, tuple(params))

        ids = [r[0] for r in rows]
        lignes_par_credit, paiements_par_credit = {}, {}
        if ids:
            for l in qall("SELECT credit_id, produit_nom, quantite, prix_unitaire, total FROM credits_lignes WHERE credit_id = ANY(%s) ORDER BY id", (ids,)):
                lignes_par_credit.setdefault(l[0], []).append(l[1:])
            for p in qall("SELECT credit_id, montant, mode_paiement, date_paiement, notes FROM credits_paiements WHERE credit_id = ANY(%s) ORDER BY id DESC", (ids,)):
                paiements_par_credit.setdefault(p[0], []).append(p[1:])

        credits = []
        for r in rows:
            reste = (r[8] or 0) - (r[9] or 0)
            credits.append({
                'id': r[0], 'client_nom': r[1], 'telephone': r[2], 'boutique': r[3],
                'date_credit': r[4], 'date_limite': r[5], 'statut': r[6], 'notes': r[7],
                'total': r[8] or 0, 'paye': r[9] or 0, 'reste': reste,
                'en_retard': bool(r[6] == 'en_cours' and r[5] and r[5] < today_iso and reste > 0),
                'lignes': lignes_par_credit.get(r[0], []),
                'paiements': paiements_par_credit.get(r[0], []),
            })

        base_kpi = f'''SELECT COUNT(*), COALESCE(SUM(t.total - t.paye),0) FROM (
              SELECT c.id,
                     COALESCE((SELECT SUM(total) FROM credits_lignes WHERE credit_id=c.id),0) AS total,
                     COALESCE((SELECT SUM(montant) FROM credits_paiements WHERE credit_id=c.id),0) AS paye
              FROM credits c WHERE c.statut='en_cours' {{extra}}{where_bq}) t'''
        kpi = q1(base_kpi.replace('{extra}', ''), params_bq) or (0, 0)
        kpi_retard = q1(base_kpi.replace('{extra}', "AND c.date_limite IS NOT NULL AND c.date_limite < ?"),
                        (today_iso,) + params_bq) or (0, 0)

        bid = boutique_active()
        produits = []
        if bid is not None:
            produits = [{'id': p[0], 'nom': p[1], 'prix': p[2], 'stock': float(p[3]), 'frac': bool(p[4])}
                        for p in qall('''SELECT id, nom, prix, stock, COALESCE(vente_fractionnable,0)
                                         FROM produits WHERE boutique_id=? AND stock>0 AND COALESCE(actif,1)=1
                                         ORDER BY nom''', (bid,))]
        boutique_nom = ''
        if bid is not None:
            b = q1("SELECT nom FROM boutiques WHERE id=?", (bid,))
            boutique_nom = b[0] if b else ''

        return render_template('admin_credits.html', credits=credits, filtre=filtre,
            nb_en_cours=kpi[0], total_du=kpi[1], nb_retard=kpi_retard[0], montant_retard=kpi_retard[1],
            produits=produits, boutique_nom=boutique_nom, boutique_ok=bid is not None,
            modes_paiement=MODES_PAIEMENT, today_iso=today_iso)
    except Exception as e:
        print(f"❌ Erreur admin_credits: {e}")
        flash('Erreur lors du chargement des crédits')
        return redirect('/dashboard')

@app.route('/admin/credits/ajouter', methods=['POST'])
def ajouter_credit():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        client_nom = request.form.get('client_nom', '').strip()
        telephone = request.form.get('telephone', '').strip()
        date_limite = request.form.get('date_limite') or None
        notes = request.form.get('notes', '').strip()
        try:
            cart = json.loads(request.form.get('cart_json', '') or '[]')
        except (ValueError, TypeError):
            cart = []

        if not client_nom:
            flash('❌ Le nom du client est obligatoire')
            return redirect('/admin/credits')
        boutique_id = boutique_active()
        if boutique_id is None:
            flash('❌ Choisissez d\'abord une boutique active (en haut) avant d\'enregistrer un crédit')
            return redirect('/admin/credits')
        if not cart:
            flash('❌ Ajoutez au moins un produit au crédit')
            return redirect('/admin/credits')

        # Tout est vérifié AVANT d'écrire quoi que ce soit
        lignes_valides, erreurs = [], []
        for ligne in cart:
            try:
                pid = int(ligne.get('produit_id', 0) or 0)
                qty = round(float(ligne.get('quantite', 0) or 0), 3)
            except (TypeError, ValueError, AttributeError):
                continue
            if pid <= 0 or qty <= 0:
                continue
            p = q1("SELECT nom, prix, stock, vente_fractionnable FROM produits WHERE id=? AND boutique_id=?", (pid, boutique_id))
            if not p:
                erreurs.append(f'Produit #{pid} introuvable dans cette boutique')
            elif not p[3] and qty != int(qty):
                erreurs.append(f'"{p[0]}" ne peut être pris qu\'en quantité entière')
            elif qty > float(p[2]):
                erreurs.append(f'Stock insuffisant pour "{p[0]}" ({format_qte(p[2])} disponible(s))')
            else:
                lignes_valides.append((pid, p[0], qty, p[1], round(p[1] * qty)))
        if erreurs or not lignes_valides:
            flash('❌ ' + (' | '.join(erreurs) if erreurs else 'Aucun produit valide'))
            return redirect('/admin/credits')

        client_id = trouver_ou_creer_client(client_nom, telephone)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        credit_id = exe('''INSERT INTO credits (client_id, client_nom, telephone, boutique_id, date_credit,
                                                date_limite, notes, statut, employe_id, date_creation)
                           VALUES (?,?,?,?,?,?,?,'en_cours',?,?)''',
                        (client_id, client_nom, telephone or None, boutique_id, now[:10],
                         date_limite, notes, session.get('user_id', 1), now), returning=True)
        if not credit_id:
            flash('❌ Échec de l\'enregistrement du crédit')
            return redirect('/admin/credits')

        total = 0
        for pid, pnom, qty, pu, tot in lignes_valides:
            exe('''INSERT INTO credits_lignes (credit_id, produit_id, produit_nom, quantite, prix_unitaire, total)
                   VALUES (?,?,?,?,?,?)''', (credit_id, pid, pnom, qty, pu, tot))
            exe("UPDATE produits SET stock = stock - ? WHERE id=?", (qty, pid))
            total += tot
        verifier_alertes_stock()
        flash(f'✅ Crédit enregistré pour "{client_nom}" : {format_prix(total)} FCFA à recevoir — stock mis à jour')
    except Exception as e:
        print(f"❌ Erreur ajouter_credit: {e}")
        flash('❌ Erreur lors de l\'enregistrement du crédit')
    return redirect('/admin/credits')

@app.route('/admin/credits/paiement/<int:id>', methods=['POST'])
def payer_credit(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        credit = q1("SELECT client_nom, statut FROM credits WHERE id=?", (id,))
        if not credit or credit[1] != 'en_cours':
            flash('❌ Crédit introuvable ou déjà soldé/annulé')
            return redirect('/admin/credits')
        try:
            montant = int(float(request.form.get('montant', 0) or 0))
        except ValueError:
            montant = 0
        mode = request.form.get('mode_paiement', 'Espèces')
        if mode not in MODES_PAIEMENT:
            mode = 'Espèces'
        notes = request.form.get('notes', '').strip()
        total, paye = _totaux_credit(id)
        reste = total - paye
        if montant <= 0:
            flash('❌ Le montant du versement doit être supérieur à 0')
            return redirect('/admin/credits')
        if montant > reste:
            flash(f'❌ Ce versement dépasse le reste à payer ({format_prix(reste)} FCFA)')
            return redirect('/admin/credits')
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        exe('''INSERT INTO credits_paiements (credit_id, montant, mode_paiement, date_paiement, notes, employe_id)
               VALUES (?,?,?,?,?,?)''', (id, montant, mode, now, notes, session.get('user_id', 1)))
        if paye + montant >= total:
            exe("UPDATE credits SET statut='solde' WHERE id=?", (id,))
            flash(f'✅ Versement de {format_prix(montant)} FCFA reçu — le crédit de "{credit[0]}" est entièrement soldé 🎉')
        else:
            flash(f'✅ Versement de {format_prix(montant)} FCFA reçu de "{credit[0]}" — reste {format_prix(reste - montant)} FCFA')
    except Exception as e:
        print(f"❌ Erreur payer_credit: {e}")
        flash('❌ Erreur lors de l\'enregistrement du versement')
    return redirect('/admin/credits')

@app.route('/admin/credits/annuler/<int:id>')
def annuler_credit(id):
    """Annule un crédit sans aucun versement et remet le stock en rayon."""
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        credit = q1("SELECT client_nom, statut FROM credits WHERE id=?", (id,))
        if not credit or credit[1] != 'en_cours':
            flash('❌ Crédit introuvable ou déjà traité')
            return redirect('/admin/credits')
        _, paye = _totaux_credit(id)
        if paye > 0:
            flash('❌ Annulation impossible : des versements ont déjà été reçus sur ce crédit')
            return redirect('/admin/credits')
        for pid, qty in qall("SELECT produit_id, quantite FROM credits_lignes WHERE credit_id=?", (id,)):
            if pid:
                exe("UPDATE produits SET stock = stock + ? WHERE id=?", (qty, pid))
        exe("UPDATE credits SET statut='annule' WHERE id=?", (id,))
        flash(f'🚫 Crédit de "{credit[0]}" annulé — stock remis en rayon')
    except Exception as e:
        print(f"❌ Erreur annuler_credit: {e}")
        flash('❌ Erreur lors de l\'annulation')
    return redirect('/admin/credits')


# ══════════════════════════════════════════════════════════════
# 3) ARRIVAGES — marchandises arrivées mais pas encore dans le logiciel.
#    Aucun effet sur le stock tant que l'admin n'a pas « intégré » une ligne,
#    et il choisit lui-même la quantité à intégrer (intégration partielle possible).
# ══════════════════════════════════════════════════════════════
def _maj_statut_arrivage(arrivage_id):
    r = q1('''SELECT COALESCE(SUM(quantite_recue - quantite_integree),0), COALESCE(SUM(quantite_integree),0)
              FROM arrivages_lignes WHERE arrivage_id=?''', (arrivage_id,))
    reste, integre = (float(r[0]), float(r[1])) if r else (0, 0)
    statut = 'integre' if reste <= 0 else ('partiel' if integre > 0 else 'en_attente')
    exe("UPDATE arrivages SET statut=? WHERE id=?", (statut, arrivage_id))

@app.route('/admin/arrivages')
def admin_arrivages():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        filtre = request.args.get('statut', 'a_traiter')
        if filtre not in ('a_traiter', 'integre', 'annule', 'tous'):
            filtre = 'a_traiter'

        where_bq, params_bq = boutique_filtre_sql('a.boutique_id')
        sql = '''SELECT a.id, a.fournisseur, a.date_arrivage, a.statut, a.notes, COALESCE(b.nom,''), a.boutique_id
                 FROM arrivages a LEFT JOIN boutiques b ON a.boutique_id=b.id WHERE 1=1'''
        if filtre == 'a_traiter':
            sql += " AND a.statut IN ('en_attente','partiel')"
        elif filtre != 'tous':
            sql += f" AND a.statut = '{filtre}'"
        sql += where_bq + " ORDER BY a.date_arrivage DESC, a.id DESC LIMIT 100"
        rows = qall(sql, params_bq)

        ids = [r[0] for r in rows]
        lignes_par_arrivage = {}
        if ids:
            for l in qall('''SELECT arrivage_id, id, produit_id, produit_nom, quantite_recue, quantite_integree, prix_achat
                             FROM arrivages_lignes WHERE arrivage_id = ANY(%s) ORDER BY id''', (ids,)):
                lignes_par_arrivage.setdefault(l[0], []).append(l[1:])
        arrivages = [{'id': r[0], 'fournisseur': r[1], 'date_arrivage': r[2], 'statut': r[3], 'notes': r[4],
                      'boutique': r[5], 'boutique_id': r[6], 'lignes': lignes_par_arrivage.get(r[0], [])} for r in rows]

        where_p, params_p = boutique_filtre_sql('boutique_id')
        produits = qall(f"SELECT id, nom, boutique_id FROM produits WHERE COALESCE(actif,1)=1{where_p} ORDER BY nom", params_p)
        bid = boutique_active()
        boutique_nom = ''
        if bid is not None:
            b = q1("SELECT nom FROM boutiques WHERE id=?", (bid,))
            boutique_nom = b[0] if b else ''
        nb_a_traiter = q1(f"SELECT COUNT(*) FROM arrivages WHERE statut IN ('en_attente','partiel'){where_p}", params_p)

        return render_template('admin_arrivages.html', arrivages=arrivages, filtre=filtre, produits=produits,
            boutique_ok=bid is not None, boutique_nom=boutique_nom,
            nb_a_traiter=nb_a_traiter[0] if nb_a_traiter else 0,
            today_iso=datetime.now().strftime('%Y-%m-%d'))
    except Exception as e:
        print(f"❌ Erreur admin_arrivages: {e}")
        flash('Erreur lors du chargement des arrivages')
        return redirect('/dashboard')

@app.route('/admin/arrivages/ajouter', methods=['POST'])
def ajouter_arrivage():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        boutique_id = boutique_active()
        if boutique_id is None:
            flash('❌ Choisissez d\'abord une boutique active (en haut) avant d\'enregistrer un arrivage')
            return redirect('/admin/arrivages')
        fournisseur = request.form.get('fournisseur', '').strip()
        date_arrivage = request.form.get('date_arrivage') or datetime.now().strftime('%Y-%m-%d')
        notes = request.form.get('notes', '').strip()

        noms = request.form.getlist('produit_nom[]')
        qtes = request.form.getlist('quantite[]')
        prix = request.form.getlist('prix_achat[]')
        pids = request.form.getlist('produit_id[]')

        lignes = []
        for i, nom in enumerate(noms):
            nom = (nom or '').strip()
            try:
                qty = round(float(qtes[i] or 0), 3)
                pa = int(float(prix[i])) if i < len(prix) and prix[i] else None
                pid = int(pids[i]) if i < len(pids) and pids[i] else None
            except (ValueError, IndexError):
                continue
            if pid:
                p = q1("SELECT nom FROM produits WHERE id=? AND boutique_id=?", (pid, boutique_id))
                if p:
                    nom = p[0]
                else:
                    pid = None
            if not nom or qty <= 0:
                continue
            lignes.append((pid, nom, qty, pa))
        if not lignes:
            flash('❌ Ajoutez au moins un produit avec une quantité')
            return redirect('/admin/arrivages')

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        arrivage_id = exe('''INSERT INTO arrivages (boutique_id, fournisseur, date_arrivage, notes, statut, employe_id, date_creation)
                             VALUES (?,?,?,?,'en_attente',?,?)''',
                          (boutique_id, fournisseur, date_arrivage, notes, session.get('user_id', 1), now), returning=True)
        if not arrivage_id:
            flash('❌ Échec de l\'enregistrement de l\'arrivage')
            return redirect('/admin/arrivages')
        for pid, nom, qty, pa in lignes:
            exe('''INSERT INTO arrivages_lignes (arrivage_id, produit_id, produit_nom, quantite_recue, quantite_integree, prix_achat)
                   VALUES (?,?,?,?,0,?)''', (arrivage_id, pid, nom, qty, pa))
        flash(f'✅ Arrivage enregistré ({len(lignes)} produit(s)) — le stock n\'est pas encore modifié')
    except Exception as e:
        print(f"❌ Erreur ajouter_arrivage: {e}")
        flash('❌ Erreur lors de l\'enregistrement de l\'arrivage')
    return redirect('/admin/arrivages')

@app.route('/admin/arrivages/integrer/<int:ligne_id>', methods=['POST'])
def integrer_ligne_arrivage(ligne_id):
    """Intègre tout ou partie d'une ligne d'arrivage au stock (via une vraie entrée de stock)."""
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        l = q1('''SELECT l.arrivage_id, l.produit_id, l.produit_nom, l.quantite_recue, l.quantite_integree,
                         l.prix_achat, a.boutique_id, a.fournisseur, a.statut
                  FROM arrivages_lignes l JOIN arrivages a ON l.arrivage_id = a.id WHERE l.id=?''', (ligne_id,))
        if not l:
            flash('❌ Ligne d\'arrivage introuvable')
            return redirect('/admin/arrivages')
        arrivage_id, produit_id_ligne, nom_ligne, recue, integree, prix_ligne, boutique_id, fournisseur, statut = l
        if statut == 'annule':
            flash('❌ Cet arrivage a été annulé')
            return redirect('/admin/arrivages')
        reste = round(float(recue) - float(integree), 3)

        try:
            quantite = round(float(request.form.get('quantite', 0) or 0), 3)
            prix_achat = int(float(request.form.get('prix_achat') or prix_ligne or 0))
        except ValueError:
            flash('❌ Quantité ou prix invalide')
            return redirect('/admin/arrivages')
        if quantite <= 0 or quantite > reste:
            flash(f'❌ La quantité à intégrer doit être comprise entre 0 et {format_qte(reste)}')
            return redirect('/admin/arrivages')
        if prix_achat <= 0:
            flash('❌ Indiquez le prix d\'achat unitaire pour enregistrer l\'entrée de stock')
            return redirect('/admin/arrivages')

        choix = request.form.get('produit_choix', 'nouveau')
        if choix == 'nouveau':
            if quantite != int(quantite):
                flash('❌ Un nouveau produit ne peut être créé qu\'avec une quantité entière (activez ensuite la vente fractionnée si besoin)')
                return redirect('/admin/arrivages')
            try:
                prix_vente = int(float(request.form.get('prix_vente', 0) or 0))
            except ValueError:
                prix_vente = 0
            if prix_vente <= 0:
                flash('❌ Indiquez le prix de vente du nouveau produit')
                return redirect('/admin/arrivages')
            nom_nouveau = request.form.get('nouveau_nom', '').strip() or nom_ligne
            doublon = produit_nom_existe(nom_nouveau, boutique_id)
            if doublon:
                flash(message_doublon_produit(doublon) + ' — choisissez-le dans la liste « Produit du catalogue » pour y ajouter le stock')
                return redirect('/admin/arrivages')
            produit_id = exe("INSERT INTO produits (nom, prix, stock, stock_min, boutique_id) VALUES (?,?,0,5,?)",
                             (nom_nouveau, prix_vente, boutique_id), returning=True)
            if not produit_id:
                flash('❌ Échec de la création du produit')
                return redirect('/admin/arrivages')
        else:
            try:
                produit_id = int(choix)
            except ValueError:
                flash('❌ Produit invalide')
                return redirect('/admin/arrivages')
            if not q1("SELECT 1 FROM produits WHERE id=? AND boutique_id=?", (produit_id, boutique_id)):
                flash('❌ Ce produit n\'appartient pas à la boutique de l\'arrivage')
                return redirect('/admin/arrivages')

        ok, message = _traiter_entree(produit_id, quantite, prix_achat, fournisseur, session.get('user_id', 1))
        if not ok:
            flash(message)
            return redirect('/admin/arrivages')

        exe('''UPDATE arrivages_lignes SET quantite_integree = quantite_integree + ?, produit_id=?, prix_achat=? WHERE id=?''',
            (quantite, produit_id, prix_achat, ligne_id))
        _maj_statut_arrivage(arrivage_id)
        flash(f'📦 {message} — intégré depuis l\'arrivage' +
              (f' (reste {format_qte(reste - quantite)} à intégrer)' if reste - quantite > 0 else ''))
    except Exception as e:
        print(f"❌ Erreur integrer_ligne_arrivage: {e}")
        flash('❌ Erreur lors de l\'intégration au stock')
    return redirect('/admin/arrivages')

@app.route('/admin/arrivages/annuler/<int:id>')
def annuler_arrivage(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        exe("UPDATE arrivages SET statut='annule' WHERE id=? AND statut IN ('en_attente','partiel')", (id,))
        flash('🚫 Arrivage annulé (ce qui avait déjà été intégré au stock reste en place)')
    except Exception as e:
        print(f"❌ Erreur annuler_arrivage: {e}")
        flash('❌ Erreur lors de l\'annulation')
    return redirect('/admin/arrivages')

@app.route('/admin/arrivages/supprimer/<int:id>')
def supprimer_arrivage(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        r = q1("SELECT COALESCE(SUM(quantite_integree),0) FROM arrivages_lignes WHERE arrivage_id=?", (id,))
        if r and float(r[0]) > 0:
            flash('❌ Suppression impossible : une partie de cet arrivage a déjà été intégrée au stock')
            return redirect('/admin/arrivages')
        exe("DELETE FROM arrivages WHERE id=?", (id,))
        flash('🗑️ Arrivage supprimé')
    except Exception as e:
        print(f"❌ Erreur supprimer_arrivage: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/arrivages')


# ══════════════════════════════════════════════════════════════
# 4) CLIENTS — enregistrement manuel + envoi de messages WhatsApp
# ══════════════════════════════════════════════════════════════
@app.route('/admin/clients/ajouter', methods=['POST'])
def ajouter_client():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        telephone = request.form.get('telephone', '').strip()   # numéro WhatsApp de préférence
        adresse = request.form.get('adresse', '').strip()
        if not nom or not telephone:
            flash('❌ Le nom et le numéro (WhatsApp de préférence) sont obligatoires')
            return redirect('/admin/clients')
        if not normaliser_whatsapp(telephone):
            flash('❌ Numéro invalide')
            return redirect('/admin/clients')
        if q1("SELECT nom FROM clients WHERE telephone=?", (telephone,)):
            flash('❌ Un client avec ce numéro existe déjà')
            return redirect('/admin/clients')
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ok = exe("INSERT INTO clients (nom, prenom, telephone, adresse, date_creation) VALUES (?,?,?,?,?)",
                 (nom, prenom, telephone, adresse, now))
        nom_complet = f'{prenom} {nom}'.strip()
        flash(f'✅ Client "{nom_complet}" enregistré' if ok else '❌ Échec de l\'enregistrement du client')
    except Exception as e:
        print(f"❌ Erreur ajouter_client: {e}")
        flash('❌ Erreur lors de l\'enregistrement du client')
    return redirect('/admin/clients')

@app.route('/admin/clients/whatsapp', methods=['POST'])
def clients_whatsapp_file():
    """Prépare la « file d'envoi » : un lien WhatsApp personnalisé par client coché.
    {prenom} et {nom} dans le message sont remplacés pour chaque destinataire."""
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        message = request.form.get('message', '').strip()
        ids = []
        for v in request.form.getlist('client_ids'):
            try:
                ids.append(int(v))
            except ValueError:
                pass
        if not ids:
            flash('❌ Cochez au moins un client')
            return redirect('/admin/clients')
        if not message:
            flash('❌ Écrivez le message à envoyer')
            return redirect('/admin/clients')
        ids = ids[:300]

        rows = qall("SELECT id, nom, COALESCE(prenom,''), telephone FROM clients WHERE id = ANY(%s) ORDER BY nom", (ids,))
        destinataires, sans_numero = [], []
        for cid, nom, prenom, tel in rows:
            nom_complet = f'{prenom} {nom}'.strip()
            prenom_aff = prenom or (nom.split(' ')[0] if nom else '')
            texte = message.replace('{prenom}', prenom_aff).replace('{nom}', nom_complet)
            lien = lien_whatsapp(tel, texte)
            if lien:
                destinataires.append({'id': cid, 'nom': nom_complet, 'telephone': tel, 'lien': lien})
            else:
                sans_numero.append(nom_complet)
        return render_template('admin_whatsapp_file.html', destinataires=destinataires,
                               sans_numero=sans_numero, message=message)
    except Exception as e:
        print(f"❌ Erreur clients_whatsapp_file: {e}")
        flash('❌ Erreur lors de la préparation des messages')
        return redirect('/admin/clients')


# ══════════════════════════════════════════════════════════════
# BUSINESS PLAN — même page qu'avant + évènements programmés.
# ⚠️ Supprimer l'ancienne fonction admin_business_plan() dans app.py
#    (deux fonctions du même nom feraient planter Flask au démarrage).
# ══════════════════════════════════════════════════════════════
@app.route('/admin/business-plan')
def admin_business_plan():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        taches_a_faire = qall('''SELECT t.id, t.titre, t.description, t.priorite, t.date_echeance,
                                         t.date_creation, u.nom
                                  FROM taches_business_plan t LEFT JOIN users u ON t.employe_id = u.id
                                  WHERE t.statut = 'a_faire'
                                  ORDER BY CASE t.priorite WHEN 'haute' THEN 0 WHEN 'normale' THEN 1 ELSE 2 END,
                                           t.date_echeance ASC NULLS LAST, t.id DESC''')
        taches_faites = qall('''SELECT t.id, t.titre, t.description, t.priorite, t.date_echeance,
                                        t.date_validation, u.nom
                                 FROM taches_business_plan t LEFT JOIN users u ON t.employe_id = u.id
                                 WHERE t.statut = 'fait'
                                 ORDER BY t.date_validation DESC LIMIT 100''')
        nb_faites = q1("SELECT COUNT(*) FROM taches_business_plan WHERE statut='fait'")
        nb_faites = nb_faites[0] if nb_faites else 0

        evenements_a_venir = qall('''SELECT e.id, e.nom, e.type_evenement, e.date_debut, e.date_fin,
                                            COALESCE(b.nom, 'Toutes les boutiques'), e.notes
                                     FROM evenements e LEFT JOIN boutiques b ON e.boutique_id = b.id
                                     WHERE e.statut = 'prevu'
                                     ORDER BY e.date_debut ASC NULLS LAST, e.id DESC''')
        evenements_passes = qall('''SELECT e.id, e.nom, e.type_evenement, e.date_debut, e.date_fin,
                                            COALESCE(b.nom, 'Toutes les boutiques'), e.notes
                                     FROM evenements e LEFT JOIN boutiques b ON e.boutique_id = b.id
                                     WHERE e.statut = 'termine'
                                     ORDER BY e.date_debut DESC LIMIT 50''')
        boutiques = qall("SELECT id, nom FROM boutiques WHERE actif = 1 ORDER BY nom")

        return render_template('admin_business_plan.html', taches_a_faire=taches_a_faire,
            taches_faites=taches_faites, nb_a_faire=len(taches_a_faire), nb_faites=nb_faites,
            evenements_a_venir=evenements_a_venir, evenements_passes=evenements_passes,
            boutiques=boutiques, today_iso=datetime.now().strftime('%Y-%m-%d'))
    except Exception as e:
        print(f"❌ Erreur admin_business_plan: {e}")
        flash('Erreur lors du chargement du business plan')
        return redirect('/dashboard')


# ══════════════════════════════════════════════════════════════
# DOUBLONS DE PRODUITS — liste des produits qui portent le même nom dans une même boutique
# (pour nettoyer ceux qui existaient avant l'interdiction des doublons)
# ══════════════════════════════════════════════════════════════
@app.route('/admin/produits/doublons')
def admin_produits_doublons():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        rows = qall('''SELECT p.id, p.nom, COALESCE(b.nom, ''), p.stock, COALESCE(p.actif, 1),
                               (SELECT COUNT(*) FROM sorties WHERE produit_id = p.id)
                             + (SELECT COUNT(*) FROM entrees WHERE produit_id = p.id)
                             + (SELECT COUNT(*) FROM pertes WHERE produit_id = p.id) AS mouvements,
                               p.boutique_id, LOWER(TRIM(p.nom)) AS cle
                        FROM produits p LEFT JOIN boutiques b ON p.boutique_id = b.id
                        WHERE (p.boutique_id, LOWER(TRIM(p.nom))) IN (
                              SELECT boutique_id, LOWER(TRIM(nom)) FROM produits
                              GROUP BY boutique_id, LOWER(TRIM(nom)) HAVING COUNT(*) > 1)
                        ORDER BY b.nom, cle, p.id''')
        groupes, index = [], {}
        for r in rows:
            cle = (r[6], r[7])
            if cle not in index:
                index[cle] = {'nom': r[1], 'boutique': r[2], 'produits': []}
                groupes.append(index[cle])
            index[cle]['produits'].append(r[:6])
        return render_template('admin_produits_doublons.html', groupes=groupes)
    except Exception as e:
        print(f"❌ Erreur admin_produits_doublons: {e}")
        flash('Erreur lors du chargement des doublons')
        return redirect('/admin/produits')


# Création des tables au chargement (app.py appelle init_db() juste avant d'importer ce module)
init_db_ajouts()
