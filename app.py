"""
MedReconcile — Backend Server
Run: python app.py
"""

from flask import Flask
from flask_cors import CORS
from routes.reconcile import reconcile_bp
from routes.ehr import ehr_bp
from routes.multi_bottle import multi_bp
from routes.review import review_bp

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

app.register_blueprint(reconcile_bp, url_prefix="/api")
app.register_blueprint(ehr_bp, url_prefix="/api")
app.register_blueprint(multi_bp, url_prefix="/api")
app.register_blueprint(review_bp, url_prefix="/api")

@app.route("/")
def index():
    return app.send_static_file("index.html")

if __name__ == "__main__":
    print("MedReconcile server starting on http://localhost:5000")
    app.run(debug=True, port=5000)
