"""Sign-in, password reset, the account page, and site management/switching."""
import hmac
from datetime import timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_

from . import config, mailer, theme
from .models import USER_ROLES, LogEntry, Site, User, db, get_setting, now, password_version, log_event
from .web import bp, current_user, require_group_admin


MAX_FAILED_LOGINS = 10   # per username within LOGIN_LOCK_MINUTES, then sign-in is refused
LOGIN_LOCK_MINUTES = 15


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        failed_msg = f"Failed login for '{name[:64]}'"
        recent = LogEntry.query.filter(LogEntry.category == "auth", LogEntry.message == failed_msg,
                                       LogEntry.ts >= now() - timedelta(minutes=LOGIN_LOCK_MINUTES)).count()
        if recent >= MAX_FAILED_LOGINS:
            flash(f"Too many failed sign-ins for this account. Try again in {LOGIN_LOCK_MINUTES} minutes.", "error")
            return render_template("login.html")
        u = User.query.filter_by(username=name).first()
        if u and u.check_password(request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["user"] = u.username
            session["must_change"] = bool(u.must_change)
            session["pwv"] = password_version(u)
            u.last_login_at = now()
            db.session.commit()
            log_event("User logged in", "auth", user=u.username, details=request.remote_addr)
            nxt = request.args.get("next") or ""
            # browsers read "/\host" and "/<tab>/host" as "//host": allow only a plain local path
            local = nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and nxt.isprintable()
            return redirect(nxt if local else url_for("web.dashboard"))
        log_event(failed_msg, "auth", "WARN", user="-", details=request.remote_addr)
        flash("Invalid username or password", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    # public endpoint, so the global CSRF check skips it: a link or <img> elsewhere must not sign users out
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or not hmac.compare_digest(str(token), str(session.get("csrf") or "")):
        abort(400, "CSRF token missing or invalid - reload the page and try again.")
    log_event("User logged out", "auth")
    session.clear()
    return redirect(url_for("web.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        ident = request.form.get("identity", "").strip()
        if not ident:
            flash("Please enter your username or registered email address.", "error")
            return render_template("forgot_password.html")

        u = User.query.filter(
            or_(
                func.lower(User.username) == ident.lower(),
                func.lower(User.email) == ident.lower()
            )
        ).first()

        # The reset link must point at the configured public URL. Never build it from the request's
        # Host / X-Forwarded-Host header: an attacker controls that and could steal the token.
        base_url = (get_setting("app_url") or "").strip().rstrip("/")
        if u and u.email and not base_url:
            log_event("Password reset email not sent: 'Application Public Web URL' is not configured in Settings",
                      "auth", "WARN", user=u.username, details=request.remote_addr)
        elif u and u.email:
            ok, err = mailer.send_password_reset_email(u, base_url)
            if ok:
                log_event(f"Password reset link emailed to '{u.email}' for user '{u.username}'", "auth",
                          user=u.username, details=request.remote_addr)
            else:
                log_event(f"Failed to send password reset email to '{u.email}': {err}", "auth", "WARN",
                          user=u.username, details=request.remote_addr)
        else:
            log_event(f"Password reset requested for unknown or email-less identity '{ident}'", "auth", "WARN",
                      user="-", details=request.remote_addr)

        # Uniform security message to prevent user enumeration
        flash("If an account matching that username or email exists, a password reset link has been sent to the registered email address. Please check your inbox.", "ok")
        return redirect(url_for("web.login"))

    return render_template("forgot_password.html")


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token") or request.form.get("token", "")
    if not token:
        flash("Password reset link is invalid or missing.", "error")
        return redirect(url_for("web.forgot_password"))

    user, err = mailer.verify_reset_token(token)
    if not user:
        flash(err or "Password reset link is invalid or has expired.", "error")
        return redirect(url_for("web.forgot_password"))

    if request.method == "POST":
        pw = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(pw) < 8:
            flash("New password must be at least 8 characters long.", "error")
            return render_template("reset_password.html", user=user, token=token)
        if pw != confirm:
            flash("Passwords do not match. Please re-enter.", "error")
            return render_template("reset_password.html", user=user, token=token)

        user.set_password(pw)
        user.must_change = False
        db.session.commit()
        log_event(f"Password successfully reset via email link for '{user.username}'", "auth",
                  user=user.username, details=request.remote_addr)
        flash("Your password has been successfully reset! Please sign in with your new password.", "ok")
        return redirect(url_for("web.login"))

    return render_template("reset_password.html", user=user, token=token)


@bp.route("/site/switch", methods=["POST"])
def site_switch():
    sid = request.form.get("site_id", "all")
    u = current_user()
    if not u:
        return redirect(url_for("web.login"))
    if u.is_group_admin:
        if sid == "all" or not sid:
            session["active_site_id"] = "all"
        else:
            try:
                s = db.session.get(Site, int(sid))
                session["active_site_id"] = s.id if s else "all"
            except (ValueError, TypeError):
                session["active_site_id"] = "all"
    else:
        allowed = u.allowed_site_ids or []
        if len(allowed) <= 1:
            session["active_site_id"] = allowed[0] if allowed else "all"
        else:
            if sid == "all":
                session["active_site_id"] = "all"
            else:
                try:
                    chosen = int(sid)
                    session["active_site_id"] = chosen if chosen in allowed else allowed[0]
                except (ValueError, TypeError):
                    session["active_site_id"] = allowed[0]
    nxt = request.referrer or url_for("web.dashboard")
    return redirect(nxt)


@bp.route("/sites", methods=["GET", "POST"])
@require_group_admin
def sites():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save":
            sid = request.form.get("id")
            s = db.session.get(Site, int(sid)) if sid else Site()
            code = request.form.get("code", "").strip().upper()
            name = request.form.get("name", "").strip()
            desc = request.form.get("description", "").strip()
            clash = Site.query.filter(Site.code == code, Site.id != (s.id or 0)).first()
            if not code or not name:
                flash("Site code and site name are required", "error")
            elif clash:
                flash(f"A site with code '{code}' already exists", "error")
            else:
                s.code = code
                s.name = name
                s.description = desc
                if not sid:
                    db.session.add(s)
                db.session.commit()
                log_event(f"Site '{s.code}' ({s.name}) saved", "system")
                flash(f"Site '{s.code}' saved", "ok")
                return redirect(url_for("web.sites"))
        elif action == "delete":
            sid = request.form.get("id")
            s = db.session.get(Site, int(sid)) if sid else None
            if s:
                dev_count = s.devices.count()
                if dev_count > 0:
                    flash(f"Cannot delete site '{s.code}' because it has {dev_count} device(s). Reassign or delete them first.", "error")
                else:
                    code = s.code
                    db.session.delete(s)
                    db.session.commit()
                    log_event(f"Site '{code}' deleted", "system", "WARN")
                    flash(f"Site '{code}' deleted", "ok")
            return redirect(url_for("web.sites"))
    edit_site = db.session.get(Site, int(request.args.get("edit", 0) or 0))
    all_sites_list = Site.query.order_by(Site.code).all()
    site_stats = {}
    for s in all_sites_list:
        site_stats[s.id] = {
            "devices": s.devices.count(),
            "creds": s.credentials.count(),
            "schedules": s.schedules.count(),
        }
    return render_template("sites.html", sites=all_sites_list, edit=edit_site, stats=site_stats)


def _site_admin_can_manage(me, u):
    """A site admin may manage a user only if that user is not a group admin, belongs to at least
    one site, and every site they belong to is one of the site admin's own sites."""
    if u.is_group_admin:
        return False
    u_sites = set(u.allowed_site_ids or [])
    my_sites = set(me.allowed_site_ids or [])
    return bool(u_sites) and u_sites.issubset(my_sites)


@bp.route("/api/account/theme", methods=["POST"])
def account_theme():
    """Save the signed-in user's look and mode (any role may; it is a personal display setting)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    look, mode = data.get("look"), data.get("mode")
    if not theme.valid(look, mode):
        return jsonify(error="Invalid theme."), 400
    me = current_user()
    me.theme_look, me.theme_mode = look, mode
    db.session.commit()
    resp = jsonify(ok=True, look=look, mode=mode)
    theme.set_cookie(resp, look, mode, secure=bool(config.get("secure_cookies")) or request.is_secure)
    return resp


@bp.route("/account", methods=["GET", "POST"])
def account():
    me = User.query.filter_by(username=session["user"]).first_or_404()
    if request.method == "POST":
        action = request.form.get("action")
        if session.get("must_change") and action != "password":
            abort(403, "Change your password before doing anything else.")
        if action == "password":
            if not me.check_password(request.form.get("current", "")):
                flash("Current password is wrong", "error")
            elif len(request.form.get("new", "")) < 8:
                flash("New password must be at least 8 characters", "error")
            elif request.form.get("new") != request.form.get("confirm"):
                flash("Passwords do not match", "error")
            else:
                me.set_password(request.form["new"])
                me.must_change = False
                session["must_change"] = False
                db.session.commit()
                session["pwv"] = password_version(me)  # keep this session; others are signed out
                log_event("Password changed", "auth")
                flash("Password changed", "ok")
                return redirect(url_for("web.dashboard"))
        elif action == "adduser":
            if not me.is_admin:
                abort(403, "Admin access required.")
            name = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            role = request.form.get("role", "read-write")
            
            selected_site_ids = []
            if me.is_group_admin:
                sids_raw = request.form.getlist("site_ids")
                if not sids_raw and request.form.get("site_id"):
                    sids_raw = [request.form.get("site_id")]
                for sid in sids_raw:
                    try:
                        selected_site_ids.append(int(sid))
                    except (ValueError, TypeError):
                        pass
            else:
                if role == "group-admin":
                    role = "site-admin"
                selected_site_ids = me.allowed_site_ids or ([me.site_id] if me.site_id else [])

            if role not in USER_ROLES:
                role = "read-write"
            if not name or len(pw) < 8:
                flash("Username required and password must be at least 8 characters", "error")
            elif User.query.filter_by(username=name).first():
                flash("User already exists", "error")
            else:
                email = request.form.get("email", "").strip() or None
                primary_site = selected_site_ids[0] if selected_site_ids else None
                u = User(username=name, email=email, must_change=True, role=role, site_id=primary_site)
                u.set_password(pw)
                if selected_site_ids and role != "group-admin":
                    u.assigned_sites = Site.query.filter(Site.id.in_(selected_site_ids)).all()
                db.session.add(u)
                db.session.commit()
                log_event(f"User '{name}' created with role '{role}'", "auth")
                flash(f"User {name} created (must change password at first login)", "ok")
        elif action == "setrole":
            if not me.is_admin:
                abort(403, "Admin access required.")
            u = db.session.get(User, int(request.form.get("id", 0)))
            new_role = request.form.get("role", "read-write")
            if u and u.username != me.username and new_role in USER_ROLES:
                if not me.is_group_admin:
                    if not _site_admin_can_manage(me, u) or new_role == "group-admin":
                        abort(403, "Cannot modify users outside your assigned site(s).")

                u.role = new_role
                if "email" in request.form:
                    u.email = request.form.get("email", "").strip() or None
                if me.is_group_admin:
                    if new_role == "group-admin":
                        u.site_id = None
                        u.assigned_sites = []
                    else:
                        sids_raw = request.form.getlist("site_ids")
                        if not sids_raw and request.form.get("site_id"):
                            sids_raw = [request.form.get("site_id")]
                        chosen_ids = []
                        for sid in sids_raw:
                            try:
                                chosen_ids.append(int(sid))
                            except (ValueError, TypeError):
                                pass
                        if chosen_ids:
                            u.assigned_sites = Site.query.filter(Site.id.in_(chosen_ids)).all()
                            u.site_id = chosen_ids[0]
                        else:
                            u.assigned_sites = []
                            u.site_id = None
                db.session.commit()
                log_event(f"User '{u.username}' updated (role='{new_role}')", "auth")
                flash(f"User updated for {u.username}", "ok")
        elif action == "deluser":
            if not me.is_admin:
                abort(403, "Admin access required.")
            u = db.session.get(User, int(request.form.get("id", 0)))
            if u and u.username != me.username:
                if not me.is_group_admin and not _site_admin_can_manage(me, u):
                    abort(403, "Cannot delete users outside your assigned site(s).")
                db.session.delete(u)
                db.session.commit()
                log_event(f"User '{u.username}' deleted", "auth")
                flash("User deleted", "ok")
        elif action == "resetpassword":
            if not me.is_admin:
                abort(403, "Admin access required.")
            u = db.session.get(User, int(request.form.get("id", 0)))
            new_pw = request.form.get("password", "").strip()
            require_change = request.form.get("must_change") == "1"
            if not u:
                flash("User not found", "error")
            elif not new_pw or len(new_pw) < 8:
                flash("Password must be at least 8 characters", "error")
            else:
                if not me.is_group_admin:
                    if u.is_group_admin:
                        abort(403, "Site administrators cannot reset passwords for group administrators.")
                    if not _site_admin_can_manage(me, u):
                        abort(403, "Cannot reset password for users outside your assigned site(s).")
                u.set_password(new_pw)
                u.must_change = require_change
                db.session.commit()
                log_event(f"Password for user '{u.username}' reset by '{me.username}'", "auth")
                flash(f"Password for '{u.username}' successfully reset" + (" (must change on next login)" if require_change else ""), "ok")
        return redirect(url_for("web.account"))
    
    users = User.query.order_by(User.username).all()
    if not me.is_group_admin:
        my_sites = set(me.allowed_site_ids or ([me.site_id] if me.site_id else []))
        users = [u for u in users if not u.is_group_admin and (
            any(s_id in my_sites for s_id in (u.allowed_site_ids or [])) or
            (u.site_id in my_sites)
        )]
    return render_template("account.html", users=users, me=me,
                           USER_ROLES=USER_ROLES, sites=Site.query.order_by(Site.code).all())
