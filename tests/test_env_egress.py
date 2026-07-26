"""Tests: probes must not fail-open on egress policy denial (but still do on timeouts)."""

import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import env

PROBES = ("probe_bilibili", "probe_zhihu", "probe_toutiao")


def _tunnel_denied():
    return urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))


def test_probes_report_unavailable_when_egress_blocked():
    """核心修复：出口被拦截时探针必须返回 False。

    此前 bare `except Exception: return True` 会把策略拒绝当成瞬时故障，
    导致 --diagnose 对完全不可达的源谎报"公开搜索 API 可用"。
    """
    for name in PROBES:
        with patch("lib.env._probe_json", side_effect=_tunnel_denied()):
            assert getattr(env, name)() is False, f"{name} 在出口被拦截时不应返回 True"


def test_probes_still_fail_open_on_timeout():
    """瞬时故障保留 fail-open：不要因为一次超时就把源判死。"""
    for name in PROBES:
        with patch("lib.env._probe_json", side_effect=TimeoutError("timed out")):
            assert getattr(env, name)() is True, f"{name} 不应因超时判定为不可用"


def test_probes_report_unavailable_on_http_error():
    error = urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None)
    for name in PROBES:
        with patch("lib.env._probe_json", side_effect=error):
            assert getattr(env, name)() is False


def test_probe_egress_detects_full_block():
    with patch("lib.env._probe_json", side_effect=_tunnel_denied()):
        status = env.probe_egress()

    assert status["blocked"] is True
    assert status["partially_blocked"] is False
    assert status["blocked_count"] == status["checked"] > 0
    assert "Tunnel connection failed" in status["reason"]


def test_probe_egress_clean_when_reachable():
    with patch("lib.env._probe_json", return_value={"data": {"result": [1]}}):
        status = env.probe_egress()

    assert status["blocked"] is False
    assert status["partially_blocked"] is False
    assert status["blocked_count"] == 0


def test_probe_egress_does_not_flag_timeouts_as_blocked():
    """关键：超时不得被误判为出口拦截，否则会跳过整轮抓取。"""
    with patch("lib.env._probe_json", side_effect=TimeoutError("timed out")):
        status = env.probe_egress()

    assert status["blocked"] is False
    assert status["blocked_count"] == 0


def test_probe_egress_partial_block():
    calls = {"n": 0}

    def _side_effect(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _tunnel_denied()
        return {"data": {"result": [1]}}

    with patch("lib.env._probe_json", side_effect=_side_effect):
        status = env.probe_egress()

    assert status["blocked"] is False
    assert status["partially_blocked"] is True
    assert status["blocked_count"] == 1
