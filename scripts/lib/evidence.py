"""证据注入：把外部采集到的记录送进既有的研究流水线。

为什么需要它：本脚本只能用 stdlib 直接发 HTTP。当运行环境的出口被组织网络
策略拦截（代理拒绝 CONNECT）时，八个源全部不可达，脚本就完全失效——尽管
归一化、日期过滤、打分、去重、跨源聚合、报告渲染这些环节其实都还能正常工作。

本模块提供一条旁路：由具备联网能力的调用方（例如具备服务端检索工具的 Agent）
先把证据收集成 JSON，再交给脚本走完整流水线，产出与实时抓取一致的
Markdown / HTML 报告。

设计原则：
  * 手写友好——一个扁平的 records 数组，字段名统一，不要求调用方了解各平台
    item 类各不相同的字段命名；
  * 不伪造——不补任何调用方没给的事实（不猜日期、不造互动数）；
  * 不崩溃——坏记录跳过并计数，最终如实报告跳过了多少条、为什么。

Author: Jesse (https://github.com/Jesseovo)
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from . import schema

# 支持的源（与 SKILL 的八源一致）
SOURCES = (
    "weibo",
    "xiaohongshu",
    "bilibili",
    "zhihu",
    "douyin",
    "wechat",
    "baidu",
    "toutiao",
)

_SOURCE_ALIASES = {
    "wb": "weibo",
    "微博": "weibo",
    "weibo.com": "weibo",
    "xhs": "xiaohongshu",
    "redbook": "xiaohongshu",
    "小红书": "xiaohongshu",
    "bili": "bilibili",
    "bilibli": "bilibili",
    "b站": "bilibili",
    "哔哩哔哩": "bilibili",
    "zh": "zhihu",
    "知乎": "zhihu",
    "dy": "douyin",
    "抖音": "douyin",
    "tiktok": "douyin",
    "wx": "wechat",
    "mp": "wechat",
    "weixin": "wechat",
    "微信": "wechat",
    "公众号": "wechat",
    "wechat_mp": "wechat",
    "bd": "baidu",
    "百度": "baidu",
    "tt": "toutiao",
    "头条": "toutiao",
    "今日头条": "toutiao",
    "toutiao.com": "toutiao",
}

# 手写证据里常见的互动字段别名 → Engagement 真实字段名
_ENGAGEMENT_ALIASES = {
    "comments": "num_comments",
    "comment": "num_comments",
    "评论": "num_comments",
    "like": "likes",
    "点赞": "likes",
    "repost": "reposts",
    "转发": "reposts",
    "share": "shares",
    "collect": "collects",
    "saves": "collects",
    "收藏": "collects",
    "fav": "favorites",
    "favorite": "favorites",
    "danmu": "danmaku",
    "弹幕": "danmaku",
    "upvotes": "voteups",
    "voteup": "voteups",
    "赞同": "voteups",
    "view": "views",
    "播放": "views",
    "read": "reads",
    "阅读": "reads",
}

_CN_UNITS = (("亿", 100_000_000), ("万", 10_000), ("w", 10_000), ("k", 1_000))


def parse_count(value: Any) -> Optional[float]:
    """解析互动数，支持 ``1.2万`` / ``3.4k`` / ``12亿`` 这类中文写法。

    无法解析时返回 None——宁可留空，也不要把猜测当成数据。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # nan / inf 会在下游 int() 时抛 ValueError，把整批注入打断，
        # 违背"坏记录跳过、绝不崩溃"的约定。
        return value if math.isfinite(value) else None
    if not isinstance(value, str):
        return None

    text = value.strip().replace(",", "").replace("+", "")
    if not text:
        return None

    lowered = text.lower()
    for unit, factor in _CN_UNITS:
        if lowered.endswith(unit):
            head = lowered[: -len(unit)].strip()
            try:
                parsed = float(head) * factor
            except ValueError:
                return None
            return parsed if math.isfinite(parsed) else None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None

_DATE_PATTERNS = (
    re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})"),
    re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日"),
    re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})"),
)


def normalize_source(value: Any) -> Optional[str]:
    """把源名归一化成八个规范 id 之一；无法识别返回 None。"""
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if not key:
        return None
    if key in SOURCES:
        return key
    return _SOURCE_ALIASES.get(key)


