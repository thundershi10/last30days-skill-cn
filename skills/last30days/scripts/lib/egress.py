"""出口（egress）故障分类：区分「组织网络策略拦截」与「平台风控/瞬时故障」。

为什么需要它：当会话运行在带策略代理的受限环境里，去往目标站点的 CONNECT
会被网关直接拒绝。此时 urllib 抛出的是 ``urllib.error.URLError``（而非
``HTTPError``），reason 形如::

    OSError('Tunnel connection failed: 403 Forbidden')

这类拒绝是**永久性**的：重试没有意义，配置 API token、Cookie 或安装
Playwright 同样没有意义——因为连接在 TLS 握手之前就被掐断了。必须与
「平台反爬返回 HTTP 403」严格区分，后者才是登录态/token 能解决的问题。

Author: Jesse (https://github.com/Jesseovo)
"""

from __future__ import annotations

import re
import urllib.error
from typing import Any, Optional

# 故障类别
EGRESS_BLOCKED = "egress_blocked"   # 组织出口策略拒绝（永久，重试无意义）
HTTP_ERROR = "http_error"           # 目标站点返回 HTTP 错误（含平台反爬 403）
TIMEOUT = "timeout"                 # 连接/读取超时（瞬时，可重试）
OTHER = "other"                     # 其他网络或未知故障

# 代理/网关拒绝隧道的特征串（全部小写比较）。
#
# 注意：不能把裸的 "403" 或 "forbidden" 放进来。平台反爬也会返回 403，
# 那是登录态或 token 能解决的问题；若误判为出口拦截，会把用户引向
# "去改网络策略" 这个错误结论。因此只匹配明确指向代理/隧道层的措辞。
_BLOCKED_MARKERS = (
    "cannot connect to proxy",
    "connect_rejected",               # 代理状态端点使用的措辞
    "proxy connection failed",
    "unable to connect to proxy",
    "407 proxy authentication required",
    "proxy authentication required",
    "egress policy",
    "blocked by policy",
    "policy denial",
)

# CPython 的 http.client._tunnel 对**任何**非 200 的 CONNECT 响应都抛出
#   OSError(f"Tunnel connection failed: {code} {message}")
# 状态码是唯一能区分"策略拒绝"和"代理/上游瞬时故障"的信息，因此必须解析出来：
# 只把鉴权/策略类状态码当作永久拒绝，5xx 与 429 属于瞬时故障，应当继续重试。
_TUNNEL_RE = re.compile(r"tunnel connection failed:\s*(\d{3})")

# 401/403/407 = 代理拒绝或要求鉴权；451 = 因法律原因不可用。均为永久性。
_POLICY_TUNNEL_CODES = frozenset({401, 403, 407, 451})

# 超时特征串
_TIMEOUT_MARKERS = (
    "timed out",
    "timeout",
    "the read operation timed out",
)


def _iter_messages(error: Any):
    """展开异常链，产出所有可用于匹配的字符串。

    URLError 把底层异常放在 ``.reason`` 里，往往只有 reason 才带
    "Tunnel connection failed" 字样，所以必须一并检查。
    """
    if error is None:
        return

    if isinstance(error, str):
        yield error
        return

    try:
        yield str(error)
    except Exception:  # pragma: no cover - 极端的 __str__ 异常
        pass

    seen = 0
    reason = getattr(error, "reason", None)
    while reason is not None and seen < 5:
        if isinstance(reason, str):
            yield reason
            break
        try:
            yield str(reason)
        except Exception:  # pragma: no cover
            pass
        reason = getattr(reason, "reason", None)
        seen += 1

    cause = getattr(error, "__cause__", None)
    if cause is not None:
        try:
            yield str(cause)
        except Exception:  # pragma: no cover
            pass


def is_policy_denial(error: Any) -> bool:
    """判断异常或消息是否为出口策略拒绝（永久性，禁止重试）。

    Args:
        error: 异常实例或消息字符串。

    Returns:
        True 表示这是代理/网关层面的策略拒绝。
    """
    # 平台自身返回的 HTTP 错误永远不算出口拦截（含反爬 403）。
    # 但 407 是代理要求鉴权，属于代理层问题。
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 407

    for message in _iter_messages(error):
        lowered = message.lower()

        # CONNECT 隧道失败：按状态码判定永久性，不能一概当作策略拒绝。
        tunnel = _TUNNEL_RE.search(lowered)
        if tunnel:
            try:
                return int(tunnel.group(1)) in _POLICY_TUNNEL_CODES
            except ValueError:  # pragma: no cover - 正则已保证是三位数字
                return False

        for marker in _BLOCKED_MARKERS:
            if marker in lowered:
                return True
    return False


def classify(error: Any) -> str:
    """把异常或消息归类为四种故障类别之一。

    Args:
        error: 异常实例或消息字符串。

    Returns:
        EGRESS_BLOCKED / HTTP_ERROR / TIMEOUT / OTHER 之一。
    """
    if error is None:
        return OTHER

    if is_policy_denial(error):
        return EGRESS_BLOCKED

    if isinstance(error, urllib.error.HTTPError):
        return HTTP_ERROR

    messages = [m.lower() for m in _iter_messages(error)]

    if isinstance(error, (TimeoutError, OSError)) or messages:
        for message in messages:
            if any(marker in message for marker in _TIMEOUT_MARKERS):
                return TIMEOUT

    if isinstance(error, TimeoutError):
        return TIMEOUT

    for message in messages:
        # http.py 会把状态码包装成 "HTTP 403: Forbidden" 之类的文本
        if message.startswith("http ") or "http error" in message:
            return HTTP_ERROR

    return OTHER


def is_blocked(error: Any) -> bool:
    """``classify(error) == EGRESS_BLOCKED`` 的简写。"""
    return classify(error) == EGRESS_BLOCKED


# 用户可见文案 ---------------------------------------------------------------

BLOCKED_LABEL = "出口被拦截"

BLOCKED_SHORT = "网络出口被策略拦截（代理拒绝 CONNECT）"

BLOCKED_REASON = (
    "本会话的网络策略不允许访问该站点：代理在 TLS 握手前就拒绝了 CONNECT。"
    "这是永久性拒绝，重试无效。"
)

BLOCKED_FIX = (
    "需要放开运行环境的出口网络策略（把目标域名加入允许清单，或改用允许外网的策略）。"
    "注意：在出口放开之前，配置 API token、Cookie 或安装 Playwright 都无法绕过——"
    "连接在鉴权之前就被切断了。"
)

BLOCKED_DOC_URL = "https://code.claude.com/docs/en/claude-code-on-the-web"


def describe(kind: str) -> str:
    """返回故障类别的中文简述。"""
    return {
        EGRESS_BLOCKED: BLOCKED_SHORT,
        HTTP_ERROR: "目标站点返回 HTTP 错误（可能是平台反爬或接口变更）",
        TIMEOUT: "连接或读取超时（可能是瞬时故障）",
        OTHER: "网络或未知故障",
    }.get(kind, "网络或未知故障")


def annotate(message: Optional[str]) -> Optional[str]:
    """给错误消息补一句出口拦截说明，便于最终报告如实呈现。

    非出口拦截的消息原样返回。
    """
    if not message:
        return message
    if is_policy_denial(message):
        if BLOCKED_LABEL in message:
            return message
        return f"{message}（{BLOCKED_LABEL}：{BLOCKED_REASON}）"
    return message
