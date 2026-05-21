from datetime import date
from flask import Blueprint, render_template, current_app
from flask_login import login_required, current_user
from app.models.guard import Guard
from app.models.absence import Absence
from app.models.schedule import TeacherSchedule
from app.utils.guards import get_available_teachers_for_slot

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    today = date.today()
    day_idx = today.weekday()  # 0=Lunes … 4=Viernes
    slots = current_app.config["TIME_SLOTS"]

    # Guardias pendientes de hoy
    pending_guards = Guard.query.filter_by(date=today, status="pending").all()

    # Ausencias de hoy
    today_absences = Absence.query.filter_by(date=today).all()

    # Tramos de guardia del profesor actual
    my_guard_slots = []
    if not current_user.is_management:
        my_guard_slots = TeacherSchedule.query.filter_by(
            teacher_id=current_user.id,
            day_of_week=day_idx,
            is_guard_slot=True,
        ).all()

    return render_template(
        "dashboard/index.html",
        today=today,
        slots=slots,
        pending_guards=pending_guards,
        today_absences=today_absences,
        my_guard_slots=my_guard_slots,
    )
