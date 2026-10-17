"""飞书群自定义机器人推送模块 — 支持运行统计摘要、高价值岗位、无新增通知、分阶段异常提醒与防重复刷屏。"""

import hashlib
import json
import logging
import os
import time
from collections import Counter
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import job_filters
import job_cohorts
import requests

logger = logging.getLogger(__name__)

_CHINA_TZ = timezone(timedelta(hours=8))

# Feishu webhook 单条消息上限为 30KB。除了限制岗位数量，还要限制每条文本，
# 否则大量新增岗位或异常长的 AI 分析仍可能让整条消息被拒绝。
_MAX_NEW_JOB_DETAILS = 8
_MAX_HIGH_SCORE_ROWS = 20
_HIGH_SCORE_THRESHOLD = 60
_CITY_TRUNCATE_LEN = 20
_JOB_LABEL_TRUNCATE_LEN = 60
_ANALYSIS_TRUNCATE_LEN = 120

_GAP_NOISE_KEYWORDS = ("专业方向", "学历要求", "经验要求", "工作年限", "不限")
DEFAULT_NOTIFY_STATE_PATH = Path(__file__).resolve().parent / "data" / "notify_state.json"


def _is_intern_title(title: str) -> bool:
    return job_filters.is_intern_title(title)


def _is_current_cohort(job: dict) -> bool:
    cohort = job_filters.cohort_year(job)
    if cohort is not None and cohort < job_cohorts.CURRENT_COHORT:
        return False
    return (
        job_filters.is_formal_campus_job(job)
        and not job_filters.is_direction_out_job(job)
        and job_cohorts.is_confirmed_current(job)
    )


def _truncate(text: str, max_len: int = _CITY_TRUNCATE_LEN) -> str:
    s = (text or "").strip()
    return s if len(s) <= max_len else s[: max_len] + "…"


_REPORT_BASE_URL = "https://career-agent-4z4.pages.dev"


def _report_url(date_str: str = "") -> str:
    """返回完整 HTML 报告链接（支持环境变量 REPORT_BASE_URL 覆盖）。"""
    base = os.environ.get("REPORT_BASE_URL", _REPORT_BASE_URL).strip().rstrip("/")
    return f"{base}/index.html"


def _format_new_job(item: dict) -> list[list[dict]]:
    """渲染单条新增岗位 → Feishu post 内容里的若干行。"""
    job = item["job"]
    a = item["analysis"]
    score = a.get("match_score", "?")
    rec = a.get("recommendation", "未分析")
    advantages = _truncate("、".join(a.get("advantages", []) or []), _ANALYSIS_TRUNCATE_LEN) or "—"
    gaps = _truncate("、".join(a.get("gaps", []) or []), _ANALYSIS_TRUNCATE_LEN) or "—"
    city = _truncate(job.get("city", "")) or "—"
    company = _truncate(job.get("company", ""), _JOB_LABEL_TRUNCATE_LEN)
    title = _truncate(job.get("title", ""), _JOB_LABEL_TRUNCATE_LEN)

    head = f"🆕 [{company}] {title}  [{score}分][{rec}]"
    row1 = [{"tag": "text", "text": head}]
    row2 = [{"tag": "text", "text": f"   📍 {city}"}]
    row3 = [{"tag": "text", "text": f"   ✅ 优势: {advantages}"}]
    row4 = [{"tag": "text", "text": f"   ⚠️ 缺口: {gaps}"}]
    row5 = [{"tag": "text", "text": "   🔗 "},
            {"tag": "a", "text": "查看岗位", "href": job["jd_url"]}]
    return [row1, row2, row3, row4, row5]


