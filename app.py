
import io
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import qrcode
from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    send_file,
    abort,
)
from PIL import Image
from qrcode.image.svg import SvgPathImage
from supabase import create_client


# =========================================================
# CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing Supabase environment variables.")

if not FLASK_SECRET_KEY:
    raise RuntimeError("Missing FLASK_SECRET_KEY in .env.")

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("VERCEL") == "1"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

BUCKET_NAME = "qr-codes"
MAX_HISTORY = 20
SIGNED_URL_EXPIRY = 3600
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


# =========================================================
# BROWSER SESSION
# =========================================================

def get_browser_id():
    if "browser_id" not in session:
        session["browser_id"] = str(uuid.uuid4())

    session.permanent = True
    return session["browser_id"]


# =========================================================
# VALIDATION
# =========================================================

def is_valid_color(color):
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", color))


# =========================================================
# LOGO PROCESSING
# =========================================================

def add_logo(qr_image, logo_file):
    if not logo_file or not logo_file.filename:
        return qr_image

    try:
        logo = Image.open(logo_file)

        if logo.format not in ALLOWED_FORMATS:
            raise ValueError("Use a PNG, JPG, or WEBP logo.")

        logo = logo.convert("RGBA")
        logo.thumbnail((qr_image.width // 5, qr_image.height // 5))

        padding = 8

        logo_background = Image.new(
            "RGBA",
            (logo.width + padding * 2, logo.height + padding * 2),
            "white"
        )

        logo_background.alpha_composite(logo, (padding, padding))

        x = (qr_image.width - logo_background.width) // 2
        y = (qr_image.height - logo_background.height) // 2

        qr_image = qr_image.convert("RGBA")
        qr_image.alpha_composite(logo_background, (x, y))

        return qr_image.convert("RGB")

    except (OSError, ValueError) as error:
        raise ValueError(str(error))


# =========================================================
# SUPABASE SIGNED URL
# =========================================================

def get_signed_url(storage_path):
    response = (
        supabase.storage
        .from_(BUCKET_NAME)
        .create_signed_url(storage_path, SIGNED_URL_EXPIRY)
    )

    if isinstance(response, dict):
        signed_url = (
            response.get("signedURL")
            or response.get("signedUrl")
        )
    else:
        signed_url = (
            getattr(response, "signedURL", None)
            or getattr(response, "signedUrl", None)
        )

    if not signed_url:
        raise RuntimeError("Could not generate a QR download URL.")

    if signed_url.startswith(("http://", "https://")):
        return signed_url

    if signed_url.startswith("/storage/v1/"):
        return urljoin(
            SUPABASE_URL.rstrip("/") + "/",
            signed_url.lstrip("/")
        )

    if signed_url.startswith("/object/"):
        return urljoin(
            SUPABASE_URL.rstrip("/") + "/",
            "storage/v1" + signed_url
        )

    return urljoin(
        SUPABASE_URL.rstrip("/") + "/",
        signed_url.lstrip("/")
    )


# =========================================================
# HISTORY
# =========================================================

def load_history(browser_id):
    response = (
        supabase.table("qr_history")
        .select("*")
        .eq("browser_id", browser_id)
        .order("created_at", desc=True)
        .limit(MAX_HISTORY)
        .execute()
    )

    history = response.data or []

    for item in history:
        item["png_url"] = get_signed_url(item["png_path"])
        item["svg_url"] = get_signed_url(item["svg_path"])

    return history


def delete_storage_files(entries):
    paths = []

    for entry in entries:
        for key in ("png_path", "svg_path"):
            path = entry.get(key)

            if path:
                paths.append(path)

    if paths:
        supabase.storage.from_(BUCKET_NAME).remove(paths)


def trim_history(browser_id):
    response = (
        supabase.table("qr_history")
        .select("id,png_path,svg_path")
        .eq("browser_id", browser_id)
        .order("created_at", desc=True)
        .execute()
    )

    history = response.data or []
    old_entries = history[MAX_HISTORY:]

    if not old_entries:
        return

    delete_storage_files(old_entries)

    old_ids = [entry["id"] for entry in old_entries]

    (
        supabase.table("qr_history")
        .delete()
        .eq("browser_id", browser_id)
        .in_("id", old_ids)
        .execute()
    )


# =========================================================
# RENDER HOMEPAGE
# =========================================================

def render_index(
    browser_id,
    qr_image=None,
    svg_image=None,
    qr_id=None,
    error=None,
    url="",
    fill_color="#000000",
    back_color="#ffffff",
    status=200
):
    history = load_history(browser_id)

    return render_template(
        "index.html",
        qr_image=qr_image,
        svg_image=svg_image,
        qr_id=qr_id,
        error=error,
        url=url,
        fill_color=fill_color,
        back_color=back_color,
        history=history
    ), status


# =========================================================
# GENERATE QR CODE
# =========================================================

@app.route("/", methods=["GET", "POST"])
def index():
    browser_id = get_browser_id()

    qr_image = None
    svg_image = None
    qr_id = None
    error = None

    url = ""
    fill_color = "#000000"
    back_color = "#ffffff"

    if request.method == "POST":

        url = request.form.get("url", "").strip()
        fill_color = request.form.get("fill_color", "#000000")
        back_color = request.form.get("back_color", "#ffffff")
        logo_file = request.files.get("logo")

        parsed_url = urlparse(url)

        if (
            parsed_url.scheme not in ("http", "https")
            or not parsed_url.netloc
            or any(char.isspace() for char in url)
        ):
            error = "Please enter a valid website URL."

        elif not is_valid_color(fill_color) or not is_valid_color(back_color):
            error = "Please select valid colors."

        else:
            record_id = str(uuid.uuid4())

            png_path = f"{browser_id}/{record_id}.png"
            svg_path = f"{browser_id}/{record_id}.svg"

            uploaded_paths = []
            database_inserted = False

            try:
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_H,
                    box_size=10,
                    border=4
                )

                qr.add_data(url)
                qr.make(fit=True)

                # Generate PNG
                png = qr.make_image(
                    fill_color=fill_color,
                    back_color=back_color
                ).convert("RGB")

                png = add_logo(png, logo_file)

                png_buffer = io.BytesIO()
                png.save(png_buffer, format="PNG", optimize=True)
                png_bytes = png_buffer.getvalue()

                # Generate SVG
                svg = qr.make_image(
                    image_factory=SvgPathImage,
                    fill_color=fill_color,
                    back_color=back_color
                )

                svg_output = svg.to_string()

                if isinstance(svg_output, str):
                    svg_bytes = svg_output.encode("utf-8")
                else:
                    svg_bytes = svg_output

                # Upload PNG to Supabase
                supabase.storage.from_(BUCKET_NAME).upload(
                    png_path,
                    png_bytes,
                    {
                        "content-type": "image/png",
                        "upsert": "false"
                    }
                )

                uploaded_paths.append(png_path)

                # Upload SVG to Supabase
                supabase.storage.from_(BUCKET_NAME).upload(
                    svg_path,
                    svg_bytes,
                    {
                        "content-type": "image/svg+xml",
                        "upsert": "false"
                    }
                )

                uploaded_paths.append(svg_path)

                # Create signed URLs for preview
                png_url = get_signed_url(png_path)
                svg_url = get_signed_url(svg_path)

                # Save history in Supabase
                record = {
                    "id": record_id,
                    "browser_id": browser_id,
                    "url": url,
                    "png_path": png_path,
                    "svg_path": svg_path,
                    "fill_color": fill_color,
                    "back_color": back_color
                }

                supabase.table("qr_history").insert(record).execute()
                database_inserted = True

                trim_history(browser_id)

                qr_image = png_url
                svg_image = svg_url
                qr_id = record_id

            except Exception:
                if database_inserted:
                    try:
                        (
                            supabase.table("qr_history")
                            .delete()
                            .eq("id", record_id)
                            .eq("browser_id", browser_id)
                            .execute()
                        )
                    except Exception:
                        app.logger.exception(
                            "Could not clean up database record."
                        )

                if uploaded_paths:
                    try:
                        supabase.storage.from_(BUCKET_NAME).remove(
                            uploaded_paths
                        )
                    except Exception:
                        app.logger.exception(
                            "Could not clean up uploaded QR files."
                        )

                app.logger.exception("QR generation failed.")
                error = "Unable to generate your QR code. Please try again."

    return render_index(
        browser_id=browser_id,
        qr_image=qr_image,
        svg_image=svg_image,
        qr_id=qr_id,
        error=error,
        url=url,
        fill_color=fill_color,
        back_color=back_color
    )


# =========================================================
# DOWNLOAD PNG AND SVG
# =========================================================

@app.route("/download/<entry_id>/<file_type>")
def download_qr(entry_id, file_type):
    browser_id = get_browser_id()

    if file_type not in ("png", "svg"):
        abort(404)

    try:
        # Find the QR record belonging to this browser.
        response = (
            supabase.table("qr_history")
            .select("id,png_path,svg_path")
            .eq("id", entry_id)
            .eq("browser_id", browser_id)
            .execute()
        )

        entries = response.data or []

        if not entries:
            abort(404)

        entry = entries[0]

        if file_type == "png":
            storage_path = entry["png_path"]
            mimetype = "image/png"
            filename = "qrcode.png"
        else:
            storage_path = entry["svg_path"]
            mimetype = "image/svg+xml"
            filename = "qrcode.svg"

        # Download file from private Supabase Storage.
        file_data = (
            supabase.storage
            .from_(BUCKET_NAME)
            .download(storage_path)
        )

        # Send it as an attachment to the browser.
        return send_file(
            io.BytesIO(file_data),
            mimetype=mimetype,
            as_attachment=True,
            download_name=filename
        )

    except Exception as exception:
        if getattr(exception, "code", None) == "404":
            abort(404)

        app.logger.exception("QR download failed.")
        return "Unable to download QR code. Please try again.", 500


# =========================================================
# DELETE ONE HISTORY ENTRY
# =========================================================

@app.route("/history/delete/<entry_id>", methods=["POST"])
def delete_history(entry_id):
    browser_id = get_browser_id()

    response = (
        supabase.table("qr_history")
        .select("*")
        .eq("id", entry_id)
        .eq("browser_id", browser_id)
        .execute()
    )

    entries = response.data or []

    if entries:
        entry = entries[0]

        delete_storage_files([entry])

        (
            supabase.table("qr_history")
            .delete()
            .eq("id", entry_id)
            .eq("browser_id", browser_id)
            .execute()
        )

    return redirect(url_for("index"))


# =========================================================
# CLEAR ALL HISTORY
# =========================================================

@app.route("/history/clear", methods=["POST"])
def clear_history():
    browser_id = get_browser_id()

    response = (
        supabase.table("qr_history")
        .select("*")
        .eq("browser_id", browser_id)
        .execute()
    )

    entries = response.data or []

    if entries:
        delete_storage_files(entries)

        (
            supabase.table("qr_history")
            .delete()
            .eq("browser_id", browser_id)
            .execute()
        )

    return redirect(url_for("index"))


# =========================================================
# FILE SIZE ERROR
# =========================================================

@app.errorhandler(413)
def file_too_large(error):
    browser_id = get_browser_id()

    return render_index(
        browser_id=browser_id,
        error="Logo is too large. Maximum upload size is 2 MB.",
        status=413
    )


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":
    app.run(debug=True)
