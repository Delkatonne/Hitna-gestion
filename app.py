from flask import Flask, render_template, request, redirect, session, flash, jsonify, url_for, send_file
from flask_mail import Mail, Message
from datetime import datetime, timedelta
import hashlib, os, random, string, io

# ══════════════════════════════════════════════════════════════
# MIGRATION SQLite → PostgreSQL
# Remplace les deux fichiers SQLite (hitna.db + archive.db)
# par une seule base PostgreSQL persistante sur Render.
# ══════════════════════════════════════════════════════════════
import psycopg2
import psycopg2.extras
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hitna_secret')

# ──────────────────────────────────────────────────────────────
# CONNEXION POSTGRESQL
# DATABASE_URL est fournie automatiquement par Render
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
    conn = get_db(); cur = conn.cursor()
    cur.execute(sql.replace('?','%s'), params)
    row = cur.fetchone(); cur.close(); conn.close()
    return row

def qall(sql, params=()):
    """fetchall — retourne une liste de tuples."""
    conn = get_db(); cur = conn.cursor()
    cur.execute(sql.replace('?','%s'), params)
    rows = cur.fetchall(); cur.close(); conn.close()
    return rows

def exe(sql, params=(), returning=False):
    """INSERT / UPDATE / DELETE avec commit. returning=True retourne le nouvel id."""
    sql2 = sql.replace('?','%s')
    if returning and 'INSERT' in sql2.upper() and 'RETURNING' not in sql2.upper():
        sql2 += ' RETURNING id'
    conn = get_db(); cur = conn.cursor()
    cur.execute(sql2, params)
    result = cur.fetchone()[0] if returning else None
    conn.commit(); cur.close(); conn.close()
    return result

# ──────────────────────────────────────────────────────────────
# INITIALISATION BASE DE DONNÉES
# ──────────────────────────────────────────────────────────────
def init_db():
    conn = get_db(); c = conn.cursor()

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

    # Archives (toutes dans la même base PostgreSQL)
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

    c.execute('CREATE INDEX IF NOT EXISTS idx_sorties_date  ON sorties(date_sortie)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_entrees_date  ON entrees(date_entree)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_archive_vd    ON archive_ventes(date_vente)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_archive_ed    ON archive_entrees(date_entree)')

    # Données initiales — seulement si aucun utilisateur n'existe
    c.execute('SELECT COUNT(*) FROM users'); 
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (role,role_personnalise,password_hash,nom,actif,permissions) VALUES (%s,%s,%s,%s,%s,%s)",
                  ('admin','Administrateur',hashlib.sha256('admin123'.encode()).hexdigest(),'Administrateur',1,'admin'))
        c.execute("INSERT INTO users (role,role_personnalise,password_hash,nom,actif,permissions) VALUES (%s,%s,%s,%s,%s,%s)",
                  ('employe','Employé',hashlib.sha256('emp123'.encode()).hexdigest(),'Employé',1,'vente'))

    conn.commit(); c.close(); conn.close()

# ──────────────────────────────────────────────────────────────
# ARCHIVAGE HEBDOMADAIRE (même logique, une seule base)
# ──────────────────────────────────────────────────────────────
def get_derniere_archive():
    row = q1("SELECT semaine FROM archive_recap ORDER BY id DESC LIMIT 1")
    return row[0] if row else 0

def archiver_hebdomadaire():
    conn = get_db(); cm = conn.cursor()
    today = datetime.now()
    debut = today - timedelta(days=7)
    fin   = today - timedelta(days=1)
    sem   = debut.isocalendar()[1]
    annee = debut.isocalendar()[0]
    now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Ventes
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

    # Entrées
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

    # Pertes
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
    conn.commit(); cm.close(); conn.close()

def archiver_si_necessaire():
    today = datetime.now()
    if today.weekday() == 0 and today.hour < 2:
        if get_derniere_archive() != today.isocalendar()[1]:
            archiver_hebdomadaire()

# ──────────────────────────────────────────────────────────────
# HELPERS MÉTIER
# ──────────────────────────────────────────────────────────────
@app.context_processor
def inject_now():
    return {'date_actuelle': datetime.now().strftime('%d/%m/%Y %H:%M')}

def get_all_roles():
    roles_raw = qall("SELECT DISTINCT role, role_personnalise FROM users WHERE actif=1 ORDER BY role")
    result, seen = [], set()
    for role, rp in roles_raw:
        if rp and rp not in seen:
            result.append({'role_base':role,'role_affiche':rp}); seen.add(rp)
        elif role not in seen:
            result.append({'role_base':role,'role_affiche':'Administrateur' if role=='admin' else 'Employé'}); seen.add(role)
    return result

