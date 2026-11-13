
from flask import Flask, render_template, request
import qrcode
import os
import uuid
from urllib.parse import urlparse

app = Flask(__name__)

QR_FOLDER = "static/qrcodes"
os.makedirs(QR_FOLDER, exist_ok=True)


@app.route("/", methods=["GET", "POST"])
def index():
    qr_image = None
    error = None

    if request.method == "POST":
        url = request.form.get("url", "").strip()
        parsed_url = urlparse(url)

        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            error = "Please enter a valid URL."
        else:
            filename = f"{uuid.uuid4().hex}.png"
            filepath = os.path.join(QR_FOLDER, filename)

            qr = qrcode.QRCode(
                version=1,
                box_size=10,
                border=4
            )

            qr.add_data(url)
            qr.make(fit=True)

            image = qr.make_image(
                fill_color="black",
                back_color="white"
            )

            image.save(filepath)
            qr_image = filepath

    return render_template(
        "index.html",
        qr_image=qr_image,
        error=error
    )


if __name__ == "__main__":
    app.run(debug=True)