def normalize_date(value: Any) -> Optional[str]:
    """把常见日期写法归一化成 ``YYYY-MM-DD``。

    下游 ``filter_by_date_range`` 按 ISO 字符串直接比较，格式不统一会导致
    错误过滤。无法解析时返回 None（当作"日期未知"，不猜）。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            year, month, day = (int(g) for g in match.groups())
            try:
                # 必须构造真实日期：仅做 1-12 / 1-31 的分量检查会放行
                # 2026-02-31 这类不存在的日期，而下游是按字符串比较，
                # 于是它会被当作"高置信度且在窗口内"保留下来。
                return datetime.date(year, month, day).isoformat()
            except ValueError:
                return None
    return None


def _stable_id(source: str, url: str, title: str) -> str:
    seed = f"{source}|{url}|{title}".encode("utf-8", "replace")
    return "ev" + hashlib.sha1(seed).hexdigest()[:10]


def _first_text(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _build_engagement(raw: Any) -> Optional[schema.Engagement]:
    """只接受调用方明确给出的互动数，缺失即留空——不估算、不补零。"""
    if not isinstance(raw, dict):
        return None
    allowed = {field.name for field in dataclasses.fields(schema.Engagement)}
    kwargs: Dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        name = key.strip()
        name = _ENGAGEMENT_ALIASES.get(name.lower(), name)
        if name not in allowed:
            continue
        parsed = parse_count(value)
        if parsed is None:
            continue
        # 计数类字段用整数呈现更自然；比例类（upvote_ratio）保留小数
        if name != "upvote_ratio" and float(parsed).is_integer():
            parsed = int(parsed)
        kwargs[name] = parsed
    if not kwargs:
        return None
    try:
        return schema.Engagement(**kwargs)
    except TypeError:
        # 未知字段：逐个过滤后重试，坏字段不应让整条记录失败
        safe = {}
        for name, value in kwargs.items():
            try:
                schema.Engagement(**{name: value})
                safe[name] = value
            except TypeError:
                continue
        return schema.Engagement(**safe) if safe else None


def _string_list(value: Any) -> list:
    """只保留字符串元素，避免脏数据进入渲染层。"""
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _int_or_none(value: Any) -> Optional[int]:
    parsed = parse_count(value)
    return int(parsed) if parsed is not None else None


_BVID_RE = re.compile(r"(BV[0-9A-Za-z]{8,12})")


def _bvid_from_url(url: str) -> str:
    match = _BVID_RE.search(url or "")
    return match.group(1) if match else ""


def _domain(url: str) -> str:
    try:
        return urlsplit(url).netloc or ""
    except ValueError:
        return ""


def _make_item(source: str, record: Dict[str, Any]):
    """把一条通用记录映射到对应平台的 item 类。"""
    url = _first_text(record, "url", "link")
    title = _first_text(record, "title", "headline")
    text = _first_text(record, "text", "content", "desc", "description")
    snippet = _first_text(record, "snippet", "excerpt", "abstract", "summary")
    author = _first_text(record, "author", "author_name", "user", "nickname", "source_name")

    # 主文本：不同平台主字段不同，这里统一回退，确保不会渲染出空条目
    main = title or text or snippet
    body = snippet or text or title

    item_id = _first_text(record, "id") or _stable_id(source, url, main)
    date = normalize_date(record.get("date") or record.get("published_at") or record.get("time"))
    date_confidence = "high" if date else "low"
    engagement = _build_engagement(record.get("engagement"))

    common = {
        "id": item_id,
        "url": url,
        "date": date,
        "date_confidence": date_confidence,
    }
    why = _first_text(record, "why_relevant")
    if why:
        common["why_relevant"] = why

    if source == "weibo":
        return schema.WeiboItem(
            text=main, author_handle=author, engagement=engagement, **common
        )
    if source == "xiaohongshu":
        return schema.XiaohongshuItem(
            title=main, desc=body, author_name=author,
            engagement=engagement, hashtags=_string_list(record.get("hashtags")),
            **common
        )
    if source == "bilibili":
        return schema.BilibiliItem(
            title=main,
            bvid=_first_text(record, "bvid") or _bvid_from_url(url),
            channel_name=author,
            description=body, engagement=engagement,
            duration=_int_or_none(record.get("duration")), **common
        )
    if source == "zhihu":
        return schema.ZhihuItem(
            title=main, excerpt=body, author=author,
            content_type=_first_text(record, "content_type") or "answer",
            engagement=engagement, **common
        )
    if source == "douyin":
        return schema.DouyinItem(
            text=main, author_name=author, engagement=engagement,
            hashtags=_string_list(record.get("hashtags")),
            duration=_int_or_none(record.get("duration")), **common
        )
    if source == "wechat":
        wechat_id = _first_text(record, "wechat_id")
        return schema.WechatItem(
            title=main, snippet=body, source_name=author,
            wechat_id=wechat_id or None, **common
        )
    if source == "baidu":
        return schema.BaiduItem(
            title=main, snippet=body,
            source_domain=_first_text(record, "source_domain") or _domain(url) or "未知",
            **common
        )
    if source == "toutiao":
        hot_value = parse_count(record.get("hot_value"))
        return schema.ToutiaoItem(
            title=main, abstract=body,
            source_name=author,
            is_hot=bool(record.get("is_hot")),
            hot_value=int(hot_value) if hot_value is not None else None,
            engagement=engagement, **common
        )
    raise ValueError(f"不支持的源: {source}")


def parse_records(records: Any) -> Tuple[Dict[str, list], List[str]]:
    """把通用记录数组转换成 ``{source: [item, ...]}``。

    Returns:
        (per_source_items, warnings)。坏记录被跳过并在 warnings 中说明原因。
    """
    per_source: Dict[str, list] = {source: [] for source in SOURCES}
    warnings: List[str] = []

    if not isinstance(records, list):
        return per_source, ["records 必须是数组；已忽略全部内容。"]

    for index, record in enumerate(records):
        position = f"第 {index + 1} 条"
        if not isinstance(record, dict):
            warnings.append(f"{position}: 不是对象，已跳过。")
            continue

        source = normalize_source(record.get("source") or record.get("platform"))
        if not source:
            warnings.append(
                f"{position}: source 缺失或无法识别（{record.get('source')!r}），已跳过。"
            )
            continue

        url = _first_text(record, "url", "link")
        if not url:
            warnings.append(f"{position}[{source}]: 缺少 url，无法引用，已跳过。")
            continue
        if not url.lower().startswith(("http://", "https://")):
            warnings.append(f"{position}[{source}]: url 非 http(s)，已跳过。")
            continue
        if not _first_text(record, "title", "headline", "text", "content",
                           "desc", "description", "snippet", "excerpt",
                           "abstract", "summary"):
            warnings.append(f"{position}[{source}]: 没有任何标题或正文，已跳过。")
            continue

        try:
            per_source[source].append(_make_item(source, record))
        except Exception as exc:  # 单条坏数据绝不应中断整批注入
            warnings.append(f"{position}[{source}]: 构造失败（{type(exc).__name__}: {exc}），已跳过。")

    return per_source, warnings


def load(path: str) -> Dict[str, Any]:
    """读取证据 JSON 文件。

    支持两种顶层形态：
      * ``{"topic": "...", "records": [...]}``
      * 直接是一个 records 数组

    Returns:
        dict: ``items`` 每源 item 列表；``warnings`` 跳过说明；
        ``topic`` / ``collected_via`` / ``notes`` 元信息；``accepted`` 计数。
    """
    file_path = Path(path).expanduser()
    if not file_path.exists():
        raise FileNotFoundError(f"证据文件不存在: {file_path}")

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"证据文件不是合法 JSON: {exc}") from exc

    if isinstance(raw, list):
        payload: Dict[str, Any] = {"records": raw}
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise ValueError("证据文件顶层必须是对象或数组。")

    records = payload.get("records")
    if records is None:
        records = payload.get("items")

    per_source, warnings = parse_records(records)
    accepted = sum(len(v) for v in per_source.values())

    return {
        "items": per_source,
        "warnings": warnings,
        "accepted": accepted,
        "topic": payload.get("topic") if isinstance(payload.get("topic"), str) else None,
        "collected_via": payload.get("collected_via")
        if isinstance(payload.get("collected_via"), str) else None,
        "notes": payload.get("notes") if isinstance(payload.get("notes"), str) else None,
    }


TEMPLATE = {
    "topic": "示例主题",
    "collected_via": "WebSearch（出口被拦截时的替代采集通道）",
    "notes": "互动数缺失就留空，不要估算。",
    "records": [
        {
            "source": "weibo",
            "title": "标题或正文首句",
            "url": "https://example.com/post/1",
            "date": "2026-07-01",
            "author": "作者或账号名",
            "snippet": "摘要或正文片段",
            "engagement": {"likes": 100, "num_comments": 20, "reposts": 5},
        },
        {
            "source": "baidu",
            "title": "媒体报道标题",
            "url": "https://news.example.com/a/2",
            "date": "2026-07-10",
            "snippet": "报道摘要",
        },
    ],
}


def template_json() -> str:
    """返回证据文件模板，供 ``--evidence-template`` 输出。"""
    return json.dumps(TEMPLATE, indent=2, ensure_ascii=False)
