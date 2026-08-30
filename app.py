import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, flash
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text


# ============================================================
# CONFIGURAÇÃO
# ============================================================

def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "sim", "on")


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

if not DATABASE_URL:
    # Desenvolvimento local. No Render, DATABASE_URL deve vir do PostgreSQL.
    DATABASE_URL = "sqlite:///licenses.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = env_bool("SESSION_COOKIE_SECURE", False)

db = SQLAlchemy(app)

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
if not ADMIN_PASSWORD:
    # Local: permite iniciar sem configurar senha; produção deve usar variável.
    ADMIN_PASSWORD = "troque-esta-senha"


# ============================================================
# MODELO
# ============================================================

class License(db.Model):
    __tablename__ = "licenses"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    owner = db.Column(db.String(160), nullable=True)
    email = db.Column(db.String(160), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    machine_id = db.Column(db.String(128), nullable=True, index=True)
    activated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_check_at = db.Column(db.DateTime(timezone=True), nullable=True)
    notes = db.Column(db.String(500), nullable=True)

    def is_expired(self):
        return bool(self.expires_at and datetime.now(timezone.utc) > self.expires_at)

    def to_dict(self):
        return {
            "key": self.key,
            "owner": self.owner,
            "email": self.email,
            "status": "active" if self.active and not self.is_expired() else (
                "expired" if self.is_expired() else "blocked"
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "machine_bound": bool(self.machine_id),
        }


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def normalize_key(value):
    return (value or "").strip().upper().replace(" ", "").replace("-", "")


def generate_license_key():
    alphabet = string.ascii_uppercase + string.digits
    # Ex.: JXCM-7F4K-9P2D-8L6Q
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def unique_license_key():
    while True:
        key = generate_license_key()
        if not License.query.filter_by(key=key).first():
            return key


def json_result(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ============================================================
# BANCO
# ============================================================

with app.app_context():
    db.create_all()


# ============================================================
# PÁGINAS
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if secrets.compare_digest(username, ADMIN_USERNAME) and secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["admin_logged"] = True
            return redirect(url_for("dashboard"))
        flash("Usuário ou senha inválidos.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@admin_required
def dashboard():
    licenses = License.query.order_by(License.created_at.desc()).all()
    return render_template("dashboard.html", licenses=licenses)


@app.route("/create", methods=["POST"])
@admin_required
def create_license():
    owner = request.form.get("owner", "").strip()
    email = request.form.get("email", "").strip()
    days_raw = request.form.get("days", "").strip()
    notes = request.form.get("notes", "").strip()

    expires_at = None
    if days_raw:
        try:
            days = int(days_raw)
            if days <= 0:
                raise ValueError
            expires_at = now_utc() + timedelta(days=days)
        except ValueError:
            flash("Quantidade de dias inválida.", "danger")
            return redirect(url_for("dashboard"))

    license_obj = License(
        key=unique_license_key(),
        owner=owner or None,
        email=email or None,
        expires_at=expires_at,
        active=True,
        notes=notes or None,
    )
    db.session.add(license_obj)
    db.session.commit()
    flash(f"Licença criada: {license_obj.key}", "success")
    return redirect(url_for("dashboard"))


@app.route("/toggle/<int:lic_id>", methods=["POST"])
@admin_required
def toggle_license(lic_id):
    lic = db.get_or_404(License, lic_id)
    lic.active = not lic.active
    db.session.commit()
    flash("Licença atualizada.", "success")
    return redirect(url_for("dashboard"))


@app.route("/reset-machine/<int:lic_id>", methods=["POST"])
@admin_required
def reset_machine(lic_id):
    lic = db.get_or_404(License, lic_id)
    lic.machine_id = None
    lic.activated_at = None
    lic.last_check_at = None
    db.session.commit()
    flash("Computador desvinculado. A próxima ativação poderá vincular um novo computador.", "success")
    return redirect(url_for("dashboard"))


@app.route("/delete/<int:lic_id>", methods=["POST"])
@admin_required
def delete_license(lic_id):
    lic = db.get_or_404(License, lic_id)
    db.session.delete(lic)
    db.session.commit()
    flash("Licença removida.", "info")
    return redirect(url_for("dashboard"))


# ============================================================
# API PARA O EXE
# ============================================================

@app.route("/api/activate", methods=["POST"])
def api_activate():
    data = request.get_json(silent=True) or {}
    key = normalize_key(data.get("key"))
    machine_id = (data.get("machine_id") or "").strip()

    if not key or not machine_id:
        return json_result({
            "ok": False,
            "error": "missing_key_or_machine_id"
        }, 400)

    lic = License.query.filter_by(key=key).first()
    if not lic:
        return json_result({"ok": False, "error": "license_not_found"}, 404)

    if not lic.active:
        return json_result({"ok": False, "error": "license_blocked"}, 403)

    if lic.is_expired():
        return json_result({
            "ok": False,
            "error": "license_expired",
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        }, 403)

    # Primeira ativação: vincula a licença ao computador.
    if not lic.machine_id:
        lic.machine_id = machine_id
        lic.activated_at = now_utc()
    elif lic.machine_id != machine_id:
        return json_result({
            "ok": False,
            "error": "machine_mismatch",
            "message": "Esta licença já está vinculada a outro computador."
        }, 409)

    lic.last_check_at = now_utc()
    db.session.commit()

    return json_result({
        "ok": True,
        "message": "Licença ativada com sucesso.",
        "license": {
            "key": lic.key,
            "owner": lic.owner,
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
            "machine_bound": True,
        }
    })


@app.route("/api/validate", methods=["POST"])
def api_validate():
    data = request.get_json(silent=True) or {}
    key = normalize_key(data.get("key"))
    machine_id = (data.get("machine_id") or "").strip()

    if not key or not machine_id:
        return json_result({
            "ok": False,
            "error": "missing_key_or_machine_id"
        }, 400)

    lic = License.query.filter_by(key=key).first()
    if not lic:
        return json_result({"ok": False, "error": "license_not_found"}, 404)

    if not lic.active:
        return json_result({"ok": False, "error": "license_blocked"}, 403)

    if lic.is_expired():
        return json_result({
            "ok": False,
            "error": "license_expired",
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        }, 403)

    if not lic.machine_id:
        return json_result({
            "ok": False,
            "error": "license_not_activated"
        }, 409)

    if lic.machine_id != machine_id:
        return json_result({
            "ok": False,
            "error": "machine_mismatch"
        }, 409)

    lic.last_check_at = now_utc()
    db.session.commit()

    return json_result({
        "ok": True,
        "license": {
            "key": lic.key,
            "owner": lic.owner,
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        }
    })


@app.route("/api/deactivate", methods=["POST"])
def api_deactivate():
    """
    Libera o computador somente quando o próprio EXE solicitar.
    O cliente não consegue trocar o computador sem passar por aqui.
    O administrador também pode usar "Resetar computador" no painel.
    """
    data = request.get_json(silent=True) or {}
    key = normalize_key(data.get("key"))
    machine_id = (data.get("machine_id") or "").strip()

    if not key or not machine_id:
        return json_result({"ok": False, "error": "missing_key_or_machine_id"}, 400)

    lic = License.query.filter_by(key=key).first()
    if not lic:
        return json_result({"ok": False, "error": "license_not_found"}, 404)

    if lic.machine_id and lic.machine_id != machine_id:
        return json_result({"ok": False, "error": "machine_mismatch"}, 409)

    lic.machine_id = None
    lic.activated_at = None
    lic.last_check_at = None
    db.session.commit()
    return json_result({"ok": True})


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return json_result({
        "ok": True,
        "service": "sistema-ecommerce-licencas",
        "time": now_utc().isoformat()
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False
    )
