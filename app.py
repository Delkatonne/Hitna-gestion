from flask import Flask, render_template, request, redirect, session, flash, jsonify, url_for, send_file
from flask_mail import Mail, Message
from datetime import datetime, timedelta
import hashlib, os, random, string, io
import psycopg2
import psycopg2.extras
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from time import time
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hitna_secret')

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
# SYSTÈME DE CACHE AVANCÉ
# ══════════════════════════════════════════════════════════════
_cache = {}
CACHE_TTL = {
    'produits': 120,       # 2 minutes
    'ventes': 30,          # 30 secondes
    'dashboard': 60,       # 1 minute
    'stats': 120,          # 2 minutes
    'notifications': 10,   # 10 secondes
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
# CONNEXION POSTGRESQL (SANS POOL)
# ──────────────────────────────────────────────────────────────
def get_db():
    url = os.environ.get('DATABASE_URL', '')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    if not url:
        raise RuntimeError("DATABASE_URL manquante. Ajoutez-la dans les variables d'environnement Render.")
    return psycopg2.connect(url)

def q1(sql, params=()):
    """fetchone — retourne un tuple ou None."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql.replace('?', '%s'), params)
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row
    except Exception as e:
        print(f"❌ Erreur q1: {e}")
        return None

def qall(sql, params=()):
    """fetchall — retourne une liste de tuples."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql.replace('?', '%s'), params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"❌ Erreur qall: {e}")
        return []

def exe(sql, params=(), returning=False):
    """INSERT / UPDATE / DELETE avec commit. returning=True retourne le nouvel id."""
    try:
        sql2 = sql.replace('?', '%s')
        if returning and 'INSERT' in sql2.upper() and 'RETURNING' not in sql2.upper():
            sql2 += ' RETURNING id'
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql2, params)
        result = cur.fetchone()[0] if returning else None
        conn.commit()
        cur.close()
        conn.close()
        clear_cache()
        return result
    except Exception as e:
        print(f"❌ Erreur exe: {e}")
        return None

# ──────────────────────────────────────────────────────────────
# INITIALISATION BASE DE DONNÉES (SÉCURISÉE)
# ──────────────────────────────────────────────────────────────
def init_db():
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='users'")
        tables_existent = c.fetchone()[0] > 0
        
        if tables_existent:
            print("✅ Tables existantes - AUCUNE MODIFICATION")
            conn.commit()
            c.close()
            conn.close()
            return

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

        try:
            c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='produits' AND column_name='unite_id'")
            if not c.fetchone():
                c.execute("ALTER TABLE produits ADD COLUMN unite_id INTEGER REFERENCES unites_mesure(id)")
                print("✅ Colonne 'unite_id' ajoutée à produits")
        except Exception as e:
            print(f"⚠️ Erreur ajout colonne unite_id: {e}")

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
            admin_hash = hashlib.sha256('admin123'.encode()).hexdigest()
            c.execute("INSERT INTO users (role,role_personnalise,password_hash,nom,actif,permissions) VALUES (%s,%s,%s,%s,%s,%s)",
                      ('admin','Administrateur', admin_hash, 'Administrateur', 1, 'admin'))
            emp_hash = hashlib.sha256('emp123'.encode()).hexdigest()
            c.execute("INSERT INTO users (role,role_personnalise,password_hash,nom,actif,permissions) VALUES (%s,%s,%s,%s,%s,%s)",
                      ('employe','Employé', emp_hash, 'Employé', 1, 'vente'))
            print("✅ Utilisateurs par défaut créés")

        conn.commit()
        c.close()
        conn.close()
        print("✅ Base de données initialisée")
    except Exception as e:
        print(f"❌ Erreur init_db: {e}")

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
                             s.date_sortie,s.client,s.employe_id,p.nom,u.nom
                      FROM sorties s JOIN produits p ON s.produit_id=p.id
                      JOIN users u ON s.employe_id=u.id
                      WHERE DATE(s.date_sortie)>=%s AND DATE(s.date_sortie)<=%s''',
                   (debut.strftime('%Y-%m-%d'), fin.strftime('%Y-%m-%d')))
        ventes = cm.fetchall()
        for v in ventes:
            cm.execute('''INSERT INTO archive_ventes VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                       (v[0],v[1],v[2],v[3],v[4],v[5],v[7],v[6],now_s,sem,annee,v[8],v[9]))
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
        conn.close()
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
    return {'date_actuelle': datetime.now().strftime('%d/%m/%Y %H:%M')}

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

