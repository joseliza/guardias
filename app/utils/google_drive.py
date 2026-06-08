"""
Utilidades para acceder a Google Drive usando un refresh token OAuth2.
Usa solo la librería requests (sin google-api-python-client) para mantener
las dependencias al mínimo. Soporta Google Sheets (exporta a CSV) y ficheros
CSV nativos alojados en Drive.
"""
import requests
from flask import current_app

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_EXPORT_URL = "https://www.googleapis.com/drive/v3/files/{id}/export"
_DOWNLOAD_URL = "https://www.googleapis.com/drive/v3/files/{id}"
_FILE_META_URL = "https://www.googleapis.com/drive/v3/files/{id}"
_SHEETS_META_URL = "https://sheets.googleapis.com/v4/spreadsheets/{id}"
_SHEETS_VALUES_URL = "https://sheets.googleapis.com/v4/spreadsheets/{id}/values/{range}"


def _get_access_token(refresh_token: str) -> str:
    """Intercambia el refresh token por un access token fresco."""
    resp = requests.post(_TOKEN_URL, data={
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ValueError(f"Error al renovar token: {data.get('error_description', data['error'])}")
    return data["access_token"]


def list_spreadsheet_sheets(file_id: str, refresh_token: str) -> list:
    """Devuelve [{id, title}, …] con todas las hojas de una Google Sheet."""
    access_token = _get_access_token(refresh_token)
    resp = requests.get(
        _SHEETS_META_URL.format(id=file_id),
        params={"fields": "sheets.properties(sheetId,title)"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    sheets = resp.json().get("sheets", [])
    return [{"id": s["properties"]["sheetId"], "title": s["properties"]["title"]} for s in sheets]


def fetch_sheet_as_csv(file_id: str, sheet_title: str, refresh_token: str) -> str:
    """Descarga una hoja concreta de Google Sheets como texto CSV."""
    import csv as _csv
    import io
    from urllib.parse import quote

    access_token = _get_access_token(refresh_token)
    # A1 notation: si el título tiene espacios o comillas hay que entrecomillarlo
    safe_title = sheet_title.replace("'", "''")
    if " " in safe_title or "'" in sheet_title:
        safe_title = f"'{safe_title}'"
    range_encoded = quote(safe_title, safe="'!")

    resp = requests.get(
        _SHEETS_VALUES_URL.format(id=file_id, range=range_encoded),
        params={"majorDimension": "ROWS"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    values = resp.json().get("values", [])
    if not values:
        return ""
    out = io.StringIO()
    writer = _csv.writer(out)
    for row in values:
        writer.writerow(row)
    return out.getvalue()


def fetch_drive_file_as_csv(file_id: str, refresh_token: str) -> str:
    """
    Descarga un fichero de Drive como texto CSV.
    - Si es Google Sheets: lo exporta como CSV.
    - Si es un CSV/TSV nativo: lo descarga directamente.
    Lanza requests.HTTPError o ValueError en caso de fallo.
    """
    access_token = _get_access_token(refresh_token)
    headers = {"Authorization": f"Bearer {access_token}"}

    # Primero comprobamos el mimeType del fichero
    meta = requests.get(
        _FILE_META_URL.format(id=file_id),
        params={"fields": "mimeType,name"},
        headers=headers,
        timeout=10,
    )
    meta.raise_for_status()
    mime = meta.json().get("mimeType", "")

    if "spreadsheet" in mime:
        # Google Sheets → exportar como CSV
        resp = requests.get(
            _EXPORT_URL.format(id=file_id),
            params={"mimeType": "text/csv"},
            headers=headers,
            timeout=30,
        )
    else:
        # Fichero nativo (CSV, TSV, texto)
        resp = requests.get(
            _DOWNLOAD_URL.format(id=file_id),
            params={"alt": "media"},
            headers=headers,
            timeout=30,
        )

    resp.raise_for_status()
    return resp.text
