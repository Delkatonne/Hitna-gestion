-- ============================================================
-- STRUCTURE DES TABLES
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY, role TEXT, role_personnalise TEXT,
    password_hash TEXT, nom TEXT, actif INTEGER DEFAULT 1,
    motif_absence TEXT DEFAULT '', permissions TEXT DEFAULT 'vente',
    email TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS produits (
    id SERIAL PRIMARY KEY, nom TEXT, prix INTEGER,
    stock INTEGER DEFAULT 0, stock_min INTEGER DEFAULT 5,
    unite_id INTEGER
);

CREATE TABLE IF NOT EXISTS sorties (
    id SERIAL PRIMARY KEY, produit_id INTEGER, quantite INTEGER,
    prix_unitaire INTEGER, total INTEGER, date_sortie TEXT,
    client TEXT, employe_id INTEGER
);

CREATE TABLE IF NOT EXISTS entrees (
    id SERIAL PRIMARY KEY, produit_id INTEGER, quantite INTEGER,
    prix_unitaire INTEGER, total INTEGER, date_entree TEXT,
    fournisseur TEXT, employe_id INTEGER
);

CREATE TABLE IF NOT EXISTS pertes (
    id SERIAL PRIMARY KEY, produit_id INTEGER, quantite INTEGER,
    prix_unitaire INTEGER, total INTEGER, motif TEXT,
    date_perte TEXT, employe_id INTEGER
);

CREATE TABLE IF NOT EXISTS fournisseurs (
    id SERIAL PRIMARY KEY, nom TEXT UNIQUE, produits TEXT,
    telephone TEXT, email TEXT, adresse TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY, user_id INTEGER, type TEXT,
    title TEXT, message TEXT, lien TEXT,
    est_lu INTEGER DEFAULT 0, date_creation TEXT
);

CREATE TABLE IF NOT EXISTS alertes_produits (
    id SERIAL PRIMARY KEY, produit_id INTEGER,
    seuil INTEGER DEFAULT 5, actif INTEGER DEFAULT 1, dernier_envoi TEXT
);

CREATE TABLE IF NOT EXISTS reset_tokens (
    id SERIAL PRIMARY KEY, user_id INTEGER, token TEXT,
    expires_at TEXT, used INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS archive_ventes (
    id INTEGER, produit_id INTEGER, quantite INTEGER,
    prix_unitaire INTEGER, total INTEGER, date_vente TEXT,
    employe_id INTEGER, client TEXT, archive_date TEXT,
    semaine INTEGER, annee INTEGER, produit_nom TEXT, employe_nom TEXT
);

CREATE TABLE IF NOT EXISTS archive_entrees (
    id INTEGER, produit_id INTEGER, quantite INTEGER,
    prix_unitaire INTEGER, total INTEGER, date_entree TEXT,
    fournisseur TEXT, employe_id INTEGER, archive_date TEXT,
    semaine INTEGER, annee INTEGER, produit_nom TEXT, employe_nom TEXT
);

CREATE TABLE IF NOT EXISTS archive_pertes (
    id INTEGER, produit_id INTEGER, quantite INTEGER,
    prix_unitaire INTEGER, total INTEGER, motif TEXT,
    date_perte TEXT, employe_id INTEGER, archive_date TEXT,
    semaine INTEGER, annee INTEGER, produit_nom TEXT, employe_nom TEXT
);

CREATE TABLE IF NOT EXISTS archive_recap (
    id SERIAL PRIMARY KEY, semaine INTEGER, annee INTEGER,
    date_debut TEXT, date_fin TEXT, nb_ventes INTEGER,
    total_ventes INTEGER, nb_entrees INTEGER, total_achats INTEGER,
    archive_date TEXT
);

CREATE TABLE IF NOT EXISTS unites_mesure (
    id SERIAL PRIMARY KEY, nom TEXT UNIQUE, symbole TEXT,
    description TEXT, actif INTEGER DEFAULT 1
);

-- ============================================================
-- UTILISATEURS PAR DÉFAUT
-- ============================================================

-- Admin : admin123
INSERT INTO users (role, role_personnalise, password_hash, nom, actif, permissions, email)
VALUES ('admin', 'Administrateur', '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9', 'Administrateur', 1, 'admin', 'hitnasuperette@gmail.com')
ON CONFLICT (id) DO NOTHING;

-- Employé : emp123
INSERT INTO users (role, role_personnalise, password_hash, nom, actif, permissions)
VALUES ('employe', 'Employé', 'e03d3ec8d5035f8721f5dc64546e59ed790dbcb3b7b598fe57057ccd7b683b00', 'Denise', 1, 'vente')
ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- UNITÉS DE MESURE PAR DÉFAUT
-- ============================================================

INSERT INTO unites_mesure (nom, symbole, description, actif) VALUES
('Litre', 'L', 'Litre (1L)', 1),
('Demi-litre', '1/2 L', 'Demi-litre (0.5L)', 1),
('Quart de litre', '1/4 L', 'Quart de litre (0.25L)', 1),
('Kilogramme', 'kg', 'Kilogramme (1kg)', 1),
('Demi-kilogramme', '1/2 kg', 'Demi-kilogramme (500g)', 1),
('Gramme', 'g', 'Gramme', 1),
('Millilitre', 'ml', 'Millilitre', 1),
('Pièce', 'pc', 'À l\'unité', 1)
ON CONFLICT (nom) DO NOTHING;