def verifier_alertes_stock():
    try:
        produits = qall('''SELECT p.id,p.nom,p.stock,COALESCE(a.seuil,p.stock_min,5)
            FROM produits p LEFT JOIN alertes_produits a ON p.id=a.produit_id AND a.actif=1
            WHERE p.stock<=COALESCE(a.seuil,p.stock_min,5)''')
        admins = qall("SELECT id FROM users WHERE role='admin' AND actif=1")
        for p in produits:
            for a in admins:
                existant = q1('''SELECT COUNT(*) FROM notifications
                    WHERE user_id=%s AND type='stock_bas' AND message LIKE %s
                    AND date_creation::timestamp > NOW() - INTERVAL '1 day' ''',
                    (a[0], f'%{p[1]}%'))
                if existant and existant[0]==0:
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
# ROUTES AUTH
# ──────────────────────────────────────────────────────────────
@app.route('/')
def accueil():
    return redirect('/login')

@app.route('/login', methods=['GET','POST'])
def login():
    try:
        if request.method == 'POST':
            sel = request.form.get('role', '')
            password = request.form.get('password', '')
            ph = hashlib.sha256(password.encode()).hexdigest()
            
            user = q1("""
                SELECT id, nom, actif, role_personnalise, role, permissions 
                FROM users 
                WHERE (role_personnalise = %s OR role = %s) AND password_hash = %s
            """, (sel, sel, ph))
            
            if not user:
                rb = None
                if sel == 'Administrateur':
                    rb = 'admin'
                elif sel == 'Employé':
                    rb = 'employe'
                if rb:
                    user = q1("""
                        SELECT id, nom, actif, role_personnalise, role, permissions 
                        FROM users 
                        WHERE role = %s AND password_hash = %s
                    """, (rb, ph))
            
            if user:
                if user[2] == 0:
                    flash('❌ Compte désactivé.')
                    return redirect('/login')
                
                session.update({
                    'user_id': user[0],
                    'role': user[4],
                    'user_nom': user[1],
                    'role_affiche': user[3] or ('Administrateur' if user[4] == 'admin' else 'Employé'),
                    'permissions': user[5]
                })
                
                flash(f'✅ Bonjour {user[1]} !')
                return redirect('/dashboard' if user[4] == 'admin' else '/vente')
            
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
                (hashlib.sha256(pwd.encode()).hexdigest(), session['user_id']))
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
            produits, unites = cached_data
        else:
            produits = qall('''SELECT p.id, p.nom, p.prix, p.stock, p.stock_min,
                                      COALESCE(u.symbole, '') as unite_symbole,
                                      COALESCE(u.nom, '') as unite_nom,
                                      p.unite_id
                               FROM produits p 
                               LEFT JOIN unites_mesure u ON p.unite_id = u.id 
                               ORDER BY p.nom''')
            unites = qall("SELECT id, nom, symbole FROM unites_mesure WHERE actif = 1 ORDER BY nom")
            set_cached(cache_key, (produits, unites))
        return render_template('produits.html', produits=produits, unites=unites)
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
        exe("INSERT INTO produits (nom, prix, stock, stock_min, unite_id) VALUES (?,?,?,?,?)",
            (nom, prix, stock, smin, unite_id))
        flash(f'✅ Produit "{nom}" ajouté ({prix} FCFA)')
        envoyer_notification_a_tous('produit','🆕 Nouveau produit',f'"{nom}" ajouté ({prix} FCFA)','/admin/produits')
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
        smin = int(request.form.get('stock_min', 5))
        unite_id = request.form.get('unite_id')
        if not unite_id or unite_id == '0':
            unite_id = None
        else:
            unite_id = int(unite_id)
        exe("UPDATE produits SET nom=?, prix=?, stock_min=?, unite_id=? WHERE id=?", 
            (nom, prix, smin, unite_id, id))
        flash(f'✅ Produit "{nom}" modifié')
    except Exception as e:
        print(f"❌ Erreur modifier_produit: {e}")
        flash('❌ Erreur lors de la modification')
    return redirect('/admin/produits')

