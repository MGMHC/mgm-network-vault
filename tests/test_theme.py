"""Per-user look/mode theme: API, cookie, rendering and fallbacks."""
import pytest

from conftest import PASSWORD, page_attr


def theme_of(client, path="/", **kw):
    html = client.get(path, **kw).get_data(as_text=True)
    return page_attr(html, "data-look"), page_attr(html, "data-mode")


def post_theme(client, body, token=True, **kw):
    return client.post("/api/account/theme", json=body, headers=client.csrf_headers if token else {}, **kw)


def theme_cookie_lines(resp):
    return [h for h in resp.headers.getlist("Set-Cookie") if h.startswith("nv_theme=")]


@pytest.fixture()
def clean_theme(app, seed):
    """Reset saved themes before and after, so tests do not leak into each other."""
    from network_vault.models import User, db

    def reset():
        with app.app_context():
            for u in User.query.all():
                u.theme_look = u.theme_mode = None
            db.session.commit()
    reset()
    yield
    reset()


@pytest.fixture()
def plain_login(app, seed):
    def make(username="t_admin", cookie=None):
        c = app.test_client()
        if cookie:
            c.set_cookie("nv_theme", cookie)
        if username:
            assert c.post("/login", data={"username": username, "password": PASSWORD}).status_code == 302
        return c
    return make


def test_default_is_glass_dark(admin, clean_theme):
    assert theme_of(admin) == ("glass", "dark")


def test_save_and_render(admin, clean_theme):
    r = post_theme(admin, {"look": "normal", "mode": "light"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "look": "normal", "mode": "light"}
    assert theme_of(admin) == ("normal", "light")
    assert "normal-light" in admin.get_cookie("nv_theme").value


def test_saved_theme_follows_user_to_other_client(admin, clean_theme, plain_login):
    post_theme(admin, {"look": "normal", "mode": "light"})
    assert theme_of(plain_login()) == ("normal", "light")


@pytest.mark.parametrize("body", [{"look": "x", "mode": "light"}, {"look": "glass", "mode": "x"}, {}])
def test_bad_values_400_and_unchanged(admin, clean_theme, body):
    post_theme(admin, {"look": "normal", "mode": "light"})
    r = post_theme(admin, body)
    assert r.status_code == 400 and "error" in r.get_json()
    assert theme_of(admin) == ("normal", "light")


@pytest.mark.parametrize("body", [[1, 2], "glass-dark", 5, None], ids=["list", "string", "number", "null"])
def test_non_dict_body_is_400(admin, clean_theme, body):
    assert post_theme(admin, body).status_code == 400


def test_bad_cookie_falls_back(plain_login, clean_theme):
    assert theme_of(plain_login(None, cookie="foo"), "/login") == ("glass", "dark")


def test_valid_cookie_used_on_login_page(plain_login, clean_theme):
    assert theme_of(plain_login(None, cookie="normal-light"), "/login") == ("normal", "light")


def test_bad_saved_value_falls_back(app, plain_login, clean_theme):
    from network_vault.models import User, db
    with app.app_context():
        u = User.query.filter_by(username="t_admin").one()
        u.theme_look, u.theme_mode = "bogus", "dark"
        db.session.commit()
    assert theme_of(plain_login()) == ("glass", "dark")


def test_read_only_user_may_save_theme(readonly, clean_theme):
    assert post_theme(readonly, {"look": "glass", "mode": "light"}).status_code == 200


def test_csrf_required(admin, clean_theme):
    assert post_theme(admin, {"look": "glass", "mode": "dark"}, token=False).status_code in (400, 403)


def test_http_cookie_is_not_secure(admin, clean_theme):
    lines = theme_cookie_lines(post_theme(admin, {"look": "glass", "mode": "dark"}))
    assert lines and "Secure" not in lines[0]


def test_https_cookie_is_secure(admin, clean_theme):
    r = post_theme(admin, {"look": "normal", "mode": "dark"}, base_url="https://localhost")
    lines = theme_cookie_lines(r)
    assert lines and "Secure" in lines[0]


def test_theme_module_resolve():
    from types import SimpleNamespace as N
    from network_vault import theme
    assert theme.resolve(None, None) == theme.DEFAULT
    assert theme.resolve(None, "normal-light") == ("normal", "light")
    assert theme.resolve(None, "normal") == theme.DEFAULT
    assert theme.resolve(N(theme_look="normal", theme_mode="dark"), "glass-light") == ("normal", "dark")
    assert theme.resolve(N(theme_look="bad", theme_mode="dark"), "glass-light") == ("glass", "light")
    assert theme.valid("glass", "dark") and not theme.valid("glass", "blue")
