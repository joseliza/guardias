"""Ejecutar una sola vez para crear las tablas e insertar el usuario admin inicial."""
from app import create_app
from app.extensions import db
from app.models.user import User

app = create_app()

with app.app_context():
    db.create_all()
    if not User.query.filter_by(email="admin@instituto.es").first():
        admin = User(
            email="admin@instituto.es",
            name="Admin",
            surname="Sistema",
            role="management",
        )
        admin.set_password("admin1234")
        db.session.add(admin)
        db.session.commit()
        print("Usuario admin creado: admin@instituto.es / admin1234")
    else:
        print("La base de datos ya estaba inicializada.")
