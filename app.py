
from flask import Flask, render_template, request
from PIL import Image
from qrcode.image.svg import SvgPathImage
import qrcode
import os
import uuid
import re
from urllib.parse import urlparse

app = Flask(__name__)

QR_FOLDER = "static/qrcodes"
os.makedirs(QR_FOLDER, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


def is_valid_color(color):
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", color))


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

            png_path = os.path.join(QR_FOLDER, f"{filename}.png")
            svg_path = os.path.join(QR_FOLDER, f"{filename}.svg")

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

                # Generate scalable SVG
                svg = qr.make_image(
                    image_factory=SvgPathImage,
                    fill_color=fill_color,
                    back_color=back_color
                )

                svg.save(svg_path)

                qr_image = f"{filename}.png"
                svg_image = f"{filename}.svg"

            except (ValueError, OSError) as exception:
                error = str(exception) or "Unable to generate QR code."

    return render_template(
        "index.html",
        qr_image=qr_image,
        svg_image=svg_image,
        error=error,
        url=url,
        fill_color=fill_color,
        back_color=back_color
    )


@app.errorhandler(413)
def file_too_large(error):
    return render_template(
        "index.html",
        qr_image=None,
        svg_image=None,
        error="Logo is too large. Maximum upload size is 2 MB.",
        url="",
        fill_color="#000000",
        back_color="#ffffff"
    ), 413


if __name__ == "__main__":
    app.run(debug=True)
