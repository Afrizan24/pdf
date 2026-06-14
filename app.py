"""Flask application entry point — Adaptive PDF Compressor."""

import os

from flask import Flask, render_template

from routes.compress import compress_bp
from routes.files import files_bp
from routes.visual import visual_bp
from routes.batch import batch_bp

app = Flask(__name__)
app.register_blueprint(compress_bp)
app.register_blueprint(files_bp)
app.register_blueprint(visual_bp)
app.register_blueprint(batch_bp)

# Ensure the uploads directory exists (used as a fallback temp location).
os.makedirs("uploads", exist_ok=True)


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/batch")
def batch():
    return render_template("batch.html")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
