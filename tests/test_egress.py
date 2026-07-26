"""Tests for egress (network policy denial) classification."""

import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import egress


def _proxy_denied_urlerror():
    """复刻真实形态：CONNECT 被网关拒绝时 reason 是一个 OSError。"""
    return urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))


def test_tunnel_rejection_is_policy_denial():
    error = _proxy_denied_urlerror()
    assert egress.is_policy_denial(error) is True
    assert egress.classify(error) == egress.EGRESS_BLOCKED
    assert egress.is_blocked(error) is True


def test_reason_chain_is_inspected():
    """"Tunnel connection failed" 只出现在 .reason 里，必须能穿透异常链。"""
    error = _proxy_denied_urlerror()
    assert "Tunnel connection failed" not in str(error.reason.__class__)
    assert egress.is_policy_denial(error)


def test_platform_403_is_not_egress_blocked():
    """平台反爬 403 必须仍归为 HTTP 错误——它可以用 token/Cookie 解决。

    若误判为出口拦截，会把用户引向"去改网络策略"这个错误结论。
    """
    error = urllib.error.HTTPError("https://example.com", 403, "Forbidden", {}, None)
    assert egress.is_policy_denial(error) is False
    assert egress.classify(error) == egress.HTTP_ERROR


def test_proxy_407_is_egress_blocked():
    error = urllib.error.HTTPError(
        "https://example.com", 407, "Proxy Authentication Required", {}, None
    )
    assert egress.is_policy_denial(error) is True
    assert egress.classify(error) == egress.EGRESS_BLOCKED


def test_other_http_codes_are_http_error():
    for code in (404, 429, 500, 503):
        error = urllib.error.HTTPError("https://example.com", code, "x", {}, None)
        assert egress.is_policy_denial(error) is False
        assert egress.classify(error) == egress.HTTP_ERROR


def test_timeout_is_not_blocked():
    assert egress.classify(TimeoutError()) == egress.TIMEOUT
    assert egress.classify(urllib.error.URLError(TimeoutError("timed out"))) == egress.TIMEOUT
    assert egress.is_policy_denial(TimeoutError("timed out")) is False


def test_dns_failure_is_not_blocked():
    """DNS 故障是瞬时问题，不能当成策略拦截（否则会误导且破坏重试）。"""
    error = urllib.error.URLError("temporary dns failure")
    assert egress.is_policy_denial(error) is False
    assert egress.classify(error) != egress.EGRESS_BLOCKED


def test_string_messages_are_classified():
    """report.<source>_error 存的是文本，分类器必须也能吃字符串。"""
    assert egress.is_policy_denial("URL Error: Tunnel connection failed: 403 Forbidden")
    assert egress.classify("HTTP 403: Forbidden") == egress.HTTP_ERROR
    assert egress.is_policy_denial("proxy authentication required")
    assert egress.is_policy_denial("connect_rejected")
    assert egress.is_policy_denial("普通的平台报错") is False


def test_none_and_empty_are_safe():
    assert egress.classify(None) == egress.OTHER
    assert egress.is_policy_denial(None) is False
    assert egress.is_policy_denial("") is False
    assert egress.annotate(None) is None
    assert egress.annotate("") == ""


def test_annotate_only_touches_blocked_messages():
    plain = "HTTP 500: Server Error"
    assert egress.annotate(plain) == plain

    blocked = "URL Error: Tunnel connection failed: 403 Forbidden"
    annotated = egress.annotate(blocked)
    assert egress.BLOCKED_LABEL in annotated
    # 幂等：重复标注不应叠加
    assert egress.annotate(annotated) == annotated


def test_describe_covers_all_kinds():
    for kind in (egress.EGRESS_BLOCKED, egress.HTTP_ERROR, egress.TIMEOUT, egress.OTHER):
        assert egress.describe(kind)


def test_blocked_markers_exclude_bare_403():
    """守卫：不得把裸 403/forbidden 加进拦截特征串。

    加了会把平台反爬误判为出口拦截，并破坏 URLError 的正常重试路径。
    """
    for marker in egress._BLOCKED_MARKERS:
        assert marker not in ("403", "forbidden", "dns", "name or service not known")
