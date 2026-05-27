"""
Instancias de las extensiones Flask compartidas por toda la aplicación
(SQLAlchemy, LoginManager, Migrate, Mail, SocketIO). Se inicializan sin app
para evitar imports circulares; la factoría en __init__.py las enlaza.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_mail import Mail
from flask_socketio import SocketIO

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
mail = Mail()
socketio = SocketIO()
