"""
Modelos ChatMessage y ChatClear.
ChatMessage: mensaje enviado al canal de incidencias (general o por tramo) vía Socket.IO.
ChatClear: registro de cada limpieza manual del chat; los mensajes se conservan en BD
pero se ocultan en la vista viva a partir de la marca de tiempo del último borrado.
"""
from datetime import datetime
from app.extensions import db


class ChatMessage(db.Model):
    """Mensaje del canal de incidencias en tiempo real."""
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Canal: "general" o "YYYY-MM-DD_slot_N" para un tramo concreto
    channel = db.Column(db.String(50), nullable=False, default="general")
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship("User")


class ChatClear(db.Model):
    """Registro de limpiezas del chat (mensajes ocultos en vista viva, conservados en BD)."""
    __tablename__ = "chat_clears"

    id = db.Column(db.Integer, primary_key=True)
    cleared_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    cleared_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    cleared_by = db.relationship("User")
