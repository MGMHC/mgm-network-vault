"""The web blueprint and what every view shares: who is signed in, which sites they may see, permission decorators, template filters and form parsing."""
import re
import secrets
from functools import wraps
from flask import Blueprint, abort, request, session

from . import config, gitsync, theme
from .models import Device, Site, User, db, now
from .version import __version__


bp = Blueprint("web", __name__)


BOM = "\ufeff"  # lets Excel open our UTF-8 CSV files correctly


def current_user():
    """Return the current User ORM object (or None)."""
    uname = session.get("user")
    return User.query.filter_by(username=uname).first() if uname else None


def selected_site_id():
    """Return the active site_id filter, or None if viewing all sites (group view)."""
    u = current_user()
    if not u:
        return None
    if u.is_group_admin:
        sid = session.get("active_site_id")
        if sid == "all" or sid is None:
            return None
        try:
            return int(sid)
        except (ValueError, TypeError):
            return None
    
    allowed = list(u.allowed_site_ids or [])
    if not allowed:
        return None
    if len(allowed) == 1:
        return allowed[0]
    
    sid = session.get("active_site_id")
    if sid == "all":
        return None
    try:
        val = int(sid)
        return val if val in allowed else allowed[0]
    except (ValueError, TypeError):
        return allowed[0]


def _requires(allowed, message):
    """Decorator factory: 403 with message unless allowed(user) for the signed-in user."""
    def decorator(f):
        @wraps(f)
        def wrapper(*a, **kw):
            u = current_user()
            if not u or not allowed(u):
                abort(403, message)
            return f(*a, **kw)
        return wrapper
    return decorator


require_write = _requires(lambda u: u.can_write, "Your account is read-only. Contact an admin.")
require_admin = _requires(lambda u: u.is_admin, "Admin access required.")
require_group_admin = _requires(lambda u: u.is_group_admin, "Group Administrator access required.")


def requested_site_id(data):
    """The one site a request narrows to (403 if the user may not see it), or None for all their sites."""
    raw = data.get("site_id")
    if not raw or raw == "all":
        return None
    try:
        sid = int(raw)
    except (ValueError, TypeError):
        return None
    u = current_user()
    if not u or not u.has_site_access(sid):
        abort(403, "Access to specified site is restricted.")
    return sid


def site_records(query, model, data):
    """IPAM / rack records the user may see: the requested (or active) site's, or all of their sites',
    plus records shared by all sites (site_id NULL)."""
    sid = requested_site_id(data) or selected_site_id()
    if sid:
        return query.filter((model.site_id == sid) | (model.site_id.is_(None)))
    u = current_user()
    if u and not u.is_group_admin:
        return query.filter(model.site_id.in_(u.allowed_site_ids or [-1]) | model.site_id.is_(None))
    return query


def scoped_devices(data):
    """Query of the devices a scan request selects (its site_id and device_ids), within the user's sites."""
    q = site_filter(Device.query, Device)
    sid = requested_site_id(data)
    if sid:
        q = q.filter(Device.site_id == sid)
    picked = data.get("device_ids")
    if isinstance(picked, list) and picked:
        q = q.filter(Device.id.in_([int(i) for i in picked if str(i).isdigit()]))
    return q


def scannable_ids(query):
    """Ids of the enabled devices in query that have SSH credentials."""
    return [d.id for d in query.filter_by(enabled=True).all() if d.credential_id]


def check_site(site_id):
    """403 unless the signed-in user may change records of this site (None: shared by all sites)."""
    u = current_user()
    if not u or not u.has_site_access(site_id):
        abort(403, "Cannot change records belonging to another site.")
    return site_id


def site_from(data):
    """The site a JSON form assigns a record to (None for all sites), once the user may use it."""
    raw = data.get("site_id")
    return check_site(int(raw) if raw and raw != "all" else None)


def check_device_access(device):
    """403 unless the current user may see this device's site."""
    u = current_user()
    if not u or not device or not u.has_site_access(device.site_id):
        abort(403, "Cannot access devices belonging to another site.")


