"""Tests: HTTP layer must fail fast on egress policy denial, never retry it."""

import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import http


def _tunnel_denied():
    return urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))


def test_policy_denial_does_not_retry():
    """代理策略拒绝是永久性的：必须 1 次尝试即失败，不得 sleep。"""
    with (
        patch("lib.http.urllib.request.urlopen", side_effect=_tunnel_denied()) as opener,
        patch("lib.http.time.sleep") as sleep,
    ):
        with pytest.raises(http.EgressBlockedError):
            http.request("GET", "https://api.bilibili.com/x", retries=5)

    assert opener.call_count == 1
    sleep.assert_not_called()


def test_policy_denial_raises_specific_subclass():
    """必须是 EgressBlockedError，而不是泛化 HTTPError。

    既有测试用 assertRaises(HTTPError) 断言，而 EgressBlockedError 是其子类，
    所以只有显式断言子类才能守住这个行为。
    """
    with (
        patch("lib.http.urllib.request.urlopen", side_effect=_tunnel_denied()),
        patch("lib.http.time.sleep"),
    ):
        with pytest.raises(http.EgressBlockedError) as excinfo:
            http.request("GET", "https://example.com", retries=3)

    assert isinstance(excinfo.value, http.HTTPError)
    assert "出口被拦截" in str(excinfo.value)


def test_proxy_407_does_not_retry():
    error = urllib.error.HTTPError(
        "https://example.com", 407, "Proxy Authentication Required", {}, None
    )
    with (
        patch("lib.http.urllib.request.urlopen", side_effect=error) as opener,
        patch("lib.http.time.sleep") as sleep,
    ):
        with pytest.raises(http.EgressBlockedError):
            http.request("GET", "https://example.com", retries=4)

    assert opener.call_count == 1
    sleep.assert_not_called()


def test_platform_403_is_plain_http_error():
    """平台反爬 403 不得被当成出口拦截。"""
    error = urllib.error.HTTPError("https://example.com", 403, "Forbidden", {}, None)
    with (
        patch("lib.http.urllib.request.urlopen", side_effect=error),
        patch("lib.http.time.sleep"),
    ):
        with pytest.raises(http.HTTPError) as excinfo:
            http.request("GET", "https://example.com", retries=3)

    assert not isinstance(excinfo.value, http.EgressBlockedError)
    assert excinfo.value.status_code == 403


def test_transient_urlerror_still_retries():
    """回归守卫：非策略性网络故障必须保留原有重试行为。"""
    with (
        patch("lib.http.urllib.request.urlopen",
              side_effect=urllib.error.URLError("temporary dns failure")) as opener,
        patch("lib.http.time.sleep") as sleep,
    ):
        with pytest.raises(http.HTTPError) as excinfo:
            http.request("GET", "https://example.com", retries=3)

    assert not isinstance(excinfo.value, http.EgressBlockedError)
    assert opener.call_count == 3
    assert sleep.call_count == 2


def test_socket_level_policy_denial_does_not_retry():
    """OSError 分支同样要短路（某些路径不包 URLError）。"""
    with (
        patch("lib.http.urllib.request.urlopen",
              side_effect=OSError("Cannot connect to proxy: blocked by policy")) as opener,
        patch("lib.http.time.sleep") as sleep,
    ):
        with pytest.raises(http.EgressBlockedError):
            http.request("GET", "https://example.com", retries=5)

    assert opener.call_count == 1
    sleep.assert_not_called()
