"""按账号代理（fork:proxy）的核心逻辑单测。

只覆盖纯逻辑与参数装配，不真的连代理：连接行为由部署环境决定，
放在这里会让 CI 依赖外网。
"""

import asyncio
import sys
import types

import pytest

from utils import proxy_utils
from utils.proxy_utils import (
    ProxyConfigError,
    apply_default_proxy,
    create_aiohttp_session,
    get_cookie_proxy,
    invalidate_proxy_cache,
    is_socks_proxy,
    mask_proxy_url,
    open_websocket,
    playwright_proxy,
    validate_proxy_url,
)


@pytest.fixture(autouse=True)
def _clear_proxy_cache():
    invalidate_proxy_cache()
    yield
    invalidate_proxy_cache()


class _FakeDB:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def get_cookie_proxy(self, cookie_id):
        self.calls += 1
        return self.value


def _install_fake_db(monkeypatch, value):
    db = _FakeDB(value)
    module = types.ModuleType("app.db_manager")
    module.db_manager = db
    monkeypatch.setitem(sys.modules, "app.db_manager", module)
    return db


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", ""),
        ("   ", ""),
        ("127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("http://127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("https://proxy.example.com:443", "https://proxy.example.com:443"),
        ("http://user:pass@1.2.3.4:8080", "http://user:pass@1.2.3.4:8080"),
        ("socks5://1.2.3.4:1080", "socks5://1.2.3.4:1080"),
        ("socks5h://1.2.3.4:1080", "socks5h://1.2.3.4:1080"),
        ("http://host:8080/", "http://host:8080/"),
    ],
)
def test_validate_proxy_url_accepts(raw, expected):
    assert validate_proxy_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://host:21",
        "http://host",              # 缺端口
        "http://host:99999",        # 端口越界
        "http://host:abc",          # 端口非数字
        "http://host:8080/path",    # 不允许路径
        "http://host:8080?a=1",     # 不允许 query
        "http://ho st:8080",        # 中间有空格
        "socks5://user:pass@host:1080",  # Chromium 不支持认证 socks
    ],
)
def test_validate_proxy_url_rejects(raw):
    with pytest.raises(ProxyConfigError):
        validate_proxy_url(raw)


def test_is_socks_proxy():
    assert is_socks_proxy("socks5://h:1080")
    assert is_socks_proxy("socks5h://h:1080")
    assert is_socks_proxy("socks4://h:1080")
    assert not is_socks_proxy("http://h:8080")
    assert not is_socks_proxy("")


def test_mask_proxy_url_hides_credentials():
    assert mask_proxy_url("") == "直连"
    assert mask_proxy_url("http://u:secret@h:8080") == "http://u:***@h:8080"
    assert mask_proxy_url("socks5://h:1080") == "socks5://h:1080"
    assert "secret" not in mask_proxy_url("http://u:secret@h:8080")


# ---------------------------------------------------------------------------
# Playwright / 请求参数装配
# ---------------------------------------------------------------------------

def test_playwright_proxy_none_when_direct():
    assert playwright_proxy("") is None
    assert playwright_proxy(None) is None


def test_playwright_proxy_http_with_auth():
    options = playwright_proxy("http://user:p%40ss@1.2.3.4:8080")
    assert options == {
        "server": "http://1.2.3.4:8080",
        "username": "user",
        "password": "p@ss",
    }


def test_playwright_proxy_socks_without_auth():
    options = playwright_proxy("socks5://1.2.3.4:1080")
    assert options == {"server": "socks5://1.2.3.4:1080"}


def test_apply_default_proxy_does_not_override_explicit():
    kwargs = {}
    assert apply_default_proxy(kwargs, "http://h:1") == {"proxy": "http://h:1"}

    explicit = {"proxy": "http://other:2"}
    apply_default_proxy(explicit, "http://h:1")
    assert explicit["proxy"] == "http://other:2"


# ---------------------------------------------------------------------------
# aiohttp 会话
# ---------------------------------------------------------------------------

def test_create_aiohttp_session_direct_has_no_hook():
    async def scenario():
        session = create_aiohttp_session("")
        try:
            assert not hasattr(session, "_xianyu_original_request")
        finally:
            await session.close()

    asyncio.run(scenario())


def test_create_aiohttp_session_http_proxy_installs_hook():
    async def scenario():
        session = create_aiohttp_session("http://127.0.0.1:7890")
        try:
            assert hasattr(session, "_xianyu_original_request")
        finally:
            await session.close()

    asyncio.run(scenario())


def test_create_aiohttp_session_socks_uses_proxy_connector():
    async def scenario():
        session = create_aiohttp_session("socks5://127.0.0.1:1080")
        try:
            connector = session.connector
            assert connector is not None
            # aiohttp-socks 的 connector 类名带 Proxy
            assert "Proxy" in type(connector).__name__
        finally:
            await session.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 账号 → 代理 解析
# ---------------------------------------------------------------------------

def test_get_cookie_proxy_direct(monkeypatch):
    _install_fake_db(monkeypatch, "")
    assert get_cookie_proxy("acc-1") == ""


def test_get_cookie_proxy_returns_url(monkeypatch):
    _install_fake_db(monkeypatch, "http://127.0.0.1:7890")
    assert get_cookie_proxy("acc-1") == "http://127.0.0.1:7890"


@pytest.mark.parametrize("marker", ["__missing__", "__disabled__"])
def test_get_cookie_proxy_raises_when_binding_broken(monkeypatch, marker):
    _install_fake_db(monkeypatch, marker)
    with pytest.raises(ProxyConfigError):
        get_cookie_proxy("acc-1")


def test_get_cookie_proxy_raises_on_db_error(monkeypatch):
    class _Broken:
        def get_cookie_proxy(self, cookie_id):
            raise RuntimeError("db down")

    module = types.ModuleType("app.db_manager")
    module.db_manager = _Broken()
    monkeypatch.setitem(sys.modules, "app.db_manager", module)

    with pytest.raises(ProxyConfigError):
        get_cookie_proxy("acc-1")


def test_get_cookie_proxy_cached_and_invalidated(monkeypatch):
    db = _install_fake_db(monkeypatch, "http://a:1")
    assert get_cookie_proxy("acc-1") == "http://a:1"
    assert get_cookie_proxy("acc-1") == "http://a:1"
    assert db.calls == 1  # 第二次命中缓存

    invalidate_proxy_cache("acc-1")
    db.value = "http://b:2"
    assert get_cookie_proxy("acc-1") == "http://b:2"
    assert db.calls == 2


# ---------------------------------------------------------------------------
# WebSocket 参数装配（不真正建立连接）
# ---------------------------------------------------------------------------

def test_open_websocket_direct_returns_connect_object():
    conn = asyncio.run(open_websocket("wss://example.com/ws", {"Cookie": "a=1"}))
    # websockets<14 返回 Connect 对象，await 才真正连接
    assert conn is not None


def test_websockets_major_version_detected():
    assert isinstance(proxy_utils._websockets_major_version(), int)
