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
from sqlalchemy import inspect, text, UniqueConstraint


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
    ADMIN_PASSWORD = "troque-esta-senha"


# ============================================================
# MODELOS
# ============================================================

class License(db.Model):
    __tablename__ = "licenses"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    owner = db.Column(db.String(160), nullable=True)
    email = db.Column(db.String(160), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    # Mantido para compatibilidade com o banco antigo.
    # A nova lógica usa LicenseMachine.
    machine_id = db.Column(db.String(128), nullable=True, index=True)
    activated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_check_at = db.Column(db.DateTime(timezone=True), nullable=True)

    notes = db.Column(db.String(500), nullable=True)

    # NOVO: quantidade máxima de computadores permitidos.
    max_machines = db.Column(db.Integer, nullable=False, default=1, server_default="1")

    machines = db.relationship(
        "LicenseMachine",
        back_populates="license",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def is_expired(self):
        return bool(
            self.expires_at and
            datetime.now(timezone.utc) > self.expires_at
        )

    @property
    def machine_count(self):
        return len(self.machines)

    @property
    def machine_limit(self):
        try:
            return max(1, int(self.max_machines or 1))
        except Exception:
            return 1

    @property
    def available_slots(self):
        return max(0, self.machine_limit - self.machine_count)

    def to_dict(self):
        return {
            "key": self.key,
            "owner": self.owner,
            "email": self.email,
            "status": (
                "active"
                if self.active and not self.is_expired()
                else ("expired" if self.is_expired() else "blocked")
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "machine_count": self.machine_count,
            "max_machines": self.machine_limit,
            "available_slots": self.available_slots,
        }


class LicenseMachine(db.Model):
    __tablename__ = "license_machines"
    __table_args__ = (
        UniqueConstraint(
            "license_id",
            "machine_id",
            name="uq_license_machine"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    license_id = db.Column(
        db.Integer,
        db.ForeignKey("licenses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    machine_id = db.Column(db.String(128), nullable=False, index=True)
    machine_name = db.Column(db.String(160), nullable=True)
    activated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    last_check_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    license = db.relationship("License", back_populates="machines")


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def normalize_key(value):
    """
    Converte a chave para o mesmo formato usado no banco:
    XXXX-XXXX-XXXX-XXXX.

    O problema do backup era que a API removia os hífens antes de
    consultar o banco, enquanto as chaves são salvas com hífens.
    """
    raw = "".join(
        ch for ch in str(value or "").strip().upper()
        if ch.isalnum()
    )

    if len(raw) == 16:
        return "-".join(raw[i:i + 4] for i in range(0, 16, 4))

    return raw


def generate_license_key():
    alphabet = string.ascii_uppercase + string.digits
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
# MIGRAÇÃO / BANCO
# ============================================================

def ensure_database_schema():
    """
    Cria tabelas novas e adiciona max_machines ao banco antigo.
    Também migra a antiga machine_id para license_machines.
    """
    db.create_all()

    inspector = inspect(db.engine)
    columns = {c["name"] for c in inspector.get_columns("licenses")}

    if "max_machines" not in columns:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE licenses "
                    "ADD COLUMN max_machines INTEGER NOT NULL DEFAULT 1"
                )
            )

    # Garante que licenças antigas tenham limite válido.
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE licenses "
                "SET max_machines = 1 "
                "WHERE max_machines IS NULL OR max_machines < 1"
            )
        )

    # Migra uma eventual máquina antiga para a tabela nova.
    # Fazemos isso usando SQL simples para funcionar tanto em SQLite quanto PostgreSQL.
    licenses = License.query.all()
    migrated = False

    for lic in licenses:
        if lic.machine_id:
            exists = LicenseMachine.query.filter_by(
                license_id=lic.id,
                machine_id=lic.machine_id
            ).first()

            if not exists:
                db.session.add(
                    LicenseMachine(
                        license_id=lic.id,
                        machine_id=lic.machine_id,
                        machine_name="Computador existente",
                        activated_at=lic.activated_at or now_utc(),
                        last_check_at=lic.last_check_at,
                    )
                )
                migrated = True

    if migrated:
        db.session.commit()


with app.app_context():
    ensure_database_schema()


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

        if (
            secrets.compare_digest(username, ADMIN_USERNAME)
            and secrets.compare_digest(password, ADMIN_PASSWORD)
        ):
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
    max_machines_raw = request.form.get("max_machines", "1").strip()

    try:
        max_machines = int(max_machines_raw or "1")
        if max_machines <= 0:
            raise ValueError
    except ValueError:
        flash("Quantidade de computadores inválida.", "danger")
        return redirect(url_for("dashboard"))

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
        max_machines=max_machines,
    )

    db.session.add(license_obj)
    db.session.commit()

    flash(
        f"Licença criada: {license_obj.key} "
        f"({license_obj.max_machines} computador(es))",
        "success"
    )
    return redirect(url_for("dashboard"))


@app.route("/update-limit/<int:lic_id>", methods=["POST"])
@admin_required
def update_limit(lic_id):
    lic = db.get_or_404(License, lic_id)
    raw = request.form.get("max_machines", "").strip()

    try:
        new_limit = int(raw)
        if new_limit <= 0:
            raise ValueError
    except ValueError:
        flash("Limite de computadores inválido.", "danger")
        return redirect(url_for("dashboard"))

    if new_limit < lic.machine_count:
        flash(
            f"Não é possível definir {new_limit} computador(es): "
            f"a licença já possui {lic.machine_count} computador(es) vinculado(s).",
            "danger"
        )
        return redirect(url_for("dashboard"))

    lic.max_machines = new_limit
    db.session.commit()

    flash("Limite de computadores atualizado.", "success")
    return redirect(url_for("dashboard"))


@app.route("/toggle/<int:lic_id>", methods=["POST"])
@admin_required
def toggle_license(lic_id):
    lic = db.get_or_404(License, lic_id)
    lic.active = not lic.active
    db.session.commit()

    flash(
        "Licença ativada." if lic.active else "Licença bloqueada.",
        "success"
    )
    return redirect(url_for("dashboard"))


@app.route(
    "/reset-machine/<int:lic_id>/<int:machine_row_id>",
    methods=["POST"]
)
@admin_required
def reset_machine(lic_id, machine_row_id):
    lic = db.get_or_404(License, lic_id)
    machine = db.get_or_404(LicenseMachine, machine_row_id)

    if machine.license_id != lic.id:
        flash("Computador inválido para esta licença.", "danger")
        return redirect(url_for("dashboard"))

    db.session.delete(machine)
    db.session.commit()

    flash(
        "Computador removido da licença. Uma nova ativação poderá usar essa vaga.",
        "success"
    )
    return redirect(url_for("dashboard"))


# Compatibilidade com o botão antigo /reset-machine/<lic_id>.
# Agora ele remove todos os computadores da licença.
@app.route("/reset-machine/<int:lic_id>", methods=["POST"])
@admin_required
def reset_all_machines(lic_id):
    lic = db.get_or_404(License, lic_id)

    for machine in list(lic.machines):
        db.session.delete(machine)

    # Limpa também o campo antigo.
    lic.machine_id = None
    lic.activated_at = None
    lic.last_check_at = None

    db.session.commit()

    flash(
        "Todos os computadores foram desvinculados da licença.",
        "success"
    )
    return redirect(url_for("dashboard"))


@app.route("/renew/<int:lic_id>", methods=["POST"])
@admin_required
def renew_license(lic_id):
    lic = db.get_or_404(License, lic_id)
    raw_days = request.form.get("days", "").strip()

    try:
        days = int(raw_days)
        if days <= 0:
            raise ValueError
    except ValueError:
        flash("Quantidade de dias para renovação inválida.", "danger")
        return redirect(url_for("dashboard"))

    now = now_utc()

    # Se ainda está válida, acrescenta os dias ao vencimento atual.
    # Se já venceu, começa a contar a partir de agora.
    if lic.expires_at and lic.expires_at > now:
        lic.expires_at = lic.expires_at + timedelta(days=days)
    else:
        lic.expires_at = now + timedelta(days=days)

    lic.active = True
    db.session.commit()

    flash(
        f"Licença renovada por {days} dia(s). "
        f"A chave {lic.key} foi mantida.",
        "success"
    )
    return redirect(url_for("dashboard"))


@app.route("/set-expiration/<int:lic_id>", methods=["POST"])
@admin_required
def set_expiration(lic_id):
    """Define uma nova validade a partir de agora, sem somar ao vencimento atual.

    Ex.: informar 1 dia faz a licença vencer exatamente 1 dia a partir deste momento.
    A chave, os computadores vinculados e os demais dados permanecem os mesmos.
    """
    lic = db.get_or_404(License, lic_id)
    raw_days = request.form.get("days", "").strip()

    try:
        days = int(raw_days)
        if days <= 0:
            raise ValueError
    except ValueError:
        flash("Quantidade de dias para definir a validade inválida.", "danger")
        return redirect(url_for("dashboard"))

    lic.expires_at = now_utc() + timedelta(days=days)
    lic.active = True
    db.session.commit()

    flash(
        f"Validade definida para {days} dia(s), mantendo a mesma chave.",
        "success"
    )
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

def get_machine_name(data):
    name = (data.get("machine_name") or "").strip()
    return name[:160] if name else "Computador"


def find_machine(lic, machine_id):
    return LicenseMachine.query.filter_by(
        license_id=lic.id,
        machine_id=machine_id
    ).first()


def license_payload(lic):
    return {
        "key": lic.key,
        "owner": lic.owner,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        "machine_count": lic.machine_count,
        "max_machines": lic.machine_limit,
        "available_slots": lic.available_slots,
    }


@app.route("/api/activate", methods=["POST"])
def api_activate():
    data = request.get_json(silent=True) or {}
    key = normalize_key(data.get("key"))
    machine_id = (data.get("machine_id") or "").strip()
    machine_name = get_machine_name(data)

    if not key or not machine_id:
        return json_result({
            "ok": False,
            "error": "missing_key_or_machine_id"
        }, 400)

    lic = License.query.filter_by(key=key).first()

    if not lic:
        return json_result({
            "ok": False,
            "error": "license_not_found"
        }, 404)

    if not lic.active:
        return json_result({
            "ok": False,
            "error": "license_blocked"
        }, 403)

    if lic.is_expired():
        return json_result({
            "ok": False,
            "error": "license_expired",
            "expires_at": (
                lic.expires_at.isoformat()
                if lic.expires_at else None
            ),
        }, 403)

    existing = find_machine(lic, machine_id)

    # Já está ativado neste PC: apenas atualiza o último acesso.
    if existing:
        existing.machine_name = machine_name
        existing.last_check_at = now_utc()

    else:
        # Ainda há vaga.
        if lic.machine_count >= lic.machine_limit:
            return json_result({
                "ok": False,
                "error": "machine_limit_reached",
                "machine_count": lic.machine_count,
                "max_machines": lic.machine_limit,
                "message": (
                    f"Limite de {lic.machine_limit} computador(es) "
                    "atingido."
                ),
            }, 409)

        new_machine = LicenseMachine(
            license_id=lic.id,
            machine_id=machine_id,
            machine_name=machine_name,
            activated_at=now_utc(),
            last_check_at=now_utc(),
        )
        db.session.add(new_machine)

    # Mantém os campos antigos sincronizados com a primeira máquina.
    first_machine = (
        existing
        if existing
        else LicenseMachine.query.filter_by(
            license_id=lic.id
        ).first()
    )

    if first_machine:
        lic.machine_id = first_machine.machine_id
        lic.activated_at = first_machine.activated_at
        lic.last_check_at = now_utc()

    db.session.commit()

    return json_result({
        "ok": True,
        "message": "Licença ativada com sucesso.",
        "license": license_payload(lic),
    })


@app.route("/api/validate", methods=["POST"])
def api_validate():
    data = request.get_json(silent=True) or {}
    key = normalize_key(data.get("key"))
    machine_id = (data.get("machine_id") or "").strip()
    machine_name = get_machine_name(data)

    if not key or not machine_id:
        return json_result({
            "ok": False,
            "error": "missing_key_or_machine_id"
        }, 400)

    lic = License.query.filter_by(key=key).first()

    if not lic:
        return json_result({
            "ok": False,
            "error": "license_not_found"
        }, 404)

    if not lic.active:
        return json_result({
            "ok": False,
            "error": "license_blocked"
        }, 403)

    if lic.is_expired():
        return json_result({
            "ok": False,
            "error": "license_expired",
            "expires_at": (
                lic.expires_at.isoformat()
                if lic.expires_at else None
            ),
        }, 403)

    machine = find_machine(lic, machine_id)

    if not machine:
        return json_result({
            "ok": False,
            "error": "machine_mismatch",
            "message": (
                "Este computador não está vinculado a esta licença."
            ),
        }, 409)

    machine.machine_name = machine_name
    machine.last_check_at = now_utc()
    db.session.commit()

    return json_result({
        "ok": True,
        "license": license_payload(lic),
    })


@app.route("/api/deactivate", methods=["POST"])
def api_deactivate():
    """
    Remove somente o computador que está executando o EXE.
    Em uma licença de vários computadores, isso libera apenas uma vaga.
    """
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
        return json_result({
            "ok": False,
            "error": "license_not_found"
        }, 404)

    machine = find_machine(lic, machine_id)

    if not machine:
        return json_result({
            "ok": False,
            "error": "machine_mismatch"
        }, 409)

    db.session.delete(machine)

    # Sincroniza campos antigos.
    db.session.flush()

    remaining = (
        LicenseMachine.query
        .filter_by(license_id=lic.id)
        .order_by(LicenseMachine.id.asc())
        .first()
    )

    if remaining:
        lic.machine_id = remaining.machine_id
        lic.activated_at = remaining.activated_at
        lic.last_check_at = remaining.last_check_at
    else:
        lic.machine_id = None
        lic.activated_at = None
        lic.last_check_at = None

    db.session.commit()

    return json_result({
        "ok": True,
        "message": "Computador removido da licença.",
        "license": license_payload(lic),
    })


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