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
    """按 URL 判定而非调用序号：探测是并发的，序号不可靠。"""
    def _side_effect(url, *args, **kwargs):
        if "bilibili" in url:
            raise _tunnel_denied()
        return {"data": {"result": [1]}}

    with patch("lib.env._probe_json", side_effect=_side_effect):
        status = env.probe_egress()

    assert status["blocked"] is False
    assert status["partially_blocked"] is True
    assert status["blocked_count"] == 1


def test_probe_egress_not_blocked_when_search_fallback_reachable():
    """cn.bing.com 是 5 个适配器共用的搜索兜底主机。

    它还通，就不能宣布"全部被拦截"并跳过整轮抓取——那些源仍有取数路径。
    """
    def _side_effect(url, *args, **kwargs):
        if "bing.com" in url:
            return {"ok": True}
        raise _tunnel_denied()

    with patch("lib.env._probe_json", side_effect=_side_effect):
        status = env.probe_egress()

    assert status["blocked"] is False
    assert status["partially_blocked"] is True


def test_probe_egress_transient_tunnel_failure_is_not_a_block():
    """代理返回 502 等瞬时隧道故障不得判定为永久策略拦截。"""
    import urllib.error

    transient = urllib.error.URLError(OSError("Tunnel connection failed: 502 Bad Gateway"))
    with patch("lib.env._probe_json", side_effect=transient):
        status = env.probe_egress()

    assert status["blocked"] is False
    assert status["blocked_count"] == 0
