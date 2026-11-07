"""Administration: GitHub mirror, export/migrate, credentials, settings and updates."""
import threading
from flask import Response, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from . import config, gitsync, mailer, portable, updater
from .models import DEFAULT_SETTINGS, Backup, Credential, Device, Schedule, Site, User, db, get_setting, now, set_setting, log_event
from .scheduler import sync_schedules
from .util import BACKUP_DIR, BASE_DIR, encrypt
from .version import __version__
from .web import bp, current_user, fmt_size, form_bool, require_admin, require_group_admin, require_write, selected_site_id, site_filter


@bp.route("/github", methods=["GET", "POST"])
def github():
    if request.method == "POST":
        # Repo URL, token, secret masking and pushes are global: Group Administrators only.
        me = current_user()
        if not me or not me.is_group_admin:
            abort(403, "Group Administrator access required.")
        action = request.form.get("action")
        app = current_app._get_current_object()
        try:
            if action == "save":
                was_reset = gitsync.save_settings(request.form)
                log_event(f"GitHub settings saved (enabled={get_setting('git_enabled')}, "
                          f"repo={get_setting('git_url')}, branch={get_setting('git_branch')})", "git")
                if gitsync.is_enabled() and get_setting("git_token_enc"):
                    gitsync.full_sync(app, session.get("user"))
                    flash("Settings saved - syncing with GitHub in the background" +
                          (" (repository changed, local copy rebuilt)" if was_reset else ""), "ok")
                else:
                    flash("Settings saved" + ("" if gitsync.is_enabled() else " - GitHub sync is off"), "ok")
            elif action == "test":
                flash(gitsync.test_connection(), "ok")
            elif action == "sync":
                if gitsync.full_sync(app, session.get("user")):
                    flash("Sync started", "ok")
                else:
                    flash("A sync is already running", "error")
            elif action == "push":
                gitsync.push(raise_errors=True)
                flash("Pushed to GitHub", "ok")
            elif action == "reset":
                gitsync.reset_local()
                log_event("Local copy of the GitHub repository discarded - re-downloading", "git", "WARN")
                gitsync.full_sync(app, session.get("user"))
                flash("Local copy discarded - re-downloading from GitHub", "ok")
        except (gitsync.GitError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("web.github"))
    try:
        commits, tags, pending = gitsync.recent_commits(), gitsync.recent_tags(), gitsync.ahead()
        local_error = None
    except gitsync.GitError as e:
        commits, tags, pending, local_error = [], [], 0, str(e)
    s = {k: get_setting(k) for k in DEFAULT_SETTINGS if k.startswith("git_") and k != "git_token_enc"}
    base = gitsync.web_url()
    site_branches = []
    for sc in sorted(gitsync.initialized_sites()):
        site_branches.append({
            "code": sc,
            "branch_url": f"{base}/tree/{sc}" if base else None,
        })
    return render_template("github.html", s=s, has_token=bool(get_setting("git_token_enc")),
                           commits=commits, tags=tags, pending=pending, local_error=local_error,
                           progress=gitsync.progress, web_url=gitsync.web_url(),
                           site_branches=site_branches)


@bp.route("/api/github/progress")
def api_github_progress():
    return jsonify(gitsync.progress)


@bp.route("/migrate")
@require_group_admin
def migrate():
    import platform
    import sys
    usage = sum(p.stat().st_size for p in BACKUP_DIR.rglob("*") if p.is_file())
    info = {
        "Config file": f"{config.CONFIG_FILE}" + ("" if config.CONFIG_FILE.exists() else " (not present - defaults)"),
        "Data folder": str(BASE_DIR),
        "Listening on": f"{config.get('host')}:{config.get('port')}",
        "Behind proxy / secure cookies": f"{config.get('behind_proxy')} / {config.get('secure_cookies')}",
        "Server": f"{platform.node()} - {platform.system()} {platform.release()}",
        "Version": f"MGM Network Vault {__version__}",
        "Python": sys.version.split()[0],
    }
    counts = {"Devices": Device.query.count(), "Backups": Backup.query.count(), "Backup data": fmt_size(usage),
              "Schedules": Schedule.query.count(), "Credential profiles": Credential.query.count(),
              "Users": User.query.count()}
    previous = sorted((p.name for p in BASE_DIR.glob("pre-restore-*") if p.is_dir()), reverse=True)
    return render_template("migrate.html", info=info, counts=counts, pending=portable.pending_restore(BASE_DIR),
                           previous=previous, exported_config=(BASE_DIR / "config.toml.from-export").exists())


