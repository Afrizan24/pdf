"""
Flask entry point — Adaptive PDF Compressor
"""

import os
from flask import Flask, render_template

from routes.compress import compress_bp
from routes.files import files_bp

app = Flask(__name__)

# uploads/ still needed as a fallback landing spot; cleaned per-request via tempfile
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.register_blueprint(compress_bp)
app.register_blueprint(files_bp)


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
