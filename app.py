from flask import Flask, render_template, request, redirect, session, flash, jsonify, url_for, send_file
from flask_mail import Mail, Message
from datetime import datetime, timedelta
import hashlib, os, random, string, io, json, uuid, socket, zipfile
import bcrypt
import psycopg2
import psycopg2.extras
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from time import time, sleep
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hitna_secret')
# Session persistante (30 jours) : sans ça, Flask crée par défaut une session
# "de navigateur" que Chrome peut effacer quand l'app installée (PWA) est
# fermée/tuée en arrière-plan sur mobile — ce qui déconnecte l'employé, qui
# ne peut alors plus jamais se reconnecter s'il est hors ligne à ce moment-là.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# ──────────────────────────────────────────────────────────────
# CORS — uniquement pour les endpoints publics /api/*, utilisés par
# le site web HITNA (hébergé séparément) pour envoyer commandes/messages.
# ──────────────────────────────────────────────────────────────
@app.after_request
def add_cors_headers(response):
    if request.path.startswith('/api/'):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

@app.route('/api/<path:_p>', methods=['OPTIONS'])
def api_cors_preflight(_p):
    return ('', 204)

# ──────────────────────────────────────────────────────────────
# CONFIGURATION EMAIL (Gmail)
# ──────────────────────────────────────────────────────────────
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'hitnasuperette@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = ('HITNA Gestion', 'hitnasuperette@gmail.com')

mail = Mail(app)

# ══════════════════════════════════════════════════════════════
# LIMITEUR DE TENTATIVES DE CONNEXION (anti brute-force)
# En mémoire (par processus) : suffisant pour dissuader un enchaînement
# rapide de tentatives ; se réinitialise si le service redémarre.
# ══════════════════════════════════════════════════════════════
_login_attempts = {}   # { ip: [timestamps des échecs récents] }
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 600     # fenêtre glissante : 10 minutes
LOGIN_LOCKOUT_SECONDS = 300    # verrouillage : 5 minutes

def _get_client_ip():
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or 'inconnu'

def login_is_locked(ip):
    now_ts = time()
    attempts = [t for t in _login_attempts.get(ip, []) if now_ts - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[ip] = attempts
    if len(attempts) < LOGIN_MAX_ATTEMPTS:
        return False, 0
    dernier = attempts[-1]
    if now_ts - dernier < LOGIN_LOCKOUT_SECONDS:
        return True, int(LOGIN_LOCKOUT_SECONDS - (now_ts - dernier))
    return False, 0

def login_record_failure(ip):
    _login_attempts.setdefault(ip, []).append(time())

def login_reset(ip):
    _login_attempts.pop(ip, None)

# ══════════════════════════════════════════════════════════════
# SYSTÈME DE CACHE AVANCÉ
# ══════════════════════════════════════════════════════════════
_cache = {}
CACHE_TTL = {
    'produits': 4,       # 4secondes
    'ventes': 4,          # 4secondes
    'dashboard': 4,       # 4secondes
    'stats': 4,          # 4secondes
    'notifications': 4,   # 4secondes
}

def get_cached(key, ttl=60):
    """Récupérer une valeur du cache avec TTL personnalisé"""
    if key in _cache:
        value, timestamp = _cache[key]
        if time() - timestamp < ttl:
            return value
        del _cache[key]
    return None

def set_cached(key, value):
    """Stocker une valeur dans le cache"""
    _cache[key] = (value, time())

def clear_cache():
    """Vider le cache"""
    _cache.clear()

def cached_query(sql, params=(), ttl=120):
    """Exécute une requête avec mise en cache"""
    key = f"q_{sql}_{str(params)}"
    result = get_cached(key, ttl)
    if result is not None:
        return result
    result = qall(sql, params)
    set_cached(key, result)
    return result

# ──────────────────────────────────────────────────────────────
# CONNEXION POSTGRESQL AVEC POOL
# Évite d'ouvrir une nouvelle connexion TCP à chaque requête.
# Chaque connexion coûtait 200-500ms sur Render → lenteur.
# ──────────────────────────────────────────────────────────────
from psycopg2 import pool as pg_pool

_db_pool = None

def _get_pool():
    global _db_pool
    if _db_pool is None:
        url = os.environ.get('DATABASE_URL', '')
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        if not url:
            raise RuntimeError("DATABASE_URL manquante.")
        _db_pool = pg_pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=url)
        print("✅ Pool de connexions PostgreSQL initialisé")
    return _db_pool

def get_db():
    return _get_pool().getconn()

def release_db(conn):
    """Remettre la connexion dans le pool plutôt que la fermer."""
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────
# SAUVEGARDE DE LA BASE DE DONNÉES
# Le plan gratuit de Supabase n'inclut aucune sauvegarde automatique.
# On génère donc ici un export JSON complet des tables métier, utilisable
# côté Flask sans dépendre du binaire pg_dump (absent de l'environnement
# Render standard). Voir /admin/backup/export.
# ──────────────────────────────────────────────────────────────
BACKUP_TABLES = [
    'users', 'produits', 'categories_produits', 'unites_mesure', 'fournisseurs',
    'sorties', 'entrees', 'pertes', 'alertes_produits', 'notifications',
    'archive_ventes', 'archive_entrees', 'archive_pertes', 'archive_recap',
    'commandes', 'messages_contact',
]

def generer_backup_json():
    """Dump complet des tables métier -> dict {table: {colonnes, lignes}}."""
    data = {}
    for table in BACKUP_TABLES:
        conn = None
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {table}")
            colonnes = [d[0] for d in cur.description]
            lignes = cur.fetchall()
            data[table] = {'colonnes': colonnes, 'lignes': [list(r) for r in lignes]}
            cur.close()
            conn.commit()
        except Exception as e:
            data[table] = {'erreur': str(e)}
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                release_db(conn)
    return data