def _format_high_score_row(item: dict) -> list[dict]:
    """高分岗位汇总单行：[分数][推荐] 公司 | 岗位 | 城市 | 链接"""
    job = item["job"]
    a = item["analysis"]
    city = _truncate(job.get("city", "")) or "—"
    company = _truncate(job.get("company", ""), _JOB_LABEL_TRUNCATE_LEN)
    title = _truncate(job.get("title", ""), _JOB_LABEL_TRUNCATE_LEN)
    line = f"[{a['match_score']}分][{a['recommendation']}] {company} | {title} | {city}  "
    return [
        {"tag": "text", "text": line},
        {"tag": "a", "text": "🔗", "href": job["jd_url"]},
    ]


def _collect_top_gaps(all_jobs: list[dict], top_n: int = 5) -> list[tuple[str, int]]:
    """只统计高分岗位（score >= 60）的 gaps；过滤"无XX"开头和噪声词。"""
    gaps: list[str] = []
    for it in all_jobs:
        a = it.get("analysis") or {}
        if a.get("match_score", 0) < _HIGH_SCORE_THRESHOLD:
            continue
        for g in (a.get("gaps", []) or []):
            g = (g or "").strip()
            if not g or g.startswith("无"):
                continue
            if any(kw in g for kw in _GAP_NOISE_KEYWORDS):
                continue
            gaps.append(g)
    return Counter(gaps).most_common(top_n)


def _format_run_stats_rows(run_stats: dict) -> list[list[dict]]:
    """将运行统计与分阶段异常状态渲染为飞书富文本行。"""
    rows: list[list[dict]] = []
    started = run_stats.get("started_at") or "—"
    finished = run_stats.get("finished_at") or datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    crawled_total = int(run_stats.get("crawled_total", 0))
    new_cnt = int(run_stats.get("new_inserted_count", 0))
    updated_cnt = int(run_stats.get("updated_existing_count", 0))
    dedup_cnt = int(run_stats.get("deduped_count", 0))
    passed_cnt = int(run_stats.get("passed_filter_count", 0))
    scored_ok = int(run_stats.get("scored_success_count", 0))
    scored_fail = int(run_stats.get("scored_failed_count", 0))
    advice_ver = run_stats.get("advice_version_id") or "—"
    advice_strategy = run_stats.get("advice_strategy") or "no_change"

    rows.append([{"tag": "text", "text": "━━━ 运行摘要与阶段状态 ━━━"}])
    rows.append([{"tag": "text", "text": f"⏱️ 运行时间: {started} → {finished}"}])
    rows.append([{
        "tag": "text",
        "text": (
            f"📥 采集与去重: 抓取 {crawled_total} 条 | 新增 {new_cnt} 条 | "
            f"更新 {updated_cnt} 条 | 去重合并 {dedup_cnt} 条"
        ),
    }])
    rows.append([{
        "tag": "text",
        "text": (
            f"🎯 筛选与评分: 通过筛选 {passed_cnt} 条 | "
            f"AI评分成功 {scored_ok} 条 | 评分失败 {scored_fail} 条"
        ),
    }])
    rows.append([{
        "tag": "text",
        "text": f"🧭 综合建议闭环: 版本 {advice_ver} ({advice_strategy})",
    }])

    # 明确区分：抓取失败、评分失败、无新增岗位与正常状态
    alerts = []
    if run_stats.get("crawl_failed"):
        alerts.append("⚠️ 抓取异常: 所有企业爬虫均未返回岗位，请检查网络或目标站点状态")
    elif run_stats.get("failed_companies_count", 0) > 0:
        alerts.append(f"ℹ️ 部分站点空窗/超时: {run_stats['failed_companies_count']} 家未返回列表（已隔离，不影响主库）")
    if scored_fail > 0:
        alerts.append(f"⚠️ AI评分异常: {scored_fail} 个岗位评分未完成（已保留待下次自动重试）")
    if run_stats.get("advice_error"):
        alerts.append(f"⚠️ 综合建议刷新异常: {job_filters.sanitize_sensitive_text(str(run_stats['advice_error']))[:80]}（已回退保留上一份有效建议）")
    if new_cnt == 0 and not run_stats.get("crawl_failed"):
        alerts.append("✅ 本轮无新增岗位（爬虫与筛选链路运行正常）")

    for al in alerts:
        rows.append([{"tag": "text", "text": al}])
    rows.append([{"tag": "text", "text": ""}])
    return rows