@bp.route("/migrate/export", methods=["POST"])
@require_group_admin
def migrate_export():
    pw = request.form.get("passphrase", "")
    if pw != request.form.get("confirm", ""):
        flash("Passphrases do not match", "error")
        return redirect(url_for("web.migrate"))
    tmp = BASE_DIR / "tmp"
    tmp.mkdir(exist_ok=True)
    out = tmp / f"network-vault-export_{now():%Y%m%d-%H%M%S}.nvault"
    try:
        m = portable.export_archive(BASE_DIR, BACKUP_DIR, out, pw, config.CONFIG_FILE)
    except portable.ExportError as e:
        flash(str(e), "error")
        return redirect(url_for("web.migrate"))
    log_event(f"Full export downloaded: {m['counts']['device']} devices, {m['counts']['backup']} backups, "
              f"{out.stat().st_size // 1024} KB", "system", "WARN")

    def stream():
        try:
            with open(out, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            out.unlink(missing_ok=True)

    return Response(stream(), mimetype="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename={out.name}", "Content-Length": str(out.stat().st_size)})


@bp.route("/migrate/import", methods=["POST"])
@require_group_admin
def migrate_import():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose an export file (.nvault)", "error")
        return redirect(url_for("web.migrate"))
    tmp = BASE_DIR / "tmp"
    tmp.mkdir(exist_ok=True)
    upload = tmp / f"upload-{now():%Y%m%d-%H%M%S}.nvault"
    f.save(upload)
    try:
        m = portable.stage_restore(BASE_DIR, upload, request.form.get("passphrase", ""))
        log_event(f"Restore staged from export of {m['source_host']} ({m['created']}): "
                  f"{m['counts']['device']} devices, {m['counts']['backup']} backups - applied on restart",
                  "system", "WARN")
        flash("Export verified. Restart to switch over to the restored data.", "ok")
    except portable.ExportError as e:
        flash(str(e), "error")
    finally:
        upload.unlink(missing_ok=True)
    return redirect(url_for("web.migrate"))


@bp.route("/migrate/cancel", methods=["POST"])
@require_group_admin
def migrate_cancel():
    portable.cancel_restore(BASE_DIR)
    log_event("Staged restore cancelled", "system")
    flash("Staged restore cancelled", "ok")
    return redirect(url_for("web.migrate"))


@bp.route("/migrate/restart", methods=["POST"])
@require_group_admin
def migrate_restart():
    import os
    import time
    log_event("Restart requested from the web UI", "system", "WARN")

    def bye():
        time.sleep(1.5)  # let the response reach the browser
        os._exit(3)      # start.bat / the service task restart the app on exit code 3

    threading.Thread(target=bye, daemon=True).start()
    return render_template("restarting.html")


@bp.route("/migrate/package")
@require_group_admin
def migrate_package():
    log_event("Application package downloaded", "system")
    return Response(portable.app_package(config.APP_ROOT), mimetype="application/zip", headers={
        "Content-Disposition": f"attachment; filename=network-vault-app_v{__version__}.zip"})


@bp.route("/migrate/config-from-export")
@require_group_admin
def migrate_exported_config():
    p = BASE_DIR / "config.toml.from-export"
    if not p.exists():
        abort(404)
    return send_file(p, as_attachment=True, download_name="config.toml.from-export", mimetype="text/plain")


@bp.route("/credentials", methods=["GET", "POST"])
@require_write
def credentials():
    site_id = selected_site_id()
    u = current_user()
    if request.method == "POST":
        cid = request.form.get("id")
        c = db.session.get(Credential, int(cid)) if cid else Credential()
        if cid and not u.has_site_access(c.site_id):
            abort(403, "Cannot edit credentials for another site.")
        c.name = request.form.get("name", "").strip()
        c.username = request.form.get("username", "").strip()
        if request.form.get("password"):
            c.password_enc = encrypt(request.form["password"])
        if request.form.get("secret") or form_bool("clear_secret"):
            c.secret_enc = encrypt(request.form.get("secret", "")) if not form_bool("clear_secret") else None
        if not c.name or not c.username or not c.password_enc:
            db.session.rollback()
            flash("Name, username and password are required", "error")
        else:
            candidate_sid = request.form.get("site_id")
            if candidate_sid and candidate_sid.isdigit():
                target_sid = int(candidate_sid)
                if u.has_site_access(target_sid):
                    c.site_id = target_sid
            elif not cid:
                c.site_id = site_id or (list(u.allowed_site_ids)[0] if (u and u.allowed_site_ids) else None)
                if not c.site_id:
                    first_s = Site.query.order_by(Site.id).first()
                    c.site_id = first_s.id if first_s else None

            if not cid:
                db.session.add(c)
            db.session.commit()
            log_event(f"Credential profile '{c.name}' saved", "system")
            flash("Credential profile saved", "ok")
        return redirect(url_for("web.credentials"))
    q_creds = site_filter(Credential.query, Credential)
    return render_template("credentials.html", creds=q_creds.order_by(Credential.name).all(),
                           edit=db.session.get(Credential, int(request.args.get("edit", 0) or 0)),
                           sites=Site.query.order_by(Site.code).all())