@app.route('/admin/produits/supprimer/<int:id>')
def supprimer_produit(id):
    try:
        if session.get('role') != 'admin':
            return redirect('/login')
        p = q1("SELECT nom FROM produits WHERE id=?", (id,))
        if p:
            ventes = q1("SELECT COUNT(*) FROM sorties WHERE produit_id=?", (id,))
            entrees = q1("SELECT COUNT(*) FROM entrees WHERE produit_id=?", (id,))
            pertes = q1("SELECT COUNT(*) FROM pertes WHERE produit_id=?", (id,))
            if (ventes and ventes[0] > 0) or (entrees and entrees[0] > 0) or (pertes and pertes[0] > 0):
                flash('❌ Ce produit a des mouvements. Impossible de le supprimer.')
                return redirect('/admin/produits')
            exe("DELETE FROM produits WHERE id=?", (id,))
            flash(f'🗑️ "{p[0]}" supprimé')
    except Exception as e:
        print(f"❌ Erreur supprimer_produit: {e}")
        flash('❌ Erreur lors de la suppression')
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

@app.route('/admin/entrees/ajouter', methods=['POST'])
def ajouter_entree():
    try:
        if not check_perm('entrees'):
            flash('❌ Permission refusée')
            return redirect('/vente')
        pid = int(request.form.get('produit_id', 0))
        qty = int(request.form.get('quantite', 0))
        pu = int(request.form.get('prix_unitaire', 0))
        f = request.form.get('fournisseur', '')
        if pid <= 0 or qty <= 0 or pu <= 0:
            flash('❌ Données invalides')
            return redirect('/admin/entrees')
        total = qty * pu
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        exe("INSERT INTO entrees (produit_id,quantite,prix_unitaire,total,date_entree,fournisseur,employe_id) VALUES (?,?,?,?,?,?,?)",
            (pid,qty,pu,total,now,f,session.get('user_id', 1)))
        exe("UPDATE produits SET stock=stock+? WHERE id=?",(qty,pid))
        flash(f'✅ Entrée : +{qty} unités')
        verifier_alertes_stock()
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
            pid = int(request.form.get('produit_id', 0))
            qty = int(request.form.get('quantite', 0))
            client = request.form.get('client', '')
            if pid <= 0 or qty <= 0:
                flash('❌ Données invalides')
                return redirect('/admin/ventes')
            p = q1("SELECT nom,prix,stock FROM produits WHERE id=?",(pid,))
            if not p:
                flash('❌ Produit introuvable')
                return redirect('/admin/ventes')
            if qty > p[2]:
                flash(f'❌ Stock insuffisant ! {p[2]} unités restantes')
            else:
                total = p[1] * qty
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                exe("INSERT INTO sorties (produit_id,quantite,prix_unitaire,total,date_sortie,client,employe_id) VALUES (?,?,?,?,?,?,?)",
                    (pid,qty,p[1],total,now,client,session.get('user_id', 1)))
                exe("UPDATE produits SET stock=stock-? WHERE id=?",(qty,pid))
                flash(f'✅ Vente : {qty} {p[0]} → {total} FCFA')
                verifier_alertes_stock()
            return redirect('/admin/ventes')
        cache_key = 'admin_ventes_data'
        cached_data = get_cached(cache_key, 30)
        if cached_data:
            produits, historique, stats_vendeurs = cached_data
        else:
            produits = qall("SELECT id,nom,prix,stock FROM produits WHERE stock>0 ORDER BY nom LIMIT 50")
            historique = qall('''SELECT s.id,p.nom,s.quantite,s.total,s.date_sortie,u.nom,s.client
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
                pid = int(request.form.get('produit_id', 0))
                qty = int(request.form.get('quantite', 0))
                client = request.form.get('client', '').strip()
                if pid <= 0:
                    flash('❌ Veuillez sélectionner un produit')
                    return redirect('/vente')
                if qty <= 0:
                    flash('❌ La quantité doit être supérieure à 0')
                    return redirect('/vente')
                p = q1("SELECT nom, prix, stock FROM produits WHERE id=?", (pid,))
                if not p:
                    flash('❌ Produit introuvable')
                    return redirect('/vente')
                if qty > p[2]:
                    flash(f'❌ Stock insuffisant ! {p[2]} unités restantes')
                    return redirect('/vente')
                total = p[1] * qty
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                exe("""INSERT INTO sorties 
                    (produit_id, quantite, prix_unitaire, total, date_sortie, client, employe_id) 
                    VALUES (?,?,?,?,?,?,?)""",
                    (pid, qty, p[1], total, now, client, session.get('user_id', 1)))
                exe("UPDATE produits SET stock = stock - ? WHERE id = ?", (qty, pid))
                flash(f'✅ Vente : {qty} {p[0]} → {total} FCFA')
                verifier_alertes_stock()
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
            produits = qall("SELECT id,nom,prix,stock FROM produits WHERE stock>0 ORDER BY nom LIMIT 30")
            historique = qall('''SELECT s.id, p.nom, s.quantite, s.total, s.date_sortie, s.client, u.nom, u.role
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
             historique, stock_bas, top_produits, stats_vendeurs,
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
            historique = qall('''SELECT s.id,p.nom,s.quantite,s.total,s.date_sortie,u.nom,s.client
                FROM sorties s JOIN produits p ON s.produit_id=p.id JOIN users u ON s.employe_id=u.id
                ORDER BY s.date_sortie DESC LIMIT 20''')
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
                                   historique, stock_bas, top_produits, stats_vendeurs,
                                   ventes_7_jours, ventes_par_heure))
        return render_template('dashboard.html',
            total_jour=total_jour, nb_produits=nb_produits,
            stock_total=stock_total, nb_stock_bas=nb_stock_bas,
            historique=historique, stock_bas=stock_bas,
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
        pid = int(request.form.get('produit_id', 0))
        qty = int(request.form.get('quantite', 0))
        motif = request.form.get('motif', '')
        if pid <= 0 or qty <= 0:
            flash('❌ Données invalides')
            return redirect('/admin/pertes')
        p = q1("SELECT nom,prix,stock FROM produits WHERE id=?",(pid,))
        if not p:
            flash('❌ Produit introuvable')
            return redirect('/admin/pertes')
        if qty > p[2]:
            flash(f'❌ Stock insuffisant ! {p[2]} unités de {p[0]}')
            return redirect('/admin/pertes')
        total = qty * p[1]
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        exe("INSERT INTO pertes (produit_id,quantite,prix_unitaire,total,motif,date_perte,employe_id) VALUES (?,?,?,?,?,?,?)",
            (pid,qty,p[1],total,motif,now,session.get('user_id', 1)))
        exe("UPDATE produits SET stock=GREATEST(0,stock-?) WHERE id=?",(qty,pid))
        flash(f'⚠️ Perte : {qty}×{p[0]} = {total} FCFA')
        envoyer_notification_a_tous('perte','⚠️ Perte signalée',
            f'{qty} unités de "{p[0]}" perdues ({total} FCFA)','/admin/pertes')
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
# ALERTES PRODUITS, ACTEURS, FOURNISSEURS, STATS, ARCHIVES
# ══════════════════════════════════════════════════════════════
# (Garder vos routes existantes pour acteurs, fournisseurs, stats, archives)
# Je les ai raccourcies pour la lisibilité mais elles restent identiques

# ══════════════════════════════════════════════════════════════
# EXPORT PDF - AVEC LOGO CORRIGÉ
# ══════════════════════════════════════════════════════════════

def format_prix(valeur):
    return f"{valeur:,.0f}".replace(",", " ")

def find_logo():
    """Recherche le logo dans différents emplacements"""
    logo_paths = [
        os.path.join('static', 'images', 'logo-hitna.jpg'),
        os.path.join('static', 'images', 'logo-hitna.jpeg'),
        os.path.join('static', 'images', 'logo-hitna.png'),
        os.path.join('static', 'images', 'logo-hitna.webp'),
        os.path.join('static', 'images', 'logo.jpg'),
        os.path.join('static', 'images', 'logo.jpeg'),
        os.path.join('static', 'images', 'logo.png'),
        os.path.join('static', 'images', 'logo-192.png'),
    ]
    for path in logo_paths:
        if os.path.exists(path):
            return path
    return None

def add_header_to_pdf(c, width, height):
    """Ajouter l'en-tête personnalisé HITNA avec logo"""
    try:
        from reportlab.lib.utils import ImageReader
        
        logo_path = find_logo()
        
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
        c.drawString(100, height - 45, "HITNA")
        
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        c.drawString(100, height - 62, "Système de gestion de superette")
        
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawString(100, height - 78, "📍 Houng-Bo, Petite Noue, Rédement")
        c.drawString(100, height - 92, "📞 64798537 | 020-11230443")
        
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

# ─── EXPORT PDF POUR EMPLOYÉ ──────────────────────────────────
@app.route('/export/pdf_employe')
def export_pdf_employe():
    """Export du point du jour pour l'employé"""
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
        
        # Ventes
        if ventes:
            c.setFont("Helvetica-Bold", 11)
            c.setFillColorRGB(0.12, 0.24, 0.45)
            c.drawString(50, y, "🛒 VENTES")
            y -= 18
            
            c.setFont("Helvetica-Bold", 8)
            c.setFillColorRGB(0.3, 0.3, 0.3)
            c.drawString(50, y, "Produit")
            c.drawString(170, y, "Qté")
            c.drawString(210, y, "Prix unit.")
            c.drawString(290, y, "Total")
            c.drawString(370, y, "Client")
            c.drawString(440, y, "Vendeur")
            y -= 14
            
            c.setFont("Helvetica", 7.5)
            c.setFillColorRGB(0, 0, 0)
            for v in ventes[:25]:
                if y < 50:
                    c.showPage()
                    add_header_to_pdf(c, width, height)
                    add_logo_to_pdf(c, width, height)
                    y = height - 100
                    c.setFont("Helvetica-Bold", 8)
                    c.setFillColorRGB(0.3, 0.3, 0.3)
                    c.drawString(50, y, "Produit")
                    c.drawString(170, y, "Qté")
                    c.drawString(210, y, "Prix unit.")
                    c.drawString(290, y, "Total")
                    c.drawString(370, y, "Client")
                    c.drawString(440, y, "Vendeur")
                    y -= 14
                    c.setFont("Helvetica", 7.5)
                    c.setFillColorRGB(0, 0, 0)
                
                c.drawString(50, y, v[1][:28] if v[1] else "-")
                c.drawString(170, y, str(v[2]) if v[2] else "-")
                c.drawString(210, y, format_prix(v[3]) if v[3] else "-")
                c.drawString(290, y, format_prix(v[4]) if v[4] else "-")
                c.drawString(370, y, v[6][:12] if v[6] else "-")
                c.drawString(440, y, v[7][:12] if v[7] else "-")
                y -= 14
            
            y -= 8
        
        # Entrées
        if entrees:
            if y < 100:
                c.showPage()
                add_header_to_pdf(c, width, height)
                add_logo_to_pdf(c, width, height)
                y = height - 100
            
            c.setFont("Helvetica-Bold", 11)
            c.setFillColorRGB(0.12, 0.24, 0.45)
            c.drawString(50, y, "📥 ENTRÉES")
            y -= 18
            
            c.setFont("Helvetica-Bold", 8)
            c.setFillColorRGB(0.3, 0.3, 0.3)
            c.drawString(50, y, "Produit")
            c.drawString(170, y, "Qté")
            c.drawString(210, y, "Prix unit.")
            c.drawString(290, y, "Total")
            c.drawString(370, y, "Fournisseur")
            c.drawString(440, y, "Enreg.")
            y -= 14
            
            c.setFont("Helvetica", 7.5)
            c.setFillColorRGB(0, 0, 0)
            for e in entrees[:20]:
                if y < 50:
                    c.showPage()
                    add_header_to_pdf(c, width, height)
                    add_logo_to_pdf(c, width, height)
                    y = height - 100
                    c.setFont("Helvetica-Bold", 8)
                    c.setFillColorRGB(0.3, 0.3, 0.3)
                    c.drawString(50, y, "Produit")
                    c.drawString(170, y, "Qté")
                    c.drawString(210, y, "Prix unit.")
                    c.drawString(290, y, "Total")
                    c.drawString(370, y, "Fournisseur")
                    c.drawString(440, y, "Enreg.")
                    y -= 14
                    c.setFont("Helvetica", 7.5)
                    c.setFillColorRGB(0, 0, 0)
                
                c.drawString(50, y, e[1][:28] if e[1] else "-")
                c.drawString(170, y, str(e[2]) if e[2] else "-")
                c.drawString(210, y, format_prix(e[3]) if e[3] else "-")
                c.drawString(290, y, format_prix(e[4]) if e[4] else "-")
                c.drawString(370, y, e[6][:12] if e[6] else "-")
                c.drawString(440, y, e[7][:12] if e[7] else "-")
                y -= 14
        
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
# API ET AUTRES ROUTES
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
# LANCEMENT
# ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)