def _build_payload(
    new_jobs: list[dict],
    all_jobs: list[dict],
    report_data: dict,
    run_stats: dict | None = None,
    compact_repeat: bool = False,
) -> dict:
    """组装 Feishu post 富文本 payload。"""
    today = report_data.get("date") or date.today().isoformat()
    stats_obj = run_stats or report_data.get("run_stats")

    content_rows: list[list[dict]] = []
    if isinstance(stats_obj, dict) and stats_obj:
        content_rows.extend(_format_run_stats_rows(stats_obj))

    # 段 1：今日新增（按相同口径过滤：score >= 60 + 非实习 + 未标不推荐）
    analysis_by_url = {
        it["job"]["jd_url"]: it["analysis"]
        for it in all_jobs
        if it.get("analysis")
    }
    filtered_new = []
    for nj in new_jobs:
        if _is_intern_title(nj.get("title", "")):
            continue
        if not _is_current_cohort(nj):
            continue
        a = analysis_by_url.get(nj["jd_url"])
        if not a:
            continue
        if a.get("recommendation") == "不推荐":
            continue
        if a.get("match_score", 0) < _HIGH_SCORE_THRESHOLD:
            continue
        filtered_new.append({"job": nj, "analysis": a})

    skipped = len(new_jobs) - len(filtered_new)
    header = f"━━━ 今日新增推荐 ({len(filtered_new)} 个)"
    if skipped:
        header += f"  ·  已过滤 {skipped} 个不相关"
    header += " ━━━"
    content_rows.append([{"tag": "text", "text": header}])

    displayed_new = filtered_new[:_MAX_NEW_JOB_DETAILS]
    if displayed_new:
        for item in displayed_new:
            content_rows.extend(_format_new_job(item))
            content_rows.append([{"tag": "text", "text": ""}])
        if len(filtered_new) > len(displayed_new):
            content_rows.append([{
                "tag": "text",
                "text": f"其余 {len(filtered_new) - len(displayed_new)} 个相关新增岗位请在完整报告中查看。",
            }])
    else:
        content_rows.append([{"tag": "text", "text": "今日无相关新增岗位"}])

    # 段 2：全部追踪高分岗位（score >= 60，过滤实习，降序）
    high = [
        it for it in all_jobs
        if it["analysis"]
        and it["analysis"].get("match_score", 0) >= _HIGH_SCORE_THRESHOLD
        and not _is_intern_title(it["job"].get("title", ""))
        and _is_current_cohort(it["job"])
        and it["analysis"].get("recommendation") != "不推荐"
    ]
    high.sort(key=lambda x: x["analysis"]["match_score"], reverse=True)
    truncated = len(high) > _MAX_HIGH_SCORE_ROWS
    high_slice = high[:_MAX_HIGH_SCORE_ROWS]

    title_suffix = f"（截断显示前 {_MAX_HIGH_SCORE_ROWS} 个）" if truncated else ""
    content_rows.append([{"tag": "text", "text": ""}])
    content_rows.append([{
        "tag": "text",
        "text": f"━━━ 全部追踪高分岗位 (score≥{_HIGH_SCORE_THRESHOLD}, 共 {len(high_slice)} 个){title_suffix} ━━━",
    }])
    if compact_repeat and not displayed_new:
        content_rows.append([{
            "tag": "text",
            "text": f"（今日已推送过相同的 {len(high_slice)} 个高分岗位列表，本次自动折叠防刷屏，详情请点下方完整报告）",
        }])
    elif high_slice:
        for it in high_slice:
            content_rows.append(_format_high_score_row(it))
            content_rows.append([{"tag": "text", "text": ""}])
    else:
        content_rows.append([{"tag": "text", "text": "暂无高分岗位"}])

    # 段 3：统计
    today_iso = date.today().isoformat()
    active_count = sum(1 for it in all_jobs if it["job"].get("last_seen_at") == today_iso)
    top_gaps = _collect_top_gaps(all_jobs)

    content_rows.append([{"tag": "text", "text": ""}])
    content_rows.append([{"tag": "text", "text": "━━━ 统计 ━━━"}])
    content_rows.append([{"tag": "text", "text": f"DB 共收录 {len(all_jobs)} 个岗位（活跃 {active_count} 个）"}])
    content_rows.append([{"tag": "text", "text": "高频缺口 Top5:"}])
    if top_gaps:
        for g, c in top_gaps:
            content_rows.append([{"tag": "text", "text": f"  • {g} ({c})"}])
    else:
        content_rows.append([{"tag": "text", "text": "  —"}])

    # 段 4：完整报告链接
    url = _report_url(today)
    if url:
        content_rows.append([{"tag": "text", "text": ""}])
        content_rows.append([
            {"tag": "text", "text": "📄 "},
            {"tag": "a", "text": "完整报告", "href": url},
        ])

    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"🤖 AI校招情报 {today}",
                    "content": content_rows,
                }
            }
        },
    }


