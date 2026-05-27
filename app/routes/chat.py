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
    return render_template("chat/report.html", today=date.today().isoformat())


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


@chat_bp.route("/informe/pdf")
@login_required
def report_pdf():
    if not current_user.is_management:
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))

    from fpdf import FPDF
    from flask import make_response
    from itertools import groupby

    FONT   = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    desde, hasta = _parse_range()
    msgs = _query_range(desde, hasta)

    pdf = FPDF()
    pdf.add_font("dv", "",  FONT)
    pdf.add_font("dv", "B", FONT_B)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Cabecera
    pdf.set_font("dv", "B", 16)
    pdf.cell(0, 10, current_app.config["INSTITUTE_NAME"], ln=True, align="C")
    pdf.set_font("dv", "", 11)
    pdf.cell(0, 7, "Informe de incidencias (chat)", ln=True, align="C")
    rango = f"{desde.strftime('%d/%m/%Y')} – {hasta.strftime('%d/%m/%Y')}"
    pdf.cell(0, 7, rango, ln=True, align="C")
    pdf.ln(4)

    if not msgs:
        pdf.set_font("dv", "", 11)
        pdf.cell(0, 8, "Sin mensajes en el período seleccionado.", ln=True)
    else:
        # Agrupar por fecha
        for day, day_msgs in groupby(msgs, key=lambda m: m.created_at.date()):
            pdf.set_font("dv", "B", 12)
            pdf.set_fill_color(230, 230, 230)
            pdf.cell(0, 8, day.strftime("%A, %d de %B de %Y").capitalize(), ln=True, fill=True)
            pdf.ln(1)

            for m in day_msgs:
                hora    = m.created_at.strftime("%H:%M:%S")
                autor   = m.author.full_name
                canal   = f"[{m.channel}]" if m.channel != "general" else ""
                cabecera = f"{hora}  {autor}  {canal}".strip()

                pdf.set_font("dv", "B", 9)
                pdf.cell(0, 5, cabecera, ln=True)
                pdf.set_font("dv", "", 10)
                pdf.multi_cell(0, 5, m.message)
                pdf.ln(1)

            pdf.ln(3)

    resp = make_response(bytes(pdf.output()))
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = "attachment; filename=chat_incidencias.pdf"
    return resp


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
