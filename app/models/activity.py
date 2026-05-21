from datetime import datetime
from app.extensions import db


class ExtraActivity(db.Model):
    """Actividad extraescolar registrada."""
    __tablename__ = "extra_activities"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    date = db.Column(db.Date, nullable=False)
    slot_ids = db.Column(db.String(50), nullable=False)   # "1,2,3" — tramos afectados
    description = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User")
    groups = db.relationship("ExtraActivityGroup", backref="activity", lazy="dynamic")
    accompanying_teachers = db.relationship("ExtraActivityTeacher", backref="activity", lazy="dynamic")

    @property
    def slot_id_list(self):
        return [int(s) for s in self.slot_ids.split(",") if s]


class ExtraActivityGroup(db.Model):
    """Grupo (o parte de él) que participa en la actividad extraescolar."""
    __tablename__ = "extra_activity_groups"

    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("extra_activities.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)
    # Si es true, el grupo completo sale → sus profesores quedan libres como apoyo
    whole_group = db.Column(db.Boolean, default=True, nullable=False)

    group = db.relationship("Group")


class ExtraActivityTeacher(db.Model):
    """Profesor acompañante en la actividad extraescolar."""
    __tablename__ = "extra_activity_teachers"

    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("extra_activities.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # true cuando se ha enviado el email pidiendo tareas
    email_sent = db.Column(db.Boolean, default=False, nullable=False)

    teacher = db.relationship("User")
