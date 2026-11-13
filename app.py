
from flask import Flask, render_template, request, redirect, url_for
from PIL import Image
from qrcode.image.svg import SvgPathImage
from datetime import datetime
from pathlib import Path
import qrcode
import json
import uuid
import re
from urllib.parse import urlparse

app = Flask(__name__)

QR_FOLDER = Path("static/qrcodes")
HISTORY_FILE = QR_FOLDER / "history.json"

QR_FOLDER.mkdir(parents=True, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
MAX_HISTORY = 20


def is_valid_color(color):
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", color))


def load_history():
    if not HISTORY_FILE.exists():
        return []

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file:
            history = json.load(file)

        return history if isinstance(history, list) else []

    except (json.JSONDecodeError, OSError):
        return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as file:
        json.dump(history, file, indent=4)


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


def delete_qr_files(entry):
    for key in ("png", "svg"):
        filename = entry.get(key, "")

        # Only delete generated files inside QR_FOLDER.
        if filename and Path(filename).name == filename:
            filepath = QR_FOLDER / filename

            if filepath.exists():
                filepath.unlink()


@app.route("/", methods=["GET", "POST"])
def index():
    qr_image = None
    svg_image = None
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
            filename = uuid.uuid4().hex

            png_path = QR_FOLDER / f"{filename}.png"
            svg_path = QR_FOLDER / f"{filename}.svg"

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
                png.save(png_path, format="PNG", optimize=True)

                # Generate SVG
                svg = qr.make_image(
                    image_factory=SvgPathImage,
                    fill_color=fill_color,
                    back_color=back_color
                )

                svg.save(svg_path)

                # Save history
                entry = {
                    "id": filename,
                    "url": url,
                    "png": f"{filename}.png",
                    "svg": f"{filename}.svg",
                    "fill_color": fill_color,
                    "back_color": back_color,
                    "created_at": datetime.now().strftime(
                        "%Y-%m-%d %H:%M"
                    )
                }

                history = load_history()
                history.insert(0, entry)

                # Keep only the latest 20 entries.
                old_entries = history[MAX_HISTORY:]

                for old_entry in old_entries:
                    delete_qr_files(old_entry)

                history = history[:MAX_HISTORY]
                save_history(history)

                qr_image = entry["png"]
                svg_image = entry["svg"]

            except (ValueError, OSError) as exception:
                png_path.unlink(missing_ok=True)
                svg_path.unlink(missing_ok=True)
                error = str(exception) or "Unable to generate QR code."

    history = load_history()

    return render_template(
        "index.html",
        qr_image=qr_image,
        svg_image=svg_image,
        error=error,
        url=url,
        fill_color=fill_color,
        back_color=back_color,
        history=history
    )


@app.route("/history/delete/<entry_id>", methods=["POST"])
def delete_history(entry_id):
    history = load_history()

    updated_history = [
        entry for entry in history
        if entry.get("id") != entry_id
    ]

    if len(updated_history) != len(history):
        deleted = next(
            entry for entry in history
            if entry.get("id") == entry_id
        )

        delete_qr_files(deleted)
        save_history(updated_history)

    return redirect(url_for("index"))


@app.route("/history/clear", methods=["POST"])
def clear_history():
    history = load_history()

    for entry in history:
        delete_qr_files(entry)

    save_history([])

    return redirect(url_for("index"))


@app.errorhandler(413)
def file_too_large(error):
    return render_template(
        "index.html",
        qr_image=None,
        svg_image=None,
        error="Logo is too large. Maximum upload size is 2 MB.",
        url="",
        fill_color="#000000",
        back_color="#ffffff",
        history=load_history()
    ), 413


if __name__ == "__main__":
    app.run(debug=True)
