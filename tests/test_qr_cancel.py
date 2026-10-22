"""扫码登录会话取消（切换代理节点时用）。"""

from utils.qr_login import QRLoginManager, QRLoginSession


def _manager_with_session(session_id: str = "sid-1") -> tuple:
    manager = QRLoginManager()
    session = QRLoginSession(session_id)
    manager.sessions[session_id] = session
    return manager, session


def test_cancel_session_removes_and_marks():
    manager, session = _manager_with_session()

    assert manager.cancel_session("sid-1") is True
    assert "sid-1" not in manager.sessions
    assert session.status == "cancelled"


def test_cancel_missing_session_returns_false():
    manager = QRLoginManager()
    assert manager.cancel_session("missing") is False


def test_cancel_is_idempotent():
    manager, _ = _manager_with_session()
    assert manager.cancel_session("sid-1") is True
    assert manager.cancel_session("sid-1") is False
