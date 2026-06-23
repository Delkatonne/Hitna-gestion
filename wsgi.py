from app import app, init_db

# init_db() est appelé ici pour que gunicorn initialise les tables
# au démarrage, avant de servir la première requête.
init_db()

if __name__ == "__main__":
    app.run()