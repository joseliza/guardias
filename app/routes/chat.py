from datetime import datetime
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from flask_socketio import emit, join_room
from app.extensions import db, socketio
from app.models.chat import ChatMessage

chat_bp = Blueprint("chat", __name__, url_prefix="/chat")


@chat_bp.route("/")
@login_required
def index():
    messages = ChatMessage.query.filter_by(channel="general").order_by(ChatMessage.created_at).limit(100).all()
    return render_template("chat/index.html", messages=messages, channel="general")


@chat_bp.route("/tramo/<channel>")
@login_required
def slot_chat(channel):
    messages = ChatMessage.query.filter_by(channel=channel).order_by(ChatMessage.created_at).limit(100).all()
    return render_template("chat/index.html", messages=messages, channel=channel)


# ── Socket.IO events ──────────────────────────────────────────────────────────

@socketio.on("join")
def on_join(data):
    room = data.get("channel", "general")
    join_room(room)


@socketio.on("load_messages")
def on_load_messages(data):
    channel = data.get("channel", "general")
    messages = ChatMessage.query.filter_by(channel=channel).order_by(ChatMessage.created_at).limit(100).all()
    emit("message_history", {
        "messages": [
            {
                "author": m.author.full_name,
                "message": m.message,
                "timestamp": m.created_at.strftime("%H:%M"),
            }
            for m in messages
        ]
    })


@socketio.on("send_message")
def on_message(data):
    if not current_user.is_authenticated:
        return
    channel = data.get("channel", "general")
    text = data.get("message", "").strip()
    if not text:
        return
    msg = ChatMessage(author_id=current_user.id, channel=channel, message=text)
    db.session.add(msg)
    db.session.commit()
    emit(
        "new_message",
        {
            "id": msg.id,
            "author": current_user.full_name,
            "message": msg.message,
            "timestamp": msg.created_at.strftime("%H:%M"),
        },
        room=channel,
    )
