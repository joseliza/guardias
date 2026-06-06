from datetime import datetime, timedelta
from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from app.extensions import db
from app.models.presence import UserPresence

presence_bp = Blueprint("presence", __name__, url_prefix="/api")

_TIMEOUT = 90  # segundos sin heartbeat → desconectado


@presence_bp.route("/presencia", methods=["POST"])
@login_required
def heartbeat():
    p = db.session.get(UserPresence, current_user.id)
    if p:
        p.last_seen = datetime.now()
    else:
        db.session.add(UserPresence(user_id=current_user.id, last_seen=datetime.now()))
    db.session.commit()
    return jsonify(ok=True)


@presence_bp.route("/presencia", methods=["GET"])
@login_required
def get_presence():
    cutoff = datetime.now() - timedelta(seconds=_TIMEOUT)
    presences = (UserPresence.query
                 .filter(UserPresence.last_seen >= cutoff)
                 .join(UserPresence.user)
                 .all())
    users = sorted(
        [{"id": p.user_id, "name": p.user.full_name} for p in presences],
        key=lambda u: u["name"],
    )
    return jsonify(count=len(users), users=users)
