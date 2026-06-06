from datetime import datetime
from app.extensions import db


class UserPresence(db.Model):
    __tablename__ = "user_presence"

    user_id   = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    last_seen = db.Column(db.DateTime, nullable=False, default=datetime.now)

    user = db.relationship("User", backref=db.backref("presence", uselist=False))
