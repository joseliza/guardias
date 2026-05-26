"""Ejecutar una sola vez para crear las tablas e insertar datos iniciales."""
from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.group import Group
from app.models.room import Room

app = create_app()

GROUPS = (
    # 1º ESO
    ["1º ESO A", "1º ESO B", "1º ESO C", "1º ESO D", "1º ESO E", "1º ESO F"],
    # 2º ESO
    ["2º ESO A", "2º ESO B", "2º ESO C", "2º ESO D", "2º ESO E", "2º ESO F"],
    # 3º ESO
    ["3º ESO A", "3º ESO B", "3º ESO C", "3º ESO D", "3º ESO E", "3º ESO F"],
    # 4º ESO
    ["4º ESO A", "4º ESO B", "4º ESO C", "4º ESO D", "4º ESO E", "4º ESO F"],
    # 1º Bachillerato
    ["1º Bach. Ciencias y Tecnología A", "1º Bach. Ciencias y Tecnología B"],
    ["1º Bach. Humanidades y CC. Sociales A", "1º Bach. Humanidades y CC. Sociales B"],
    # 2º Bachillerato
    ["2º Bach. Ciencias y Tecnología A", "2º Bach. Ciencias y Tecnología B"],
    ["2º Bach. Humanidades y CC. Sociales A", "2º Bach. Humanidades y CC. Sociales B"],
    # FP
    ["1º CFGB Informática y Comunicaciones"],
    ["2º CFGB Informática y Comunicaciones"],
    ["1º CFGM Sistemas Microinformáticos y Redes"],
    ["2º CFGM Sistemas Microinformáticos y Redes"],
    ["1º CFGS Administración de Sistemas Informáticos en Red"],
    ["2º CFGS Administración de Sistemas Informáticos en Red"],
)

with app.app_context():
    db.create_all()

    # Usuario admin
    admin_email = app.config["ADMIN_EMAIL"]
    if not User.query.filter_by(email=admin_email).first():
        admin = User(
            email=admin_email,
            name="Admin",
            surname="Sistema",
            role="management",
        )
        admin.set_password("admin1234")
        db.session.add(admin)
        print(f"Usuario admin creado: {admin_email} / admin1234")
    else:
        print("El usuario admin ya existe.")

    # Aulas 1-40
    rooms_created = 0
    for n in range(1, 41):
        room_name = f"Aula {n}"
        if not Room.query.filter_by(name=room_name).first():
            db.session.add(Room(name=room_name))
            rooms_created += 1
    db.session.commit()
    print(f"Aulas creadas: {rooms_created}")

    # Grupos
    created = 0
    for group_list in GROUPS:
        for name in group_list:
            if not Group.query.filter_by(name=name).first():
                db.session.add(Group(name=name))
                created += 1

    db.session.commit()
    print(f"Grupos creados: {created}")
