"""Tests for the evidence-injection ingestion path."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import evidence


def test_normalize_source_accepts_aliases():
    assert evidence.normalize_source("xhs") == "xiaohongshu"
    assert evidence.normalize_source("小红书") == "xiaohongshu"
    assert evidence.normalize_source("WX") == "wechat"
    assert evidence.normalize_source(" 头条 ") == "toutiao"
    assert evidence.normalize_source("B站") == "bilibili"
    assert evidence.normalize_source("weibo") == "weibo"
    assert evidence.normalize_source("不认识") is None
    assert evidence.normalize_source(None) is None
    assert evidence.normalize_source("") is None


def test_normalize_date_formats():
    assert evidence.normalize_date("2026-07-01") == "2026-07-01"
    assert evidence.normalize_date("2026-7-1") == "2026-07-01"
    assert evidence.normalize_date("2026/07/01") == "2026-07-01"
    assert evidence.normalize_date("2026年7月1日") == "2026-07-01"
    assert evidence.normalize_date("2026.07.01") == "2026-07-01"
    assert evidence.normalize_date("2026-07-01T10:30:00Z") == "2026-07-01"
    # 不猜：无法解析就是 None
    assert evidence.normalize_date("上周") is None
    assert evidence.normalize_date("") is None
    assert evidence.normalize_date(None) is None
    assert evidence.normalize_date("2026-13-01") is None


def test_parse_count_chinese_units():
    assert evidence.parse_count("1.2万") == 12000
    assert evidence.parse_count("2.5W") == 25000
    assert evidence.parse_count("3.4k") == 3400
    assert evidence.parse_count("12亿") == 1_200_000_000
    assert evidence.parse_count("1,234") == 1234
    assert evidence.parse_count(560) == 560
    # 不猜：无法解析返回 None，绝不返回 0
    assert evidence.parse_count("很多") is None
    assert evidence.parse_count(True) is None
    assert evidence.parse_count(None) is None


def test_records_map_to_correct_item_types():
    records = [
        {"source": "weibo", "url": "https://weibo.com/1/a", "text": "微博正文",
         "author": "老张", "date": "2026-07-20",
         "engagement": {"likes": "1.2万", "comments": 340}},
        {"source": "xhs", "url": "https://www.xiaohongshu.com/explore/a",
         "title": "标题", "text": "正文", "author": "小鹿",
         "hashtags": ["AI", 123, "  "], "engagement": {"collects": 800}},
        {"source": "bilibili", "url": "https://www.bilibili.com/video/BV1xx411c7mD",
         "title": "横评", "author": "评测室", "duration": 812},
        {"source": "zhihu", "url": "https://www.zhihu.com/question/1/answer/2",
         "title": "问题", "text": "回答", "author": "工程师"},
        {"source": "douyin", "url": "https://www.douyin.com/video/7", "text": "视频"},
        {"source": "wechat", "url": "https://mp.weixin.qq.com/s/a", "title": "文章",
         "snippet": "摘要", "author": "硬件观察"},
        {"source": "baidu", "url": "https://news.example.com/a", "title": "报道"},
        {"source": "toutiao", "url": "https://www.toutiao.com/article/7", "title": "头条",
         "is_hot": True, "hot_value": "482万"},
    ]
    per_source, warnings = evidence.parse_records(records)

    assert not warnings
    assert per_source["weibo"][0].text == "微博正文"
    assert per_source["weibo"][0].author_handle == "老张"
    assert per_source["weibo"][0].engagement.likes == 12000
    assert per_source["weibo"][0].engagement.num_comments == 340

    xhs = per_source["xiaohongshu"][0]
    assert xhs.title == "标题" and xhs.desc == "正文"
    assert xhs.hashtags == ["AI"]  # 非字符串与空白被剔除
    assert xhs.engagement.collects == 800

    # bvid 未给出时从 URL 提取
    assert per_source["bilibili"][0].bvid == "BV1xx411c7mD"
    assert per_source["bilibili"][0].duration == 812

    assert per_source["zhihu"][0].excerpt == "回答"
    assert per_source["douyin"][0].text == "视频"
    assert per_source["wechat"][0].snippet == "摘要"
    # source_domain 缺失时从 URL 推导
    assert per_source["baidu"][0].source_domain == "news.example.com"
    assert per_source["toutiao"][0].is_hot is True
    assert per_source["toutiao"][0].hot_value == 4_820_000


def test_malformed_records_are_skipped_with_reasons():
    records = [
        {"source": "不认识的平台", "url": "https://a.com/1", "title": "x"},
        {"source": "weibo", "title": "缺 url"},
        {"source": "baidu", "url": "https://a.com/3"},          # 无标题无正文
        "不是对象",
        {"source": "xhs", "url": "javascript:alert(1)", "title": "非法协议"},
        {"source": "weibo", "url": "https://ok.com/x", "text": "好记录"},
    ]
    per_source, warnings = evidence.parse_records(records)

    assert len(warnings) == 5
    assert sum(len(v) for v in per_source.values()) == 1
    joined = " ".join(warnings)
    assert "无法识别" in joined
    assert "缺少 url" in joined
    assert "没有任何标题或正文" in joined
    assert "不是对象" in joined
    assert "非 http(s)" in joined


def test_parse_records_rejects_non_list():
    per_source, warnings = evidence.parse_records({"not": "a list"})
    assert sum(len(v) for v in per_source.values()) == 0
    assert warnings and "必须是数组" in warnings[0]


def test_ids_are_stable_and_unique():
    record = {"source": "weibo", "url": "https://a.com/1", "text": "x"}
    first, _ = evidence.parse_records([record])
    second, _ = evidence.parse_records([record])
    assert first["weibo"][0].id == second["weibo"][0].id

    other, _ = evidence.parse_records([{**record, "url": "https://a.com/2"}])
    assert other["weibo"][0].id != first["weibo"][0].id


def test_explicit_id_wins():
    per_source, _ = evidence.parse_records(
        [{"source": "weibo", "url": "https://a.com/1", "text": "x", "id": "WB9"}]
    )
    assert per_source["weibo"][0].id == "WB9"


def test_date_confidence_reflects_whether_date_was_given():
    per_source, _ = evidence.parse_records([
        {"source": "weibo", "url": "https://a.com/1", "text": "有日期", "date": "2026-07-01"},
        {"source": "weibo", "url": "https://a.com/2", "text": "无日期"},
    ])
    dated, undated = per_source["weibo"]
    assert dated.date == "2026-07-01" and dated.date_confidence == "high"
    assert undated.date is None and undated.date_confidence == "low"


def test_engagement_absent_when_not_provided():
    """不估算：调用方没给互动数就必须留空。"""
    per_source, _ = evidence.parse_records(
        [{"source": "weibo", "url": "https://a.com/1", "text": "x"}]
    )
    assert per_source["weibo"][0].engagement is None

    per_source, _ = evidence.parse_records(
        [{"source": "weibo", "url": "https://a.com/1", "text": "x",
          "engagement": {"unknown_field": 5, "likes": "无法解析"}}]
    )
    assert per_source["weibo"][0].engagement is None


def test_load_accepts_object_and_bare_array(tmp_path):
    obj = tmp_path / "obj.json"
    obj.write_text(json.dumps({
        "topic": "主题", "collected_via": "WebSearch",
        "records": [{"source": "weibo", "url": "https://a.com/1", "text": "x"}],
    }), encoding="utf-8")
    payload = evidence.load(str(obj))
    assert payload["topic"] == "主题"
    assert payload["collected_via"] == "WebSearch"
    assert payload["accepted"] == 1

    arr = tmp_path / "arr.json"
    arr.write_text(json.dumps(
        [{"source": "baidu", "url": "https://a.com/2", "title": "t"}]
    ), encoding="utf-8")
    payload = evidence.load(str(arr))
    assert payload["accepted"] == 1
    assert payload["topic"] is None


def test_load_reports_bad_input_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        evidence.load(str(tmp_path / "missing.json"))

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        evidence.load(str(broken))

    scalar = tmp_path / "scalar.json"
    scalar.write_text("42", encoding="utf-8")
    with pytest.raises(ValueError):
        evidence.load(str(scalar))


def test_template_is_valid_and_ingestible(tmp_path):
    text = evidence.template_json()
    parsed = json.loads(text)
    per_source, warnings = evidence.parse_records(parsed["records"])
    assert not warnings
    assert sum(len(v) for v in per_source.values()) == len(parsed["records"])


def test_unknown_author_is_left_empty_not_fabricated():
    """不得给缺失作者填占位名。

    score.apply_per_author_cap 每作者最多保留 3 条，但对空作者不设上限。
    若把未知作者写成"未知"，8 条无作者证据会被静默丢到只剩 3 条，
    而运行摘要还会把损失归因于日期窗口。
    """
    from lib import score

    records = [
        {"source": "weibo", "url": f"https://weibo.com/x/{i}", "text": f"不同内容{i}"}
        for i in range(8)
    ]
    per_source, warnings = evidence.parse_records(records)
    items = per_source["weibo"]

    assert not warnings
    assert len(items) == 8
    assert all(score.item_author(item) == "" for item in items)
    # 关键断言：无作者不触发上限
    assert len(score.apply_per_author_cap(items)) == 8
    assert all("未知" not in (item.author_handle or "") for item in items)


def test_real_shared_author_is_still_capped():
    """回归守卫：真实的同一作者仍应受上限约束。"""
    from lib import score

    records = [
        {"source": "weibo", "url": f"https://weibo.com/x/{i}",
         "text": f"不同内容{i}", "author": "同一个账号"}
        for i in range(8)
    ]
    per_source, _ = evidence.parse_records(records)
    assert len(score.apply_per_author_cap(per_source["weibo"])) == 3


def test_wechat_author_not_filled_with_domain():
    """公众号名缺失时不能用 mp.weixin.qq.com 冒充发布者。"""
    per_source, _ = evidence.parse_records([
        {"source": "wechat", "url": "https://mp.weixin.qq.com/s/abc", "title": "文章"}
    ])
    assert per_source["wechat"][0].source_name == ""


def test_baidu_source_domain_still_derived():
    """百度结果的 source_domain 本就是从链接推导的真实信息，应保留。"""
    per_source, _ = evidence.parse_records([
        {"source": "baidu", "url": "https://news.example.com/a", "title": "报道"}
    ])
    assert per_source["baidu"][0].source_domain == "news.example.com"
