"""账号级浏览器身份（fork:fingerprint）。

同一台机器上跑多个闲鱼账号时，如果所有账号共用一套视口、缓存目录和浏览器
存储，平台侧看到的几乎是同一台设备。这里按账号 id 派生一组**确定性**特征：
同一账号每次启动完全一致，不同账号之间有稳定差异 —— 既不会像随机那样每次
重启就"换设备"，也不会所有号长成一样。

同时提供按账号隔离的磁盘缓存目录与 storage_state（localStorage/Cookie）
持久化，让每个账号的浏览器都有自己的一份历史，而不是每次都是干净的临时
profile。

注意：这些措施只能降低关联度，做不到彻底隔离。真正的隔离需要一账号一容器/
一机器；TLS 指纹（Python 栈）也不在这里处理。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

# 真实常见的桌面分辨率；同一账号固定取其中一个
_VIEWPORTS = (
    (1920, 1080),
    (1680, 1050),
    (1600, 900),
    (1536, 864),
    (1440, 900),
)
# 只用浅色/不指定：深色会改变页面渲染，可能影响依赖选择器的抓取逻辑
_COLOR_SCHEMES = ("light", "no-preference")
_DEVICE_SCALES = (1, 1.25)

_ROOT = Path("browser_data") / "accounts"

_SAFE_ID = re.compile(r"[^0-9A-Za-z_-]")


def safe_account_id(account_id: Optional[str]) -> str:
    """账号 id 转成可安全用作目录名的形式。"""
    text = (account_id or "default").strip() or "default"
    return _SAFE_ID.sub("_", text)[:64]


def _digest(account_id: Optional[str]) -> bytes:
    return hashlib.sha256(
        f"xianyu-identity::{safe_account_id(account_id)}".encode("utf-8")
    ).digest()


def fingerprint_for(account_id: Optional[str]) -> Dict[str, Any]:
    """返回该账号固定的浏览器特征。"""
    digest = _digest(account_id)
    width, height = _VIEWPORTS[digest[0] % len(_VIEWPORTS)]
    return {
        "viewport": {"width": width, "height": height},
        "window_size": f"{width},{height}",
        "device_scale_factor": _DEVICE_SCALES[digest[1] % len(_DEVICE_SCALES)],
        "color_scheme": _COLOR_SCHEMES[digest[2] % len(_COLOR_SCHEMES)],
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
    }


def context_options(account_id: Optional[str], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """在调用方已有选项上补齐账号级特征（已有键不覆盖）。

    浏览器内核与 UA 保持原生：伪造 UA 会让 Client Hints 对不上，反而更像
    机器人（项目里已经踩过这个坑），所以这里只动视口/缩放/配色/区域。
    """
    options: Dict[str, Any] = dict(base or {})
    fp = fingerprint_for(account_id)
    options.setdefault("viewport", fp["viewport"])
    options.setdefault("device_scale_factor", fp["device_scale_factor"])
    options.setdefault("color_scheme", fp["color_scheme"])
    options.setdefault("locale", fp["locale"])
    options.setdefault("timezone_id", fp["timezone_id"])
    return options


def account_dir(account_id: Optional[str]) -> Path:
    path = _ROOT / safe_account_id(account_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir(account_id: Optional[str]) -> Path:
    path = account_dir(account_id) / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def launch_args(account_id: Optional[str]) -> list:
    """追加到 Chromium 启动参数里的账号级设置。

    ``--disk-cache-dir`` 不占用 user-data-dir 的单例锁，可以和 Playwright
    托管的临时 profile 共存，因此不用改浏览器生命周期就能拿到按账号的缓存。
    """
    try:
        return [f"--disk-cache-dir={cache_dir(account_id)}"]
    except Exception:
        return []


def storage_state_path(account_id: Optional[str]) -> Path:
    return account_dir(account_id) / "storage_state.json"


def load_storage_state(account_id: Optional[str]) -> Optional[str]:
    """读取账号的 storage_state 路径；不存在或损坏时返回 None。"""
    path = storage_state_path(account_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return str(path)
    except Exception:
        return None


async def save_storage_state(context: Any, account_id: Optional[str]) -> bool:
    """把当前上下文的 localStorage/Cookie 落盘，供下次复用。

    失败只影响持久化，不影响主流程，因此吞掉异常并返回 False。
    """
    if context is None:
        return False
    try:
        path = storage_state_path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        await context.storage_state(path=str(tmp))
        os.replace(tmp, path)
        return True
    except Exception:
        return False