def authorized_device_ids(ids, need_backup=True):
    """Validate a list of device ids from a JSON action: the user must be allowed to trigger
    actions and have site access to every device. Returns the ids of devices that exist."""
    u = current_user()
    if not u or (need_backup and not u.can_backup):
        abort(403, "Your account is read-only. Contact an admin.")
    if not ids:
        return []
    devs = Device.query.filter(Device.id.in_(ids)).all()
    if any(not u.has_site_access(d.site_id) for d in devs):
        abort(403, "Cannot run actions on devices belonging to another site.")
    return [d.id for d in devs]


@bp.app_template_filter("dt")
def fmt_dt(v):
    return v.strftime("%Y-%m-%d %H:%M:%S") if v else "—"


@bp.app_template_filter("ago")
def fmt_ago(v):
    if not v:
        return "never"
    if v.tzinfo:
        v = v.astimezone().replace(tzinfo=None)
    s = (now() - v).total_seconds()
    future = s < 0
    s = abs(s)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            t = f"{int(s // n)}{unit}"
            return f"in {t}" if future else f"{t} ago"
    return "just now" if not future else "in <1m"


@bp.app_template_filter("gh_commit")
def gh_commit(sha):
    return gitsync.commit_url(sha) if gitsync.is_enabled() else None


@bp.app_context_processor
def _gh_helpers():
    return {"gh_device_url": lambda dev_or_name: gitsync.device_url(dev_or_name) if gitsync.is_enabled() else None}


@bp.app_template_filter("size")
def fmt_size(n):
    n = n or 0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def form_bool(name):
    return request.form.get(name) in ("1", "on", "true", "yes")


def form_ids(values):
    return [int(v) for v in values if str(v).strip().isdigit()]


def site_filter(query, model=Device):
    """Filter a query by site: the header's selected site, or all of the user's sites."""
    sid = selected_site_id()
    return query.filter(model.site_id == sid) if sid else _user_sites(query, model)


def _user_sites(query, model):
    u = current_user()
    if u and not u.is_group_admin:
        return query.filter(model.site_id.in_(u.allowed_site_ids or [-1]))
    return query


def report_devices(args):
    """Devices a report covers. An explicit site_id ("all" = all the user's sites) wins over the header's site."""
    if not args.get("site_id"):
        return scoped_devices(args)
    sid = requested_site_id(args)
    q = _user_sites(Device.query, Device)
    return q.filter(Device.site_id == sid) if sid else q


def group_sort_key(name):
    """Sort key for device groups: Level-11 -> Level-1 -> Ground -> Utility -> Distribution -> others -> (No Group)."""
    if not name:
        return (99, 0, "")
    val = name.strip()
    m = re.match(r'^(?:level|floor)[\s\-_]*(\d+)$', val, re.IGNORECASE)
    if m:
        return (0, -int(m.group(1)), "")
    
    vl = val.lower()
    if vl in ("ground", "ground floor", "gf"):
        return (1, 0, "")
    if vl in ("utility", "utilities"):
        return (2, 0, "")
    if vl in ("distribution", "dist", "distribution switch", "distribution switches"):
        return (3, 0, "")
    if vl in ("core", "core switch", "core switches"):
        return (4, 0, "")
    if val.startswith("("):
        return (98, 0, vl)
    return (10, 0, vl)


@bp.app_context_processor
def template_context():
    """What every page template gets: CSRF token, theme, signed-in user, the site switcher's choices."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    user = current_user()
    all_sites = Site.query.order_by(Site.code).all()
    sites = all_sites if not user or user.is_group_admin else [s for s in all_sites if s.id in user.allowed_site_ids]
    sid = selected_site_id() if user else None
    active = db.session.get(Site, sid) if sid else None
    current = active.id if active else ("all" if user and (user.is_group_admin or len(sites) > 1) else None)
    theme_look, theme_mode = theme.resolve(user, request.cookies.get(theme.COOKIE))
    return {
        "theme_look": theme_look,
        "theme_mode": theme_mode,
        "csrf_token": session["csrf"],
        "current_user": session.get("user"),
        "current_user_obj": user,
        "app_version": __version__,
        "all_sites": all_sites,
        "user_available_sites": sites,
        "active_site": active,
        "current_site_id": current,
        "syslog": {
            "enabled": config.get("syslog_enabled"),
            "port": config.get("syslog_port"),
            "debounce_sec": config.get("syslog_debounce_sec"),
            "log_dir": config.get("syslog_log_dir"),
        },
    }