def q1(sql, params=()):
    """fetchone — retourne un tuple ou None."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql.replace('?', '%s'), params)
        row = cur.fetchone()
        cur.close()
        conn.commit()
        return row
    except Exception as e:
        print(f"❌ Erreur q1: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return None
    finally:
        if conn:
            release_db(conn)

def qall(sql, params=()):
    """fetchall — retourne une liste de tuples."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql.replace('?', '%s'), params)
        rows = cur.fetchall()
        cur.close()
        conn.commit()
        return rows
    except Exception as e:
        print(f"❌ Erreur qall: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return []
    finally:
        if conn:
            release_db(conn)

def exe(sql, params=(), returning=False):
    """INSERT / UPDATE / DELETE avec commit. returning=True retourne le nouvel id.
    Retourne False en cas d'échec (permet aux routes de détecter un vrai échec)."""
    conn = None
    try:
        sql2 = sql.replace('?', '%s')
        if returning and 'INSERT' in sql2.upper() and 'RETURNING' not in sql2.upper():
            sql2 += ' RETURNING id'
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql2, params)
        result = cur.fetchone()[0] if returning else True
        conn.commit()
        cur.close()
        clear_cache()
        return result
    except Exception as e:
        print(f"❌ Erreur exe: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            release_db(conn)

# ──────────────────────────────────────────────────────────────
# INITIALISATION BASE DE DONNÉES (SÉCURISÉE)
# ──────────────────────────────────────────────────────────────
def init_db():
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='users'")
        tables_existent = c.fetchone()[0] > 0

        if tables_existent:
            print("✅ Tables existantes - vérification des migrations uniquement")
        else:
            print("⚠️ Tables non trouvées - Création des tables...")
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, role TEXT, role_personnalise TEXT,
            password_hash TEXT, nom TEXT, actif INTEGER DEFAULT 1,
            motif_absence TEXT DEFAULT '', permissions TEXT DEFAULT 'vente',
            email TEXT DEFAULT '')''')

        c.execute('''CREATE TABLE IF NOT EXISTS produits (
            id SERIAL PRIMARY KEY, nom TEXT, prix INTEGER,
            stock INTEGER DEFAULT 0, stock_min INTEGER DEFAULT 5)''')

        c.execute('''CREATE TABLE IF NOT EXISTS sorties (
            id SERIAL PRIMARY KEY, produit_id INTEGER, quantite INTEGER,
            prix_unitaire INTEGER, total INTEGER, date_sortie TEXT,
            client TEXT, employe_id INTEGER)''')

        c.execute('''CREATE TABLE IF NOT EXISTS entrees (
            id SERIAL PRIMARY KEY, produit_id INTEGER, quantite INTEGER,
            prix_unitaire INTEGER, total INTEGER, date_entree TEXT,
            fournisseur TEXT, employe_id INTEGER)''')

        c.execute('''CREATE TABLE IF NOT EXISTS pertes (
            id SERIAL PRIMARY KEY, produit_id INTEGER, quantite INTEGER,
            prix_unitaire INTEGER, total INTEGER, motif TEXT,
            date_perte TEXT, employe_id INTEGER)''')

        c.execute('''CREATE TABLE IF NOT EXISTS fournisseurs (
            id SERIAL PRIMARY KEY, nom TEXT UNIQUE, produits TEXT,
            telephone TEXT, email TEXT, adresse TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY, user_id INTEGER, type TEXT,
            title TEXT, message TEXT, lien TEXT,
            est_lu INTEGER DEFAULT 0, date_creation TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS alertes_produits (
            id SERIAL PRIMARY KEY, produit_id INTEGER,
            seuil INTEGER DEFAULT 5, actif INTEGER DEFAULT 1, dernier_envoi TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS reset_tokens (
            id SERIAL PRIMARY KEY, user_id INTEGER, token TEXT,
            expires_at TEXT, used INTEGER DEFAULT 0)''')

        c.execute('''CREATE TABLE IF NOT EXISTS archive_ventes (
            id INTEGER, produit_id INTEGER, quantite INTEGER,
            prix_unitaire INTEGER, total INTEGER, date_vente TEXT,
            employe_id INTEGER, client TEXT, archive_date TEXT,
            semaine INTEGER, annee INTEGER, produit_nom TEXT, employe_nom TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS archive_entrees (
            id INTEGER, produit_id INTEGER, quantite INTEGER,
            prix_unitaire INTEGER, total INTEGER, date_entree TEXT,
            fournisseur TEXT, employe_id INTEGER, archive_date TEXT,
            semaine INTEGER, annee INTEGER, produit_nom TEXT, employe_nom TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS archive_pertes (
            id INTEGER, produit_id INTEGER, quantite INTEGER,
            prix_unitaire INTEGER, total INTEGER, motif TEXT,
            date_perte TEXT, employe_id INTEGER, archive_date TEXT,
            semaine INTEGER, annee INTEGER, produit_nom TEXT, employe_nom TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS archive_recap (
            id SERIAL PRIMARY KEY, semaine INTEGER, annee INTEGER,
            date_debut TEXT, date_fin TEXT, nb_ventes INTEGER,
            total_ventes INTEGER, nb_entrees INTEGER, total_achats INTEGER,
            archive_date TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS unites_mesure (
            id SERIAL PRIMARY KEY, 
            nom TEXT UNIQUE, 
            symbole TEXT,
            description TEXT,
            actif INTEGER DEFAULT 1)''')

        c.execute('''CREATE TABLE IF NOT EXISTS categories_produits (
            id SERIAL PRIMARY KEY,
            nom TEXT UNIQUE,
            icone TEXT DEFAULT '📦',
            actif INTEGER DEFAULT 1)''')

        c.execute('''CREATE TABLE IF NOT EXISTS commandes (
            id SERIAL PRIMARY KEY,
            nom_client TEXT,
            telephone TEXT,
            email TEXT DEFAULT '',
            adresse TEXT DEFAULT '',
            details TEXT,
            statut TEXT DEFAULT 'nouvelle',
            date_creation TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS messages_contact (
            id SERIAL PRIMARY KEY,
            nom TEXT,
            email TEXT DEFAULT '',
            telephone TEXT DEFAULT '',
            sujet TEXT DEFAULT '',
            message TEXT,
            statut TEXT DEFAULT 'non_lu',
            date_creation TEXT)''')

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='produits' AND column_name='categorie_id'")
            if not c.fetchone():
                c.execute("ALTER TABLE produits ADD COLUMN categorie_id INTEGER REFERENCES categories_produits(id)")
                print("✅ Colonne 'categorie_id' ajoutée à produits")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne categorie_id: {e}")

        c.execute("SELECT COUNT(*) FROM categories_produits")
        row = c.fetchone()
        if row and row[0] == 0:
            categories_defaut = [
                ('Légumes', '🥬'), ('Fruits', '🍎'), ('Lait & Produits laitiers', '🥛'),
                ('Céréales', '🌾'), ('Viandes & Poissons', '🥩'), ('Boissons', '🥤'),
                ('Épicerie', '🛒'), ('Hygiène & Entretien', '🧼'), ('Autre', '📦'),
            ]
            for cat in categories_defaut:
                c.execute("INSERT INTO categories_produits (nom, icone, actif) VALUES (%s,%s,1)", cat)
            print("✅ Catégories de produits par défaut ajoutées")

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='produits' AND column_name='unite_id'")
            if not c.fetchone():
                c.execute("ALTER TABLE produits ADD COLUMN unite_id INTEGER REFERENCES unites_mesure(id)")
                print("✅ Colonne 'unite_id' ajoutée à produits")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne unite_id: {e}")

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='produits' AND column_name='valeur_unite'")
            if not c.fetchone():
                c.execute("ALTER TABLE produits ADD COLUMN valeur_unite NUMERIC")
                print("✅ Colonne 'valeur_unite' ajoutée à produits (ex: 7 pour '7 g')")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne valeur_unite: {e}")

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='produits' AND column_name='vendu_par_carton'")
            if not c.fetchone():
                c.execute("ALTER TABLE produits ADD COLUMN vendu_par_carton INTEGER DEFAULT 0")
                print("✅ Colonne 'vendu_par_carton' ajoutée à produits")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne vendu_par_carton: {e}")

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='produits' AND column_name='unites_par_carton'")
            if not c.fetchone():
                c.execute("ALTER TABLE produits ADD COLUMN unites_par_carton INTEGER")
                print("✅ Colonne 'unites_par_carton' ajoutée à produits (vente au détail depuis un carton)")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne unites_par_carton: {e}")

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='produits' AND column_name='prix_carton'")
            if not c.fetchone():
                c.execute("ALTER TABLE produits ADD COLUMN prix_carton INTEGER")
                print("✅ Colonne 'prix_carton' ajoutée à produits (prix du carton complet, distinct du prix unitaire)")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne prix_carton: {e}")

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='sorties' AND column_name='groupe_vente'")
            if not c.fetchone():
                c.execute("ALTER TABLE sorties ADD COLUMN groupe_vente TEXT")
                c.execute("CREATE INDEX IF NOT EXISTS idx_sorties_groupe ON sorties(groupe_vente)")
                print("✅ Colonne 'groupe_vente' ajoutée à sorties (paniers multi-produits + reçus)")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne groupe_vente: {e}")

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='archive_ventes' AND column_name='groupe_vente'")
            if not c.fetchone():
                c.execute("ALTER TABLE archive_ventes ADD COLUMN groupe_vente TEXT")
                c.execute("CREATE INDEX IF NOT EXISTS idx_archive_ventes_groupe ON archive_ventes(groupe_vente)")
                print("✅ Colonne 'groupe_vente' ajoutée à archive_ventes (reçus des ventes archivées)")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne groupe_vente à archive_ventes: {e}")

        c.execute('CREATE INDEX IF NOT EXISTS idx_sorties_date ON sorties(date_sortie)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_entrees_date ON entrees(date_entree)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_produits_nom ON produits(nom)')

        c.execute("SELECT COUNT(*) FROM unites_mesure")
        row = c.fetchone()
        if row and row[0] == 0:
            unites_defaut = [
                ('Litre', 'L', 'Litre (1L)'),
                ('Demi-litre', '1/2 L', 'Demi-litre (0.5L)'),
                ('Quart de litre', '1/4 L', 'Quart de litre (0.25L)'),
                ('Kilogramme', 'kg', 'Kilogramme (1kg)'),
                ('Demi-kilogramme', '1/2 kg', 'Demi-kilogramme (500g)'),
                ('Gramme', 'g', 'Gramme'),
                ('Millilitre', 'ml', 'Millilitre'),
                ('Pièce', 'pc', 'À l\'unité'),
            ]
            for u in unites_defaut:
                c.execute("INSERT INTO unites_mesure (nom, symbole, description, actif) VALUES (%s,%s,%s,%s)", 
                          (u[0], u[1], u[2], 1))
            print("✅ Unités de mesure par défaut ajoutées")

        c.execute('SELECT COUNT(*) FROM users')
        row = c.fetchone()
        if row and row[0] == 0:
            admin_hash = hash_password('admin123')
            c.execute("INSERT INTO users (role,role_personnalise,password_hash,nom,actif,permissions) VALUES (%s,%s,%s,%s,%s,%s)",
                      ('admin','Administrateur', admin_hash, 'Administrateur', 1, 'admin'))
            emp_hash = hash_password('emp123')
            c.execute("INSERT INTO users (role,role_personnalise,password_hash,nom,actif,permissions) VALUES (%s,%s,%s,%s,%s,%s)",
                      ('employe','Employé', emp_hash, 'Employé', 1, 'vente'))
            print("✅ Utilisateurs par défaut créés")

        conn.commit()
        c.close()
        release_db(conn)
        conn = None
        print("✅ Base de données initialisée")
    except Exception as e:
        print(f"❌ Erreur init_db: {e}")
    finally:
        if conn:
            release_db(conn)

# ──────────────────────────────────────────────────────────────
# ARCHIVAGE HEBDOMADAIRE
# ──────────────────────────────────────────────────────────────
def get_derniere_archive():
    try:
        row = q1("SELECT semaine FROM archive_recap ORDER BY id DESC LIMIT 1")
        return row[0] if row else 0
    except Exception:
        return 0

def archiver_hebdomadaire():
    try:
        conn = get_db()
        cm = conn.cursor()
        today = datetime.now()
        debut = today - timedelta(days=7)
        fin = today - timedelta(days=1)
        sem = debut.isocalendar()[1]
        annee = debut.isocalendar()[0]
        now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cm.execute('''SELECT s.id,s.produit_id,s.quantite,s.prix_unitaire,s.total,
                             s.date_sortie,s.client,s.employe_id,p.nom,u.nom,s.groupe_vente
                      FROM sorties s JOIN produits p ON s.produit_id=p.id
                      JOIN users u ON s.employe_id=u.id
                      WHERE DATE(s.date_sortie)>=%s AND DATE(s.date_sortie)<=%s''',
                   (debut.strftime('%Y-%m-%d'), fin.strftime('%Y-%m-%d')))
        ventes = cm.fetchall()
        for v in ventes:
            cm.execute('''INSERT INTO archive_ventes VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                       (v[0],v[1],v[2],v[3],v[4],v[5],v[7],v[6],now_s,sem,annee,v[8],v[9],v[10]))
            cm.execute("DELETE FROM sorties WHERE id=%s",(v[0],))

        cm.execute('''SELECT e.id,e.produit_id,e.quantite,e.prix_unitaire,e.total,
                             e.date_entree,e.fournisseur,e.employe_id,p.nom,u.nom
                      FROM entrees e JOIN produits p ON e.produit_id=p.id
                      JOIN users u ON e.employe_id=u.id
                      WHERE DATE(e.date_entree)>=%s AND DATE(e.date_entree)<=%s''',
                   (debut.strftime('%Y-%m-%d'), fin.strftime('%Y-%m-%d')))
        entrees = cm.fetchall()
        for e in entrees:
            cm.execute('''INSERT INTO archive_entrees VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                       (e[0],e[1],e[2],e[3],e[4],e[5],e[6],e[7],now_s,sem,annee,e[8],e[9]))
            cm.execute("DELETE FROM entrees WHERE id=%s",(e[0],))

        cm.execute('''SELECT p.id,p.produit_id,p.quantite,p.prix_unitaire,p.total,
                             p.motif,p.date_perte,p.employe_id,pr.nom,u.nom
                      FROM pertes p JOIN produits pr ON p.produit_id=pr.id
                      JOIN users u ON p.employe_id=u.id
                      WHERE DATE(p.date_perte)>=%s AND DATE(p.date_perte)<=%s''',
                   (debut.strftime('%Y-%m-%d'), fin.strftime('%Y-%m-%d')))
        pertes = cm.fetchall()
        for p in pertes:
            cm.execute('''INSERT INTO archive_pertes VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                       (p[0],p[1],p[2],p[3],p[4],p[5],p[6],p[7],now_s,sem,annee,p[8],p[9]))
            cm.execute("DELETE FROM pertes WHERE id=%s",(p[0],))

        tv = sum(v[4] for v in ventes) if ventes else 0
        ta = sum(e[4] for e in entrees) if entrees else 0
        cm.execute('''INSERT INTO archive_recap
                      (semaine,annee,date_debut,date_fin,nb_ventes,total_ventes,nb_entrees,total_achats,archive_date)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                   (sem,annee,debut.strftime('%Y-%m-%d'),fin.strftime('%Y-%m-%d'),
                    len(ventes),tv,len(entrees),ta,now_s))
        conn.commit()
        cm.close()
        release_db(conn)
    except Exception as e:
        print(f"❌ Erreur archiver_hebdomadaire: {e}")

def archiver_si_necessaire():
    try:
        today = datetime.now()
        if today.weekday() == 0 and today.hour < 2:
            if get_derniere_archive() != today.isocalendar()[1]:
                archiver_hebdomadaire()
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────
# HELPERS MÉTIER
# ──────────────────────────────────────────────────────────────
@app.context_processor
def inject_now():
    ctx = {'date_actuelle': datetime.now().strftime('%d/%m/%Y %H:%M')}
    try:
        is_admin = session.get('role') == 'admin'
        perms = (session.get('permissions') or '').split(',')
        if is_admin or 'commandes' in perms:
            nc = q1("SELECT COUNT(*) FROM commandes WHERE statut='nouvelle'")
            ctx['nb_commandes_nouvelles'] = nc[0] if nc else 0
        if is_admin:
            nm = q1("SELECT COUNT(*) FROM messages_contact WHERE statut='non_lu'")
            ctx['nb_messages_non_lus'] = nm[0] if nm else 0
    except Exception:
        ctx['nb_commandes_nouvelles'] = 0
        ctx['nb_messages_non_lus'] = 0
    return ctx

def get_all_roles():
    try:
        roles_raw = qall("SELECT DISTINCT role, role_personnalise FROM users WHERE actif=1 ORDER BY role")
        result, seen = [], set()
        for role, rp in roles_raw:
            if rp and rp not in seen:
                result.append({'role_base':role,'role_affiche':rp})
                seen.add(rp)
            elif role not in seen:
                result.append({'role_base':role,'role_affiche':'Administrateur' if role=='admin' else 'Employé'})
                seen.add(role)
        return result
    except Exception:
        return []

def creer_notification(user_id, type_n, titre, message, lien=None):
    try:
        exe("INSERT INTO notifications (user_id,type,title,message,lien,date_creation) VALUES (?,?,?,?,?,?)",
            (user_id,type_n,titre,message,lien,datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    except Exception as e:
        print(f"❌ Erreur creer_notification: {e}")

def envoyer_notification_a_tous(type_n, titre, message, lien=None):
    try:
        users = qall("SELECT id FROM users WHERE actif=1")
        for u in users:
            creer_notification(u[0],type_n,titre,message,lien)
    except Exception as e:
        print(f"❌ Erreur envoyer_notification_a_tous: {e}")

_last_alertes_check = 0

def verifier_alertes_stock():
    """Vérifie les stocks bas et notifie les admins.
    Throttlée à un contrôle max toutes les 5 minutes (indépendamment du cache
    général, qui est vidé à chaque écriture) car cette fonction est appelée
    à chaque chargement du dashboard ET après chaque vente."""
    global _last_alertes_check
    try:
        now_ts = time()
        if now_ts - _last_alertes_check < 300:
            return
        _last_alertes_check = now_ts

        produits = qall('''SELECT p.id,p.nom,p.stock,COALESCE(a.seuil,p.stock_min,5)
            FROM produits p LEFT JOIN alertes_produits a ON p.id=a.produit_id AND a.actif=1
            WHERE p.stock<=COALESCE(a.seuil,p.stock_min,5)''')
        if not produits:
            return
        admins = qall("SELECT id FROM users WHERE role='admin' AND actif=1")
        if not admins:
            return

        # Une seule requête pour récupérer les notifications déjà envoyées dans
        # les dernières 24h, au lieu d'une requête par (produit, admin) — c'était
        # jusqu'à N x M requêtes séquentielles à chaque appel.
        deja_notifies = qall('''SELECT user_id, message FROM notifications
            WHERE type='stock_bas' AND date_creation::timestamp > NOW() - INTERVAL '1 day' ''')
        deja_set = set()
        for n in deja_notifies:
            msg = n[1] or ''
            for p in produits:
                if p[1] in msg:
                    deja_set.add((n[0], p[0]))

        for p in produits:
            for a in admins:
                if (a[0], p[0]) not in deja_set:
                    creer_notification(a[0],'stock_bas','⚠️ Stock bas',
                        f'Le produit "{p[1]}" n\'a plus que {p[2]} unités (seuil: {p[3]})','/admin/produits')
    except Exception as e:
        print(f"❌ Erreur verifier_alertes_stock: {e}")

def generate_reset_token(user_id):
    try:
        token = ''.join(random.choices(string.ascii_letters+string.digits, k=50))
        expires = (datetime.now()+timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        exe("INSERT INTO reset_tokens (user_id,token,expires_at) VALUES (?,?,?)",(user_id,token,expires))
        return token
    except Exception:
        return None

def check_perm(perm):
    try:
        if session.get('role')=='admin':
            return True
        r = q1("SELECT permissions FROM users WHERE id=?",(session.get('user_id'),))
        return r and perm in r[0].split(',')
    except Exception:
        return False

# ──────────────────────────────────────────────────────────────
# MOTS DE PASSE — bcrypt (salé, résistant au brute-force).
# Les comptes créés avant cette mise à jour ont un hash SHA-256 non salé
# (toujours 64 caractères hexadécimaux) ; verify_password() les reconnaît
# et les migre automatiquement vers bcrypt dès la prochaine connexion
# réussie, sans que personne n'ait à réinitialiser son mot de passe.
# ──────────────────────────────────────────────────────────────
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def _is_legacy_sha256(stored_hash):
    return bool(stored_hash) and len(stored_hash) == 64 and all(c in '0123456789abcdef' for c in stored_hash.lower())

def verify_password(password, stored_hash, user_id=None):
    if not stored_hash:
        return False
    if _is_legacy_sha256(stored_hash):
        ok = hashlib.sha256(password.encode('utf-8')).hexdigest() == stored_hash
        if ok and user_id:
            try:
                exe("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
            except Exception as e:
                print(f"⚠️ Migration bcrypt échouée pour user {user_id}: {e}")
        return ok
    try:
        return bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
    except Exception:
        return False

# ──────────────────────────────────────────────────────────────
# ROUTES AUTH
# ──────────────────────────────────────────────────────────────
@app.route('/')
def accueil():
    return redirect('/login')

@app.route('/login', methods=['GET','POST'])
def login():
    try:
        if request.method == 'POST':
            ip = _get_client_ip()
            locked, attente_s = login_is_locked(ip)
            if locked:
                minutes = max(1, attente_s // 60)
                flash(f'❌ Trop de tentatives échouées. Réessayez dans environ {minutes} minute(s).')
                return redirect('/login')

            sel = request.form.get('role', '')
            password = request.form.get('password', '')

            candidats = qall("""
                SELECT id, nom, actif, role_personnalise, role, permissions, password_hash
                FROM users
                WHERE role_personnalise = %s OR role = %s
            """, (sel, sel))

            if not candidats:
                rb = None
                if sel == 'Administrateur':
                    rb = 'admin'
                elif sel == 'Employé':
                    rb = 'employe'
                if rb:
                    candidats = qall("""
                        SELECT id, nom, actif, role_personnalise, role, permissions, password_hash
                        FROM users
                        WHERE role = %s
                    """, (rb,))

            user = None
            for c in candidats:
                if verify_password(password, c[6], user_id=c[0]):
                    user = c
                    break
            
            if user:
                if user[2] == 0:
                    flash('❌ Compte désactivé.')
                    return redirect('/login')
                
                login_reset(ip)
                session.permanent = True
                session.update({
                    'user_id': user[0],
                    'role': user[4],
                    'user_nom': user[1],
                    'role_affiche': user[3] or ('Administrateur' if user[4] == 'admin' else 'Employé'),
                    'permissions': user[5]
                })
                
                flash(f'✅ Bonjour {user[1]} !')
                return redirect('/dashboard' if user[4] == 'admin' else '/vente')
            
            login_record_failure(ip)
            flash('❌ Identifiants incorrects')
        
        roles = get_all_roles()
        return render_template('login.html', roles=roles)
        
    except Exception as e:
        print(f"❌ Erreur login: {e}")
        flash('Erreur de connexion')
        return redirect('/login')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/changer_mdp', methods=['GET','POST'])
def changer_mdp():
    try:
        if 'user_id' not in session:
            return redirect('/login')
        
        if request.method == 'POST':
            pwd = request.form.get('new_password', '')
            if len(pwd) < 4:
                flash('❌ Minimum 4 caractères')
                return redirect('/changer_mdp')
            
            exe("UPDATE users SET password_hash=? WHERE id=?", 
                (hash_password(pwd), session['user_id']))
            flash('✅ Mot de passe changé !')
            return redirect('/dashboard' if session['role'] == 'admin' else '/vente')
        
        return render_template('changer_mdp.html')
    except Exception as e:
        print(f"❌ Erreur changer_mdp: {e}")
        flash('Erreur lors du changement de mot de passe')
        return redirect('/dashboard' if session.get('role') == 'admin' else '/vente')

# ══════════════════════════════════════════════════════════════
# ROUTES PRINCIPALES AVEC CACHE
# ══════════════════════════════════════════════════════════════

@app.route('/admin/categories')
def admin_categories():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        categories = qall("SELECT * FROM categories_produits ORDER BY nom")
        return render_template('admin_categories.html', categories=categories)
    except Exception as e:
        print(f"❌ Erreur admin_categories: {e}")
        flash('Erreur lors du chargement des catégories')
        return redirect('/dashboard')

@app.route('/admin/categories/ajouter', methods=['POST'])
def ajouter_categorie():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '').strip()
        icone = request.form.get('icone', '').strip() or '📦'
        if not nom:
            flash('❌ Le nom de la catégorie est obligatoire')
            return redirect('/admin/categories')
        ok = exe("INSERT INTO categories_produits (nom, icone, actif) VALUES (?,?,1)", (nom, icone))
        if ok:
            flash(f'✅ Catégorie "{nom}" ajoutée')
        else:
            flash(f'❌ Échec de l\'ajout — ce nom existe peut-être déjà')
    except Exception as e:
        print(f"❌ Erreur ajouter_categorie: {e}")
        flash('❌ Erreur lors de l\'ajout (nom peut-être déjà utilisé)')
    return redirect('/admin/categories')

@app.route('/admin/categories/modifier/<int:id>', methods=['POST'])
def modifier_categorie(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '').strip()
        icone = request.form.get('icone', '').strip() or '📦'
        actif = 1 if request.form.get('actif') else 0
        exe("UPDATE categories_produits SET nom=?, icone=?, actif=? WHERE id=?", (nom, icone, actif, id))
        flash(f'✅ Catégorie "{nom}" modifiée')
    except Exception as e:
        print(f"❌ Erreur modifier_categorie: {e}")
        flash('❌ Erreur lors de la modification')
    return redirect('/admin/categories')

@app.route('/admin/categories/supprimer/<int:id>')
def supprimer_categorie(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        used = q1("SELECT COUNT(*) FROM produits WHERE categorie_id=?", (id,))
        if used and used[0] > 0:
            flash('❌ Cette catégorie est utilisée par des produits. Réaffectez-les d\'abord.')
            return redirect('/admin/categories')
        cat = q1("SELECT nom FROM categories_produits WHERE id=?", (id,))
        if cat:
            exe("DELETE FROM categories_produits WHERE id=?", (id,))
            flash(f'🗑️ Catégorie "{cat[0]}" supprimée')
    except Exception as e:
        print(f"❌ Erreur supprimer_categorie: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/categories')

# ══════════════════════════════════════════════════════════════
# COMMANDES (issues du futur site web HITNA)
# ══════════════════════════════════════════════════════════════

@app.route('/admin/commandes')
def admin_commandes():
    try:
        if not check_perm('commandes'):
            flash('❌ Permission refusée')
            return redirect('/vente' if session.get('role') != 'admin' else '/dashboard')
        commandes = qall("SELECT * FROM commandes ORDER BY date_creation DESC")
        nb_nouvelles = q1("SELECT COUNT(*) FROM commandes WHERE statut='nouvelle'")
        return render_template('admin_commandes.html', commandes=commandes,
            nb_nouvelles=nb_nouvelles[0] if nb_nouvelles else 0)
    except Exception as e:
        print(f"❌ Erreur admin_commandes: {e}")
        flash('Erreur lors du chargement des commandes')
        return redirect('/dashboard')

@app.route('/admin/commandes/statut/<int:id>', methods=['POST'])
def modifier_statut_commande(id):
    try:
        if not check_perm('commandes'):
            flash('❌ Permission refusée')
            return redirect('/vente' if session.get('role') != 'admin' else '/dashboard')
        statut = request.form.get('statut', 'nouvelle')
        if statut not in ('nouvelle', 'en_cours', 'traitee', 'annulee'):
            statut = 'nouvelle'
        exe("UPDATE commandes SET statut=? WHERE id=?", (statut, id))
        flash('✅ Statut de la commande mis à jour')
    except Exception as e:
        print(f"❌ Erreur modifier_statut_commande: {e}")
        flash('❌ Erreur lors de la mise à jour')
    return redirect('/admin/commandes')

@app.route('/admin/commandes/supprimer/<int:id>')
def supprimer_commande(id):
    try:
        # Suppression réservée à l'admin (les employés peuvent voir/traiter,
        # mais pas supprimer définitivement une commande).
        if session.get('role') != 'admin':
            flash('❌ Permission refusée')
            return redirect('/admin/commandes')
        exe("DELETE FROM commandes WHERE id=?", (id,))
        flash('🗑️ Commande supprimée')
    except Exception as e:
        print(f"❌ Erreur supprimer_commande: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/commandes')


# ══════════════════════════════════════════════════════════════
# SAUVEGARDE
# ══════════════════════════════════════════════════════════════
BACKUP_SECRET = os.environ.get('BACKUP_SECRET', '')

@app.route('/admin/backup/export')
def backup_export():
    """
    Deux usages :
    - Admin connecté, visite directe dans le navigateur -> téléchargement du zip.
    - Appel automatisé (cron externe) avec ?token=BACKUP_SECRET -> le zip est
      envoyé par email à l'adresse HITNA configurée, sans session requise.
    """
    try:
        token = request.args.get('token', '')
        session_admin = session.get('role') == 'admin'
        token_valide = bool(BACKUP_SECRET) and token == BACKUP_SECRET

        if not (session_admin or token_valide):
            return jsonify({'error': 'Non autorisé'}), 403

        data = generer_backup_json()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('backup.json', json.dumps(data, ensure_ascii=False, default=str, indent=2))
        buf.seek(0)

        date_str = datetime.now().strftime('%Y-%m-%d_%H%M')
        filename = f'hitna_backup_{date_str}.zip'

        if token_valide and not session_admin:
            # Déclenché automatiquement : personne n'est devant l'écran,
            # on envoie donc le fichier par email plutôt que de le "télécharger".
            try:
                msg = Message(
                    subject=f"📦 Sauvegarde HITNA — {date_str}",
                    recipients=[app.config['MAIL_USERNAME']],
                    body="Sauvegarde automatique de la base de données HITNA en pièce jointe (fichier .zip contenant un export JSON complet)."
                )
                msg.attach(filename, 'application/zip', buf.read())
                ancien_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(20)
                try:
                    mail.send(msg)
                finally:
                    socket.setdefaulttimeout(ancien_timeout)
                return jsonify({'success': True, 'message': 'Sauvegarde envoyée par email'})
            except Exception as e:
                print(f"❌ Erreur envoi backup par email: {e}")
                return jsonify({'error': f'Sauvegarde générée mais email échoué: {e}'}), 500

        return send_file(buf, mimetype='application/zip', as_attachment=True, download_name=filename)
    except Exception as e:
        print(f"❌ Erreur backup_export: {e}")
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
# MESSAGES (formulaire de contact du futur site web HITNA)
# ══════════════════════════════════════════════════════════════

@app.route('/admin/messages')
def admin_messages():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        messages = qall("SELECT * FROM messages_contact ORDER BY date_creation DESC")
        nb_non_lus = q1("SELECT COUNT(*) FROM messages_contact WHERE statut='non_lu'")
        return render_template('admin_messages.html', messages=messages,
            nb_non_lus=nb_non_lus[0] if nb_non_lus else 0)
    except Exception as e:
        print(f"❌ Erreur admin_messages: {e}")
        flash('Erreur lors du chargement des messages')
        return redirect('/dashboard')

@app.route('/admin/messages/statut/<int:id>', methods=['POST'])
def modifier_statut_message(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        statut = request.form.get('statut', 'lu')
        if statut not in ('non_lu', 'lu', 'repondu'):
            statut = 'lu'
        exe("UPDATE messages_contact SET statut=? WHERE id=?", (statut, id))
        flash('✅ Statut du message mis à jour')
    except Exception as e:
        print(f"❌ Erreur modifier_statut_message: {e}")
        flash('❌ Erreur lors de la mise à jour')
    return redirect('/admin/messages')

@app.route('/admin/messages/supprimer/<int:id>')
def supprimer_message(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        exe("DELETE FROM messages_contact WHERE id=?", (id,))
        flash('🗑️ Message supprimé')
    except Exception as e:
        print(f"❌ Erreur supprimer_message: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/messages')


# ══════════════════════════════════════════════════════════════
# API PUBLIQUE — à appeler depuis le futur site web HITNA
# Pas d'authentification (endpoints publics), mais validation stricte
# des champs. CORS à configurer plus tard selon le domaine du site.
# ══════════════════════════════════════════════════════════════

@app.route('/api/commandes', methods=['POST'])
def api_creer_commande():
    try:
        data = request.get_json(force=True, silent=True) or request.form
        nom_client = (data.get('nom_client') or data.get('nom') or '').strip()
        telephone = (data.get('telephone') or '').strip()
        email = (data.get('email') or '').strip()
        adresse = (data.get('adresse') or '').strip()
        details = (data.get('details') or data.get('produits') or '').strip()

        if not nom_client or not telephone or not details:
            return jsonify({'success': False, 'error': 'Champs obligatoires manquants (nom, téléphone, détails)'}), 400

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cmd_id = exe('''INSERT INTO commandes (nom_client, telephone, email, adresse, details, statut, date_creation)
                        VALUES (?,?,?,?,?,?,?)''',
                     (nom_client, telephone, email, adresse, details, 'nouvelle', now), returning=True)
        if not cmd_id:
            return jsonify({'success': False, 'error': 'Erreur serveur'}), 500

        admins = qall("SELECT id FROM users WHERE role='admin' AND actif=1")
        for a in admins:
            creer_notification(a[0], 'commande', '🛍️ Nouvelle commande',
                f'{nom_client} a passé une commande', '/admin/commandes')

        return jsonify({'success': True, 'id': cmd_id}), 201
    except Exception as e:
        print(f"❌ Erreur api_creer_commande: {e}")
        return jsonify({'success': False, 'error': 'Erreur serveur'}), 500

@app.route('/api/messages', methods=['POST'])
def api_creer_message():
    try:
        data = request.get_json(force=True, silent=True) or request.form
        nom = (data.get('nom') or '').strip()
        email = (data.get('email') or '').strip()
        telephone = (data.get('telephone') or '').strip()
        sujet = (data.get('sujet') or '').strip()
        message = (data.get('message') or '').strip()

        if not nom or not message:
            return jsonify({'success': False, 'error': 'Champs obligatoires manquants (nom, message)'}), 400

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg_id = exe('''INSERT INTO messages_contact (nom, email, telephone, sujet, message, statut, date_creation)
                        VALUES (?,?,?,?,?,?,?)''',
                     (nom, email, telephone, sujet, message, 'non_lu', now), returning=True)
        if not msg_id:
            return jsonify({'success': False, 'error': 'Erreur serveur'}), 500

        admins = qall("SELECT id FROM users WHERE role='admin' AND actif=1")
        for a in admins:
            creer_notification(a[0], 'message', '✉️ Nouveau message',
                f'{nom} : {sujet or message[:40]}', '/admin/messages')

        return jsonify({'success': True, 'id': msg_id}), 201
    except Exception as e:
        print(f"❌ Erreur api_creer_message: {e}")
        return jsonify({'success': False, 'error': 'Erreur serveur'}), 500


@app.route('/admin/unites')
def admin_unites():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        unites = qall("SELECT * FROM unites_mesure ORDER BY nom")
        return render_template('admin_unites.html', unites=unites)
    except Exception as e:
        print(f"❌ Erreur admin_unites: {e}")
        flash('Erreur lors du chargement des unités')
        return redirect('/dashboard')

@app.route('/admin/unites/ajouter', methods=['POST'])
def ajouter_unite():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '').strip()
        symbole = request.form.get('symbole', '').strip()
        description = request.form.get('description', '').strip()
        if not nom:
            flash('❌ Le nom de l\'unité est obligatoire')
            return redirect('/admin/unites')
        exe("INSERT INTO unites_mesure (nom, symbole, description, actif) VALUES (?,?,?,1)",
            (nom, symbole, description))
        flash(f'✅ Unité "{nom}" ajoutée')
    except Exception as e:
        print(f"❌ Erreur ajouter_unite: {e}")
        flash('❌ Erreur lors de l\'ajout')
    return redirect('/admin/unites')

@app.route('/admin/unites/modifier/<int:id>', methods=['POST'])
def modifier_unite(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '').strip()
        symbole = request.form.get('symbole', '').strip()
        description = request.form.get('description', '').strip()
        actif = 1 if request.form.get('actif') else 0
        exe("UPDATE unites_mesure SET nom=?, symbole=?, description=?, actif=? WHERE id=?", 
            (nom, symbole, description, actif, id))
        flash(f'✅ Unité "{nom}" modifiée')
    except Exception as e:
        print(f"❌ Erreur modifier_unite: {e}")
        flash('❌ Erreur lors de la modification')
    return redirect('/admin/unites')

@app.route('/admin/unites/supprimer/<int:id>')
def supprimer_unite(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        used = q1("SELECT COUNT(*) FROM produits WHERE unite_id=?", (id,))
        if used and used[0] > 0:
            flash('❌ Cette unité est utilisée par des produits. Supprimez-les d\'abord.')
            return redirect('/admin/unites')
        u = q1("SELECT nom FROM unites_mesure WHERE id=?", (id,))
        if u:
            exe("DELETE FROM unites_mesure WHERE id=?", (id,))
            flash(f'🗑️ Unité "{u[0]}" supprimée')
    except Exception as e:
        print(f"❌ Erreur supprimer_unite: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/unites')

# ─── PRODUITS ──────────────────────────────────────────────────
@app.route('/admin/produits')
def produits_list():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        cache_key = 'produits_list'
        cached_data = get_cached(cache_key, 120)
        if cached_data:
            produits, unites, categories = cached_data
        else:
            produits = qall('''SELECT p.id, p.nom, p.prix, p.stock, p.stock_min,
                                      COALESCE(u.symbole, '') as unite_symbole,
                                      COALESCE(u.nom, '') as unite_nom,
                                      p.unite_id,
                                      COALESCE(c.nom, '') as categorie_nom,
                                      COALESCE(c.icone, '') as categorie_icone,
                                      p.categorie_id,
                                      p.valeur_unite,
                                      COALESCE(p.vendu_par_carton, 0),
                                      p.unites_par_carton,
                                      p.prix_carton
                               FROM produits p 
                               LEFT JOIN unites_mesure u ON p.unite_id = u.id 
                               LEFT JOIN categories_produits c ON p.categorie_id = c.id
                               ORDER BY p.nom''')
            unites = qall("SELECT id, nom, symbole FROM unites_mesure WHERE actif = 1 ORDER BY nom")
            categories = qall("SELECT id, nom, icone FROM categories_produits WHERE actif = 1 ORDER BY nom")
            set_cached(cache_key, (produits, unites, categories))
        return render_template('produits.html', produits=produits, unites=unites, categories=categories)
    except Exception as e:
        print(f"❌ Erreur produits_list: {e}")
        flash('Erreur lors du chargement des produits')
        return redirect('/dashboard')

@app.route('/admin/produits/ajouter', methods=['POST'])
def ajouter_produit():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '')
        prix = int(float(request.form.get('prix', 0)))
        stock = int(request.form.get('stock', 0))
        smin = int(request.form.get('stock_min', 5))
        unite_id = request.form.get('unite_id')
        if not unite_id or unite_id == '0':
            unite_id = None
        else:
            unite_id = int(unite_id)
        categorie_id = request.form.get('categorie_id')
        if not categorie_id or categorie_id == '0':
            categorie_id = None
        else:
            categorie_id = int(categorie_id)
        valeur_unite = request.form.get('valeur_unite', '').strip()
        valeur_unite = float(valeur_unite) if valeur_unite else None
        upc = request.form.get('unites_par_carton', '').strip()
        vendu_par_carton = 0
        unites_par_carton = None
        if upc:
            try:
                unites_par_carton = int(upc)
                if unites_par_carton > 1:
                    vendu_par_carton = 1
            except ValueError:
                unites_par_carton = None
        pc = request.form.get('prix_carton', '').strip()
        prix_carton = None
        if pc:
            try:
                prix_carton = int(float(pc))
            except ValueError:
                prix_carton = None
        ok = exe("""INSERT INTO produits (nom, prix, stock, stock_min, unite_id, categorie_id, valeur_unite,
                    vendu_par_carton, unites_par_carton, prix_carton) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (nom, prix, stock, smin, unite_id, categorie_id, valeur_unite, vendu_par_carton, unites_par_carton, prix_carton))
        if ok:
            flash(f'✅ Produit "{nom}" ajouté ({prix} FCFA)')
            envoyer_notification_a_tous('produit','🆕 Nouveau produit',f'"{nom}" ajouté ({prix} FCFA)','/admin/produits')
        else:
            flash(f'❌ Échec de l\'ajout de "{nom}" — vérifiez les logs serveur')
    except Exception as e:
        print(f"❌ Erreur ajouter_produit: {e}")
        flash('❌ Erreur lors de l\'ajout du produit')
    return redirect('/admin/produits')

@app.route('/admin/produits/modifier/<int:id>', methods=['POST'])
def modifier_produit(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '')
        prix = int(float(request.form.get('prix', 0)))
        stock = int(request.form.get('stock', 0))
        smin = int(request.form.get('stock_min', 5))
        unite_id = request.form.get('unite_id')
        if not unite_id or unite_id == '0':
            unite_id = None
        else:
            unite_id = int(unite_id)
        categorie_id = request.form.get('categorie_id')
        if not categorie_id or categorie_id == '0':
            categorie_id = None
        else:
            categorie_id = int(categorie_id)
        valeur_unite = request.form.get('valeur_unite', '').strip()
        valeur_unite = float(valeur_unite) if valeur_unite else None
        upc = request.form.get('unites_par_carton', '').strip()
        unites_par_carton = None
        if upc:
            try:
                unites_par_carton = int(upc)
            except ValueError:
                unites_par_carton = None
        pc = request.form.get('prix_carton', '').strip()
        prix_carton = None
        if pc:
            try:
                prix_carton = int(float(pc))
            except ValueError:
                prix_carton = None
        ok = exe("""UPDATE produits SET nom=?, prix=?, stock=?, stock_min=?, unite_id=?, categorie_id=?,
                    valeur_unite=?, unites_par_carton=?, prix_carton=? WHERE id=?""",
            (nom, prix, stock, smin, unite_id, categorie_id, valeur_unite, unites_par_carton, prix_carton, id))
        if ok:
            flash(f'✅ Produit "{nom}" modifié')
        else:
            flash(f'❌ Échec de la modification de "{nom}"')
    except Exception as e:
        print(f"❌ Erreur modifier_produit: {e}")
        flash('❌ Erreur lors de la modification')
    return redirect('/admin/produits')

@app.route('/admin/produits/convertir_carton/<int:id>')
def convertir_carton_form(id):
    """Affiche un aperçu avant/après avant de convertir un produit compté en
    cartons vers un suivi de stock en unités individuelles (vente au détail)."""
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        p = q1("SELECT id, nom, prix, stock, vendu_par_carton, unites_par_carton FROM produits WHERE id=?", (id,))
        if not p:
            flash('❌ Produit introuvable')
            return redirect('/admin/produits')
        if p[4] == 1:
            flash(f'ℹ️ "{p[1]}" est déjà en vente au détail (unités individuelles)')
            return redirect('/admin/produits')
        return render_template('convertir_carton.html', produit=p)
    except Exception as e:
        print(f"❌ Erreur convertir_carton_form: {e}")
        flash('❌ Erreur lors du chargement de la conversion')
        return redirect('/admin/produits')

@app.route('/admin/produits/convertir_carton/<int:id>', methods=['POST'])
def convertir_carton_appliquer(id):
    """Applique la conversion : stock (cartons) -> stock (unités), prix (carton) -> prix (unité).
    Action irréversible d'un simple clic ; nécessite une confirmation explicite du formulaire."""
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        if request.form.get('confirmation') != 'CONVERTIR':
            flash('❌ Conversion annulée : confirmation non saisie correctement')
            return redirect(f'/admin/produits/convertir_carton/{id}')

        p = q1("SELECT id, nom, prix, stock, vendu_par_carton FROM produits WHERE id=?", (id,))
        if not p:
            flash('❌ Produit introuvable')
            return redirect('/admin/produits')
        if p[4] == 1:
            flash(f'ℹ️ "{p[1]}" est déjà en vente au détail')
            return redirect('/admin/produits')

        unites_par_carton = int(request.form.get('unites_par_carton', 0))
        if unites_par_carton < 2:
            flash('❌ Le nombre d\'unités par carton doit être d\'au moins 2')
            return redirect(f'/admin/produits/convertir_carton/{id}')

        nom, prix_carton, stock_cartons = p[1], p[2], p[3]
        nouveau_stock = stock_cartons * unites_par_carton
        nouveau_prix = round(prix_carton / unites_par_carton)

        exe("""UPDATE produits SET stock=?, prix=?, vendu_par_carton=1, unites_par_carton=? WHERE id=?""",
            (nouveau_stock, nouveau_prix, unites_par_carton, id))

        flash(f'✅ "{nom}" converti : {stock_cartons} carton(s) → {nouveau_stock} unités, '
              f'prix unitaire {nouveau_prix} FCFA (était {prix_carton} FCFA/carton)')
    except Exception as e:
        print(f"❌ Erreur convertir_carton_appliquer: {e}")
        flash('❌ Erreur lors de la conversion')
    return redirect('/admin/produits')

@app.route('/admin/produits/supprimer/<int:id>')
def supprimer_produit(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        force = request.args.get('force') == '1'
        p = q1("SELECT nom FROM produits WHERE id=?", (id,))
        if p:
            ventes = q1("SELECT COUNT(*) FROM sorties WHERE produit_id=?", (id,))
            entrees = q1("SELECT COUNT(*) FROM entrees WHERE produit_id=?", (id,))
            pertes = q1("SELECT COUNT(*) FROM pertes WHERE produit_id=?", (id,))
            a_des_mouvements = (ventes and ventes[0] > 0) or (entrees and entrees[0] > 0) or (pertes and pertes[0] > 0)
            if a_des_mouvements and not force:
                flash(f'❌ "{p[0]}" a des mouvements (ventes/entrées/pertes). Utilise la sélection multiple avec "Forcer" pour le supprimer quand même.')
                return redirect('/admin/produits')
            if a_des_mouvements and force:
                exe("DELETE FROM sorties WHERE produit_id=?", (id,))
                exe("DELETE FROM entrees WHERE produit_id=?", (id,))
                exe("DELETE FROM pertes WHERE produit_id=?", (id,))
            exe("DELETE FROM alertes_produits WHERE produit_id=?", (id,))
            exe("DELETE FROM produits WHERE id=?", (id,))
            flash(f'🗑️ "{p[0]}" supprimé' + (' (avec son historique)' if a_des_mouvements else ''))
        else:
            flash('❌ Produit introuvable')
    except Exception as e:
        print(f"❌ Erreur supprimer_produit: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/produits')

@app.route('/admin/produits/supprimer_multiple', methods=['POST'])
def supprimer_produits_multiple():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        ids = request.form.getlist('produit_ids')
        force = request.form.get('force') == '1'
        if not ids:
            flash('⚠️ Aucun produit sélectionné')
            return redirect('/admin/produits')

        supprimes = []
        bloques = []
        for id_str in ids:
            try:
                id = int(id_str)
            except (ValueError, TypeError):
                continue
            p = q1("SELECT nom FROM produits WHERE id=?", (id,))
            if not p:
                continue
            ventes = q1("SELECT COUNT(*) FROM sorties WHERE produit_id=?", (id,))
            entrees = q1("SELECT COUNT(*) FROM entrees WHERE produit_id=?", (id,))
            pertes = q1("SELECT COUNT(*) FROM pertes WHERE produit_id=?", (id,))
            a_des_mouvements = (ventes and ventes[0] > 0) or (entrees and entrees[0] > 0) or (pertes and pertes[0] > 0)
            if a_des_mouvements and not force:
                bloques.append(p[0])
                continue
            if a_des_mouvements and force:
                exe("DELETE FROM sorties WHERE produit_id=?", (id,))
                exe("DELETE FROM entrees WHERE produit_id=?", (id,))
                exe("DELETE FROM pertes WHERE produit_id=?", (id,))
            exe("DELETE FROM alertes_produits WHERE produit_id=?", (id,))
            exe("DELETE FROM produits WHERE id=?", (id,))
            supprimes.append(p[0])

        if supprimes:
            flash(f'🗑️ {len(supprimes)} produit(s) supprimé(s) : {", ".join(supprimes)}')
        if bloques:
            flash(f'❌ {len(bloques)} produit(s) non supprimé(s) (ont des mouvements — coche "Forcer" pour les supprimer quand même) : {", ".join(bloques)}')
        if not supprimes and not bloques:
            flash('⚠️ Aucun produit valide sélectionné')
    except Exception as e:
        print(f"❌ Erreur supprimer_produits_multiple: {e}")
        flash('❌ Erreur lors de la suppression multiple')
    return redirect('/admin/produits')

# ─── ENTRÉES ──────────────────────────────────────────────────
@app.route('/admin/entrees')
def entrees_list():
    try:
        if not check_perm('entrees'):
            flash('❌ Permission refusée')
            return redirect('/vente')
        cache_key = 'entrees_list'
        cached_data = get_cached(cache_key, 30)
        if cached_data:
            entrees, produits = cached_data
        else:
            entrees = qall('''SELECT e.id,p.nom,e.quantite,e.prix_unitaire,e.total,e.date_entree,e.fournisseur
                FROM entrees e JOIN produits p ON e.produit_id=p.id ORDER BY e.date_entree DESC LIMIT 30''')
            produits = qall("SELECT id,nom,stock FROM produits ORDER BY nom")
            set_cached(cache_key, (entrees, produits))
        return render_template('entrees.html', entrees=entrees, produits=produits)
    except Exception as e:
        print(f"❌ Erreur entrees_list: {e}")
        flash('Erreur lors du chargement des entrées')
        return redirect('/vente')

# ══════════════════════════════════════════════════════════════
# TRAITEMENT PARTAGÉ VENTE / ENTRÉE / PERTE
# Utilisé à la fois par les routes normales (en ligne) et par
# /api/sync (actions mises en file d'attente pendant une coupure réseau)
# ══════════════════════════════════════════════════════════════

def _traiter_vente_cart(cart, client, employe_id):
    """cart: liste de {produit_id, quantite}. Retourne (groupe_vente, lignes_ok, erreurs)."""
    groupe_vente = uuid.uuid4().hex[:12]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lignes_ok = []
    erreurs = []
    for item in cart:
        try:
            pid = int(item.get('produit_id', 0))
            qty = int(item.get('quantite', 0))
        except (TypeError, ValueError, AttributeError):
            continue
        if pid <= 0 or qty <= 0:
            continue
        p = q1("SELECT nom, prix, stock, prix_carton, unites_par_carton FROM produits WHERE id=?", (pid,))
        if not p:
            erreurs.append(f'Produit #{pid} introuvable')
            continue
        nom, prix_unitaire, stock, prix_carton, unites_par_carton = p
        if qty > stock:
            erreurs.append(f'Stock insuffisant pour "{nom}" ({stock} restant(s))')
            continue
        # Prix carton appliqué uniquement si la quantité vendue correspond EXACTEMENT
        # au nombre d'unités par carton défini pour ce produit — jamais deviné côté
        # client, toujours vérifié ici à partir des données réelles en base.
        if prix_carton and unites_par_carton and qty == unites_par_carton:
            total = prix_carton
            prix_unitaire_ligne = round(prix_carton / qty)
        else:
            total = prix_unitaire * qty
            prix_unitaire_ligne = prix_unitaire
        insert_ok = exe("""INSERT INTO sorties
            (produit_id, quantite, prix_unitaire, total, date_sortie, client, employe_id, groupe_vente)
            VALUES (?,?,?,?,?,?,?,?)""",
            (pid, qty, prix_unitaire_ligne, total, now, client, employe_id, groupe_vente))
        if not insert_ok:
            erreurs.append(f'Échec d\'enregistrement pour "{nom}" (erreur serveur)')
            continue
        exe("UPDATE produits SET stock = stock - ? WHERE id = ?", (qty, pid))
        lignes_ok.append(f'{qty} x {nom}')
    if lignes_ok:
        verifier_alertes_stock()
    return groupe_vente, lignes_ok, erreurs


def _traiter_entree(pid, qty, pu, fournisseur, employe_id):
    """Retourne (ok: bool, message: str)."""
    try:
        pid, qty, pu = int(pid), int(qty), int(pu)
    except (TypeError, ValueError):
        return False, 'Données invalides'
    if pid <= 0 or qty <= 0 or pu <= 0:
        return False, 'Données invalides'
    p = q1("SELECT nom FROM produits WHERE id=?", (pid,))
    if not p:
        return False, f'Produit #{pid} introuvable'
    total = qty * pu
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    insert_ok = exe("INSERT INTO entrees (produit_id,quantite,prix_unitaire,total,date_entree,fournisseur,employe_id) VALUES (?,?,?,?,?,?,?)",
        (pid, qty, pu, total, now, fournisseur or '', employe_id))
    if not insert_ok:
        return False, f'❌ Échec d\'enregistrement de l\'entrée pour "{p[0]}" (erreur serveur)'
    exe("UPDATE produits SET stock=stock+? WHERE id=?", (qty, pid))
    verifier_alertes_stock()
    return True, f'✅ Entrée : +{qty} {p[0]}'


def _traiter_perte(pid, qty, motif, employe_id):
    """Retourne (ok: bool, message: str)."""
    try:
        pid, qty = int(pid), int(qty)
    except (TypeError, ValueError):
        return False, 'Données invalides'
    if pid <= 0 or qty <= 0:
        return False, 'Données invalides'
    p = q1("SELECT nom,prix,stock FROM produits WHERE id=?", (pid,))
    if not p:
        return False, f'Produit #{pid} introuvable'
    if qty > p[2]:
        return False, f'Stock insuffisant ! {p[2]} unités de {p[0]}'
    total = qty * p[1]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    insert_ok = exe("INSERT INTO pertes (produit_id,quantite,prix_unitaire,total,motif,date_perte,employe_id) VALUES (?,?,?,?,?,?,?)",
        (pid, qty, p[1], total, motif or 'Non précisé', now, employe_id))
    if not insert_ok:
        return False, f'❌ Échec d\'enregistrement de la perte pour "{p[0]}" (erreur serveur)'
    exe("UPDATE produits SET stock=GREATEST(0,stock-?) WHERE id=?", (qty, pid))
    envoyer_notification_a_tous('perte', '⚠️ Perte signalée',
        f'{qty} unités de "{p[0]}" perdues ({total} FCFA)', '/admin/pertes')
    return True, f'⚠️ Perte : {qty}×{p[0]} = {total} FCFA'


@app.route('/admin/entrees/ajouter', methods=['POST'])
def ajouter_entree():
    try:
        if not check_perm('entrees'):
            flash('❌ Permission refusée')
            return redirect('/vente')
        ok, message = _traiter_entree(
            request.form.get('produit_id', 0),
            request.form.get('quantite', 0),
            request.form.get('prix_unitaire', 0),
            request.form.get('fournisseur', ''),
            session.get('user_id', 1))
        flash(message)
    except Exception as e:
        print(f"❌ Erreur ajouter_entree: {e}")
        flash('❌ Erreur lors de l\'ajout de l\'entrée')
    return redirect('/admin/entrees')

# ─── VENTES ADMIN ──────────────────────────────────────────────
@app.route('/admin/ventes', methods=['GET','POST'])
def admin_ventes():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        if request.method == 'POST':
            cart_json = request.form.get('cart_json', '')
            client = request.form.get('client', '').strip()
            try:
                cart = json.loads(cart_json) if cart_json else []
            except (ValueError, TypeError):
                cart = []

            if not cart:
                flash('❌ Le panier est vide')
                return redirect('/admin/ventes')

            groupe_vente, lignes_ok, erreurs = _traiter_vente_cart(cart, client, session.get('user_id', 1))

            for e in erreurs:
                flash(f'⚠️ {e}')

            if lignes_ok:
                flash(f'✅ Vente enregistrée : {", ".join(lignes_ok)}')
                return redirect(f'/vente/recu/{groupe_vente}')
            else:
                flash('❌ Aucun produit n\'a pu être vendu')
            return redirect('/admin/ventes')
        cache_key = 'admin_ventes_data'
        cached_data = get_cached(cache_key, 30)
        if cached_data:
            produits, historique, stats_vendeurs = cached_data
        else:
            produits = qall('''SELECT p.id, p.nom, p.prix, p.stock,
                                       COALESCE(u.symbole,'') as unite_symbole,
                                       COALESCE(u.nom,'') as unite_nom,
                                       p.valeur_unite,
                                       COALESCE(p.vendu_par_carton, 0),
                                       p.unites_par_carton,
                                       p.prix_carton
                                FROM produits p
                                LEFT JOIN unites_mesure u ON p.unite_id = u.id
                                WHERE p.stock>0 ORDER BY p.nom''')
            historique = qall('''SELECT s.id,p.nom,s.quantite,s.total,s.date_sortie,u.nom,s.client,s.groupe_vente
                FROM sorties s JOIN produits p ON s.produit_id=p.id JOIN users u ON s.employe_id=u.id
                ORDER BY s.date_sortie DESC LIMIT 20''')
            stats_vendeurs = qall('''SELECT u.nom,u.role,COUNT(s.id),COALESCE(SUM(s.total),0)
                FROM sorties s JOIN users u ON s.employe_id=u.id
                WHERE DATE(s.date_sortie)=CURRENT_DATE GROUP BY u.id,u.nom,u.role ORDER BY 4 DESC''')
            set_cached(cache_key, (produits, historique, stats_vendeurs))
        return render_template('admin_ventes.html', produits=produits, historique=historique, stats_vendeurs=stats_vendeurs)
    except Exception as e:
        print(f"❌ Erreur admin_ventes: {e}")
        flash('Erreur lors du chargement des ventes')
        return redirect('/dashboard')

# ─── VENTES EMPLOYÉ ──────────────────────────────────────────────
@app.route('/vente', methods=['GET','POST'])
def vente():
    try:
        if 'user_id' not in session:
            flash('❌ Veuillez vous connecter')
            return redirect('/login')
        if session.get('role') != 'employe':
            flash('❌ Accès réservé aux employés')
            return redirect('/dashboard' if session.get('role') == 'admin' else '/login')
        if request.method == 'POST':
            try:
                cart_json = request.form.get('cart_json', '')
                client = request.form.get('client', '').strip()
                try:
                    cart = json.loads(cart_json) if cart_json else []
                except (ValueError, TypeError):
                    cart = []

                if not cart:
                    flash('❌ Le panier est vide')
                    return redirect('/vente')

                groupe_vente, lignes_ok, erreurs = _traiter_vente_cart(cart, client, session.get('user_id', 1))

                for e in erreurs:
                    flash(f'⚠️ {e}')

                if lignes_ok:
                    flash(f'✅ Vente enregistrée : {", ".join(lignes_ok)}')
                    return redirect(f'/vente/recu/{groupe_vente}')
                else:
                    flash('❌ Aucun produit n\'a pu être vendu')
                    return redirect('/vente')
            except ValueError as e:
                flash(f'❌ Erreur de saisie: {str(e)}')
            except Exception as e:
                flash(f'❌ Erreur lors de la vente: {str(e)}')
            return redirect('/vente')
        cache_key = 'vente_data'
        cached_data = get_cached(cache_key, 30)
        if cached_data:
            produits, historique, stats_vendeurs, total_general = cached_data
        else:
            produits = qall('''SELECT p.id, p.nom, p.prix, p.stock,
                                       COALESCE(u.symbole,'') as unite_symbole,
                                       COALESCE(u.nom,'') as unite_nom,
                                       p.valeur_unite,
                                       COALESCE(p.vendu_par_carton, 0),
                                       p.unites_par_carton,
                                       p.prix_carton
                                FROM produits p
                                LEFT JOIN unites_mesure u ON p.unite_id = u.id
                                WHERE p.stock>0 ORDER BY p.nom''')
            historique = qall('''SELECT s.id, p.nom, s.quantite, s.total, s.date_sortie, s.client, u.nom, u.role, s.groupe_vente
                FROM sorties s 
                JOIN produits p ON s.produit_id = p.id 
                JOIN users u ON s.employe_id = u.id
                WHERE DATE(s.date_sortie) = CURRENT_DATE 
                ORDER BY s.date_sortie DESC LIMIT 20''')
            stats_vendeurs = qall('''SELECT u.role, COUNT(s.id), COALESCE(SUM(s.total), 0)
                FROM sorties s 
                JOIN users u ON s.employe_id = u.id
                WHERE DATE(s.date_sortie) = CURRENT_DATE 
                GROUP BY u.role''')
            total_general = q1("SELECT COALESCE(SUM(total), 0), COUNT(*) FROM sorties WHERE DATE(date_sortie) = CURRENT_DATE")
            if not total_general:
                total_general = (0, 0)
            set_cached(cache_key, (produits, historique, stats_vendeurs, total_general))
        return render_template('vente.html', 
            produits=produits, 
            historique=historique,
            stats_vendeurs=stats_vendeurs, 
            total_general=total_general)
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'❌ Erreur: {str(e)}')
        return redirect('/login')

def _recuperer_lignes_recu(groupe_vente):
    """Retourne (lignes, archivee) pour un groupe_vente, en cherchant d'abord dans
    sorties (ventes récentes) puis dans archive_ventes (ventes archivées)."""
    lignes = qall('''SELECT s.produit_id, p.nom, s.quantite, s.prix_unitaire, s.total,
                             s.date_sortie, s.client, u.nom
                      FROM sorties s
                      JOIN produits p ON s.produit_id = p.id
                      JOIN users u ON s.employe_id = u.id
                      WHERE s.groupe_vente = ?
                      ORDER BY s.id''', (groupe_vente,))
    if not lignes:
        # Filet de sécurité : en cas de raté transitoire de connexion DB
        # juste après l'enregistrement de la vente, on retente une fois.
        sleep(0.4)
        lignes = qall('''SELECT s.produit_id, p.nom, s.quantite, s.prix_unitaire, s.total,
                                 s.date_sortie, s.client, u.nom
                          FROM sorties s
                          JOIN produits p ON s.produit_id = p.id
                          JOIN users u ON s.employe_id = u.id
                          WHERE s.groupe_vente = ?
                          ORDER BY s.id''', (groupe_vente,))
    archivee = False
    if not lignes:
        # La vente n'est plus dans "sorties" : elle a peut-être été archivée
        # (archivage hebdomadaire). On cherche alors dans archive_ventes.
        lignes_archive = qall('''SELECT produit_id, produit_nom, quantite, prix_unitaire, total,
                                         date_vente, client, employe_nom
                                  FROM archive_ventes
                                  WHERE groupe_vente = ?
                                  ORDER BY id''', (groupe_vente,))
        if lignes_archive:
            lignes = lignes_archive
            archivee = True
    return lignes, archivee


@app.route('/vente/recu/<groupe_vente>')
def recu_vente(groupe_vente):
    """Affiche un reçu imprimable pour un panier de vente donné."""
    try:
        if 'user_id' not in session:
            return redirect('/login')
        lignes, archivee = _recuperer_lignes_recu(groupe_vente)
        if not lignes:
            flash('❌ Reçu introuvable (vente trop ancienne ou identifiant invalide)')
            return redirect('/vente' if session.get('role') == 'employe' else '/dashboard')
        total_recu = sum(l[4] for l in lignes)
        return render_template('recu.html',
            lignes=lignes,
            total_recu=total_recu,
            groupe_vente=groupe_vente,
            client=lignes[0][6],
            vendeur=lignes[0][7],
            date_vente=lignes[0][5],
            archivee=archivee)
    except Exception as e:
        print(f"❌ Erreur recu_vente: {e}")
        flash('❌ Erreur lors du chargement du reçu')
        return redirect('/vente')


@app.route('/export/pdf_recu/<groupe_vente>')
def export_pdf_recu(groupe_vente):
    """Exporte un reçu de vente individuel en PDF (format ticket), pour archivage
    ou remise ultérieure au client."""
    try:
        if 'user_id' not in session:
            return redirect('/login')
        lignes, archivee = _recuperer_lignes_recu(groupe_vente)
        if not lignes:
            flash('❌ Reçu introuvable')
            return redirect('/vente' if session.get('role') == 'employe' else '/dashboard')

        total_recu = sum(l[4] for l in lignes)
        client = lignes[0][6] or 'Non renseigné'
        vendeur = lignes[0][7]
        date_vente = lignes[0][5]

        # Format ticket compact (largeur réduite, hauteur adaptée au contenu)
        largeur = 226  # ~8cm
        hauteur = 330 + len(lignes) * 16
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(largeur, hauteur))

        y = hauteur - 20

        logo_path = find_logo()
        if logo_path:
            try:
                from reportlab.lib.utils import ImageReader
                img = ImageReader(logo_path)
                logo_size = 40
                c.drawImage(img, (largeur - logo_size) / 2, y - logo_size + 10,
                    width=logo_size, height=logo_size, mask='auto')
                y -= logo_size + 5
            except Exception:
                pass

        c.setFont("Helvetica-Bold", 13)
        c.setFillColorRGB(0.12, 0.24, 0.45)
        c.drawCentredString(largeur / 2, y, "HITNA Superette")
        y -= 14
        c.setFont("Helvetica", 7)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawCentredString(largeur / 2, y, "Hounsa, Porto-Novo - Rép. Bénin")
        y -= 10
        c.drawCentredString(largeur / 2, y, "Tél: 01 67 19 85 31")
        y -= 16

        c.setStrokeColorRGB(0.7, 0.7, 0.7)
        c.setDash(2, 2)
        c.line(10, y, largeur - 10, y)
        c.setDash()
        y -= 14

        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.2, 0.2, 0.2)
        c.drawString(10, y, f"Date: {date_vente}")
        y -= 12
        c.drawString(10, y, f"Vendeur: {vendeur}")
        y -= 12
        c.drawString(10, y, f"Client: {client}")
        y -= 12
        c.drawString(10, y, f"N° reçu: {groupe_vente}")
        if archivee:
            y -= 12
            c.setFillColorRGB(0.6, 0.4, 0)
            c.drawString(10, y, "(vente archivée)")
            c.setFillColorRGB(0.2, 0.2, 0.2)
        y -= 14

        c.setDash(2, 2)
        c.line(10, y, largeur - 10, y)
        c.setDash()
        y -= 14

        c.setFont("Helvetica-Bold", 8)
        c.drawString(10, y, "Produit")
        c.drawRightString(largeur - 10, y, "Total")
        y -= 12
        c.setFont("Helvetica", 8)
        for l in lignes:
            nom = l[1] if len(l[1]) <= 22 else l[1][:20] + '…'
            c.drawString(10, y, f"{l[2]} x {nom}")
            c.drawRightString(largeur - 10, y, format_prix(l[4]))
            y -= 14

        c.setDash(2, 2)
        c.line(10, y, largeur - 10, y)
        c.setDash()
        y -= 16

        c.setFont("Helvetica-Bold", 11)
        c.setFillColorRGB(0.12, 0.24, 0.45)
        c.drawString(10, y, "TOTAL")
        c.drawRightString(largeur - 10, y, f"{format_prix(total_recu)} FCFA")
        y -= 20

        c.setFont("Helvetica-Oblique", 7)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawCentredString(largeur / 2, y, "Merci pour votre achat !")

        c.save()
        buffer.seek(0)
        return send_file(buffer, as_attachment=True,
            download_name=f"recu_{groupe_vente}.pdf", mimetype='application/pdf')
    except Exception as e:
        print(f"❌ Erreur export_pdf_recu: {e}")
        flash('❌ Erreur lors de l\'export du reçu')
        return redirect('/vente')

# ─── DASHBOARD ──────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        archiver_si_necessaire()
        verifier_alertes_stock()
        cache_key = 'dashboard_data'
        cached_data = get_cached(cache_key, 60)
        if cached_data:
            (total_jour, nb_produits, stock_total, nb_stock_bas, 
             stock_bas, top_produits, stats_vendeurs,
             ventes_7_jours, ventes_par_heure) = cached_data
        else:
            total_jour = q1("SELECT COALESCE(SUM(total),0) FROM sorties WHERE DATE(date_sortie)=CURRENT_DATE")
            total_jour = total_jour[0] if total_jour else 0
            nb_produits = q1("SELECT COUNT(*) FROM produits")
            nb_produits = nb_produits[0] if nb_produits else 0
            stock_total = q1("SELECT COALESCE(SUM(stock),0) FROM produits")
            stock_total = stock_total[0] if stock_total else 0
            nb_stock_bas = q1("SELECT COUNT(*) FROM produits WHERE stock<=stock_min")
            nb_stock_bas = nb_stock_bas[0] if nb_stock_bas else 0
            stock_bas = qall("SELECT nom,stock,stock_min FROM produits WHERE stock<=stock_min LIMIT 20")
            top_produits = qall('''SELECT p.nom,COALESCE(SUM(s.quantite),0) as tv
                FROM produits p LEFT JOIN sorties s ON p.id=s.produit_id
                GROUP BY p.id,p.nom ORDER BY tv DESC LIMIT 5''')
            stats_vendeurs = qall('''SELECT u.nom,u.role,COUNT(s.id),COALESCE(SUM(s.total),0)
                FROM sorties s JOIN users u ON s.employe_id=u.id
                WHERE DATE(s.date_sortie)=CURRENT_DATE GROUP BY u.id,u.nom,u.role ORDER BY 4 DESC''')
            ventes_7_jours = qall('''SELECT DATE(date_sortie::timestamp),COALESCE(SUM(total),0)
                FROM sorties WHERE date_sortie::timestamp >= NOW() - INTERVAL '7 days'
                GROUP BY DATE(date_sortie::timestamp) ORDER BY DATE(date_sortie::timestamp)''')
            ventes_par_heure = qall('''SELECT EXTRACT(HOUR FROM date_sortie::timestamp)::int,COALESCE(SUM(total),0)
                FROM sorties WHERE DATE(date_sortie::timestamp) = CURRENT_DATE
                GROUP BY 1 ORDER BY 1''')
            set_cached(cache_key, (total_jour, nb_produits, stock_total, nb_stock_bas, 
                                   stock_bas, top_produits, stats_vendeurs,
                                   ventes_7_jours, ventes_par_heure))
        return render_template('dashboard.html',
            total_jour=total_jour, nb_produits=nb_produits,
            stock_total=stock_total, nb_stock_bas=nb_stock_bas,
            stock_bas=stock_bas,
            top_produits=top_produits, stats_vendeurs=stats_vendeurs,
            ventes_7_jours=ventes_7_jours, ventes_par_heure=ventes_par_heure)
    except Exception as e:
        print(f"❌ Erreur dashboard: {e}")
        flash('Erreur lors du chargement du dashboard')
        return redirect('/login')

# ─── PERTES ──────────────────────────────────────────────────────
@app.route('/admin/pertes')
def pertes_list():
    try:
        if not check_perm('pertes'):
            flash('❌ Permission refusée')
            return redirect('/vente')
        pertes = qall('''SELECT p.id,pr.nom,p.quantite,p.prix_unitaire,p.total,p.motif,p.date_perte,u.nom
            FROM pertes p JOIN produits pr ON p.produit_id=pr.id JOIN users u ON p.employe_id=u.id
            ORDER BY p.date_perte DESC LIMIT 100''')
        produits = qall("SELECT id,nom,prix,stock FROM produits ORDER BY nom")
        s_auj = q1("SELECT COUNT(*),COALESCE(SUM(total),0),COALESCE(SUM(quantite),0) FROM pertes WHERE DATE(date_perte)=CURRENT_DATE")
        s_auj = s_auj if s_auj else (0,0,0)
        s_mois = q1("SELECT COUNT(*),COALESCE(SUM(total),0),COALESCE(SUM(quantite),0) FROM pertes WHERE date_perte::timestamp >= NOW() - INTERVAL '30 days'")
        s_mois = s_mois if s_mois else (0,0,0)
        return render_template('admin_pertes.html', pertes=pertes, produits=produits,
                               stats_aujourdhui=s_auj, stats_mois=s_mois)
    except Exception as e:
        print(f"❌ Erreur pertes_list: {e}")
        flash('Erreur lors du chargement des pertes')
        return redirect('/vente')

@app.route('/admin/pertes/ajouter', methods=['POST'])
def ajouter_perte():
    try:
        if not check_perm('pertes'):
            flash('❌ Permission refusée')
            return redirect('/vente')
        ok, message = _traiter_perte(
            request.form.get('produit_id', 0),
            request.form.get('quantite', 0),
            request.form.get('motif', ''),
            session.get('user_id', 1))
        flash(message)
    except Exception as e:
        print(f"❌ Erreur ajouter_perte: {e}")
        flash('❌ Erreur lors de l\'ajout de la perte')
    return redirect('/admin/pertes')

@app.route('/admin/pertes/supprimer/<int:id>')
def supprimer_perte(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        p = q1("SELECT produit_id,quantite FROM pertes WHERE id=?",(id,))
        if p:
            exe("UPDATE produits SET stock=stock+? WHERE id=?",(p[1],p[0]))
            exe("DELETE FROM pertes WHERE id=?",(id,))
            flash('✅ Perte annulée, stock restauré')
    except Exception as e:
        print(f"❌ Erreur supprimer_perte: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/pertes')

# ══════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════
@app.route('/api/notifications')
def api_notifications():
    try:
        if 'user_id' not in session:
            return jsonify({'error':'Non autorisé'}),401
        cache_key = f'notifications_{session["user_id"]}'
        cached_data = get_cached(cache_key, 10)
        if cached_data:
            return jsonify(cached_data)
        notifs = qall('''SELECT id,type,title,message,lien,date_creation
            FROM notifications WHERE user_id=? AND est_lu=0
            ORDER BY date_creation DESC LIMIT 20''',(session['user_id'],))
        total = q1("SELECT COUNT(*) FROM notifications WHERE user_id=? AND est_lu=0",(session['user_id'],))
        data = {
            'notifications':[{'id':n[0],'type':n[1],'title':n[2],'message':n[3],'lien':n[4],'date':n[5]} for n in notifs],
            'total_non_lus': total[0] if total else 0
        }
        set_cached(cache_key, data)
        return jsonify(data)
    except Exception as e:
        print(f"❌ Erreur api_notifications: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/notifications/marquer_lu/<int:id>', methods=['POST'])
def marquer_notification_lue(id):
    try:
        if 'user_id' not in session:
            return jsonify({'error':'Non autorisé'}),401
        exe("UPDATE notifications SET est_lu=1 WHERE id=? AND user_id=?",(id,session['user_id']))
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/notifications/marquer_tout_lu', methods=['POST'])
def marquer_tout_lu():
    try:
        if 'user_id' not in session:
            return jsonify({'error':'Non autorisé'}),401
        exe("UPDATE notifications SET est_lu=1 WHERE user_id=?",(session['user_id'],))
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/notifications')
def page_notifications():
    try:
        if 'user_id' not in session:
            return redirect('/login')
        notifs = qall('''SELECT id,type,title,message,lien,date_creation,est_lu
            FROM notifications WHERE user_id=? ORDER BY date_creation DESC LIMIT 100''',(session['user_id'],))
        total = q1("SELECT COUNT(*) FROM notifications WHERE user_id=? AND est_lu=0",(session['user_id'],))
        return render_template('notifications.html', notifications=notifs, total_non_lus=total[0] if total else 0)
    except Exception as e:
        print(f"❌ Erreur page_notifications: {e}")
        flash('Erreur lors du chargement des notifications')
        return redirect('/dashboard' if session.get('role') == 'admin' else '/vente')

@app.route('/api/stock_bas')
def api_stock_bas():
    try:
        rows = qall("SELECT nom,stock,stock_min FROM produits WHERE stock<=stock_min")
        return jsonify([{'nom':r[0],'stock':r[1],'stock_min':r[2]} for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ══════════════════════════════════════════════════════════════
# ACTEURS
# ══════════════════════════════════════════════════════════════
@app.route('/admin/acteurs')
def admin_acteurs():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        acteurs = qall('''SELECT id,nom,role,role_personnalise,password_hash,
            COALESCE(actif,1),COALESCE(motif_absence,''),COALESCE(permissions,'vente'),COALESCE(email,'')
            FROM users ORDER BY role DESC,actif DESC,id''')
        return render_template('admin_acteurs.html', acteurs=acteurs)
    except Exception as e:
        print(f"❌ Erreur admin_acteurs: {e}")
        flash('Erreur lors du chargement des acteurs')
        return redirect('/dashboard')

@app.route('/admin/acteurs/ajouter', methods=['POST'])
def ajouter_acteur():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '')
        rb = request.form.get('role_base', 'employe')
        rp = request.form.get('role_personnalise', '')
        mdp = request.form.get('mot_de_passe', '')
        email = request.form.get('email', '')
        if not nom or not mdp:
            flash('❌ Nom et mot de passe obligatoires')
            return redirect('/admin/acteurs')
        ph = hash_password(mdp)
        perms = 'admin' if rb == 'admin' else 'vente'
        exe("INSERT INTO users (role,role_personnalise,password_hash,nom,actif,permissions,email) VALUES (?,?,?,?,1,?,?)",
            (rb,rp,ph,nom,perms,email))
        flash(f'✅ Acteur "{nom}" créé')
    except Exception as e:
        print(f"❌ Erreur ajouter_acteur: {e}")
        flash('❌ Erreur lors de la création de l\'acteur')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/modifier/<int:id>', methods=['POST'])
def modifier_acteur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '')
        rp = request.form.get('role_personnalise', '')
        email = request.form.get('email', '')
        perms = ','.join(request.form.getlist('permissions')) or 'vente'
        actif = int(request.form.get('actif', 1))
        motif = request.form.get('motif_absence', '')
        exe("UPDATE users SET nom=?,role_personnalise=?,email=?,permissions=?,actif=?,motif_absence=? WHERE id=?",
            (nom,rp,email,perms,actif,motif,id))
        if request.form.get('new_password'):
            exe("UPDATE users SET password_hash=? WHERE id=?",
                (hash_password(request.form['new_password']),id))
        flash(f'✅ Acteur "{nom}" modifié')
    except Exception as e:
        print(f"❌ Erreur modifier_acteur: {e}")
        flash('❌ Erreur lors de la modification')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/permissions/<int:id>', methods=['POST'])
def modifier_permissions_acteur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        perms = request.form.getlist('permissions')
        if not perms:
            perms = ['vente']
        permissions_str = ','.join(perms)
        exe("UPDATE users SET permissions = %s WHERE id = %s", (permissions_str, id))
        flash('✅ Permissions mises à jour avec succès')
    except Exception as e:
        print(f"❌ Erreur modifier_permissions_acteur: {e}")
        flash(f'❌ Erreur: {str(e)}')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/modifier_email/<int:id>', methods=['POST'])
def modifier_email_acteur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        email = request.form.get('email', '')
        exe("UPDATE users SET email = %s WHERE id = %s", (email, id))
        flash('✅ Email mis à jour avec succès')
    except Exception as e:
        print(f"❌ Erreur modifier_email_acteur: {e}")
        flash(f'❌ Erreur: {str(e)}')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/modifier_role/<int:id>', methods=['POST'])
def modifier_role_acteur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        role_personnalise = request.form.get('role_personnalise', '')
        exe("UPDATE users SET role_personnalise = %s WHERE id = %s", (role_personnalise, id))
        flash('✅ Rôle personnalisé mis à jour avec succès')
    except Exception as e:
        print(f"❌ Erreur modifier_role_acteur: {e}")
        flash(f'❌ Erreur: {str(e)}')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/reset_mdp/<int:id>', methods=['POST'])
def reset_mdp_acteur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nouveau_mdp = request.form.get('nouveau_mdp', '')
        if len(nouveau_mdp) < 4:
            flash('❌ Le mot de passe doit contenir au moins 4 caractères')
            return redirect('/admin/acteurs')
        password_hash = hash_password(nouveau_mdp)
        exe("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, id))
        flash('✅ Mot de passe réinitialisé avec succès')
    except Exception as e:
        print(f"❌ Erreur reset_mdp_acteur: {e}")
        flash(f'❌ Erreur: {str(e)}')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/desactiver/<int:id>', methods=['POST'])
def desactiver_acteur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        if id == session.get('user_id'):
            flash('❌ Impossible de désactiver votre propre compte')
            return redirect('/admin/acteurs')
        motif = request.form.get('motif', '')
        motif_autre = request.form.get('motif_autre', '')
        if motif == 'Autre' and motif_autre:
            motif = motif_autre
        exe("UPDATE users SET actif = 0, motif_absence = %s WHERE id = %s", (motif, id))
        flash(f'✅ Compte désactivé avec succès. Motif: {motif}')
    except Exception as e:
        print(f"❌ Erreur desactiver_acteur: {e}")
        flash(f'❌ Erreur: {str(e)}')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/reactiver/<int:id>')
def reactiver_acteur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        if id == session.get('user_id'):
            flash('❌ Impossible de réactiver votre propre compte')
            return redirect('/admin/acteurs')
        exe("UPDATE users SET actif = 1, motif_absence = '' WHERE id = %s", (id,))
        flash('✅ Compte réactivé avec succès')
    except Exception as e:
        print(f"❌ Erreur reactiver_acteur: {e}")
        flash(f'❌ Erreur: {str(e)}')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/supprimer/<int:id>')
def supprimer_acteur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        if id == session.get('user_id'):
            flash('❌ Impossible de supprimer votre propre compte')
            return redirect('/admin/acteurs')
        u = q1("SELECT nom FROM users WHERE id=?",(id,))
        if u:
            exe("DELETE FROM users WHERE id=?",(id,))
            flash(f'🗑️ "{u[0]}" supprimé')
    except Exception as e:
        print(f"❌ Erreur supprimer_acteur: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/verifier_mdp', methods=['POST'])
def verifier_mdp_admin():
    try:
        if session.get('role') != 'admin':
            return jsonify({'success':False,'message':'Non autorisé'})
        data = request.get_json()
        mdp = data.get('mot_de_passe','')
        r = q1("SELECT password_hash FROM users WHERE id=? AND role='admin'",(session.get('user_id'),))
        if r and verify_password(mdp, r[0], user_id=session.get('user_id')):
            session['mdp_verifie'] = True
            return jsonify({'success':True})
        return jsonify({'success':False,'message':'Mot de passe incorrect'})
    except Exception as e:
        return jsonify({'success':False,'message': str(e)})

# ══════════════════════════════════════════════════════════════
# FOURNISSEURS
# ══════════════════════════════════════════════════════════════
@app.route('/admin/fournisseurs')
def admin_fournisseurs():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        fournisseurs = qall("SELECT * FROM fournisseurs ORDER BY nom")
        return render_template('admin_fournisseurs.html', fournisseurs=fournisseurs)
    except Exception as e:
        print(f"❌ Erreur admin_fournisseurs: {e}")
        flash('Erreur lors du chargement des fournisseurs')
        return redirect('/dashboard')

@app.route('/admin/fournisseurs/ajouter', methods=['POST'])
def ajouter_fournisseur():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '')
        produits = request.form.get('produits', '')
        telephone = request.form.get('telephone', '')
        email = request.form.get('email', '')
        adresse = request.form.get('adresse', '')
        if not nom:
            flash('❌ Le nom du fournisseur est obligatoire')
            return redirect('/admin/fournisseurs')
        exe("INSERT INTO fournisseurs (nom,produits,telephone,email,adresse) VALUES (?,?,?,?,?)",
            (nom,produits,telephone,email,adresse))
        flash('✅ Fournisseur ajouté')
    except Exception as e:
        print(f"❌ Erreur ajouter_fournisseur: {e}")
        flash('❌ Erreur lors de l\'ajout du fournisseur')
    return redirect('/admin/fournisseurs')

@app.route('/admin/fournisseurs/modifier/<int:id>', methods=['POST'])
def modifier_fournisseur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        nom = request.form.get('nom', '')
        produits = request.form.get('produits', '')
        telephone = request.form.get('telephone', '')
        email = request.form.get('email', '')
        adresse = request.form.get('adresse', '')
        exe("UPDATE fournisseurs SET nom=?,produits=?,telephone=?,email=?,adresse=? WHERE id=?",
            (nom,produits,telephone,email,adresse,id))
        flash(f'✅ "{nom}" modifié')
    except Exception as e:
        print(f"❌ Erreur modifier_fournisseur: {e}")
        flash('❌ Erreur lors de la modification')
    return redirect('/admin/fournisseurs')

@app.route('/admin/fournisseurs/supprimer/<int:id>')
def supprimer_fournisseur(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        f = q1("SELECT nom FROM fournisseurs WHERE id=?",(id,))
        if f:
            exe("DELETE FROM fournisseurs WHERE id=?",(id,))
            flash(f'🗑️ "{f[0]}" supprimé')
    except Exception as e:
        print(f"❌ Erreur supprimer_fournisseur: {e}")
        flash('❌ Erreur lors de la suppression')
    return redirect('/admin/fournisseurs')

# ══════════════════════════════════════════════════════════════
# STATISTIQUES
# ══════════════════════════════════════════════════════════════
@app.route('/admin/stats')
def admin_stats():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        cache_key = 'stats_data'
        cached_data = get_cached(cache_key, 120)
        if cached_data:
            ventes_jour, ventes_mois, top_produits, marge, marge_produits = cached_data
        else:
            ventes_jour = qall('''SELECT DATE(date_sortie::timestamp),COALESCE(SUM(total),0),COUNT(*)
                FROM sorties WHERE date_sortie::timestamp >= NOW() - INTERVAL '7 days'
                GROUP BY DATE(date_sortie::timestamp) ORDER BY DATE(date_sortie::timestamp)''')
            ventes_mois = qall('''SELECT TO_CHAR(date_sortie::timestamp,'YYYY-MM'),COALESCE(SUM(total),0),COUNT(*)
                FROM sorties WHERE date_sortie::timestamp >= NOW() - INTERVAL '6 months'
                GROUP BY 1 ORDER BY 1''')
            top_produits = qall('''SELECT p.nom,COALESCE(SUM(s.quantite),0) as tv
                FROM produits p LEFT JOIN sorties s ON p.id=s.produit_id
                GROUP BY p.id,p.nom ORDER BY tv DESC LIMIT 10''')
            marge = q1('''SELECT COALESCE((SELECT SUM(total) FROM sorties),0),
                                 COALESCE((SELECT SUM(total) FROM entrees),0)''')
            marge = marge if marge else (0,0)
            marge_produits = qall('''SELECT p.nom,COALESCE(SUM(s.total),0),COALESCE(SUM(e.total),0),
                COALESCE(SUM(s.total),0)-COALESCE(SUM(e.total),0)
                FROM produits p LEFT JOIN sorties s ON p.id=s.produit_id
                LEFT JOIN entrees e ON p.id=e.produit_id GROUP BY p.id,p.nom
                HAVING COALESCE(SUM(s.total),0)+COALESCE(SUM(e.total),0)>0
                ORDER BY 4 DESC LIMIT 10''')
            set_cached(cache_key, (ventes_jour, ventes_mois, top_produits, marge, marge_produits))
        return render_template('admin_stats.html', ventes_jour=ventes_jour, ventes_mois=ventes_mois,
            top_produits=top_produits, marge_totale=marge, marge_produits=marge_produits)
    except Exception as e:
        print(f"❌ Erreur admin_stats: {e}")
        flash('Erreur lors du chargement des statistiques')
        return redirect('/dashboard')

# ══════════════════════════════════════════════════════════════
# ARCHIVES
# ══════════════════════════════════════════════════════════════
@app.route('/admin/recus')
def admin_recus():
    """Recherche de reçus de vente par date et/ou nom de client, pour retrouver
    et réimprimer/exporter un reçu même longtemps après la vente."""
    try:
        if 'user_id' not in session:
            return redirect('/login')
        date_filtre = request.args.get('date', '').strip()
        client_filtre = request.args.get('client', '').strip()

        conditions = ["groupe_vente IS NOT NULL"]
        params = []
        conditions_arch = ["groupe_vente IS NOT NULL"]
        params_arch = []

        if date_filtre:
            conditions.append("DATE(date_sortie::date) = %s")
            params.append(date_filtre)
            conditions_arch.append("DATE(date_vente::date) = %s")
            params_arch.append(date_filtre)
        if client_filtre:
            conditions.append("client ILIKE %s")
            params.append(f'%{client_filtre}%')
            conditions_arch.append("client ILIKE %s")
            params_arch.append(f'%{client_filtre}%')

        where_sql = " AND ".join(conditions)
        where_sql_arch = " AND ".join(conditions_arch)

        recus = qall(f'''SELECT groupe_vente, client, MIN(date_sortie) as date_v,
                                 SUM(total) as total_v, COUNT(*) as nb_lignes, false as archivee
                          FROM sorties WHERE {where_sql}
                          GROUP BY groupe_vente, client
                          ORDER BY date_v DESC LIMIT 100''', tuple(params))

        recus_archives = qall(f'''SELECT groupe_vente, client, MIN(date_vente) as date_v,
                                          SUM(total) as total_v, COUNT(*) as nb_lignes, true as archivee
                                   FROM archive_ventes WHERE {where_sql_arch}
                                   GROUP BY groupe_vente, client
                                   ORDER BY date_v DESC LIMIT 100''', tuple(params_arch))

        tous_recus = sorted(list(recus) + list(recus_archives), key=lambda r: r[2] or '', reverse=True)[:150]

        return render_template('admin_recus.html', recus=tous_recus,
            date_filtre=date_filtre, client_filtre=client_filtre)
    except Exception as e:
        print(f"❌ Erreur admin_recus: {e}")
        flash('❌ Erreur lors de la recherche de reçus')
        return redirect('/dashboard')


@app.route('/rapport-journalier')
def rapport_journalier():
    """
    Liste des jours (7 derniers jours glissants) ayant une activité en cours
    (ventes/entrées/pertes), tirée directement des tables actives — donc bornée
    naturellement à ce qui n'a pas encore été archivé. Accessible à l'admin
    et à tout employé connecté (lecture seule, pas de permission spécifique).
    """
    try:
        if 'user_id' not in session:
            return redirect('/login')

        ventes_j = qall('''SELECT DATE(date_sortie::timestamp) as jour, COUNT(*), COALESCE(SUM(total),0)
            FROM sorties WHERE date_sortie::timestamp >= NOW() - INTERVAL '7 days'
            GROUP BY jour''')
        entrees_j = qall('''SELECT DATE(date_entree::timestamp) as jour, COUNT(*), COALESCE(SUM(total),0)
            FROM entrees WHERE date_entree::timestamp >= NOW() - INTERVAL '7 days'
            GROUP BY jour''')
        pertes_j = qall('''SELECT DATE(date_perte::timestamp) as jour, COUNT(*), COALESCE(SUM(total),0)
            FROM pertes WHERE date_perte::timestamp >= NOW() - INTERVAL '7 days'
            GROUP BY jour''')

        jours_data = {}
        def _ensure(js):
            return jours_data.setdefault(js, {'nb_ventes':0,'total_ventes':0,
                                                'nb_entrees':0,'total_entrees':0,
                                                'nb_pertes':0,'total_pertes':0})
        for jour, nb, total in ventes_j:
            d = _ensure(str(jour)); d['nb_ventes'] = nb; d['total_ventes'] = total
        for jour, nb, total in entrees_j:
            d = _ensure(str(jour)); d['nb_entrees'] = nb; d['total_entrees'] = total
        for jour, nb, total in pertes_j:
            d = _ensure(str(jour)); d['nb_pertes'] = nb; d['total_pertes'] = total

        rapport = [(j, jours_data[j]) for j in sorted(jours_data.keys(), reverse=True)]

        return render_template('rapport_journalier.html', rapport=rapport,
            aujourdhui=datetime.now().strftime('%Y-%m-%d'))
    except Exception as e:
        print(f"❌ Erreur rapport_journalier: {e}")
        flash('Erreur lors du chargement du rapport journalier')
        return redirect('/dashboard' if session.get('role') == 'admin' else '/vente')


@app.route('/rapport-journalier/<jour>')
def rapport_journalier_jour(jour):
    """Détail d'un jour (ventes/entrées/pertes) tiré des tables actives.
    Une fois la semaine archivée, ce même jour redevient consultable via
    /admin/archives à la place."""
    try:
        if 'user_id' not in session:
            return redirect('/login')

        ventes_jour = qall('''SELECT s.id, p.nom, s.quantite, s.prix_unitaire, s.total,
                                      s.date_sortie, s.client, u.nom, s.groupe_vente
                               FROM sorties s
                               JOIN produits p ON s.produit_id = p.id
                               JOIN users u ON s.employe_id = u.id
                               WHERE DATE(s.date_sortie) = ?
                               ORDER BY s.date_sortie ASC''', (jour,))
        entrees_jour = qall('''SELECT e.id, p.nom, e.quantite, e.prix_unitaire, e.total,
                                       e.date_entree, e.fournisseur, u.nom
                                FROM entrees e
                                JOIN produits p ON e.produit_id = p.id
                                JOIN users u ON e.employe_id = u.id
                                WHERE DATE(e.date_entree) = ?
                                ORDER BY e.date_entree ASC''', (jour,))
        pertes_jour = qall('''SELECT pe.id, p.nom, pe.quantite, pe.prix_unitaire, pe.total,
                                      pe.motif, pe.date_perte, u.nom
                               FROM pertes pe
                               JOIN produits p ON pe.produit_id = p.id
                               JOIN users u ON pe.employe_id = u.id
                               WHERE DATE(pe.date_perte) = ?
                               ORDER BY pe.date_perte ASC''', (jour,))

        stats_ventes = (len(ventes_jour), sum(v[2] for v in ventes_jour), sum(v[4] for v in ventes_jour))
        stats_entrees = (len(entrees_jour), sum(e[2] for e in entrees_jour), sum(e[4] for e in entrees_jour))
        stats_pertes = (len(pertes_jour), sum(p[2] for p in pertes_jour), sum(p[4] for p in pertes_jour))

        if not ventes_jour and not entrees_jour and not pertes_jour:
            flash("ℹ️ Aucune donnée pour ce jour ici — si la semaine a déjà été archivée, consultez Archives.")

        return render_template('rapport_journalier_jour.html',
            jour=jour, ventes_jour=ventes_jour, entrees_jour=entrees_jour, pertes_jour=pertes_jour,
            stats_ventes=stats_ventes, stats_entrees=stats_entrees, stats_pertes=stats_pertes)
    except Exception as e:
        print(f"❌ Erreur rapport_journalier_jour: {e}")
        flash('❌ Erreur lors du chargement du détail du jour')
        return redirect('/rapport-journalier')


@app.route('/admin/archives')
def admin_archives():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        type_arch = request.args.get('type', 'ventes')
        date_debut = request.args.get('date_debut', '')
        date_fin = request.args.get('date_fin', '')
        produit_filtre = request.args.get('produit', '')
        tri = request.args.get('tri', 'date_desc')
        order = 'DESC' if 'desc' in tri else 'ASC'

        rows = []
        if type_arch == 'entrees':
            conditions = []
            params = []
            if date_debut:
                conditions.append("date_entree >= %s")
                params.append(date_debut)
            if date_fin:
                conditions.append("date_entree <= %s")
                params.append(date_fin + " 23:59:59")
            if produit_filtre:
                conditions.append("LOWER(produit_nom) LIKE LOWER(%s)")
                params.append(f'%{produit_filtre}%')
            where_sql = (" AND " + " AND ".join(conditions)) if conditions else ""
            rows = qall(f'''SELECT id,produit_nom,quantite,prix_unitaire,total,date_entree,fournisseur,employe_nom,archive_date
                FROM archive_entrees WHERE 1=1{where_sql}
                ORDER BY date_entree {order} LIMIT 200''', tuple(params))
        elif type_arch == 'pertes':
            conditions = []
            params = []
            if date_debut:
                conditions.append("date_perte >= %s")
                params.append(date_debut)
            if date_fin:
                conditions.append("date_perte <= %s")
                params.append(date_fin + " 23:59:59")
            if produit_filtre:
                conditions.append("LOWER(produit_nom) LIKE LOWER(%s)")
                params.append(f'%{produit_filtre}%')
            where_sql = (" AND " + " AND ".join(conditions)) if conditions else ""
            rows = qall(f'''SELECT id,produit_nom,quantite,prix_unitaire,total,motif,date_perte,employe_nom,archive_date
                FROM archive_pertes WHERE 1=1{where_sql}
                ORDER BY date_perte {order} LIMIT 200''', tuple(params))
        else:
            conditions = []
            params = []
            if date_debut:
                conditions.append("date_vente >= %s")
                params.append(date_debut)
            if date_fin:
                conditions.append("date_vente <= %s")
                params.append(date_fin + " 23:59:59")
            if produit_filtre:
                conditions.append("LOWER(produit_nom) LIKE LOWER(%s)")
                params.append(f'%{produit_filtre}%')
            where_sql = (" AND " + " AND ".join(conditions)) if conditions else ""
            rows = qall(f'''SELECT id,produit_nom,quantite,prix_unitaire,total,date_vente,client,employe_nom,archive_date
                FROM archive_ventes WHERE 1=1{where_sql}
                ORDER BY date_vente {order} LIMIT 200''', tuple(params))

        nb_ventes_arch = q1("SELECT COUNT(*),COALESCE(SUM(total),0) FROM archive_ventes") or (0,0)
        nb_entrees_arch = q1("SELECT COUNT(*),COALESCE(SUM(total),0) FROM archive_entrees") or (0,0)
        nb_pertes_arch = q1("SELECT COUNT(*),COALESCE(SUM(total),0) FROM archive_pertes") or (0,0)

        total_ca_archive = nb_ventes_arch[1] if nb_ventes_arch else 0
        total_achats_archive = nb_entrees_arch[1] if nb_entrees_arch else 0
        total_pertes_ca = nb_pertes_arch[1] if nb_pertes_arch else 0

        return render_template('archives.html', 
            archives=rows, 
            type_archive=type_arch,
            ventes_archive=rows if type_arch=='ventes' else [],
            entrees_archive=rows if type_arch=='entrees' else [],
            pertes_archive=rows if type_arch=='pertes' else [],
            nb_ventes_arch=nb_ventes_arch[0] if nb_ventes_arch else 0,
            nb_entrees_arch=nb_entrees_arch[0] if nb_entrees_arch else 0,
            nb_pertes_arch=nb_pertes_arch[0] if nb_pertes_arch else 0,
            total_ventes_archive=nb_ventes_arch[0] if nb_ventes_arch else 0,
            total_entrees_archive=nb_entrees_arch[0] if nb_entrees_arch else 0,
            total_pertes_archive=nb_pertes_arch[0] if nb_pertes_arch else 0,
            total_ca_archive=total_ca_archive,
            total_achats_archive=total_achats_archive,
            total_pertes_ca=total_pertes_ca,
            date_debut=date_debut, 
            date_fin=date_fin, 
            produit_filtre=produit_filtre, 
            tri=tri,
            type_data=type_arch)
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'❌ Erreur lors du chargement des archives: {str(e)}')
        return render_template('archives.html', 
            archives=[],
            type_archive=type_arch,
            ventes_archive=[],
            entrees_archive=[],
            pertes_archive=[],
            nb_ventes_arch=0,
            nb_entrees_arch=0,
            nb_pertes_arch=0,
            total_ventes_archive=0,
            total_entrees_archive=0,
            total_pertes_archive=0,
            total_ca_archive=0,
            total_achats_archive=0,
            total_pertes_ca=0,
            date_debut=date_debut,
            date_fin=date_fin,
            produit_filtre=produit_filtre,
            tri=tri,
            type_data=type_arch)

@app.route('/admin/archives/jour/<jour>')
def admin_archive_jour(jour):
    """Détail des ventes/entrées archivées pour une journée donnée (YYYY-MM-DD)."""
    if session.get('role') != 'admin':
        return redirect('/login')
    try:
        ventes_jour = qall('''SELECT id,produit_id,quantite,prix_unitaire,total,date_vente,
                               employe_id,client,archive_date,semaine,annee,produit_nom,employe_nom,groupe_vente
                               FROM archive_ventes
                               WHERE date_vente LIKE ?
                               ORDER BY date_vente ASC''', (jour + '%',))
        entrees_jour = qall('''SELECT id,produit_id,quantite,prix_unitaire,total,date_entree,
                                fournisseur,employe_id,archive_date,semaine,annee,produit_nom,employe_nom
                                FROM archive_entrees
                                WHERE date_entree LIKE ?
                                ORDER BY date_entree ASC''', (jour + '%',))

        pertes_jour = qall('''SELECT id,produit_id,quantite,prix_unitaire,total,motif,date_perte,
                               employe_id,archive_date,semaine,annee,produit_nom,employe_nom
                               FROM archive_pertes
                               WHERE date_perte LIKE ?
                               ORDER BY date_perte ASC''', (jour + '%',))

        stats_ventes = q1('''SELECT COUNT(*), COALESCE(SUM(quantite),0), COALESCE(SUM(total),0)
                              FROM archive_ventes WHERE date_vente LIKE ?''', (jour + '%',)) or (0, 0, 0)
        stats_entrees = q1('''SELECT COUNT(*), COALESCE(SUM(quantite),0), COALESCE(SUM(total),0)
                               FROM archive_entrees WHERE date_entree LIKE ?''', (jour + '%',)) or (0, 0, 0)
        stats_pertes = q1('''SELECT COUNT(*), COALESCE(SUM(quantite),0), COALESCE(SUM(total),0)
                              FROM archive_pertes WHERE date_perte LIKE ?''', (jour + '%',)) or (0, 0, 0)

        if not ventes_jour and not entrees_jour and not pertes_jour:
            flash("ℹ️ Aucune donnée archivée pour ce jour (seules les semaines déjà archivées sont consultables ici).")

        return render_template('archive_jour.html',
            jour=jour,
            ventes_jour=ventes_jour,
            entrees_jour=entrees_jour,
            pertes_jour=pertes_jour,
            stats_ventes=stats_ventes,
            stats_entrees=stats_entrees,
            stats_pertes=stats_pertes)
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'❌ Erreur lors du chargement du détail du jour : {str(e)}')
        return redirect('/admin/archives')


@app.route('/admin/archives/semaines')
def admin_archive_semaines():
    """Liste des récapitulatifs hebdomadaires archivés."""
    if session.get('role') != 'admin':
        return redirect('/login')
    try:
        semaines = qall('''SELECT semaine, annee, date_debut, date_fin, nb_ventes, total_ventes,
                            nb_entrees, total_achats, archive_date
                            FROM archive_recap
                            ORDER BY annee DESC, semaine DESC''')
        return render_template('archive_semaines.html', semaines=semaines)
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'❌ Erreur lors du chargement des semaines archivées : {str(e)}')
        return redirect('/admin/archives')


# ══════════════════════════════════════════════════════════════
# EXPORT PDF - AVEC LOGO CORRIGÉ
# ══════════════════════════════════════════════════════════════

def format_prix(valeur):
    return f"{valeur:,.0f}".replace(",", " ")

def find_logo():
    """Recherche le logo dans différents emplacements - PRIORITÉ à logo-hitna"""
    logo_paths = [
        os.path.join('static', 'images', 'logo-hitna.jpg'),      # ← 1er choix
        os.path.join('static', 'images', 'logo-hitna.jpeg'),
        os.path.join('static', 'images', 'logo-hitna.png'),
        os.path.join('static', 'images', 'logo-hitna.webp'),
        # Fallback vers logo.jpg si logo-hitna n'existe pas
        os.path.join('static', 'images', 'logo.jpg'),
        os.path.join('static', 'images', 'logo.jpeg'),
        os.path.join('static', 'images', 'logo.png'),
        os.path.join('static', 'images', 'logo-192.png'),
    ]
    for path in logo_paths:
        if os.path.exists(path):
            print(f"✅ Logo trouvé : {path}")
            return path
    print("⚠️ Aucun logo trouvé dans static/images/")
    return None

def add_header_to_pdf(c, width, height):
    """Ajouter l'en-tête personnalisé HITNA avec logo"""
    try:
        from reportlab.lib.utils import ImageReader
        
        logo_path = os.path.join('static', 'images', 'logo.jpg')
        if not os.path.exists(logo_path):
            logo_path = None
        
        if logo_path:
            img = ImageReader(logo_path)
            logo_size = 45
            # Logo en haut à gauche
            c.drawImage(img, 40, height - 55, width=logo_size, height=logo_size, mask='auto')
            # Logo en haut à droite
            c.drawImage(img, width - 40 - logo_size, height - 55, width=logo_size, height=logo_size, mask='auto')
            print("✅ Logo trouvé dans les PDF")
        else:
            print("⚠️ Aucun logo trouvé pour les PDF")
        
        # ── En-tête texte ──
        c.setFont("Helvetica-Bold", 24)
        c.setFillColorRGB(0.12, 0.24, 0.45)
        c.drawString(100, height - 45, "HITNA Superette")
        
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.3, 0.3, 0.3)
    
        
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawString(100, height - 78, "📍 Hounsa ; Porto-Novo  Rép: Bénin")
        c.drawString(100, height - 92, "📞 01 67 19 85 31 | IFU : 02211238429")
        
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.setLineWidth(1)
        c.line(40, height - 105, width - 40, height - 105)
        
        return True
    except Exception as e:
        print(f"⚠️ Erreur ajout en-tête: {e}")
        return False

def add_logo_to_pdf(c, width, height):
    """Ajouter le logo en arrière-plan (filigrane)"""
    try:
        from reportlab.lib.utils import ImageReader
        
        logo_path = find_logo()
        
        if logo_path:
            img = ImageReader(logo_path)
            c.saveState()
            c.setFillAlpha(0.06)
            logo_center_size = 220
            x_center = (width - logo_center_size) / 2
            y_center = (height - logo_center_size) / 2
            c.drawImage(img, x_center, y_center, width=logo_center_size, height=logo_center_size, mask='auto')
            c.restoreState()
            return True
    except Exception as e:
        print(f"⚠️ Erreur ajout filigrane: {e}")
    return False

# ─── HELPERS : sections de tableau paginées pour les exports "point du jour" ───
def _pdf_table_headers(c, colonnes, x_positions, y, font_size=9):
    c.setFont("Helvetica-Bold", font_size)
    c.setFillColorRGB(0.3, 0.3, 0.3)
    for label, x in zip(colonnes, x_positions):
        c.drawString(x, y, label)
    return y - 15

def _pdf_draw_section(c, width, height, y, titre, titre_rgb, colonnes, x_positions,
                       rows, row_to_cells, message_vide, font_header=9, font_row=8, row_h=15):
    """
    Dessine un titre de section puis son tableau à partir de la position y courante,
    SANS limite sur le nombre de lignes : si le contenu dépasse la page, une nouvelle
    page est ajoutée automatiquement (avec en-tête/logo HITNA et répétition des
    colonnes) plutôt que de tronquer les données.
    Si `rows` est vide, affiche `message_vide` à la place du tableau (la section
    reste visible, elle ne disparaît pas).
    Retourne la position y après le contenu dessiné.
    """
    c.setFont("Helvetica-Bold", 13)
    c.setFillColorRGB(*titre_rgb)
    c.drawString(50, y, titre)
    y -= 25

    if not rows:
        c.setFont("Helvetica-Oblique", 10)
        c.setFillColorRGB(0.55, 0.55, 0.55)
        c.drawString(50, y, message_vide)
        return y - 20

    y = _pdf_table_headers(c, colonnes, x_positions, y, font_header)
    c.setFont("Helvetica", font_row)
    c.setFillColorRGB(0, 0, 0)

    for row in rows:
        if y < 50:
            c.showPage()
            add_header_to_pdf(c, width, height)
            add_logo_to_pdf(c, width, height)
            y = height - 100
            y = _pdf_table_headers(c, colonnes, x_positions, y, font_header)
            c.setFont("Helvetica", font_row)
            c.setFillColorRGB(0, 0, 0)
        for val, x in zip(row_to_cells(row), x_positions):
            c.drawString(x, y, str(val))
        y -= row_h

    return y

# ─── EXPORT PDF POUR ADMIN ────────────────────────────────────
@app.route('/export/pdf')
def export_pdf():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        
        add_header_to_pdf(c, width, height)
        add_logo_to_pdf(c, width, height)
        
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawString(50, height - 125, f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        
        y = height - 150
        data = qall("SELECT DATE(date_sortie::timestamp),COALESCE(SUM(total),0),COUNT(*) FROM sorties GROUP BY DATE(date_sortie::timestamp) ORDER BY 1 DESC LIMIT 30")
        
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(0.12, 0.24, 0.45)
        c.drawString(50, y, "Date")
        c.drawString(150, y, "Montant (FCFA)")
        c.drawString(280, y, "Ventes")
        y -= 20
        
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0, 0, 0)
        for row in data:
            c.drawString(50, y, str(row[0]))
            c.drawString(150, y, f"{row[1]:,}")
            c.drawString(280, y, str(row[2]))
            y -= 20
            if y < 50:
                c.showPage()
                add_header_to_pdf(c, width, height)
                add_logo_to_pdf(c, width, height)
                y = height - 100
                c.setFont("Helvetica-Bold", 10)
                c.setFillColorRGB(0.12, 0.24, 0.45)
                c.drawString(50, y, "Date")
                c.drawString(150, y, "Montant (FCFA)")
                c.drawString(280, y, "Ventes")
                y -= 20
        
        c.save()
        buffer.seek(0)
        
        return send_file(buffer, as_attachment=True,
            download_name=f"rapport_{datetime.now().strftime('%Y%m%d')}.pdf", mimetype='application/pdf')
    except Exception as e:
        print(f"❌ Erreur export_pdf: {e}")
        flash('Erreur lors de l\'export PDF')
        return redirect('/dashboard')

# ─── EXPORT PDF POUR ADMIN (JOURNÉE SPÉCIFIQUE) ─────────────
@app.route('/export/pdf_jour/<date>')
def export_pdf_jour(date):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d/%m/%Y')
        date_sql = date_obj.strftime('%Y-%m-%d')
        
        ventes = qall('''SELECT s.id, p.nom, s.quantite, s.prix_unitaire, s.total, 
                                s.date_sortie, s.client, u.nom as vendeur
                         FROM sorties s 
                         JOIN produits p ON s.produit_id = p.id 
                         JOIN users u ON s.employe_id = u.id
                         WHERE DATE(s.date_sortie) = %s
                         ORDER BY s.date_sortie DESC''', (date_sql,))
        
        entrees = qall('''SELECT e.id, p.nom, e.quantite, e.prix_unitaire, e.total, 
                                 e.date_entree, e.fournisseur, u.nom as enregistreur
                          FROM entrees e 
                          JOIN produits p ON e.produit_id = p.id 
                          JOIN users u ON e.employe_id = u.id
                          WHERE DATE(e.date_entree) = %s
                          ORDER BY e.date_entree DESC''', (date_sql,))
        
        pertes = qall('''SELECT pe.id, p.nom, pe.quantite, pe.prix_unitaire, pe.total,
                                 pe.date_perte, pe.motif, u.nom as enregistreur
                          FROM pertes pe
                          JOIN produits p ON pe.produit_id = p.id
                          JOIN users u ON pe.employe_id = u.id
                          WHERE DATE(pe.date_perte) = %s
                          ORDER BY pe.date_perte DESC''', (date_sql,))
        
        total_ventes = sum(v[4] for v in ventes) if ventes else 0
        total_entrees = sum(e[4] for e in entrees) if entrees else 0
        total_pertes = sum(p[4] for p in pertes) if pertes else 0
        nb_ventes = len(ventes)
        nb_entrees = len(entrees)
        nb_pertes = len(pertes)
        
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        
        add_header_to_pdf(c, width, height)
        add_logo_to_pdf(c, width, height)
        
        y = height - 130
        
        # ── DATE DU RAPPORT ──
        c.setFont("Helvetica-Bold", 11)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        c.drawString(50, y, f"📅 Rapport du {date_str}")
        y -= 25
        
        # ── RÉSUMÉ DU JOUR ──
        c.setFont("Helvetica-Bold", 14)
        c.setFillColorRGB(0.12, 0.24, 0.45)
        c.drawString(50, y, "📊 RÉSUMÉ DU JOUR")
        y -= 25
        
        c.setFont("Helvetica", 11)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(50, y, f"💰 Ventes : {nb_ventes} vente(s) - {format_prix(total_ventes)} FCFA")
        y -= 20
        c.drawString(50, y, f"📥 Entrées : {nb_entrees} entrée(s) - {format_prix(total_entrees)} FCFA")
        y -= 20
        c.drawString(50, y, f"⚠️ Pertes : {nb_pertes} perte(s) - {format_prix(total_pertes)} FCFA")
        y -= 30
        
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.setLineWidth(0.5)
        c.line(50, y, width - 50, y)
        y -= 20

        X = [50, 180, 220, 300, 380, 440]

        # ── PAGE : VENTES (toujours affichée, même vide) ──
        cols_ventes = ["Produit", "Qté", "Prix unit.", "Total", "Client", "Vendeur"]
        y = _pdf_draw_section(
            c, width, height, y, "🛒 VENTES DU JOUR", (0.12, 0.24, 0.45),
            cols_ventes, X, ventes,
            lambda v: [v[1][:30] if v[1] else "-", str(v[2]), format_prix(v[3]),
                       format_prix(v[4]), v[6][:15] if v[6] else "-", v[7][:15] if v[7] else "-"],
            "Aucune vente ce jour."
        )

        # ── PAGE DÉDIÉE : ENTRÉES ──
        c.showPage()
        add_header_to_pdf(c, width, height)
        add_logo_to_pdf(c, width, height)
        y = height - 100
        cols_entrees = ["Produit", "Qté", "Prix unit.", "Total", "Fournisseur", "Enreg."]
        y = _pdf_draw_section(
            c, width, height, y, "📥 ENTRÉES DE STOCK", (0.12, 0.24, 0.45),
            cols_entrees, X, entrees,
            lambda e: [e[1][:30] if e[1] else "-", str(e[2]), format_prix(e[3]),
                       format_prix(e[4]), e[6][:15] if e[6] else "-", e[7][:15] if e[7] else "-"],
            "Aucune entrée de stock ce jour."
        )

        # ── PAGE DÉDIÉE : PERTES ──
        c.showPage()
        add_header_to_pdf(c, width, height)
        add_logo_to_pdf(c, width, height)
        y = height - 100
        cols_pertes = ["Produit", "Qté", "Prix unit.", "Total", "Motif", "Enreg."]
        y = _pdf_draw_section(
            c, width, height, y, "⚠️ PERTES DU JOUR", (0.72, 0.11, 0.11),
            cols_pertes, X, pertes,
            lambda p: [p[1][:30] if p[1] else "-", str(p[2]), format_prix(p[3]),
                       format_prix(p[4]), p[6][:15] if p[6] else "-", p[7][:15] if p[7] else "-"],
            "Aucune perte ce jour."
        )

        # ── SIGNATURE : sur la page des pertes si la place le permet,
        #    sinon sur une nouvelle page (pour ne pas chevaucher le tableau) ──
        if y < 60:
            c.showPage()
            add_header_to_pdf(c, width, height)
            add_logo_to_pdf(c, width, height)
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawString(50, 30, "HITNA - Système de gestion - Rapport généré automatiquement")
        
        c.save()
        buffer.seek(0)
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"rapport_{date}.pdf",
            mimetype='application/pdf'
        )
        
    except Exception as e:
        print(f"❌ Erreur export PDF: {e}")
        flash(f'❌ Erreur lors de l\'export PDF: {str(e)}')
        return redirect('/admin/archives')

# ─── EXPORT PDF POUR EMPLOYÉ (POINT DU JOUR) ────────────────
@app.route('/export/pdf_employe')
def export_pdf_employe():
    """Export du point du jour pour l'employé (ventes et entrées uniquement)"""
    try:
        if 'user_id' not in session:
            flash('❌ Veuillez vous connecter')
            return redirect('/login')
        
        date_sql = datetime.now().strftime('%Y-%m-%d')
        date_str = datetime.now().strftime('%d/%m/%Y')
        
        ventes = qall('''SELECT s.id, p.nom, s.quantite, s.prix_unitaire, s.total, 
                                s.date_sortie, s.client, u.nom as vendeur
                         FROM sorties s 
                         JOIN produits p ON s.produit_id = p.id 
                         JOIN users u ON s.employe_id = u.id
                         WHERE DATE(s.date_sortie) = %s
                         ORDER BY s.date_sortie DESC''', (date_sql,))
        
        entrees = qall('''SELECT e.id, p.nom, e.quantite, e.prix_unitaire, e.total, 
                                 e.date_entree, e.fournisseur, u.nom as enregistreur
                          FROM entrees e 
                          JOIN produits p ON e.produit_id = p.id 
                          JOIN users u ON e.employe_id = u.id
                          WHERE DATE(e.date_entree) = %s
                          ORDER BY e.date_entree DESC''', (date_sql,))
        
        total_ventes = sum(v[4] for v in ventes) if ventes else 0
        total_entrees = sum(e[4] for e in entrees) if entrees else 0
        nb_ventes = len(ventes)
        nb_entrees = len(entrees)
        
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        
        add_header_to_pdf(c, width, height)
        add_logo_to_pdf(c, width, height)
        
        # Titre
        c.setFont("Helvetica-Bold", 16)
        c.setFillColorRGB(0.12, 0.24, 0.45)
        c.drawString(50, height - 125, f"📋 POINT DU JOUR - {date_str}")
        
        y = height - 155
        
        # Résumé
        c.setFont("Helvetica-Bold", 12)
        c.setFillColorRGB(0.12, 0.24, 0.45)
        c.drawString(50, y, "📊 RÉSUMÉ")
        y -= 22
        
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(50, y, f"💰 Ventes : {nb_ventes} vente(s) - {format_prix(total_ventes)} FCFA")
        y -= 18
        c.drawString(50, y, f"📥 Entrées : {nb_entrees} entrée(s) - {format_prix(total_entrees)} FCFA")
        y -= 25
        
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.setLineWidth(0.5)
        c.line(50, y, width - 50, y)
        y -= 18

        X = [50, 170, 210, 290, 370, 440]

        # ── PAGE : VENTES (toujours affichée, même vide) ──
        cols_ventes = ["Produit", "Qté", "Prix unit.", "Total", "Client", "Vendeur"]
        y = _pdf_draw_section(
            c, width, height, y, "🛒 VENTES", (0.12, 0.24, 0.45),
            cols_ventes, X, ventes,
            lambda v: [v[1][:28] if v[1] else "-", str(v[2]) if v[2] else "-",
                       format_prix(v[3]) if v[3] else "-", format_prix(v[4]) if v[4] else "-",
                       v[6][:12] if v[6] else "-", v[7][:12] if v[7] else "-"],
            "Aucune vente ce jour.",
            font_header=8, font_row=7.5, row_h=14
        )

        # ── PAGE DÉDIÉE : ENTRÉES ──
        c.showPage()
        add_header_to_pdf(c, width, height)
        add_logo_to_pdf(c, width, height)
        y = height - 100
        cols_entrees = ["Produit", "Qté", "Prix unit.", "Total", "Fournisseur", "Enreg."]
        y = _pdf_draw_section(
            c, width, height, y, "📥 ENTRÉES", (0.12, 0.24, 0.45),
            cols_entrees, X, entrees,
            lambda e: [e[1][:28] if e[1] else "-", str(e[2]) if e[2] else "-",
                       format_prix(e[3]) if e[3] else "-", format_prix(e[4]) if e[4] else "-",
                       e[6][:12] if e[6] else "-", e[7][:12] if e[7] else "-"],
            "Aucune entrée ce jour.",
            font_header=8, font_row=7.5, row_h=14
        )

        # ── SIGNATURE : sur la page des entrées si la place le permet ──
        if y < 60:
            c.showPage()
            add_header_to_pdf(c, width, height)
            add_logo_to_pdf(c, width, height)
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawString(50, 30, f"HITNA - Point du jour {date_str} - Généré automatiquement")
        
        c.save()
        buffer.seek(0)
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"point_du_jour_{datetime.now().strftime('%Y%m%d')}.pdf",
            mimetype='application/pdf'
        )
        
    except Exception as e:
        print(f"❌ Erreur export PDF employé: {e}")
        flash(f'❌ Erreur lors de l\'export PDF: {str(e)}')
        return redirect('/vente' if session.get('role') == 'employe' else '/dashboard')

# ══════════════════════════════════════════════════════════════
# API DE SYNCHRONISATION POUR MODE HORS LIGNE
# ══════════════════════════════════════════════════════════════
@app.route('/api/sync/sorties', methods=['POST'])
def api_sync_sorties():
    try:
        data = request.get_json()
        if not data.get('produit_id') or not data.get('quantite'):
            return jsonify({'error': 'Données manquantes'}), 400
        produit = q1("SELECT prix FROM produits WHERE id = %s", (data['produit_id'],))
        if not produit:
            return jsonify({'error': 'Produit non trouvé'}), 404
        prix_unitaire = produit[0]
        total = data['quantite'] * prix_unitaire
        exe("""INSERT INTO sorties 
            (produit_id, quantite, prix_unitaire, total, date_sortie, client, employe_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (data['produit_id'], data['quantite'], prix_unitaire, total,
             data.get('date_sortie', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
             data.get('client', ''), data.get('employe_id', 1)))
        exe("UPDATE produits SET stock = stock - %s WHERE id = %s", (data['quantite'], data['produit_id']))
        verifier_alertes_stock()
        return jsonify({'success': True, 'message': 'Vente synchronisée'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync/entrees', methods=['POST'])
def api_sync_entrees():
    try:
        data = request.get_json()
        if not data.get('produit_id') or not data.get('quantite'):
            return jsonify({'error': 'Données manquantes'}), 400
        produit = q1("SELECT prix FROM produits WHERE id = %s", (data['produit_id'],))
        if not produit:
            return jsonify({'error': 'Produit non trouvé'}), 404
        prix_unitaire = data.get('prix_unitaire', produit[0])
        total = data['quantite'] * prix_unitaire
        exe("""INSERT INTO entrees 
            (produit_id, quantite, prix_unitaire, total, date_entree, fournisseur, employe_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (data['produit_id'], data['quantite'], prix_unitaire, total,
             data.get('date_entree', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
             data.get('fournisseur', ''), data.get('employe_id', 1)))
        exe("UPDATE produits SET stock = stock + %s WHERE id = %s", (data['quantite'], data['produit_id']))
        verifier_alertes_stock()
        return jsonify({'success': True, 'message': 'Entrée synchronisée'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync/pertes', methods=['POST'])
def api_sync_pertes():
    try:
        data = request.get_json()
        if not data.get('produit_id') or not data.get('quantite'):
            return jsonify({'error': 'Données manquantes'}), 400
        produit = q1("SELECT prix FROM produits WHERE id = %s", (data['produit_id'],))
        if not produit:
            return jsonify({'error': 'Produit non trouvé'}), 404
        prix_unitaire = produit[0]
        total = data['quantite'] * prix_unitaire
        exe("""INSERT INTO pertes 
            (produit_id, quantite, prix_unitaire, total, motif, date_perte, employe_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (data['produit_id'], data['quantite'], prix_unitaire, total,
             data.get('motif', 'Synchronisation hors ligne'),
             data.get('date_perte', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
             data.get('employe_id', 1)))
        exe("UPDATE produits SET stock = GREATEST(0, stock - %s) WHERE id = %s", (data['quantite'], data['produit_id']))
        return jsonify({'success': True, 'message': 'Perte synchronisée'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ══════════════════════════════════════════════════════════════
# MOT DE PASSE OUBLIÉ
# ══════════════════════════════════════════════════════════════
@app.route('/mot_de_passe_oublie', methods=['GET','POST'])
def mot_de_passe_oublie():
    try:
        if request.method == 'POST':
            email = request.form.get('email', '').strip()
            if email != 'hitnasuperette@gmail.com':
                flash('❌ Seul l\'administrateur peut réinitialiser son mot de passe.', 'error')
                return redirect('/mot_de_passe_oublie')
            user = q1("SELECT id, nom FROM users WHERE role='admin' AND email = %s AND actif = 1", (email,))
            if user:
                token = generate_reset_token(user[0])
                reset_url = url_for('reset_password', token=token, _external=True)
                try:
                    msg = Message(
                        subject="🔐 Réinitialisation de votre mot de passe - HITNA",
                        recipients=[email],
                        body=f"""
Bonjour {user[1]},

Vous avez demandé la réinitialisation de votre mot de passe pour l'application HITNA.

Cliquez sur le lien ci-dessous pour créer un nouveau mot de passe :
{reset_url}

Ce lien est valable 24 heures.

Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.

Cordialement,
L'équipe HITNA
""",
                        html=f"""
<h2>🔐 Réinitialisation de votre mot de passe</h2>
<p>Bonjour <strong>{user[1]}</strong>,</p>
<p>Vous avez demandé la réinitialisation de votre mot de passe pour l'application <strong>HITNA</strong>.</p>
<p>Cliquez sur le bouton ci-dessous pour créer un nouveau mot de passe :</p>
<p><a href="{reset_url}" style="background: #1e3c72; color: white; padding: 10px 20px; border-radius: 5px; text-decoration: none;">🔑 Réinitialiser mon mot de passe</a></p>
<p>Ce lien est valable <strong>24 heures</strong>.</p>
<p>Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.</p>
<br>
<p>Cordialement,<br><strong>L'équipe HITNA</strong></p>
"""
                    )
                    # Borne stricte : un problème réseau/SMTP (ex: identifiants Gmail
                    # invalides, connexion filtrée) peut sinon bloquer la requête
                    # jusqu'à faire dépasser le timeout du worker gunicorn -> 502
                    # pour TOUTE la page, au lieu d'une simple erreur gérée ici.
                    ancien_timeout = socket.getdefaulttimeout()
                    socket.setdefaulttimeout(10)
                    try:
                        mail.send(msg)
                        flash('✅ Un email de réinitialisation a été envoyé à hitnasuperette@gmail.com', 'success')
                    finally:
                        socket.setdefaulttimeout(ancien_timeout)
                except Exception as e:
                    print(f"Erreur envoi email: {e}")
                    flash(f'🔗 Lien de réinitialisation : {reset_url}', 'info')
            else:
                flash('❌ Aucun administrateur actif avec cet email', 'error')
            return redirect('/login')
        return render_template('mot_de_passe_oublie.html')
    except Exception as e:
        print(f"❌ Erreur mot_de_passe_oublie: {e}")
        flash('Erreur lors de la réinitialisation')
        return redirect('/login')

@app.route('/reset_password/<token>', methods=['GET','POST'])
def reset_password(token):
    try:
        td = q1('''SELECT rt.user_id,rt.expires_at,rt.used,u.actif,u.nom
            FROM reset_tokens rt JOIN users u ON rt.user_id=u.id WHERE rt.token=? AND rt.used=0''',(token,))
        if not td:
            flash('❌ Lien invalide')
            return redirect('/login')
        user_id, expires, used, actif, nom = td
        if actif == 0:
            flash('❌ Compte désactivé')
            return redirect('/login')
        if datetime.now() > datetime.strptime(expires, '%Y-%m-%d %H:%M:%S'):
            flash('❌ Lien expiré')
            return redirect('/mot_de_passe_oublie')
        if request.method == 'POST':
            pwd = request.form.get('new_password', '')
            cpwd = request.form.get('confirm_password', '')
            if pwd != cpwd:
                flash('❌ Mots de passe différents')
                return redirect(f'/reset_password/{token}')
            if len(pwd) < 4:
                flash('❌ Minimum 4 caractères')
                return redirect(f'/reset_password/{token}')
            exe("UPDATE users SET password_hash=? WHERE id=?",(hash_password(pwd), user_id))
            exe("UPDATE reset_tokens SET used=1 WHERE token=?",(token,))
            flash('✅ Mot de passe réinitialisé !')
            return redirect('/login')
        return render_template('reset_password.html', token=token)
    except Exception as e:
        print(f"❌ Erreur reset_password: {e}")
        flash('Erreur lors de la réinitialisation')
        return redirect('/login')

# ──────────────────────────────────────────────────────────────
# RÉAPPROVISIONNEMENT (suggestions basées sur la vitesse de vente)
# ──────────────────────────────────────────────────────────────
JOURS_PERIODE_VITESSE = 30    # période d'observation pour calculer la vitesse de vente
JOURS_COUVERTURE_CIBLE = 21   # nombre de jours de stock visés par la suggestion de commande
SEUIL_ALERTE_JOURS = 7        # en dessous de ce nombre de jours restants -> "à commander bientôt"

@app.route('/admin/reapprovisionnement')
def admin_reapprovisionnement():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')

        lignes = qall(f'''
            SELECT p.id, p.nom, p.stock, p.stock_min,
                   COALESCE(v.total_vendu, 0) as total_vendu_periode
            FROM produits p
            LEFT JOIN (
                SELECT produit_id, SUM(quantite) as total_vendu
                FROM sorties
                WHERE date_sortie::timestamp >= NOW() - INTERVAL '{JOURS_PERIODE_VITESSE} days'
                GROUP BY produit_id
            ) v ON v.produit_id = p.id
            ORDER BY p.nom
        ''')

        produits_urgents = []
        produits_ok = []
        produits_sans_vente = []

        for pid, nom, stock, stock_min, total_vendu in lignes:
            vitesse_jour = total_vendu / JOURS_PERIODE_VITESSE if total_vendu else 0

            if vitesse_jour > 0:
                jours_restants = stock / vitesse_jour
                quantite_cible = vitesse_jour * JOURS_COUVERTURE_CIBLE
                suggestion = max(0, round(quantite_cible - stock))
                item = {
                    'id': pid, 'nom': nom, 'stock': stock, 'stock_min': stock_min,
                    'vitesse_jour': round(vitesse_jour, 2),
                    'total_vendu_periode': total_vendu,
                    'jours_restants': round(jours_restants, 1),
                    'suggestion': suggestion
                }
                if jours_restants <= SEUIL_ALERTE_JOURS:
                    produits_urgents.append(item)
                else:
                    produits_ok.append(item)
            else:
                produits_sans_vente.append({
                    'id': pid, 'nom': nom, 'stock': stock, 'stock_min': stock_min
                })

        produits_urgents.sort(key=lambda x: x['jours_restants'])
        produits_ok.sort(key=lambda x: x['jours_restants'])

        return render_template('reapprovisionnement.html',
            produits_urgents=produits_urgents,
            produits_ok=produits_ok,
            produits_sans_vente=produits_sans_vente,
            jours_periode=JOURS_PERIODE_VITESSE,
            jours_couverture=JOURS_COUVERTURE_CIBLE,
            seuil_alerte=SEUIL_ALERTE_JOURS)
    except Exception as e:
        print(f"❌ Erreur admin_reapprovisionnement: {e}")
        flash('Erreur lors du calcul des suggestions de réapprovisionnement')
        return redirect('/dashboard')

# ──────────────────────────────────────────────────────────────
# ALERTES PRODUITS (seuils de stock personnalisés)
# ──────────────────────────────────────────────────────────────
@app.route('/admin/alertes/produits')
def admin_alertes_produits():
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        produits = qall('''SELECT p.id, p.nom, p.stock, p.stock_min,
                                   COALESCE(a.seuil, p.stock_min, 5) as seuil,
                                   COALESCE(a.actif, 1) as actif
                            FROM produits p
                            LEFT JOIN alertes_produits a ON p.id = a.produit_id
                            ORDER BY p.nom''')
        return render_template('admin_alertes_produits.html', produits=produits)
    except Exception as e:
        print(f"❌ Erreur admin_alertes_produits: {e}")
        flash('Erreur lors du chargement des alertes')
        return redirect('/dashboard')

@app.route('/admin/alertes/produits/modifier/<int:produit_id>', methods=['POST'])
def modifier_alerte_produit(produit_id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        seuil = request.form.get('seuil', 5)
        try:
            seuil = int(seuil)
        except (ValueError, TypeError):
            seuil = 5
        actif = 1 if request.form.get('actif') else 0

        existant = q1("SELECT id FROM alertes_produits WHERE produit_id=?", (produit_id,))
        if existant:
            exe("UPDATE alertes_produits SET seuil=?, actif=? WHERE produit_id=?",
                (seuil, actif, produit_id))
        else:
            exe("INSERT INTO alertes_produits (produit_id, seuil, actif) VALUES (?,?,?)",
                (produit_id, seuil, actif))
        flash('✅ Seuil d\'alerte enregistré')
        return redirect('/admin/alertes/produits')
    except Exception as e:
        print(f"❌ Erreur modifier_alerte_produit: {e}")
        flash('Erreur lors de l\'enregistrement du seuil')
        return redirect('/admin/alertes/produits')

# ══════════════════════════════════════════════════════════════
# API JSON
# ══════════════════════════════════════════════════════════════
@app.route('/api/produits')
def api_produits():
    try:
        if 'user_id' not in session:
            return jsonify({'error':'Non autorisé'}),401
        cache_key = 'api_produits'
        cached_data = get_cached(cache_key, 60)
        if cached_data:
            return jsonify(cached_data)
        produits = qall("SELECT id,nom,prix,stock FROM produits ORDER BY nom")
        data = {
            'produits':[{'id':p[0],'nom':p[1],'prix':p[2],'stock':p[3]} for p in produits],
            'timestamp':datetime.now().isoformat()
        }
        set_cached(cache_key, data)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync', methods=['POST'])
def api_sync():
    """Traite un lot d'actions enregistrées hors ligne (vente/entrée/perte)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Non autorisé'}), 401
    try:
        data = request.get_json(force=True, silent=True) or {}
        actions = data.get('actions', [])
        employe_id = session.get('user_id', 1)
        results = []

        for action in actions:
            client_id = action.get('client_id', '')
            atype = action.get('type', '')
            payload = action.get('payload', {}) or {}
            try:
                if atype == 'vente':
                    cart = payload.get('cart', [])
                    client = (payload.get('client') or '').strip()
                    if not cart:
                        results.append({'client_id': client_id, 'status': 'error_definitif', 'message': 'Panier vide'})
                        continue
                    groupe_vente, lignes_ok, erreurs = _traiter_vente_cart(cart, client, employe_id)
                    if lignes_ok:
                        results.append({'client_id': client_id, 'status': 'ok', 'message': ', '.join(lignes_ok), 'groupe_vente': groupe_vente})
                    else:
                        results.append({'client_id': client_id, 'status': 'error_definitif', 'message': '; '.join(erreurs) or 'Vente impossible'})

                elif atype == 'entree':
                    ok, message = _traiter_entree(
                        payload.get('produit_id'), payload.get('quantite'),
                        payload.get('prix_unitaire'), payload.get('fournisseur', ''), employe_id)
                    results.append({'client_id': client_id, 'status': 'ok' if ok else 'error_definitif', 'message': message})

                elif atype == 'perte':
                    ok, message = _traiter_perte(
                        payload.get('produit_id'), payload.get('quantite'),
                        payload.get('motif', ''), employe_id)
                    results.append({'client_id': client_id, 'status': 'ok' if ok else 'error_definitif', 'message': message})

                else:
                    results.append({'client_id': client_id, 'status': 'error_definitif', 'message': f'Type d\'action inconnu: {atype}'})
            except Exception as item_error:
                print(f"❌ Erreur sync action {client_id}: {item_error}")
                results.append({'client_id': client_id, 'status': 'retry', 'message': str(item_error)})

        clear_cache()
        return jsonify({'results': results})
    except Exception as e:
        print(f"❌ Erreur api_sync: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/offline')
def offline():
    return render_template('offline.html')

@app.route('/sw.js')
def service_worker():
    from flask import make_response
    try:
        with open('sw.js', 'r') as f:
            content = f.read()
        resp = make_response(content, 200)
        resp.headers['Content-Type'] = 'application/javascript'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp
    except Exception as e:
        print(f"❌ Erreur service_worker: {e}")
        return "Service Worker non disponible", 404

@app.route('/manifest.json')
def manifest():
    try:
        return app.send_static_file('manifest.json')
    except Exception as e:
        print(f"❌ Erreur manifest: {e}")
        return "Manifest non disponible", 404

# ──────────────────────────────────────────────────────────────
# INITIALISATION AU CHARGEMENT DU MODULE
# Important : avec gunicorn (Render), le bloc "if __name__ == '__main__'"
# n'est JAMAIS exécuté. Il faut donc appeler init_db() ici, en dehors
# de ce bloc, pour que les tables soient créées aussi en production.
# ──────────────────────────────────────────────────────────────
init_db()

# ──────────────────────────────────────────────────────────────
# LANCEMENT (uniquement en local, ex: python app.py)
# ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)