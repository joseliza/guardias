from app.extensions import db

from .school_year import SchoolYear
from .user import User
from .group import Group
from .schedule import TeacherSchedule
from .absence import Absence
from .task import Task
from .guard import Guard, GuardRecord
from .activity import ExtraActivity, ExtraActivityGroup, ExtraActivityTeacher
from .availability import AvailabilityPeriod, AvailabilityPeriodGroup
from .chat import ChatMessage
from .subject import Subject

__all__ = [
    "db",
    "SchoolYear",
    "User",
    "Group",
    "TeacherSchedule",
    "Absence",
    "Task",
    "Guard",
    "GuardRecord",
    "ExtraActivity",
    "ExtraActivityGroup",
    "ExtraActivityTeacher",
    "AvailabilityPeriod",
    "AvailabilityPeriodGroup",
    "ChatMessage",
    "Subject",
]
