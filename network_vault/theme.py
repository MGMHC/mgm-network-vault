"""Per-user UI theme: a look (glass | normal) and a mode (dark | light)."""

LOOKS = ("glass", "normal")
MODES = ("dark", "light")
DEFAULT = ("glass", "dark")
COOKIE = "nv_theme"


def valid(look, mode):
    return look in LOOKS and mode in MODES


def cookie_value(look, mode):
    return f"{look}-{mode}"


def resolve(user, cookie_value_raw):
    """The user's saved theme, else the cookie's, else the default."""
    if user is not None and valid(getattr(user, "theme_look", None), getattr(user, "theme_mode", None)):
        return user.theme_look, user.theme_mode
    look, _, mode = (cookie_value_raw or "").partition("-")
    if valid(look, mode):
        return look, mode
    return DEFAULT


def set_cookie(resp, look, mode, secure):
    """Mirror the theme into a cookie that app.js and the login page can read (no secret in it)."""
    resp.set_cookie(COOKIE, cookie_value(look, mode), max_age=365 * 24 * 3600, samesite="Lax", path="/",
                    httponly=False, secure=secure)
