"""
Blueprint del chat de incidencias. Sirve la página del canal general y canales
por tramo. Gestiona los eventos Socket.IO (join, load_messages, send_message)
y la limpieza del chat: los mensajes se conservan en BD pero se ocultan en
la vista viva a partir del último ChatClear del día (o desde medianoche si no hubo ninguno).
"""
from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from flask_socketio import emit, join_room
from app.extensions import db, socketio
from app.models.chat import ChatMessage, ChatClear

chat_bp = Blueprint("chat", __name__, url_prefix="/chat")


def _get_cutoff():
    """Devuelve la fecha/hora desde la que se muestran mensajes: el máximo entre
    la medianoche de hoy y la última limpieza manual de hoy (si existe)."""
    today_midnight = datetime.combine(date.today(), datetime.min.time())
    last_clear = (ChatClear.query
                  .filter(ChatClear.cleared_at >= today_midnight)
                  .order_by(ChatClear.cleared_at.desc())
                  .first())
    return last_clear.cleared_at if last_clear else today_midnight


@chat_bp.route("/")
@login_required
def index():
    cutoff = _get_cutoff()
    messages = (ChatMessage.query
                .filter(ChatMessage.channel == "general",
                        ChatMessage.created_at >= cutoff)
                .order_by(ChatMessage.created_at)
                .limit(200).all())
    return render_template("chat/index.html", messages=messages, channel="general")


@chat_bp.route("/tramo/<channel>")
@login_required
def slot_chat(channel):
    cutoff = _get_cutoff()
    messages = (ChatMessage.query
                .filter(ChatMessage.channel == channel,
                        ChatMessage.created_at >= cutoff)
                .order_by(ChatMessage.created_at)
                .limit(200).all())
    return render_template("chat/index.html", messages=messages, channel=channel)


@chat_bp.route("/informe")
@login_required
def report():
    if not current_user.is_management:
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))
    from app.models.school_year import SchoolYear
    years = SchoolYear.query.order_by(SchoolYear.start_date.desc()).all()
    return render_template("chat/report.html", today=date.today().isoformat(), years=years)


@chat_bp.route("/informe/csv")
@login_required
def report_csv():
    if not current_user.is_management:
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))

    import csv
    import io
    from flask import Response

    desde, hasta = _parse_range()
    msgs = _query_range(desde, hasta)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Fecha", "Hora", "Canal", "Autor", "Mensaje"])
    for m in msgs:
        writer.writerow([
            m.created_at.strftime("%d/%m/%Y"),
            m.created_at.strftime("%H:%M:%S"),
            m.channel,
            m.author.full_name,
            m.message,
        ])
    output.seek(0)
    return Response(
        "﻿" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=chat_incidencias.csv"},
    )


@chat_bp.route("/informe/imprimir")
@login_required
def report_pdf():
    if not current_user.is_management:
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))

    desde, hasta = _parse_range()
    msgs = _query_range(desde, hasta)

    return render_template(
        "chat/print_report.html",
        desde=desde,
        hasta=hasta,
        msgs=msgs,
        institute_name=current_app.config.get("INSTITUTE_NAME", ""),
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_range():
    """Devuelve (desde, hasta) como objetos date según los query params, o todo el historial."""
    from datetime import datetime as _dt
    raw_desde = request.args.get("desde", "")
    raw_hasta = request.args.get("hasta", "")
    try:
        desde = _dt.strptime(raw_desde, "%Y-%m-%d").date()
    except ValueError:
        desde = date(2000, 1, 1)
    try:
        hasta = _dt.strptime(raw_hasta, "%Y-%m-%d").date()
    except ValueError:
        hasta = date.today()
    return desde, hasta


def _query_range(desde, hasta):
    """Mensajes del canal general ordenados por fecha/hora en el rango indicado."""
    from datetime import datetime as _dt, time as _time
    inicio = _dt.combine(desde, _time.min)
    fin    = _dt.combine(hasta, _time.max)
    return (ChatMessage.query
            .filter(ChatMessage.created_at >= inicio,
                    ChatMessage.created_at <= fin)
            .order_by(ChatMessage.created_at)
            .all())


@chat_bp.route("/limpiar", methods=["POST"])
@login_required
def clear():
    if not current_user.is_management:
        flash("Sin permiso.", "danger")
        return redirect(url_for("chat.index"))
    db.session.add(ChatClear(cleared_by_id=current_user.id))
    db.session.commit()
    socketio.emit("chat_cleared", {}, room="general")
    flash("Chat limpiado. Los mensajes anteriores se conservan en la base de datos.", "success")
    return redirect(request.referrer or url_for("chat.index"))


# ── Socket.IO events ──────────────────────────────────────────────────────────

@socketio.on("join")
def on_join(data):
    room = data.get("channel", "general")
    join_room(room)


@socketio.on("load_messages")
def on_load_messages(data):
    channel = data.get("channel", "general")
    cutoff = _get_cutoff()
    messages = (ChatMessage.query
                .filter(ChatMessage.channel == channel,
                        ChatMessage.created_at >= cutoff)
                .order_by(ChatMessage.created_at)
                .limit(200).all())
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