@bp.route("/credentials/<int:cid>/delete", methods=["POST"])
@require_write
def credential_delete(cid):
    c = db.get_or_404(Credential, cid)
    u = current_user()
    if not u.has_site_access(c.site_id):
        abort(403, "Cannot delete credentials for another site.")
    dev_count = c.devices.count() if hasattr(c.devices, 'count') else len(c.devices)
    if dev_count:
        flash(f"Profile is used by {dev_count} device(s) - reassign them first", "error")
    else:
        db.session.delete(c)
        db.session.commit()
        log_event(f"Credential profile '{c.name}' deleted", "system", "WARN")
        flash("Deleted", "ok")
    return redirect(url_for("web.credentials"))


SETTINGS_FORM = {
    "retention_count": "text", "retention_days": "text", "skip_unchanged": "bool", "ping_interval_min": "text",
    "log_retention_days": "text", "max_workers": "text", "ssh_timeout": "text", "read_timeout": "text",
    "mac_history_enabled": "bool", "mac_history_interval_hours": "count", "mac_history_retention_days": "count",
    "smtp_enabled": "bool", "smtp_host": "text", "smtp_port": "text", "smtp_encryption": "text",
    "smtp_user": "text", "smtp_pass_enc": "secret", "smtp_from_name": "text", "smtp_from": "text",
    "app_url": "text",
}


GROUP_ADMIN_SETTINGS = {"mac_history_enabled", "mac_history_interval_hours", "mac_history_retention_days",
                        "smtp_enabled", "smtp_host", "smtp_port", "smtp_encryption", "smtp_user",
                        "smtp_pass_enc", "smtp_from_name", "smtp_from", "app_url"}


@bp.route("/settings", methods=["GET", "POST"])
@require_admin
def settings():
    if request.method == "POST":
        me = current_user()
        for k, kind in SETTINGS_FORM.items():
            if k in GROUP_ADMIN_SETTINGS and not me.is_group_admin:
                continue  # mail server and public URL affect every site's password resets
            if kind == "bool":
                set_setting(k, "1" if form_bool(k) else "0")
            elif kind == "count":
                raw = request.form.get(k, "").strip()
                if raw.isdigit() and int(raw) >= 1:
                    set_setting(k, raw)
                elif raw:
                    flash(f"{k.replace('_', ' ')} must be a whole number of at least 1 - kept the old value", "error")
            elif kind == "secret":
                new_pw = request.form.get("smtp_password", "").strip()
                if new_pw:
                    set_setting(k, encrypt(new_pw))
            elif k in request.form:
                set_setting(k, request.form[k].strip())
        db.session.commit()
        sync_schedules()
        log_event("Settings updated", "system")
        flash("Settings saved", "ok")
        return redirect(url_for("web.settings"))
    usage = sum(p.stat().st_size for p in BACKUP_DIR.rglob("*") if p.is_file())
    s = {k: get_setting(k) for k in DEFAULT_SETTINGS}
    has_smtp_password = bool(get_setting("smtp_pass_enc"))
    return render_template("settings.html", s=s, has_smtp_password=has_smtp_password,
                           backup_dir=BACKUP_DIR, usage=usage, is_git=updater.is_git_installation())


@bp.route("/settings/test-email", methods=["POST"])
@require_group_admin
def settings_test_email():
    target = request.form.get("test_email", "").strip() or get_setting("smtp_from") or DEFAULT_SETTINGS["smtp_from"]
    ok, err = mailer.send_email(
        target,
        "MGM Network Vault - SMTP Test Message",
        f"<p>This is a test email confirming that outbound email delivery through <b>{get_setting('smtp_host')}</b> is functioning properly.</p>",
        "This is a test email confirming that outbound email delivery is functioning properly."
    )
    if ok:
        log_event(f"Test email sent successfully to '{target}'", "system")
        flash(f"Test email successfully sent to {target}!", "ok")
    else:
        log_event(f"Failed to send test email to '{target}': {err}", "system", "WARN")
        flash(f"Failed to send test email: {err}", "error")
    return redirect(url_for("web.settings"))


@bp.route("/api/update/check", methods=["POST"])
@require_group_admin
def api_update_check():
    """Check GitHub repository for software updates."""
    status = updater.check_for_updates(force_fetch=True)
    return jsonify(status)


@bp.route("/api/update/apply", methods=["POST"])
@require_group_admin
def api_update_apply():
    """Pull the latest software update from GitHub and restart the service."""
    res = updater.apply_update()
    if not res.get("ok"):
        return jsonify(res), 500
    return jsonify(res)
