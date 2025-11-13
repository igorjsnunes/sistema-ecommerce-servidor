from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, flash, make_response
)
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
import uuid

# ---------- CONFIG ----------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "slipknot66"
SECRET_KEY = os.environ.get("FLASK_SECRET", "troque_esse_secret_em_producao")
DB_PATH = os.path.join(os.path.dirname(__file__), "licenses.db")
# ----------------------------

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{DB_PATH}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# ----------------- MODELO -----------------
class License(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    owner = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.String(255), nullable=True)

    def to_dict(self):
        return {
            "key": self.key,
            "owner": self.owner,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "active": self.active,
            "notes": self.notes,
        }


# cria o DB na inicialização (antes do primeiro request)
@app.before_first_request
def init_db():
    db.create_all()


# ----------------- HELPERS / CORS -----------------
def json_response(data, status=200):
    """Retorna jsonify com cabeçalhos CORS básicos."""
    resp = make_response(jsonify(data), status)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp


@app.after_request
def add_cors_headers(response):
    """Garante CORS também para outras rotas/arquivos."""
    response.headers.setdefault('Access-Control-Allow-Origin', '*')
    response.headers.setdefault('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    response.headers.setdefault('Access-Control-Allow-Headers', 'Content-Type')
    return response


# ----------------- PÁGINAS -----------------
@app.route("/")
def index():
    return render_template("index.html")


# ----------------- LOGIN -----------------
def is_logged_in():
    return session.get("admin_logged", False)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged"] = True
            return redirect(url_for("dashboard"))
        else:
            flash("Usuário ou senha inválidos.", "danger")
            return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("admin_logged", None)
    return redirect(url_for("login"))


# ----------------- DASHBOARD -----------------
@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))
    licenses = License.query.order_by(License.created_at.desc()).all()
    return render_template("dashboard.html", licenses=licenses)


@app.route("/create", methods=["POST"])
def create_license():
    if not is_logged_in():
        return redirect(url_for("login"))
    owner = request.form.get("owner")
    days = request.form.get("days")
    notes = request.form.get("notes")
    expires = None
    if days:
        try:
            days_i = int(days)
            expires = datetime.utcnow() + timedelta(days=days_i)
        except Exception:
            expires = None

    new_key = str(uuid.uuid4()).replace('-', '')[:24].upper()
    lic = License(key=new_key, owner=owner, expires_at=expires, active=True, notes=notes)
    db.session.add(lic)
    db.session.commit()
    flash(f"Licença criada: {new_key}", "success")
    return redirect(url_for("dashboard"))


@app.route("/toggle/<int:lic_id>")
def toggle_license(lic_id):
    if not is_logged_in():
        return redirect(url_for("login"))
    lic = License.query.get_or_404(lic_id)
    lic.active = not lic.active
    db.session.commit()
    return redirect(url_for("dashboard"))


@app.route("/delete/<int:lic_id>")
def delete_license(lic_id):
    if not is_logged_in():
        return redirect(url_for("login"))
    lic = License.query.get_or_404(lic_id)
    db.session.delete(lic)
    db.session.commit()
    flash("Licença removida.", "info")
    return redirect(url_for("dashboard"))


# ----------------- API -----------------
@app.route("/api/validate", methods=["GET", "POST", "OPTIONS"])
def api_validate():
    # permite preflight
    if request.method == "OPTIONS":
        return json_response({"ok": True, "msg": "options"}, status=200)

    # aceita POST JSON {"key": "..."} ou GET ?key=...
    data = {}
    if request.method == "POST":
        try:
            data = request.get_json(silent=True) or {}
        except Exception:
            data = {}

    key = None
    if isinstance(data, dict):
        key = data.get("key")
    if not key:
        key = request.args.get("key", "")

    key = (key or "").strip()
    if not key:
        return json_response({"ok": False, "error": "missing_key"}, status=400)

    lic = License.query.filter_by(key=key).first()
    if not lic:
        return json_response({"ok": False, "error": "license_not_found"}, status=404)

    if not lic.active:
        return json_response({"ok": False, "error": "license_blocked", "license": lic.to_dict()}, status=403)

    if lic.expires_at and datetime.utcnow() > lic.expires_at:
        return json_response({"ok": False, "error": "license_expired", "license": lic.to_dict()}, status=403)

    return json_response({
        "ok": True,
        "license": {
            "key": lic.key,
            "status": "active",
            "owner": lic.owner,
            "expires": lic.expires_at.isoformat() if lic.expires_at else None
        }
    }, status=200)


@app.route("/health")
def health():
    return json_response({"ok": True, "time": datetime.utcnow().isoformat()})


# ----------------- MAIN -----------------
if __name__ == "__main__":
    # usar PORT do ambiente (Render) ou 5000 local
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
