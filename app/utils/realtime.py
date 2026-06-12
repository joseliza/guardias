"""
Notifica en tiempo real a las pantallas de guardia (rol `display`) cuando se
modifican datos relevantes para esa vista, sin tener que emitir el evento a
mano desde cada ruta. Se enlaza a los eventos before_commit/after_commit de
la sesión de SQLAlchemy: si la transacción que se confirma incluye objetos
nuevos, modificados o eliminados de algún modelo relevante para /pantalla,
emite `guard_updated` a la sala "display" para que recarguen la página.
"""
from sqlalchemy import event
from app.extensions import db, socketio

_registered = False


def register_display_notifications():
    global _registered
    if _registered:
        return
    _registered = True

    from app.models.guard import Guard, GuardRecord
    from app.models.absence import Absence
    from app.models.task import Task
    from app.models.group import Group
    from app.models.room import Room
    from app.models.activity import ExtraActivity, ExtraActivityGroup, ExtraActivityTeacher
    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup, AvailabilityPeriodSlot
    from app.models.schedule import TeacherSchedule
    from app.models.user import User

    relevant_models = (
        Guard, GuardRecord, Absence, Task, Group, Room,
        ExtraActivity, ExtraActivityGroup, ExtraActivityTeacher,
        AvailabilityPeriod, AvailabilityPeriodGroup, AvailabilityPeriodSlot,
        TeacherSchedule, User,
    )

    @event.listens_for(db.session, "before_commit")
    def _mark_display_dirty(session):
        changed = set(session.new) | set(session.dirty) | set(session.deleted)
        if any(isinstance(obj, relevant_models) for obj in changed):
            session.info["display_dirty"] = True

    @event.listens_for(db.session, "after_commit")
    def _notify_display(session):
        if session.info.pop("display_dirty", False):
            socketio.emit("guard_updated", {}, room="display")
