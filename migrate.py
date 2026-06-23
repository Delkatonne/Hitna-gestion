"""
migrate.py — Migration des données SQLite vers PostgreSQL pour HITNA
====================================================================
Exécuter UNE SEULE FOIS depuis le Shell Render après le premier déploiement :

    python migrate.py

Le script insère directement les données de hitna.db dans PostgreSQL.
DATABASE_URL est lue automatiquement depuis les variables d'environnement Render.
"""
import os, sys, hashlib
import psycopg2

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if not DATABASE_URL:
    print("❌ DATABASE_URL manquante.")
    print("   Lance ce script depuis le Shell Render où DATABASE_URL est définie.")
    sys.exit(1)

if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

print("🔌 Connexion à PostgreSQL...")
pg = psycopg2.connect(DATABASE_URL)
c  = pg.cursor()

print("📋 Insertion des données HITNA...\n")

# ── UTILISATEURS ──────────────────────────────────────────────
users = [
    (1, 'admin', 'Administrateur',
     '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9',
     'Administrateur', 1, '', 'admin', 'hitnasuperette@gmail.com'),
    (2, 'employe', 'Employé',
     'e03d3ec8d5035f8721f5dc64546e59ed790dbcb3b7b598fe57057ccd7b683b00',
     'Denise', 1, '', 'vente', ''),
]
for u in users:
    c.execute('''INSERT INTO users (id,role,role_personnalise,password_hash,nom,actif,motif_absence,permissions,email)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET
                 nom=EXCLUDED.nom, role_personnalise=EXCLUDED.role_personnalise,
                 permissions=EXCLUDED.permissions, email=EXCLUDED.email''', u)
print(f"  👥 users : {len(users)} utilisateurs migrés")
print(f"     - Administrateur (admin)")
print(f"     - Denise (employée)")

# ── PRODUITS ──────────────────────────────────────────────────
# ⚠️ Remplace ces produits par les vrais produits HITNA
# si tu en as d'autres à ajouter, rajoute-les ici
produits = [
    (1, 'Coca-Cola 33cl', 500,  20, 5),
    (2, 'Fanta 33cl',     500,  15, 5),
    (3, 'Eau 1.5L',       300,  30, 5),
    (4, 'Pringles',       1200, 10, 3),
    (5, 'Chocolat',       600,  25, 5),
    (6, 'Bonbon',         100,  50, 10),
    (7, 'Jus Orange',     400,  12, 5),
]
for p in produits:
    c.execute('''INSERT INTO produits (id,nom,prix,stock,stock_min)
                 VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET
                 nom=EXCLUDED.nom, prix=EXCLUDED.prix,
                 stock=EXCLUDED.stock, stock_min=EXCLUDED.stock_min''', p)
print(f"\n  📦 produits : {len(produits)} produits migrés")
for p in produits:
    print(f"     - {p[1]} ({p[2]} FCFA, stock: {p[3]})")

# ── Resync des séquences SERIAL ───────────────────────────────
# Indispensable pour que les nouveaux INSERT prennent les bons IDs
for table, col in [('users','id'), ('produits','id'), ('sorties','id'),
                   ('entrees','id'), ('pertes','id'), ('notifications','id'),
                   ('alertes_produits','id'), ('reset_tokens','id'), ('archive_recap','id')]:
    try:
        c.execute(f"SELECT setval(pg_get_serial_sequence('{table}','{col}'), COALESCE(MAX({col}),1), true) FROM {table}")
    except Exception as e:
        print(f"  ⚠️  Séquence {table}.{col} : {e}")

pg.commit()
c.close()
pg.close()

print("\n✅ Migration terminée avec succès !")
print("\n📌 Prochaines étapes :")
print("   1. Allez sur /admin/produits pour vérifier vos produits")
print("   2. Supprimez les produits de démo et ajoutez les vrais produits HITNA")
print("   3. Denise peut se connecter avec son mot de passe habituel")