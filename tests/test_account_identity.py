"""账号级浏览器身份（fork:fingerprint）单测。"""

import asyncio
import json
from pathlib import Path

import pytest

from utils import account_identity
from utils.xianyu_utils import generate_device_id


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(account_identity, "_ROOT", tmp_path / "accounts")
    yield


# ---------------------------------------------------------------------------
# 设备 ID
# ---------------------------------------------------------------------------

def test_device_id_is_stable():
    first = generate_device_id("1234567890")
    second = generate_device_id("1234567890")
    assert first == second


def test_device_id_differs_per_account():
    assert generate_device_id("111") != generate_device_id("222")


def test_device_id_keeps_expected_shape():
    value = generate_device_id("999")
    uuid_part, _, account_part = value.rpartition("-")
    assert account_part == "999"
    assert len(uuid_part) == 36
    assert uuid_part[8] == "-" and uuid_part[13] == "-"
    assert uuid_part[14] == "4"          # version 4
    assert uuid_part[19] in "89ab"       # variant
    assert uuid_part[18] == "-" and uuid_part[23] == "-"


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------

def test_fingerprint_is_deterministic():
    assert account_identity.fingerprint_for("acc-1") == account_identity.fingerprint_for("acc-1")


def test_fingerprint_varies_across_accounts():
    values = {json.dumps(account_identity.fingerprint_for(f"acc-{i}"), sort_keys=True) for i in range(12)}
    assert len(values) > 1


def test_fingerprint_values_are_reasonable():
    fp = account_identity.fingerprint_for("acc-1")
    assert fp["viewport"]["width"] >= 1440
    assert fp["viewport"]["height"] >= 800
    assert fp["device_scale_factor"] in (1, 1.25)
    assert fp["color_scheme"] in ("light", "no-preference")
    assert fp["locale"] == "zh-CN"
    assert fp["timezone_id"] == "Asia/Shanghai"


def test_context_options_do_not_override_explicit_values():
    options = account_identity.context_options(
        "acc-1",
        {"viewport": {"width": 1280, "height": 720}, "user_agent": "ua"},
    )
    assert options["viewport"] == {"width": 1280, "height": 720}
    assert options["user_agent"] == "ua"
    assert "device_scale_factor" in options


# ---------------------------------------------------------------------------
# 目录与持久化
# ---------------------------------------------------------------------------

def test_directories_are_per_account():
    a = account_identity.cache_dir("acc-a")
    b = account_identity.cache_dir("acc-b")
    assert a != b
    assert a.exists() and b.exists()
    assert a.name == "cache"


def test_launch_args_contains_cache_dir():
    args = account_identity.launch_args("acc-a")
    assert len(args) == 1
    assert args[0].startswith("--disk-cache-dir=")
    assert "acc-a" in args[0]


def test_unsafe_account_id_is_sanitized():
    safe = account_identity.safe_account_id("../../etc/passwd")
    assert "/" not in safe and ".." not in safe
    assert account_identity.cache_dir("../../etc/passwd").parent.name == safe


def test_load_storage_state_missing_returns_none():
    assert account_identity.load_storage_state("nobody") is None


def test_storage_state_roundtrip():
    class FakeContext:
        async def storage_state(self, path):
            Path(path).write_text(
                json.dumps({"cookies": [], "origins": []}), encoding="utf-8"
            )

    ok = asyncio.run(account_identity.save_storage_state(FakeContext(), "acc-a"))
    assert ok is True
    path = account_identity.load_storage_state("acc-a")
    assert path is not None
    assert json.loads(Path(path).read_text(encoding="utf-8")) == {"cookies": [], "origins": []}


def test_storage_state_corrupt_file_returns_none():
    path = account_identity.storage_state_path("acc-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert account_identity.load_storage_state("acc-a") is None


def test_save_storage_state_without_context_is_safe():
    assert asyncio.run(account_identity.save_storage_state(None, "acc-a")) is False
