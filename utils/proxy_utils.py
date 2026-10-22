"""按账号出站代理。

每个闲鱼账号可以单独配置一个代理，让该账号的 WebSocket、HTTP 接口和浏览器
流量都从同一个出口 IP 出去。多账号同机部署时，这是把「一个 IP 挂着 N 个号」
拆成「一账号一 IP」的唯一办法。

支持的代理写法::

    http://127.0.0.1:7890
    http://user:pass@host:8080
    socks5://user:pass@host:1080      # 远程 DNS
    socks5h://host:1080               # 同上，显式写法
    socks4://host:1080

设计上的两条硬约束：

1. **绝不静默回退直连**。配置了代理却连不上时，宁可让账号连不上并打出明确
   日志，也不能悄悄走本机 IP —— 一次泄漏就足以让平台把几个号关联到一起。
2. **一处漏网等于没有**。账号的出站流量分散在 WebSocket、aiohttp、浏览器三处，
   新增网络调用时必须走本模块，否则那条请求会带着真实 IP 出去。

依赖：``python-socks[asyncio]``（WebSocket 隧道），socks5 的 HTTP 请求额外
需要 ``aiohttp-socks``。两者都是可选依赖，只用 HTTP 代理时不装也能跑。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from loguru import logger

# 允许的代理协议。socks5h 与 socks5 等价（python-socks 默认远端解析域名）。
ALLOWED_PROXY_SCHEMES = {"http", "https", "socks4", "socks5", "socks5h"}

# 建立代理隧道 / 自检的超时（秒）
PROXY_CONNECT_TIMEOUT = 15.0


class ProxyConfigError(ValueError):
    """代理配置不合法，或当前环境缺少对应依赖。"""


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------

def validate_proxy_url(raw: Optional[str]) -> str:
    """校验并规范化代理地址，空串表示不使用代理。

    Raises:
        ProxyConfigError: 地址不合法。保存设置时应当直接把这个错误抛给用户，
            而不是存进数据库等运行期才炸。
    """
    text = (raw or "").strip()
    if not text:
        return ""

    if any(ch in text for ch in " \t\r\n"):
        raise ProxyConfigError("代理地址不能包含空格或换行")

    if "://" not in text:
        # 允许只填 host:port，默认按 HTTP 代理处理
        text = "http://" + text

    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_PROXY_SCHEMES:
        raise ProxyConfigError(
            f"不支持的代理协议 {scheme!r}，仅支持 "
            + "、".join(sorted(ALLOWED_PROXY_SCHEMES))
        )
    if not parsed.hostname:
        raise ProxyConfigError("代理地址缺少主机名")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProxyConfigError("代理端口不是合法数字") from exc
    if port is None:
        raise ProxyConfigError("代理地址必须带端口，例如 http://127.0.0.1:7890")
    if not (0 < port < 65536):
        raise ProxyConfigError("代理端口必须在 1-65535 之间")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ProxyConfigError("代理地址只能是 scheme://[user:pass@]host:port")

    # Chromium 不支持带认证的 SOCKS。既然要求「所有操作都走代理」，就必须在
    # 保存时拦住这种配置 —— 否则浏览器链路会静默失去认证、或者干脆连不上。
    if scheme.startswith("socks") and (parsed.username or parsed.password):
        raise ProxyConfigError(
            "Chromium 不支持带账号密码的 SOCKS 代理，浏览器操作无法走该出口；"
            "请改用 HTTP 代理，或使用免认证的 SOCKS5"
        )
    return text


def is_socks_proxy(proxy_url: Optional[str]) -> bool:
    if not proxy_url:
        return False
    return urlparse(proxy_url).scheme.lower().startswith("socks")


def _for_socks_client(proxy_url: str) -> str:
    """python-socks / aiohttp-socks 不认 socks5h，映射成 socks5（两者都远端解析）。"""
    if proxy_url.lower().startswith("socks5h://"):
        return "socks5://" + proxy_url[len("socks5h://"):]
    return proxy_url


def mask_proxy_url(proxy_url: Optional[str]) -> str:
    """日志用：隐藏代理账号密码。"""
    text = (proxy_url or "").strip()
    if not text:
        return "直连"
    if "://" not in text:
        return text
    try:
        parsed = urlparse(text)
    except Exception:
        return "***"
    auth = ""
    if parsed.username:
        auth = parsed.username + ":***@"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{auth}{host}{port}"


# ---------------------------------------------------------------------------
# 账号 → 代理 的读取
# ---------------------------------------------------------------------------

_PROXY_CACHE_TTL = 30.0
_proxy_cache: Dict[str, tuple] = {}


def get_cookie_proxy(cookie_id: str) -> str:
    """读取账号配置的代理；空串表示直连。

    配置损坏时抛 ``ProxyConfigError`` 而不是退回直连 —— 静默直连等于泄漏真实
    IP，宁可让这个账号起不来并留下明确的错误日志。

    加了一层短 TTL 缓存：``XianyuSellerAPI`` 之类的对象会在一次业务里被反复
    创建，每次都打一次 SQLite 没必要。设置变更时调用 ``invalidate_proxy_cache``。
    """
    if not cookie_id:
        return ""

    cached = _proxy_cache.get(cookie_id)
    if cached and (time.monotonic() - cached[1]) < _PROXY_CACHE_TTL:
        return cached[0]

    try:
        from app.db_manager import db_manager

        raw = db_manager.get_cookie_proxy(cookie_id) or ""
    except Exception as exc:
        # 读不到配置就无法判断这个号该不该走代理。按严格模式处理：直接失败，
        # 不假设「没配代理」——假设错了就是把真实 IP 放出去。
        raise ProxyConfigError(
            f"账号 {cookie_id} 的代理配置读取失败，无法安全出网: {exc}"
        ) from exc

    # 账号在代理库里选了代理，但该代理被删或停用：这属于配置故障，必须报错。
    # 如果这里返回空串，上层会当成「直连」，真实 IP 就直接出去了。
    if raw == "__missing__":
        raise ProxyConfigError(
            f"账号 {cookie_id} 绑定的代理已不存在，请到「代理管理」重新选择或改为直连"
        )
    if raw == "__disabled__":
        raise ProxyConfigError(
            f"账号 {cookie_id} 绑定的代理已被停用，请启用该代理或改为直连"
        )

    value = validate_proxy_url(raw)
    _proxy_cache[cookie_id] = (value, time.monotonic())
    return value


def invalidate_proxy_cache(cookie_id: Optional[str] = None) -> None:
    if cookie_id is None:
        _proxy_cache.clear()
    else:
        _proxy_cache.pop(cookie_id, None)


# ---------------------------------------------------------------------------
# HTTP 会话
# ---------------------------------------------------------------------------

def create_aiohttp_session(
    proxy_url: Optional[str] = None,
    *args: Any,
    **kwargs: Any,
):
    """创建一个带默认代理的 aiohttp 会话。

    - HTTP/HTTPS 代理：普通 ClientSession，给每个请求补 ``proxy=``；
    - SOCKS 代理：走 ``aiohttp-socks`` 的 ProxyConnector（aiohttp 本身不支持）。

    用法与 ``aiohttp.ClientSession`` 一致::

        session = create_aiohttp_session(proxy_url, headers=..., timeout=...)
        async with session.get(url) as resp:
            ...
    """
    import aiohttp

    proxy_url = validate_proxy_url(proxy_url)

    if proxy_url and is_socks_proxy(proxy_url):
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError as exc:  # pragma: no cover - 取决于部署环境
            raise ProxyConfigError(
                "使用 socks 代理需要安装 aiohttp-socks：pip install aiohttp-socks"
            ) from exc

        if kwargs.get("connector") is not None:
            logger.debug("socks 代理会接管 connector，忽略调用方传入的 connector")
        kwargs.pop("connector", None)
        kwargs["connector"] = ProxyConnector.from_url(
            _for_socks_client(proxy_url), rdns=True
        )
        return aiohttp.ClientSession(*args, **kwargs)

    session = aiohttp.ClientSession(*args, **kwargs)
    if proxy_url:
        _install_proxy_hook(session, proxy_url)
    return session


def apply_default_proxy(kwargs: Dict[str, Any], proxy_url: str) -> Dict[str, Any]:
    """给请求参数补上默认代理（调用方显式指定时不覆盖）。"""
    if proxy_url and kwargs.get("proxy") is None:
        kwargs["proxy"] = proxy_url
    return kwargs


def _install_proxy_hook(session: Any, proxy_url: str) -> None:
    """让会话内所有请求默认走代理。

    aiohttp 的公开 API 只支持「每个请求传 proxy=」，而项目里有几十处
    ``session.get/post``，逐个改既啰嗦又容易漏。这里包一层 ``_request``，
    仅在调用方没显式指定 proxy 时补上。
    """
    original_request = session._request

    async def _request_with_proxy(method: str, str_or_url: Any, **kwargs: Any):
        apply_default_proxy(kwargs, proxy_url)
        return await original_request(method, str_or_url, **kwargs)

    session._request = _request_with_proxy
    # 保留原始引用，便于调试
    session._xianyu_original_request = original_request


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

def _websockets_major_version() -> int:
    try:
        import websockets

        return int(str(getattr(websockets, "__version__", "12")).split(".")[0])
    except Exception:
        return 12


def _build_ws_connection(url: str, headers: Optional[Dict[str, str]], **kwargs: Any):
    """按 websockets 版本选择正确的 header 参数名。

    10~13 用 ``extra_headers``，14+ 改名 ``additional_headers`` 并原生支持
    ``proxy=``（不再需要自己建隧道）。
    """
    import websockets

    if not headers:
        return websockets.connect(url, **kwargs)

    if _websockets_major_version() >= 14:
        return websockets.connect(url, additional_headers=headers, **kwargs)
    return websockets.connect(url, extra_headers=headers, **kwargs)


async def open_proxy_tunnel(proxy_url: str, dest_url: str) -> Any:
    """通过代理建立到目标地址的 TCP 隧道，返回已连接的 socket。

    WebSocket 走的不是 HTTP 客户端，拿不到 aiohttp 的 proxy 能力；
    ``websockets.connect(..., sock=...)`` 可以把现成 socket 交给它，
    TLS 仍由 websockets 按 URL 里的主机名完成（SNI 不会错）。
    """
    try:
        from python_socks.async_.asyncio import Proxy
    except ImportError as exc:  # pragma: no cover - 取决于部署环境
        raise ProxyConfigError(
            "使用代理需要安装 python-socks：pip install 'python-socks[asyncio]'"
        ) from exc

    parsed = urlparse(dest_url)
    dest_host = parsed.hostname
    if not dest_host:
        raise ProxyConfigError(f"无法解析 WebSocket 目标地址: {dest_url}")
    dest_port = parsed.port or (443 if parsed.scheme in ("wss", "https") else 80)

    proxy = Proxy.from_url(_for_socks_client(proxy_url))
    try:
        sock = await proxy.connect(
            dest_host=dest_host,
            dest_port=dest_port,
            timeout=PROXY_CONNECT_TIMEOUT,
        )
    except Exception as exc:
        raise ProxyConfigError(
            f"通过代理 {mask_proxy_url(proxy_url)} 连接 {dest_host}:{dest_port} 失败: {exc}"
        ) from exc

    logger.debug(
        f"代理隧道已建立: {mask_proxy_url(proxy_url)} -> {dest_host}:{dest_port}"
    )
    return sock


async def open_websocket(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    proxy_url: Optional[str] = None,
    **kwargs: Any,
):
    """按账号代理建立 WebSocket 连接（返回 websockets 的异步上下文管理器）。

    websockets < 14 没有 proxy 参数，这里用 python-socks 先建隧道再传 ``sock=``；
    >= 14 直接用原生 ``proxy=``。
    """
    proxy_url = validate_proxy_url(proxy_url)
    if not proxy_url:
        return _build_ws_connection(url, headers, **kwargs)

    if _websockets_major_version() >= 14:
        return _build_ws_connection(url, headers, proxy=proxy_url, **kwargs)

    sock = await open_proxy_tunnel(proxy_url, url)
    try:
        return _build_ws_connection(url, headers, sock=sock, **kwargs)
    except TypeError:
        # header 参数名不匹配等构造期异常：别把已建立的隧道 socket 泄漏掉
        try:
            sock.close()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# 浏览器（Playwright）
# ---------------------------------------------------------------------------

def playwright_proxy(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    """转换成 Playwright ``proxy=`` 需要的字典；直连返回 None。

    注意 Chromium 的限制：SOCKS5 代理**不支持账号密码认证**。配了认证信息的
    socks 代理只去掉认证信息交给浏览器（HTTP 链路仍然带认证），并打警告，
    避免整条浏览器链路直接报错。
    """
    proxy_url = validate_proxy_url(proxy_url)
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    port = parsed.port
    options: Dict[str, str] = {"server": f"{scheme}://{host}:{port}"}

    username = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""
    if username or password:
        # validate_proxy_url 已经拦住「带认证的 socks」，这里只剩 http(s)
        options["username"] = username
        options["password"] = password
    return options


# ---------------------------------------------------------------------------
# 连通性自检（保存设置时用）
# ---------------------------------------------------------------------------

async def check_proxy(proxy_url: str, timeout: float = 8.0) -> Dict[str, Any]:
    """用代理访问闲鱼登录态检查接口，返回 ``{"ok", "message", "elapsed_ms"}``。

    只做一次轻量 HTTPS 请求，用来在保存设置时告诉用户「代理能不能通」，
    不校验账号 Cookie。
    """
    import aiohttp

    proxy_url = validate_proxy_url(proxy_url)
    if not proxy_url:
        return {"ok": False, "message": "未配置代理", "elapsed_ms": 0}

    target = "https://passport.goofish.com/newlogin/hasLogin.do"
    started = time.monotonic()
    session = None
    try:
        session = create_aiohttp_session(
            proxy_url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        async with session.get(target, allow_redirects=False) as resp:
            elapsed = int((time.monotonic() - started) * 1000)
            if resp.status >= 500:
                return {
                    "ok": False,
                    "message": f"代理可达，但目标返回 HTTP {resp.status}",
                    "elapsed_ms": elapsed,
                }
            return {"ok": True, "message": "代理连通", "elapsed_ms": elapsed}
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return {"ok": False, "message": f"代理不可用: {exc}", "elapsed_ms": elapsed}
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