def _compute_push_digest(today: str, new_jobs: list[dict], all_jobs: list[dict]) -> str:
    new_urls = sorted(str(j.get("jd_url") or "") for j in new_jobs)
    high_urls = sorted(
        str(it["job"].get("jd_url") or "")
        for it in all_jobs
        if it.get("analysis") and (it["analysis"].get("match_score") or 0) >= _HIGH_SCORE_THRESHOLD
    )[:25]
    raw = json.dumps({"date": today, "new": new_urls, "high": high_urls}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def send(
    new_jobs: list[dict],
    all_jobs: list[dict],
    report_data: dict,
    run_stats: dict | None = None,
    state_path: str | Path | None = None,
    retries: int = 3,
) -> bool:
    """推送到飞书。支持同日防刷屏折叠、HTTP 退避重试与日志密钥脱敏。"""
    webhook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        logger.info("未设置 FEISHU_WEBHOOK，跳过推送")
        return False

    today = report_data.get("date") or date.today().isoformat()
    dedup_enabled = os.environ.get("DEDUP_FEISHU_PUSH") == "1" or bool(report_data.get("dedup_push"))
    npath = Path(state_path) if state_path else DEFAULT_NOTIFY_STATE_PATH
    digest = _compute_push_digest(today, new_jobs, all_jobs)

    compact_repeat = False
    if dedup_enabled and npath.exists():
        try:
            prev_state = json.loads(npath.read_text(encoding="utf-8"))
            if prev_state.get("date") == today and prev_state.get("digest") == digest:
                compact_repeat = True
        except Exception:
            pass

    payload = _build_payload(
        new_jobs, all_jobs, report_data, run_stats=run_stats, compact_repeat=compact_repeat
    )

    for attempt in range(1, max(1, retries) + 1):
        try:
            resp = requests.post(webhook, json=payload, timeout=15)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(8, 2 ** attempt))
                continue
            if resp.status_code != 200:
                logger.warning(
                    "飞书推送返回 %d: %s",
                    resp.status_code,
                    job_filters.sanitize_sensitive_text(resp.text[:200]),
                )
                return False
            body = resp.json()
            if body.get("code", 0) != 0:
                logger.warning("飞书推送失败: %s", job_filters.sanitize_sensitive_text(str(body)))
                return False
            logger.info("飞书推送成功")
            if dedup_enabled:
                try:
                    npath.parent.mkdir(parents=True, exist_ok=True)
                    npath.write_text(
                        json.dumps({"date": today, "digest": digest, "pushed_at": datetime.now(_CHINA_TZ).isoformat()}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return True
        except Exception as e:
            if attempt < retries:
                time.sleep(min(8, 2 ** attempt))
                continue
            logger.warning("飞书推送异常: %s", job_filters.sanitize_sensitive_text(str(e)))
            return False
    return False