def creer_notification(user_id, type_n, titre, message, lien=None):
    exe("INSERT INTO notifications (user_id,type,title,message,lien,date_creation) VALUES (?,?,?,?,?,?)",
        (user_id,type_n,titre,message,lien,datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

def envoyer_notification_a_tous(type_n, titre, message, lien=None):
    for u in qall("SELECT id FROM users WHERE actif=1"):
        creer_notification(u[0],type_n,titre,message,lien)

def verifier_alertes_stock():
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

def generate_reset_token(user_id):
    token = ''.join(random.choices(string.ascii_letters+string.digits, k=50))
    expires = (datetime.now()+timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    exe("INSERT INTO reset_tokens (user_id,token,expires_at) VALUES (?,?,?)",(user_id,token,expires))
    return token

def check_perm(perm):
    if session.get('role')=='admin': return True
    r = q1("SELECT permissions FROM users WHERE id=?",(session['user_id'],))
    return r and perm in r[0].split(',')

# ──────────────────────────────────────────────────────────────
# ROUTES AUTH
# ──────────────────────────────────────────────────────────────
@app.route('/')
def accueil(): return redirect('/login')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        sel = request.form['role']
        ph  = hashlib.sha256(request.form['password'].encode()).hexdigest()
        user = q1("SELECT id,nom,actif,role_personnalise,role,permissions FROM users WHERE (role_personnalise=? OR role=?) AND password_hash=?",(sel,sel,ph))
        if not user:
            rb = 'admin' if sel=='Administrateur' else ('employe' if sel=='Employé' else None)
            if rb: user = q1("SELECT id,nom,actif,role_personnalise,role,permissions FROM users WHERE role=? AND password_hash=?",(rb,ph))
        if user:
            if user[2]==0: flash('❌ Compte désactivé.'); return redirect('/login')
            session.update({'user_id':user[0],'role':user[4],'user_nom':user[1],
                'role_affiche':user[3] or ('Administrateur' if user[4]=='admin' else 'Employé'),
                'permissions':user[5]})
            return redirect('/dashboard' if user[4]=='admin' else '/vente')
        flash('Identifiants incorrects')
    return render_template('login.html', roles=get_all_roles())

@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/changer_mdp', methods=['GET','POST'])
def changer_mdp():
    if 'user_id' not in session: return redirect('/login')
    if request.method=='POST':
        pwd = request.form.get('new_password','')
        if len(pwd)<4: flash('❌ Minimum 4 caractères'); return redirect('/changer_mdp')
        exe("UPDATE users SET password_hash=? WHERE id=?",(hashlib.sha256(pwd.encode()).hexdigest(),session['user_id']))
        flash('✅ Mot de passe changé !')
        return redirect('/dashboard' if session['role']=='admin' else '/vente')
    return render_template('changer_mdp.html')

# ──────────────────────────────────────────────────────────────
# PRODUITS — SUPPRESSION MULTIPLE INCLUSE
# ──────────────────────────────────────────────────────────────
@app.route('/admin/produits')
def produits_list():
    if session.get('role')!='admin': return redirect('/login')
    produits = qall("SELECT id,nom,prix,stock,stock_min FROM produits ORDER BY nom")
    return render_template('produits.html', produits=produits)

@app.route('/admin/produits/ajouter', methods=['POST'])
def ajouter_produit():
    if session.get('role')!='admin': return redirect('/login')
    nom = request.form['nom']; prix = int(float(request.form['prix']))
    stock = int(request.form.get('stock',0)); smin = int(request.form.get('stock_min',5))
    exe("INSERT INTO produits (nom,prix,stock,stock_min) VALUES (?,?,?,?)",(nom,prix,stock,smin))
    flash(f'✅ Produit "{nom}" ajouté ({prix} FCFA)')
    envoyer_notification_a_tous('produit','🆕 Nouveau produit',f'"{nom}" ajouté ({prix} FCFA)','/admin/produits')
    return redirect('/admin/produits')

@app.route('/admin/produits/modifier/<int:id>', methods=['POST'])
def modifier_produit(id):
    if session.get('role')!='admin': return redirect('/login')
    nom = request.form['nom']; prix = int(float(request.form['prix']))
    smin = int(request.form.get('stock_min',5))
    exe("UPDATE produits SET nom=?,prix=?,stock_min=? WHERE id=?",(nom,prix,smin,id))
    flash(f'✅ Produit "{nom}" modifié')
    return redirect('/admin/produits')

@app.route('/admin/produits/supprimer/<int:id>')
def supprimer_produit(id):
    if session.get('role')!='admin': return redirect('/login')
    p = q1("SELECT nom FROM produits WHERE id=?",(id,))
    if p: exe("DELETE FROM produits WHERE id=?",(id,)); flash(f'🗑️ "{p[0]}" supprimé')
    return redirect('/admin/produits')

@app.route('/admin/produits/supprimer_multiple', methods=['POST'])
def supprimer_produits_multiple():
    if session.get('role')!='admin': return redirect('/login')
    ids = request.form.getlist('produit_ids')
    if not ids:
        flash('⚠️ Aucun produit sélectionné'); return redirect('/admin/produits')
    noms_supprimes = []
    for pid in ids:
        try:
            pid_int = int(pid)
            p = q1("SELECT nom FROM produits WHERE id=?",(pid_int,))
            if p:
                exe("DELETE FROM produits WHERE id=?",(pid_int,))
                noms_supprimes.append(p[0])
        except (ValueError, Exception):
            continue
    if noms_supprimes:
        flash(f'🗑️ {len(noms_supprimes)} produit(s) supprimé(s) : {", ".join(noms_supprimes)}')
    return redirect('/admin/produits')

# ──────────────────────────────────────────────────────────────
# ENTRÉES DE STOCK
# ──────────────────────────────────────────────────────────────
@app.route('/admin/entrees')
def entrees_list():
    if not check_perm('entrees'): flash('❌ Permission refusée'); return redirect('/vente')
    entrees = qall('''SELECT e.id,p.nom,e.quantite,e.prix_unitaire,e.total,e.date_entree,e.fournisseur
        FROM entrees e JOIN produits p ON e.produit_id=p.id ORDER BY e.date_entree DESC LIMIT 50''')
    produits = qall("SELECT id,nom,stock FROM produits ORDER BY nom")
    return render_template('entrees.html', entrees=entrees, produits=produits)

@app.route('/admin/entrees/ajouter', methods=['POST'])
def ajouter_entree():
    if not check_perm('entrees'): flash('❌ Permission refusée'); return redirect('/vente')
    pid=int(request.form['produit_id']); qty=int(request.form['quantite'])
    pu=int(request.form['prix_unitaire']); f=request.form.get('fournisseur','')
    total=qty*pu; now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    exe("INSERT INTO entrees (produit_id,quantite,prix_unitaire,total,date_entree,fournisseur,employe_id) VALUES (?,?,?,?,?,?,?)",
        (pid,qty,pu,total,now,f,session['user_id']))
    exe("UPDATE produits SET stock=stock+? WHERE id=?",(qty,pid))
    flash(f'✅ Entrée : +{qty} unités'); verifier_alertes_stock()
    return redirect('/admin/entrees')

# ──────────────────────────────────────────────────────────────
# VENTES ADMIN
# ──────────────────────────────────────────────────────────────
@app.route('/admin/ventes', methods=['GET','POST'])
def admin_ventes():
    if session.get('role')!='admin': return redirect('/login')
    produits = qall("SELECT id,nom,prix,stock FROM produits WHERE stock>0 ORDER BY nom")
    if request.method=='POST':
        pid=int(request.form['produit_id']); qty=int(request.form['quantite'])
        client=request.form.get('client','')
        p=q1("SELECT nom,prix,stock FROM produits WHERE id=?",(pid,))
        if qty>p[2]: flash(f'Stock insuffisant ! {p[2]} unités restantes de {p[0]}')
        else:
            total=p[1]*qty; now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            exe("INSERT INTO sorties (produit_id,quantite,prix_unitaire,total,date_sortie,client,employe_id) VALUES (?,?,?,?,?,?,?)",
                (pid,qty,p[1],total,now,client,session['user_id']))
            exe("UPDATE produits SET stock=stock-? WHERE id=?",(qty,pid))
            flash(f'✅ Vente : {qty} {p[0]} → {total} FCFA'); verifier_alertes_stock()
    historique = qall('''SELECT s.id,p.nom,s.quantite,s.total,s.date_sortie,u.nom,s.client
        FROM sorties s JOIN produits p ON s.produit_id=p.id JOIN users u ON s.employe_id=u.id
        ORDER BY s.date_sortie DESC LIMIT 100''')
    stats_vendeurs = qall('''SELECT u.nom,u.role,COUNT(s.id),COALESCE(SUM(s.total),0)
        FROM sorties s JOIN users u ON s.employe_id=u.id
        WHERE DATE(s.date_sortie)=CURRENT_DATE GROUP BY u.id,u.nom,u.role ORDER BY 4 DESC''')
    return render_template('admin_ventes.html', produits=produits, historique=historique, stats_vendeurs=stats_vendeurs)

# ──────────────────────────────────────────────────────────────
# VENTES EMPLOYÉ
# ──────────────────────────────────────────────────────────────
@app.route('/vente', methods=['GET','POST'])
def vente():
    if session.get('role')!='employe': return redirect('/login')
    produits = qall("SELECT id,nom,prix,stock FROM produits WHERE stock>0 ORDER BY nom")
    if request.method=='POST':
        pid=int(request.form['produit_id']); qty=int(request.form['quantite'])
        client=request.form.get('client','')
        p=q1("SELECT nom,prix,stock FROM produits WHERE id=?",(pid,))
        if qty>p[2]: flash(f'Stock insuffisant ! {p[2]} unités restantes de {p[0]}')
        else:
            total=p[1]*qty; now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            exe("INSERT INTO sorties (produit_id,quantite,prix_unitaire,total,date_sortie,client,employe_id) VALUES (?,?,?,?,?,?,?)",
                (pid,qty,p[1],total,now,client,session['user_id']))
            exe("UPDATE produits SET stock=stock-? WHERE id=?",(qty,pid))
            flash(f'✅ Vente : {qty} {p[0]} → {total} FCFA')
    historique = qall('''SELECT s.id,p.nom,s.quantite,s.total,s.date_sortie,s.client,u.nom,u.role
        FROM sorties s JOIN produits p ON s.produit_id=p.id JOIN users u ON s.employe_id=u.id
        WHERE DATE(s.date_sortie)=CURRENT_DATE ORDER BY s.date_sortie DESC''')
    stats_vendeurs = qall('''SELECT u.role,COUNT(s.id),COALESCE(SUM(s.total),0)
        FROM sorties s JOIN users u ON s.employe_id=u.id
        WHERE DATE(s.date_sortie)=CURRENT_DATE GROUP BY u.role''')
    total_general = q1("SELECT COALESCE(SUM(total),0),COUNT(*) FROM sorties WHERE DATE(date_sortie)=CURRENT_DATE") or (0,0)
    return render_template('vente.html', produits=produits, historique=historique,
                           stats_vendeurs=stats_vendeurs, total_general=total_general)

# ──────────────────────────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if session.get('role')!='admin': return redirect('/login')
    archiver_si_necessaire(); verifier_alertes_stock()
    total_jour  = q1("SELECT COALESCE(SUM(total),0) FROM sorties WHERE DATE(date_sortie)=CURRENT_DATE")[0]
    nb_produits = q1("SELECT COUNT(*) FROM produits")[0]
    stock_total = q1("SELECT COALESCE(SUM(stock),0) FROM produits")[0]
    nb_stock_bas= q1("SELECT COUNT(*) FROM produits WHERE stock<=stock_min")[0]
    historique = qall('''SELECT s.id,p.nom,s.quantite,s.total,s.date_sortie,u.nom,s.client
        FROM sorties s JOIN produits p ON s.produit_id=p.id JOIN users u ON s.employe_id=u.id
        ORDER BY s.date_sortie DESC LIMIT 50''')
    stock_bas = qall("SELECT nom,stock,stock_min FROM produits WHERE stock<=stock_min")
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
    
    return render_template('dashboard.html',
        total_jour=total_jour, nb_produits=nb_produits,
        stock_total=stock_total, nb_stock_bas=nb_stock_bas,
        historique=historique, stock_bas=stock_bas,
        top_produits=top_produits, stats_vendeurs=stats_vendeurs,
        ventes_7_jours=ventes_7_jours, ventes_par_heure=ventes_par_heure)

# ──────────────────────────────────────────────────────────────
# PERTES
# ──────────────────────────────────────────────────────────────
@app.route('/admin/pertes')
def pertes_list():
    if not check_perm('pertes'): flash('❌ Permission refusée'); return redirect('/vente')
    pertes = qall('''SELECT p.id,pr.nom,p.quantite,p.prix_unitaire,p.total,p.motif,p.date_perte,u.nom
        FROM pertes p JOIN produits pr ON p.produit_id=pr.id JOIN users u ON p.employe_id=u.id
        ORDER BY p.date_perte DESC LIMIT 100''')
    produits = qall("SELECT id,nom,prix,stock FROM produits ORDER BY nom")
    s_auj = q1("SELECT COUNT(*),COALESCE(SUM(total),0),COALESCE(SUM(quantite),0) FROM pertes WHERE DATE(date_perte)=CURRENT_DATE")
    s_mois= q1("SELECT COUNT(*),COALESCE(SUM(total),0),COALESCE(SUM(quantite),0) FROM pertes WHERE date_perte::timestamp >= NOW() - INTERVAL '30 days'")
    return render_template('admin_pertes.html', pertes=pertes, produits=produits,
                           stats_aujourdhui=s_auj, stats_mois=s_mois)

@app.route('/admin/pertes/ajouter', methods=['POST'])
def ajouter_perte():
    if not check_perm('pertes'): flash('❌ Permission refusée'); return redirect('/vente')
    pid=int(request.form['produit_id']); qty=int(request.form['quantite'])
    motif=request.form.get('motif','')
    p=q1("SELECT nom,prix,stock FROM produits WHERE id=?",(pid,))
    if not p: flash('❌ Produit introuvable'); return redirect('/admin/pertes')
    if qty>p[2]: flash(f'❌ Stock insuffisant ! {p[2]} unités de {p[0]}'); return redirect('/admin/pertes')
    total=qty*p[1]; now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    exe("INSERT INTO pertes (produit_id,quantite,prix_unitaire,total,motif,date_perte,employe_id) VALUES (?,?,?,?,?,?,?)",
        (pid,qty,p[1],total,motif,now,session['user_id']))
    exe("UPDATE produits SET stock=GREATEST(0,stock-?) WHERE id=?",(qty,pid))
    flash(f'⚠️ Perte : {qty}×{p[0]} = {total} FCFA')
    envoyer_notification_a_tous('perte','⚠️ Perte signalée',
        f'{qty} unités de "{p[0]}" perdues ({total} FCFA)','/admin/pertes')
    return redirect('/admin/pertes')

@app.route('/admin/pertes/supprimer/<int:id>')
def supprimer_perte(id):
    if session.get('role')!='admin': return redirect('/login')
    p=q1("SELECT produit_id,quantite FROM pertes WHERE id=?",(id,))
    if p:
        exe("UPDATE produits SET stock=stock+? WHERE id=?",(p[1],p[0]))
        exe("DELETE FROM pertes WHERE id=?",(id,))
        flash('✅ Perte annulée, stock restauré')
    return redirect('/admin/pertes')

# ──────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ──────────────────────────────────────────────────────────────
@app.route('/api/notifications')
def api_notifications():
    if 'user_id' not in session: return jsonify({'error':'Non autorisé'}),401
    notifs = qall('''SELECT id,type,title,message,lien,date_creation
        FROM notifications WHERE user_id=? AND est_lu=0
        ORDER BY date_creation DESC LIMIT 20''',(session['user_id'],))
    total = q1("SELECT COUNT(*) FROM notifications WHERE user_id=? AND est_lu=0",(session['user_id'],))
    return jsonify({
        'notifications':[{'id':n[0],'type':n[1],'title':n[2],'message':n[3],'lien':n[4],'date':n[5]} for n in notifs],
        'total_non_lus': total[0] if total else 0
    })

@app.route('/notifications/marquer_lu/<int:id>', methods=['POST'])
def marquer_notification_lue(id):
    if 'user_id' not in session: return jsonify({'error':'Non autorisé'}),401
    exe("UPDATE notifications SET est_lu=1 WHERE id=? AND user_id=?",(id,session['user_id']))
    return jsonify({'success':True})

@app.route('/notifications/marquer_tout_lu', methods=['POST'])
def marquer_tout_lu():
    if 'user_id' not in session: return jsonify({'error':'Non autorisé'}),401
    exe("UPDATE notifications SET est_lu=1 WHERE user_id=?",(session['user_id'],))
    return jsonify({'success':True})

@app.route('/notifications')
def page_notifications():
    if 'user_id' not in session: return redirect('/login')
    notifs = qall('''SELECT id,type,title,message,lien,date_creation,est_lu
        FROM notifications WHERE user_id=? ORDER BY date_creation DESC LIMIT 100''',(session['user_id'],))
    total = q1("SELECT COUNT(*) FROM notifications WHERE user_id=? AND est_lu=0",(session['user_id'],))
    return render_template('notifications.html', notifications=notifs, total_non_lus=total[0] if total else 0)

@app.route('/api/stock_bas')
def api_stock_bas():
    rows = qall("SELECT nom,stock,stock_min FROM produits WHERE stock<=stock_min")
    return jsonify([{'nom':r[0],'stock':r[1],'stock_min':r[2]} for r in rows])

# ──────────────────────────────────────────────────────────────
# ALERTES PRODUITS
# ──────────────────────────────────────────────────────────────
@app.route('/admin/alertes/produits')
def admin_alertes_produits():
    if session.get('role')!='admin': return redirect('/login')
    produits = qall('''SELECT p.id,p.nom,p.stock,p.stock_min,
        COALESCE(a.seuil,p.stock_min,5),COALESCE(a.actif,1)
        FROM produits p LEFT JOIN alertes_produits a ON p.id=a.produit_id ORDER BY p.nom''')
    return render_template('admin_alertes_produits.html', produits=produits)

@app.route('/admin/alertes/produits/modifier/<int:id>', methods=['POST'])
def modifier_alerte_produit(id):
    if session.get('role')!='admin': return redirect('/login')
    seuil=int(request.form['seuil']); actif=1 if request.form.get('actif') else 0
    existe = q1("SELECT id FROM alertes_produits WHERE produit_id=?",(id,))
    if existe: exe("UPDATE alertes_produits SET seuil=?,actif=? WHERE produit_id=?",(seuil,actif,id))
    else:      exe("INSERT INTO alertes_produits (produit_id,seuil,actif) VALUES (?,?,?)",(id,seuil,actif))
    flash('✅ Seuil d\'alerte mis à jour')
    return redirect('/admin/alertes/produits')

# ──────────────────────────────────────────────────────────────
# ACTEURS
# ──────────────────────────────────────────────────────────────
@app.route('/admin/acteurs')
def admin_acteurs():
    if session.get('role')!='admin': return redirect('/login')
    acteurs = qall('''SELECT id,nom,role,role_personnalise,password_hash,
        COALESCE(actif,1),COALESCE(motif_absence,''),COALESCE(permissions,'vente'),COALESCE(email,'')
        FROM users ORDER BY role DESC,actif DESC,id''')
    return render_template('admin_acteurs.html', acteurs=acteurs)

@app.route('/admin/acteurs/ajouter', methods=['POST'])
def ajouter_acteur():
    if session.get('role')!='admin': return redirect('/login')
    nom=request.form['nom']; rb=request.form['role_base']
    rp=request.form.get('role_personnalise',''); mdp=request.form['mot_de_passe']
    email=request.form.get('email',''); ph=hashlib.sha256(mdp.encode()).hexdigest()
    perms='admin' if rb=='admin' else 'vente'
    exe("INSERT INTO users (role,role_personnalise,password_hash,nom,actif,permissions,email) VALUES (?,?,?,?,1,?,?)",
        (rb,rp,ph,nom,perms,email))
    flash(f'✅ Acteur "{nom}" créé')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/modifier/<int:id>', methods=['POST'])
def modifier_acteur(id):
    if session.get('role')!='admin': return redirect('/login')
    nom=request.form['nom']; rp=request.form.get('role_personnalise','')
    email=request.form.get('email','')
    perms=','.join(request.form.getlist('permissions')) or 'vente'
    actif=int(request.form.get('actif',1)); motif=request.form.get('motif_absence','')
    exe("UPDATE users SET nom=?,role_personnalise=?,email=?,permissions=?,actif=?,motif_absence=? WHERE id=?",
        (nom,rp,email,perms,actif,motif,id))
    if request.form.get('new_password'):
        exe("UPDATE users SET password_hash=? WHERE id=?",
            (hashlib.sha256(request.form['new_password'].encode()).hexdigest(),id))
    flash(f'✅ Acteur "{nom}" modifié')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/supprimer/<int:id>')
def supprimer_acteur(id):
    if session.get('role')!='admin': return redirect('/login')
    if id==session['user_id']: flash('❌ Impossible de supprimer votre propre compte'); return redirect('/admin/acteurs')
    u=q1("SELECT nom FROM users WHERE id=?",(id,))
    if u: exe("DELETE FROM users WHERE id=?",(id,)); flash(f'🗑️ "{u[0]}" supprimé')
    return redirect('/admin/acteurs')

@app.route('/admin/acteurs/verifier_mdp', methods=['POST'])
def verifier_mdp_admin():
    if session.get('role')!='admin': return jsonify({'success':False,'message':'Non autorisé'})
    data=request.get_json(); mdp=data.get('mot_de_passe','')
    r=q1("SELECT password_hash FROM users WHERE id=? AND role='admin'",(session['user_id'],))
    if r and r[0]==hashlib.sha256(mdp.encode()).hexdigest():
        session['mdp_verifie']=True; return jsonify({'success':True})
    return jsonify({'success':False,'message':'Mot de passe incorrect'})

# ──────────────────────────────────────────────────────────────
# FOURNISSEURS
# ──────────────────────────────────────────────────────────────
@app.route('/admin/fournisseurs')
def admin_fournisseurs():
    if session.get('role')!='admin': return redirect('/login')
    return render_template('admin_fournisseurs.html', fournisseurs=qall("SELECT * FROM fournisseurs ORDER BY nom"))

@app.route('/admin/fournisseurs/ajouter', methods=['POST'])
def ajouter_fournisseur():
    if session.get('role')!='admin': return redirect('/login')
    try:
        exe("INSERT INTO fournisseurs (nom,produits,telephone,email,adresse) VALUES (?,?,?,?,?)",
            (request.form['nom'],request.form.get('produits',''),
             request.form.get('telephone',''),request.form.get('email',''),request.form.get('adresse','')))
        flash('✅ Fournisseur ajouté')
    except Exception: flash('❌ Ce fournisseur existe déjà')
    return redirect('/admin/fournisseurs')

@app.route('/admin/fournisseurs/modifier/<int:id>', methods=['POST'])
def modifier_fournisseur(id):
    if session.get('role')!='admin': return redirect('/login')
    nom=request.form['nom']
    exe("UPDATE fournisseurs SET nom=?,produits=?,telephone=?,email=?,adresse=? WHERE id=?",
        (nom,request.form.get('produits',''),request.form.get('telephone',''),
         request.form.get('email',''),request.form.get('adresse',''),id))
    flash(f'✅ "{nom}" modifié')
    return redirect('/admin/fournisseurs')

@app.route('/admin/fournisseurs/supprimer/<int:id>')
def supprimer_fournisseur(id):
    if session.get('role')!='admin': return redirect('/login')
    f=q1("SELECT nom FROM fournisseurs WHERE id=?",(id,))
    if f: exe("DELETE FROM fournisseurs WHERE id=?",(id,)); flash(f'🗑️ "{f[0]}" supprimé')
    return redirect('/admin/fournisseurs')

# ──────────────────────────────────────────────────────────────
# STATISTIQUES
# ──────────────────────────────────────────────────────────────
@app.route('/admin/stats')
def admin_stats():
    if session.get('role')!='admin': return redirect('/login')
    
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
                         COALESCE((SELECT SUM(total) FROM entrees),0)''') or (0,0)
    marge_produits = qall('''SELECT p.nom,COALESCE(SUM(s.total),0),COALESCE(SUM(e.total),0),
        COALESCE(SUM(s.total),0)-COALESCE(SUM(e.total),0)
        FROM produits p LEFT JOIN sorties s ON p.id=s.produit_id
        LEFT JOIN entrees e ON p.id=e.produit_id GROUP BY p.id,p.nom
        HAVING COALESCE(SUM(s.total),0)+COALESCE(SUM(e.total),0)>0
        ORDER BY 4 DESC LIMIT 10''')
    return render_template('admin_stats.html', ventes_jour=ventes_jour, ventes_mois=ventes_mois,
        top_produits=top_produits, marge_totale=marge, marge_produits=marge_produits)

# ──────────────────────────────────────────────────────────────
# ARCHIVES - VERSION CORRIGÉE
# ──────────────────────────────────────────────────────────────
@app.route('/admin/archives')
def admin_archives():
    if session.get('role')!='admin': return redirect('/login')
    type_arch=request.args.get('type','ventes')
    date_debut=request.args.get('date_debut','')
    date_fin=request.args.get('date_fin','')
    produit_filtre=request.args.get('produit','')
    tri=request.args.get('tri','date_desc')
    order = 'DESC' if 'desc' in tri else 'ASC'

    try:
        if type_arch=='entrees':
            rows = qall(f'''SELECT id,produit_nom,quantite,prix_unitaire,total,date_entree,fournisseur,employe_nom,archive_date
                FROM archive_entrees WHERE 1=1
                {"AND date_entree>='"+date_debut+"'" if date_debut else ""}
                {"AND date_entree<='"+date_fin+" 23:59:59'" if date_fin else ""}
                {"AND LOWER(produit_nom) LIKE LOWER('%"+produit_filtre+"%')" if produit_filtre else ""}
                ORDER BY date_entree {order} LIMIT 200''')
        elif type_arch=='pertes':
            rows = qall(f'''SELECT id,produit_nom,quantite,prix_unitaire,total,motif,date_perte,employe_nom,archive_date
                FROM archive_pertes WHERE 1=1
                {"AND date_perte>='"+date_debut+"'" if date_debut else ""}
                {"AND date_perte<='"+date_fin+" 23:59:59'" if date_fin else ""}
                {"AND LOWER(produit_nom) LIKE LOWER('%"+produit_filtre+"%')" if produit_filtre else ""}
                ORDER BY date_perte {order} LIMIT 200''')
        else:
            rows = qall(f'''SELECT id,produit_nom,quantite,prix_unitaire,total,date_vente,client,employe_nom,archive_date
                FROM archive_ventes WHERE 1=1
                {"AND date_vente>='"+date_debut+"'" if date_debut else ""}
                {"AND date_vente<='"+date_fin+" 23:59:59'" if date_fin else ""}
                {"AND LOWER(produit_nom) LIKE LOWER('%"+produit_filtre+"%')" if produit_filtre else ""}
                ORDER BY date_vente {order} LIMIT 200''')

        nb_ventes_arch = q1("SELECT COUNT(*),COALESCE(SUM(total),0) FROM archive_ventes") or (0,0)
        nb_entrees_arch = q1("SELECT COUNT(*),COALESCE(SUM(total),0) FROM archive_entrees") or (0,0)
        nb_pertes_arch = q1("SELECT COUNT(*),COALESCE(SUM(total),0) FROM archive_pertes") or (0,0)
        
        total_ca_archive = nb_ventes_arch[1] if nb_ventes_arch else 0
        total_achats_archive = nb_entrees_arch[1] if nb_entrees_arch else 0
        total_pertes_ca = nb_pertes_arch[1] if nb_pertes_arch else 0
        
        return render_template('admin_archives.html', 
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
        print(f"Erreur archives: {e}")
        flash(f'❌ Erreur lors du chargement des archives: {str(e)}')
        return render_template('admin_archives.html', 
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

# ──────────────────────────────────────────────────────────────
# MOT DE PASSE OUBLIÉ
# ──────────────────────────────────────────────────────────────
@app.route('/mot_de_passe_oublie', methods=['GET','POST'])
def mot_de_passe_oublie():
    if request.method=='POST':
        email=request.form.get('email','').strip()
        if email!='hitnasuperette@gmail.com':
            flash('❌ Seul l\'administrateur peut réinitialiser son mot de passe.','error')
            return redirect('/mot_de_passe_oublie')
        user=q1("SELECT id FROM users WHERE role='admin' AND email=? AND actif=1",(email,))
        if user:
            token=generate_reset_token(user[0])
            flash(f'🔗 Lien : {url_for("reset_password",token=token,_external=True)}','info')
        else: flash('❌ Aucun admin actif avec cet email','error')
        return redirect('/login')
    return render_template('mot_de_passe_oublie.html')

@app.route('/reset_password/<token>', methods=['GET','POST'])
def reset_password(token):
    td=q1('''SELECT rt.user_id,rt.expires_at,rt.used,u.actif,u.nom
        FROM reset_tokens rt JOIN users u ON rt.user_id=u.id WHERE rt.token=? AND rt.used=0''',(token,))
    if not td: flash('❌ Lien invalide'); return redirect('/login')
    user_id,expires,used,actif,nom=td
    if actif==0: flash('❌ Compte désactivé'); return redirect('/login')
    if datetime.now()>datetime.strptime(expires,'%Y-%m-%d %H:%M:%S'):
        flash('❌ Lien expiré'); return redirect('/mot_de_passe_oublie')
    if request.method=='POST':
        pwd=request.form['new_password']; cpwd=request.form['confirm_password']
        if pwd!=cpwd: flash('❌ Mots de passe différents'); return redirect(f'/reset_password/{token}')
        if len(pwd)<4: flash('❌ Minimum 4 caractères'); return redirect(f'/reset_password/{token}')
        exe("UPDATE users SET password_hash=? WHERE id=?",(hashlib.sha256(pwd.encode()).hexdigest(),user_id))
        exe("UPDATE reset_tokens SET used=1 WHERE token=?",(token,))
        flash('✅ Mot de passe réinitialisé !'); return redirect('/login')
    return render_template('reset_password.html', token=token)

# ──────────────────────────────────────────────────────────────
# EXPORT PDF
# ──────────────────────────────────────────────────────────────
@app.route('/export/pdf')
def export_pdf():
    if session.get('role')!='admin': return redirect('/login')
    buffer=io.BytesIO(); p=canvas.Canvas(buffer,pagesize=A4); w,h=A4
    p.setFont("Helvetica-Bold",16); p.drawString(50,h-50,"HITNA - Rapport")
    p.setFont("Helvetica",10); p.drawString(50,h-70,f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    y=h-100
    data=qall("SELECT DATE(date_sortie::timestamp),COALESCE(SUM(total),0),COUNT(*) FROM sorties GROUP BY DATE(date_sortie::timestamp) ORDER BY 1 DESC LIMIT 30")
    p.setFont("Helvetica-Bold",10)
    p.drawString(50,y,"Date"); p.drawString(150,y,"Montant (FCFA)"); p.drawString(280,y,"Ventes"); y-=20
    for row in data:
        p.setFont("Helvetica",10)
        p.drawString(50,y,str(row[0])); p.drawString(150,y,f"{row[1]:,}"); p.drawString(280,y,str(row[2])); y-=20
        if y<50: p.showPage(); y=h-50
    p.save(); buffer.seek(0)
    return send_file(buffer,as_attachment=True,
        download_name=f"rapport_{datetime.now().strftime('%Y%m%d')}.pdf",mimetype='application/pdf')

# ──────────────────────────────────────────────────────────────
# API JSON
# ──────────────────────────────────────────────────────────────
@app.route('/api/produits')
def api_produits():
    if 'user_id' not in session: return jsonify({'error':'Non autorisé'}),401
    produits=qall("SELECT id,nom,prix,stock FROM produits ORDER BY nom")
    return jsonify({'produits':[{'id':p[0],'nom':p[1],'prix':p[2],'stock':p[3]} for p in produits],
                    'timestamp':datetime.now().isoformat()})

@app.route('/offline')
def offline():
    return render_template('offline.html')

@app.route('/sw.js')
def service_worker():
    from flask import make_response
    resp = make_response(app.send_static_file('sw.js'))
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')

# ──────────────────────────────────────────────────────────────
# LANCEMENT
# ──────────────────────────────────────────────────────────────
print("🔧 Initialisation de la base de données...")
init_db()
print("✅ Base de données initialisée")

if __name__=='__main__':
    init_db()
    port=int(os.environ.get('PORT',5000))
    app.run(debug=False, host='0.0.0.0', port=port)