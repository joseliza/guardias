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
