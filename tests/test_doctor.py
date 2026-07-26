"""Tests for doctor-style diagnostics."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import doctor


UNBLOCKED_EGRESS = {
    "blocked": False,
    "partially_blocked": False,
    "checked": 2,
    "blocked_count": 0,
    "reason": "",
}

BLOCKED_EGRESS = {
    "blocked": True,
    "partially_blocked": False,
    "checked": 2,
    "blocked_count": 2,
    "reason": "Tunnel connection failed: 403 Forbidden",
}


def test_doctor_errors_include_fix_cli_when_source_has_no_working_path():
    config = {}
    with (
        # 必须 patch 出口预检，否则该测试会发真实网络请求，
        # 在受限出口环境下所有源都会被覆盖为 error。
        patch("lib.doctor.env.probe_egress", return_value=UNBLOCKED_EGRESS),
        patch("lib.doctor.env.is_weibo_available", return_value=False),
        patch("lib.doctor.env.is_xiaohongshu_available", return_value=True),
        patch("lib.doctor.env.probe_bilibili", return_value=False),
        patch("lib.doctor.env.probe_zhihu", return_value=True),
        patch("lib.doctor.env.is_douyin_available", return_value=False),
        patch("lib.doctor.env.is_wechat_available", return_value=False),
        patch("lib.doctor.env.is_baidu_api_available", return_value=False),
        patch("lib.doctor.env.probe_toutiao", return_value=True),
        patch("lib.doctor.crawler_bridge.get_crawler_status", return_value={
            "playwright_available": False,
            "cached_logins": [],
            "cookie_dir": "cookies",
        }),
    ):
        report = doctor.build_report(config)

    errors = [s for s in report["sources"] if s["status"] == "error"]
    assert errors
    assert all(s["fix_cli"] for s in errors)
    assert any(s["source"] == "bilibili" for s in errors)


def test_doctor_marks_all_sources_blocked_when_egress_denied():
    """出口被策略拒绝时，不得对任何源谎报"可用"。"""
    with (
        patch("lib.doctor.env.probe_egress", return_value=BLOCKED_EGRESS),
        patch("lib.doctor.crawler_bridge.get_crawler_status", return_value={
            "playwright_available": True,
            "cached_logins": ["weibo"],
            "cookie_dir": "cookies",
        }),
    ):
        report = doctor.build_report({"WEIBO_ACCESS_TOKEN": "t", "TIKHUB_API_KEY": "t"})

    assert report["summary"]["ok"] == 0
    assert report["summary"]["error"] == len(report["sources"])
    assert all(s["status"] == "error" for s in report["sources"])
    assert all(s["available"] is False for s in report["sources"])
    assert report["egress"]["blocked"] is True
    # 即便配置了 token 且 Playwright 可用，也不能声称可用
    joined_notes = " ".join(report["notes"])
    assert "出口" in joined_notes
    assert "无法绕过" in joined_notes


def test_doctor_text_shows_egress_banner_when_blocked():
    report = {
        "summary": {"ok": 0, "warn": 0, "error": 1},
        "sources": [{
            "source": "weibo", "label": "微博", "status": "error",
            "available": False, "reason": "被拦截", "fix": "", "fix_cli": "",
        }],
        "crawler_engine": {"playwright_available": False, "cached_logins": []},
        "egress": BLOCKED_EGRESS,
        "notes": [],
    }
    text = doctor.render_text(report)
    assert "出口被拦截" in text
    assert "凭据无法补救" in text


def test_doctor_text_has_no_egress_banner_when_unblocked():
    report = {
        "summary": {"ok": 1, "warn": 0, "error": 0},
        "sources": [{
            "source": "weibo", "label": "微博", "status": "ok",
            "available": True, "reason": "ok", "fix": "", "fix_cli": "",
        }],
        "crawler_engine": {"playwright_available": False, "cached_logins": []},
        "egress": UNBLOCKED_EGRESS,
        "notes": [],
    }
    text = doctor.render_text(report)
    assert "出口被拦截" not in text


def test_doctor_render_json_keeps_machine_fields():
    report = {
        "summary": {"ok": 1, "warn": 1, "error": 0},
        "sources": [
            {
                "source": "weibo",
                "label": "微博",
                "status": "ok",
                "available": True,
                "reason": "ok",
                "fix": "",
                "fix_cli": "",
            }
        ],
        "crawler_engine": {"playwright_available": True, "cached_logins": ["weibo"]},
        "notes": [],
    }
    payload = doctor.render_json(report)
    assert payload["summary"]["ok"] == 1
    assert payload["sources"][0]["source"] == "weibo"
    assert "crawler_engine" in payload
