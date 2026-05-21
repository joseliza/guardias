from app.extensions import db


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)          # e.g. "1ºA ESO"
    level = db.Column(db.String(30), nullable=False)         # e.g. "1 ESO", "2 Bach"
    # Si es true, las guardias en este grupo dan puntos extra
    high_difficulty = db.Column(db.Boolean, default=False, nullable=False)
    difficulty_multiplier = db.Column(db.Float, default=1.0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    schedule_entries = db.relationship("TeacherSchedule", backref="group", lazy="dynamic")

    def __repr__(self):
        return f"<Group {self.name}>"
