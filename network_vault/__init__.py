"""MGM Network Vault - network switch configuration backup manager."""
import hmac
from datetime import timedelta

from flask import Flask, abort, g, redirect, request, session, url_for
from sqlalchemy import event, pool

from . import config, migrations, portable, theme
from .models import User, db, password_version
from .util import BASE_DIR, FLASK_SECRET

PUBLIC_ENDPOINTS = {
    "web.login", "web.logout", "web.forgot_password", "web.reset_password",
    "web.devices_template", "static"
}


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=FLASK_SECRET,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(BASE_DIR / portable.DB_FILE).as_posix()}",
        SQLALCHEMY_ENGINE_OPTIONS={
            "poolclass": pool.NullPool,
            "connect_args": {"timeout": 30, "check_same_thread": False}
        },
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

        migrations.run(BASE_DIR)

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
            if request.path == "/api/account/theme":
                abort(401, "Not signed in.")
            return redirect(url_for("web.login", next=request.full_path))
        # The session cookie is signed but stateless: re-check that the account still exists and
        # that its password has not changed since this session logged in (deleted users and
        # password changes/resets end every other session).
        u = User.query.filter_by(username=session.get("user")).first()
        if not u or session.get("pwv") != password_version(u):
            session.clear()
            if request.path.startswith("/api/"):
                abort(401, "Session expired - please sign in again.")
            return redirect(url_for("web.login", next=request.full_path))
        g.current_user = u
        if request.method == "POST":
            token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not token or not hmac.compare_digest(str(token), str(session.get("csrf") or "")):
                abort(400, "CSRF token missing or invalid - reload the page and try again.")
        if session.get("must_change") and request.endpoint not in ("web.account", "web.logout"):
            return redirect(url_for("web.account"))
        return None

    @app.after_request
    def static_cache_headers(resp):
        # CSS/JS are linked with ?v=<app version>, so a release changes the URL: let browsers keep
        # them instead of re-checking on every page load. Unversioned files (images) get one day.
        if request.path.startswith("/static/") and resp.status_code in (200, 304):
            resp.cache_control.no_cache = None  # Flask's default; it would force a re-check every time
            resp.cache_control.public = True
            if "v" in request.args:
                resp.cache_control.max_age = 31536000
                resp.cache_control.immutable = True
            else:
                resp.cache_control.max_age = 86400
        return resp

    @app.after_request
    def theme_cookie(resp):
        # Keep the theme cookie in step with a logged-in user's saved choice, so the login page
        # (and a fresh browser tab) can already show it.
        if request.path.startswith("/static/") or not session.get("user"):
            return resp
        if any(c.startswith(theme.COOKIE + "=") for c in resp.headers.getlist("Set-Cookie")):
            return resp
        user = g.get("current_user") or User.query.filter_by(username=session["user"]).first()
        look, mode = theme.resolve(user, request.cookies.get(theme.COOKIE))
        want = theme.cookie_value(look, mode)
        if user and request.cookies.get(theme.COOKIE) != want:
            theme.set_cookie(resp, look, mode, secure=bool(config.get("secure_cookies")) or request.is_secure)
        return resp

    return app
