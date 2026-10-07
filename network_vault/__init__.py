"""MGM Network Vault - network switch configuration backup manager."""
import secrets
from datetime import timedelta

from flask import Flask, abort, redirect, request, session, url_for
from sqlalchemy import event

from . import config
from .models import User, db
from .util import BASE_DIR, FLASK_SECRET, RESTORED, log_event
from .version import __version__

PUBLIC_ENDPOINTS = {"web.login", "web.logout", "web.devices_template", "static"}


def _add_missing_columns():
    """Tiny migration: add columns introduced by newer versions to an existing SQLite database."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    with db.engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    ddl = col.type.compile(dialect=db.engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
        # Backfill: any user with NULL role gets 'admin' (pre-existing accounts)
        conn.execute(text("UPDATE \"user\" SET role = 'admin' WHERE role IS NULL OR role = ''"))


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=FLASK_SECRET,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(BASE_DIR / 'network_vault.db').as_posix()}",
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 30, "check_same_thread": False}},
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=4 * 1024 ** 3,  # export files can be large; CSV import checks its own size
        TEMPLATES_AUTO_RELOAD=True,  # pick up template edits without a restart
        SESSION_COOKIE_SECURE=config.get("secure_cookies"),
    )
    db.init_app(app)

    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        db.create_all()
        _add_missing_columns()
        if not User.query.first():
            u = User(username="admin", must_change=True)
            u.set_password("admin")
            db.session.add(u)
            db.session.commit()
            log_event("Created default user 'admin' (password 'admin' - change on first login)", "auth", user="system")
        if RESTORED:
            m = RESTORED
            log_event(f"Restored from export of {m.get('source_host')} created {m.get('created')}: "
                      f"{m['counts'].get('device', 0)} devices, {m['counts'].get('backup', 0)} backups. "
                      f"Previous data kept in {m.get('previous_data_kept_in')}", "system", "WARN", user="system")
            (BASE_DIR / "restored.json").unlink(missing_ok=True)

    if config.get("behind_proxy"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    from .views import bp
    app.register_blueprint(bp)

    @app.before_request
    def guard():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if "user" not in session:
            return redirect(url_for("web.login", next=request.full_path))
        if request.method == "POST":
            token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not token or token != session.get("csrf"):
                abort(400, "CSRF token missing or invalid - reload the page and try again.")
        if session.get("must_change") and request.endpoint not in ("web.account", "web.logout"):
            return redirect(url_for("web.account"))
        return None

    @app.context_processor
    def inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        uname = session.get("user")
        user_obj = User.query.filter_by(username=uname).first() if uname else None
        return {"csrf_token": session["csrf"], "current_user": uname,
                "current_user_obj": user_obj, "app_version": __version__}

    return app
