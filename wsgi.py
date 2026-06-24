from app import app, init_db

# ⚠️ Initialiser la base de données au démarrage du serveur
print("🔧 Initialisation de la base de données...")
try:
    init_db()
    print("✅ Base de données initialisée avec succès")
except Exception as e:
    print(f"⚠️ Erreur: {e}")

if __name__ == "__main__":
    app.run()