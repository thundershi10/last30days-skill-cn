"""Tests: rendering must never report an egress block as "no recent discussion"."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import render, schema

BLOCKED = (
    "网络出口被策略拦截（代理拒绝 CONNECT）：Tunnel connection failed: 403 Forbidden"
)

SOURCES = ("weibo", "xiaohongshu", "bilibili", "zhihu",
           "douyin", "wechat", "baidu", "toutiao")


def _report(mode="all", errors=None, weibo_items=None):
    report = schema.create_report("测试主题", "2026-06-26", "2026-07-26", mode)
    for source in SOURCES:
        setattr(report, f"{source}_error", (errors or {}).get(source))
    if weibo_items:
        report.weibo = weibo_items
    return report


def _weibo_item(date="2026-07-01"):
    return schema.WeiboItem(
        id="WB1", text="正文", url="https://weibo.com/1/a",
        author_handle="作者", date=date, date_confidence="high",
    )


def test_all_blocked_replaces_sparse_message():
    """核心回归：全部源被拦截时不得输出"近期数据较少"。

    把"请求没发出去"说成"讨论不多"是错误归因，会直接误导最终判断。
    """
    report = _report(errors={s: BLOCKED for s in SOURCES})
    out = render.render_compact(report)

    assert "本次未采集到数据" in out
    assert "近期数据较少" not in out
    assert "这不代表相关话题没有讨论" in out


def test_all_blocked_notice_names_blocked_sources():
    report = _report(errors={"weibo": BLOCKED, "zhihu": BLOCKED})
    out = render.render_compact(report)
    assert "微博" in out and "知乎" in out


def test_partial_block_keeps_coverage_warning():
    report = _report(errors={"weibo": BLOCKED}, weibo_items=[_weibo_item()])
    out = render.render_compact(report)
    assert "部分数据源被拦截" in out
    assert "覆盖缺口" in out


def test_genuine_sparsity_still_reported():
    """真的没数据（非拦截）时，仍要提示数据稀疏——不能把这条也吞掉。"""
    report = _report(errors={"weibo": "HTTP 500: Server Error"})
    out = render.render_compact(report)
    assert "近期数据较少" in out
    assert "本次未采集到数据" not in out


def test_egress_diagnosis_flags():
    blocked = render.egress_diagnosis(_report(errors={s: BLOCKED for s in SOURCES}))
    assert blocked["blocked"] is True
    assert blocked["all_blocked"] is True

    partial = render.egress_diagnosis(
        _report(errors={"weibo": BLOCKED}, weibo_items=[_weibo_item()])
    )
    assert partial["blocked"] is True
    assert partial["all_blocked"] is False

    clean = render.egress_diagnosis(_report())
    assert clean["blocked"] is False
    assert clean["all_blocked"] is False


def test_platform_error_is_not_treated_as_block():
    diag = render.egress_diagnosis(_report(errors={"weibo": "HTTP 403: Forbidden"}))
    assert diag["blocked"] is False


def test_source_status_distinguishes_block_from_error():
    report = _report(errors={"weibo": BLOCKED, "zhihu": "HTTP 500: Server Error"})
    out = render.render_source_status(report)
    assert "⛔ 微博: 出口被拦截" in out
    assert "❌ 知乎: 错误" in out


def test_notice_appears_in_md_and_context_modes():
    report = _report(errors={s: BLOCKED for s in SOURCES})
    assert "本次未采集到数据" in render.render_full_report(report)
    assert "本次未采集到数据" in render.render_context_snippet(report)


def test_notice_appears_in_html_and_is_escaped():
    report = _report(errors={s: BLOCKED for s in SOURCES})
    html = render.render_html_report(report)
    assert "本次未采集到数据" in html
    assert "近期可确认数据较少" not in html
    # 提示文本不得引入未转义的尖括号
    assert "<script" not in html.lower()


def test_evidence_mode_declares_provenance():
    """证据注入产出的报告必须自述来源，避免被当成平台原生抓取引用。"""
    report = _report(mode="evidence", weibo_items=[_weibo_item()])
    for text in (
        render.render_compact(report),
        render.render_full_report(report),
        render.render_context_snippet(report),
    ):
        assert "证据注入模式" in text
        assert "非平台原生抓取" in text

    html = render.render_html_report(report)
    assert "证据注入模式" in html


def test_live_mode_has_no_provenance_banner():
    report = _report(mode="all", weibo_items=[_weibo_item()])
    assert "证据注入模式" not in render.render_compact(report)


def test_all_blocked_requires_no_source_succeeded_empty():
    """某源顺利跑完却确实 0 条 = 真实稀疏，不能归因于出口拦截。

    否则会压制真实的稀疏提示，并让调用方跳过缓存。
    """
    report = _report(errors={"weibo": BLOCKED})
    # zhihu / baidu 无 error 即视为"成功但 0 条"
    report.attempted_sources = ["weibo", "zhihu", "baidu"]
    diag = render.egress_diagnosis(report)

    assert diag["blocked"] is True
    assert diag["all_blocked"] is False

    out = render.render_compact(report)
    assert "本次未采集到数据" not in out
    assert "部分数据源被拦截" in out
    assert "近期数据较少" in out          # 真实稀疏提示必须保留


def test_all_blocked_true_when_every_attempted_source_blocked():
    report = _report(errors={"weibo": BLOCKED, "zhihu": BLOCKED})
    report.attempted_sources = ["weibo", "zhihu"]
    diag = render.egress_diagnosis(report)

    assert diag["all_blocked"] is True
    assert "本次未采集到数据" in render.render_compact(report)


def test_legacy_report_without_attempted_sources_still_works():
    """旧缓存报告没有 attempted_sources，需保持向后兼容。"""
    report = _report(errors={s: BLOCKED for s in SOURCES})
    report.attempted_sources = []
    assert render.egress_diagnosis(report)["all_blocked"] is True


def test_all_blocked_when_other_sources_errored_for_other_reasons():
    """其余源是"报错"而非"成功返回 0 条"时，本轮仍是颗粒无收 + 存在拦截。

    此时必须给出明确的出口拦截结论，不能退回"近期数据较少"这种错误归因
    （默认配置下小红书走本地后端，连不上会报错，正是这种情况）。
    """
    report = _report(errors={
        "weibo": BLOCKED, "zhihu": BLOCKED,
        "xiaohongshu": "URLError: Connection refused",
    })
    report.attempted_sources = ["weibo", "zhihu", "xiaohongshu"]

    assert render.egress_diagnosis(report)["all_blocked"] is True
    out = render.render_compact(report)
    assert "本次未采集到数据" in out
    assert "近期数据较少" not in out
