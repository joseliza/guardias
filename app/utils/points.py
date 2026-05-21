from app.extensions import db
from app.models.user import User

EXCLUDED_ROLES = ("management", "display")


def award_guard_points(teacher_id: int, points: float):
    teacher = User.query.get(teacher_id)
    if teacher and teacher.role not in EXCLUDED_ROLES:
        teacher.points = round(teacher.points + points, 2)
        db.session.add(teacher)


def apply_absence_penalty(teacher_id: int, penalty: float = -1.0):
    teacher = User.query.get(teacher_id)
    if teacher and teacher.role not in EXCLUDED_ROLES:
        teacher.points = round(teacher.points + penalty, 2)
        db.session.add(teacher)
