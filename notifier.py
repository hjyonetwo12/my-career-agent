"""飞书群自定义机器人推送模块 — 支持运行统计摘要、高价值岗位、无新增通知、部署状态与HTTP可达性校验及防重复刷屏。"""

import argparse
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


def _normalize_base_or_page_url(raw_url: str) -> str:
    """将基础域名或含 /index.html 的完整地址标准化为不带末尾斜杠的基础 URL。"""
    u = (raw_url or "").strip().rstrip("/")
    if not u:
        return ""
    if u.endswith("/index.html"):
        u = u[: -len("/index.html")].rstrip("/")
    return u


def _resolve_report_base_url(deployed_url: str = "") -> str:
    """按优先级解析正式报告基础 URL：
    1. 部署步骤动态返回的实际地址 deployed_url（或环境变量 DEPLOYED_REPORT_URL）
    2. 显式配置的环境变量 REPORT_BASE_URL（如自定义 Cloudflare Pages 或 GitHub Pages 域名）
    3. GitHub Actions 自动注入的 GITHUB_REPOSITORY（自动推导为 https://<owner>.github.io/<repo>）
    4. 本地未配置外部域名时返回空字符串，绝不回退任何第三方或原作者硬编码旧域名。
    """
    dynamic = _normalize_base_or_page_url(deployed_url or os.environ.get("DEPLOYED_REPORT_URL") or "")
    if dynamic:
        return dynamic

    explicit = _normalize_base_or_page_url(os.environ.get("REPORT_BASE_URL") or "")
    if explicit:
        return explicit

    gh_repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip().strip("/")
    if "/" in gh_repo:
        owner, repo = gh_repo.split("/", 1)
        owner = owner.strip()
        repo = repo.strip()
        if owner and repo:
            if repo.lower() == f"{owner.lower()}.github.io":
                return f"https://{owner}.github.io"
            return f"https://{owner}.github.io/{repo}"
    return ""


def _report_url(date_str: str = "", report_version_id: str = "", deployed_url: str = "") -> str:
    """返回完整 HTML 报告链接（带可选版本查询参数防缓存；无有效公开域名时返回空字符串）。"""
    base = _resolve_report_base_url(deployed_url=deployed_url)
    if not base:
        return ""
    url = f"{base}/index.html"
    ver = (report_version_id or "").strip()
    if ver:
        return f"{url}?v={ver}"
    return url


def verify_deployed_report_url(
    url: str,
    expected_version_id: str = "",
    timeout: int = 10,
    retries: int = 4,
    wait_seconds: float = 4.0,
) -> dict:
    """对部署后的在线报告 URL 执行真实 HTTP 状态码与内嵌版本号校验。

    能确认的内容：
      - 目标网址是否真实返回 HTTP 200（区分正常可访问 vs HTTP 404 未开启 Pages/私有仓库限制 vs 网络超时）；
      - 返回的 HTML 是否为本项目的校招情报报告（包含 AI校招情报 或 career-agent-report-version 标记）；
      - 页面内嵌的 report_version_id 是否已同步为本轮最新生成的版本（或 CDN 正在刷新缓存）。
    不能确认的内容：
      - 无法代替用户手机端特定运营商网络环境（如校园网/移动数据防火墙）做端到端渲染测试；
      - 无法在无外网权限的纯离线沙盒中发起公网请求。
    """
    target = (url or "").strip()
    if not target:
        return {
            "reachable": False,
            "status_code": 0,
            "version_matched": False,
            "check_status": "no_url",
            "message": "未配置公开报告域名，跳过在线 HTTP 可达性检查",
        }

    last_status = 0
    last_err = ""
    headers = {
        "User-Agent": "CareerAgent-DeployVerifier/1.0",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }

    for attempt in range(1, max(1, retries) + 1):
        try:
            resp = requests.get(target, headers=headers, timeout=timeout, allow_redirects=True)
            last_status = resp.status_code
            if resp.status_code == 200:
                body_head = resp.text[:15000]
                is_report_html = (
                    "career-agent-report-version" in body_head
                    or "AI校招情报" in body_head
                    or "window.__REPORT_META" in body_head
                )
                if not is_report_html:
                    return {
                        "reachable": False,
                        "status_code": 200,
                        "version_matched": False,
                        "check_status": "invalid_content",
                        "message": "目标网址返回 HTTP 200，但页面内容不是 AI 校招情报报告，请检查部署目录配置",
                    }
                if not expected_version_id or expected_version_id in body_head:
                    return {
                        "reachable": True,
                        "status_code": 200,
                        "version_matched": True,
                        "check_status": "verified_latest",
                        "message": f"在线网页已验证可访问 (HTTP 200)，且版本号已确认为最新 ({expected_version_id or 'latest'})",
                    }
                # HTTP 200 且是报告，但 CDN 节点可能尚未刷新到最新版本号，等待后重试
                if attempt < retries and wait_seconds > 0:
                    time.sleep(wait_seconds)
                    continue
                return {
                    "reachable": True,
                    "status_code": 200,
                    "version_matched": False,
                    "check_status": "verified_live_cdn_caching",
                    "message": (
                        f"在线网页已可正常打开 (HTTP 200)，GitHub Pages CDN 正在刷新至最新版本 {expected_version_id}（约 1~2 分钟生效）"
                    ),
                }

            if resp.status_code == 404:
                if attempt < retries and wait_seconds > 0:
                    time.sleep(wait_seconds)
                    continue
                return {
                    "reachable": False,
                    "status_code": 404,
                    "version_matched": False,
                    "check_status": "http_404_pages_not_enabled",
                    "message": (
                        "网页分支 (gh-pages) 已推送，但网址返回 HTTP 404（原因：尚未在 GitHub 仓库 Settings → Pages 中将 Branch 设为 gh-pages，或当前仓库为 Private 私有且账号不支持免费私有 Pages）"
                    ),
                }

            if attempt < retries and wait_seconds > 0:
                time.sleep(wait_seconds)
                continue
        except Exception as exc:
            last_err = job_filters.sanitize_sensitive_text(str(exc))[:120]
            if attempt < retries and wait_seconds > 0:
                time.sleep(wait_seconds)
                continue

    if last_status > 0:
        return {
            "reachable": False,
            "status_code": last_status,
            "version_matched": False,
            "check_status": f"http_{last_status}",
            "message": f"在线报告地址返回 HTTP {last_status}，请检查 GitHub Pages 部署状态与访问权限",
        }
    return {
        "reachable": False,
        "status_code": 0,
        "version_matched": False,
        "check_status": "network_unreachable",
        "message": f"无法连接在线报告地址（{last_err or '网络超时'}），请检查域名或稍后重试",
    }


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
        if (a.get("match_score") or 0) < _HIGH_SCORE_THRESHOLD:
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
    """将运行统计、评分明细与分阶段异常状态渲染为飞书富文本行。"""
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
    local_scored = int(run_stats.get("local_scored_count", 0))
    advice_ver = run_stats.get("advice_version_id") or "—"
    advice_strategy = run_stats.get("advice_strategy") or "no_change"
    report_ver = run_stats.get("report_version_id") or ""
    deploy_status = str(run_stats.get("deploy_status") or "").strip().lower()
    pipeline_status = str(run_stats.get("pipeline_status") or "").strip().lower()
    url_verify_status = str(run_stats.get("url_verify_status") or "").strip().lower()
    url_verify_msg = str(run_stats.get("url_verify_message") or "").strip()

    rows.append([{"tag": "text", "text": "━━━ 运行摘要与阶段状态 ━━━"}])
    rows.append([{"tag": "text", "text": f"⏱️ 运行时间: {started} → {finished}"}])
    rows.append([{
        "tag": "text",
        "text": (
            f"📥 采集与去重: 抓取 {crawled_total} 条 | 新增 {new_cnt} 条 | "
            f"更新 {updated_cnt} 条 | 去重合并 {dedup_cnt} 条"
        ),
    }])
    score_line = (
        f"🎯 筛选与评分: 通过筛选 {passed_cnt} 条 | "
        f"AI评分成功 {scored_ok} 条 | 评分失败 {scored_fail} 条"
    )
    if local_scored > 0:
        score_line += f" | 五维规则覆盖 {local_scored} 条"
    rows.append([{"tag": "text", "text": score_line}])
    ver_line = f"🧭 综合建议闭环: 版本 {advice_ver} ({advice_strategy})"
    if report_ver:
        ver_line += f" | 报告版本 {report_ver}"
    rows.append([{"tag": "text", "text": ver_line}])

    alerts = []
    if pipeline_status in ("failure", "cancelled", "failed"):
        alerts.append("❌ 流水线异常: 本次主程序执行未成功，请检查 GitHub Actions 运行日志")
    if deploy_status in ("failure", "cancelled", "failed"):
        alerts.append(
            "❌ 网页部署失败: 本次报告未能发布至 GitHub Pages，为防旧网页冒充新报告已拦截旧链接，请在 GitHub Actions Artifacts 下载最新报告包"
        )
    elif url_verify_status == "http_404_pages_not_enabled":
        alerts.append(f"⚠️ 网页未激活 (HTTP 404): {url_verify_msg}")
    elif url_verify_status in ("invalid_content", "network_unreachable") or url_verify_status.startswith("http_"):
        alerts.append(f"⚠️ 网页可达性校验提醒: {url_verify_msg}")
    elif deploy_status == "success":
        if url_verify_msg:
            alerts.append(f"✅ 网页部署成功: {url_verify_msg}")
        else:
            alerts.append("✅ 网页部署成功: 最新报告已发布至正式分支并完成版本校验")

    if run_stats.get("crawl_failed"):
        alerts.append("⚠️ 抓取异常: 所有企业爬虫均未返回岗位，请检查网络或目标站点状态")
    elif run_stats.get("failed_companies_count", 0) > 0:
        alerts.append(f"ℹ️ 部分站点空窗/超时: {run_stats['failed_companies_count']} 家未返回列表（已隔离，不影响主库）")
    if scored_fail > 0:
        alerts.append(f"⚠️ AI评分异常: {scored_fail} 个岗位评分未完成（已保留待下次自动重试）")
    if run_stats.get("advice_error"):
        alerts.append(f"⚠️ 综合建议刷新异常: {job_filters.sanitize_sensitive_text(str(run_stats['advice_error']))[:80]}（已回退保留上一份有效建议）")
    if new_cnt == 0 and not run_stats.get("crawl_failed") and pipeline_status not in ("failure", "cancelled", "failed"):
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
    stats_obj = run_stats or report_data.get("run_stats") or {}
    report_meta = report_data.get("report_meta") or {}
    report_version_id = (
        stats_obj.get("report_version_id")
        or report_meta.get("report_version_id")
        or ""
    )
    deploy_status = str(
        stats_obj.get("deploy_status")
        or report_data.get("deploy_status")
        or ""
    ).strip().lower()
    deployed_url = str(
        stats_obj.get("deployed_url")
        or report_data.get("deployed_url")
        or ""
    ).strip()
    url_verify_status = str(stats_obj.get("url_verify_status") or "").strip().lower()

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
        if a.get("recommendation") in ("不推荐", "未评估", "资格不符"):
            continue
        if (a.get("match_score") or 0) < _HIGH_SCORE_THRESHOLD:
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
        if it.get("analysis")
        and (it["analysis"].get("match_score") or 0) >= _HIGH_SCORE_THRESHOLD
        and not _is_intern_title(it["job"].get("title", ""))
        and _is_current_cohort(it["job"])
        and it["analysis"].get("recommendation") not in ("不推荐", "未评估", "资格不符")
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

    # 段 4：完整报告直达链接（严格校验部署状态，部署失败或未验证通过时绝不误导）
    content_rows.append([{"tag": "text", "text": ""}])
    if deploy_status in ("failure", "cancelled", "failed"):
        content_rows.append([{
            "tag": "text",
            "text": "⚠️ 本次网页部署失败，已拦截旧版网页链接（请前往 GitHub Actions 下载最新 career-agent-report Artifact 查看）",
        }])
    elif url_verify_status in ("http_404_pages_not_enabled", "invalid_content", "network_unreachable") or url_verify_status.startswith("http_"):
        pending_url = _report_url(today, report_version_id=report_version_id, deployed_url=deployed_url)
        content_rows.append([{
            "tag": "text",
            "text": (
                "⚠️ 在线报告网址尚未就绪（校验未通过，为防打不开暂不标注为已上线）：\n"
                "   1. 请确认 GitHub 仓库为 Public（或账号具备 GitHub Pro），进入 Settings → Pages；\n"
                "   2. 在 Build and deployment 下选择 Deploy from a branch，Branch 选 gh-pages 与 / (root) 并点击 Save；\n"
                f"   3. 保存约 1 分钟后即可点击打开：{pending_url or '未配置域名'}"
            ),
        }])
    else:
        url = _report_url(today, report_version_id=report_version_id, deployed_url=deployed_url)
        if url:
            link_label = (
                f"打开最新校招情报 · 完整报告 ({report_version_id})"
                if report_version_id
                else "打开最新校招情报 · 完整报告"
            )
            content_rows.append([
                {"tag": "text", "text": "🌐 "},
                {"tag": "a", "text": link_label, "href": url},
            ])
            if report_version_id:
                content_rows.append([{
                    "tag": "text",
                    "text": f"   🔖 版本核对: 打开网页后顶部显示「🔖 版本：{report_version_id}」即为最新报告",
                }])
        else:
            content_rows.append([{
                "tag": "text",
                "text": "📄 本地报告已生成至 reports/index.html（如需飞书直接打开在线网页，请在 GitHub Actions 中运行并启用 GitHub Pages，或配置 REPORT_BASE_URL）",
            }])

    title_ver = f" [{report_version_id}]" if report_version_id else ""
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"🤖 AI校招情报 {today}{title_ver}",
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


def send_from_workspace(
    deploy_status: str = "",
    pipeline_status: str = "success",
    deployed_url: str = "",
    verify_url: bool = False,
    db_path: str | Path | None = None,
    summary_path: str | Path | None = None,
    report_meta_path: str | Path | None = None,
) -> bool:
    """从数据库、运行摘要与报告元数据读取最新状态，可选校验在线 URL 可达性后推送到飞书。"""
    import db as db_module

    root = Path(__file__).resolve().parent
    dpath = Path(db_path) if db_path else root / "data" / "jobs.db"
    spath = Path(summary_path) if summary_path else root / "outputs" / "latest_run_summary.json"
    mpath = Path(report_meta_path) if report_meta_path else root / "reports" / "report_meta.json"

    run_stats = {}
    if spath.exists():
        try:
            run_stats = json.loads(spath.read_text(encoding="utf-8")) or {}
        except Exception:
            run_stats = {}

    report_meta = {}
    if mpath.exists():
        try:
            report_meta = json.loads(mpath.read_text(encoding="utf-8")) or {}
        except Exception:
            report_meta = {}

    if deploy_status:
        run_stats["deploy_status"] = deploy_status
    if pipeline_status:
        run_stats["pipeline_status"] = pipeline_status
    if deployed_url:
        run_stats["deployed_url"] = deployed_url
    if report_meta.get("report_version_id"):
        run_stats["report_version_id"] = report_meta["report_version_id"]

    scan_date = report_meta.get("scan_date") or date.today().isoformat()
    ver_id = run_stats.get("report_version_id") or ""

    # 若部署步骤成功且开启了在线 URL 校验，则实际探测目标网页是否返回 HTTP 200
    if verify_url and str(deploy_status).strip().lower() == "success":
        target_url = _report_url(scan_date, report_version_id=ver_id, deployed_url=deployed_url)
        if target_url:
            check_res = verify_deployed_report_url(target_url, expected_version_id=ver_id)
            run_stats["url_verify_status"] = check_res["check_status"]
            run_stats["url_verify_message"] = check_res["message"]
            logger.info("在线报告可达性校验结果 (%s): %s - %s", target_url, check_res["check_status"], check_res["message"])

    all_items = []
    notify_new = []
    if dpath.exists():
        conn = db_module.init_db(str(dpath))
        try:
            all_items = db_module.get_all_jobs_with_analysis(conn)
            latest_crawl = db_module.get_latest_crawl_date(conn)
            active = db_module.get_active_jobs(conn)
            notify_new = [
                j for j in active
                if job_cohorts.is_confirmed_current(j) and j.get("crawled_at") == latest_crawl
            ]
        finally:
            conn.close()

    report_data = {
        "date": scan_date,
        "run_stats": run_stats,
        "report_meta": report_meta,
        "deploy_status": deploy_status,
        "deployed_url": deployed_url,
    }
    return send(notify_new, all_items, report_data, run_stats=run_stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Feishu notification after report deployment.")
    parser.add_argument("--deploy-status", default="", help="Deployment step outcome (success/failure/skipped)")
    parser.add_argument("--pipeline-status", default="success", help="Pipeline step outcome (success/failure)")
    parser.add_argument("--deployed-url", default="", help="Actual deployed report URL returned by Pages/hosting")
    parser.add_argument("--verify-url", action="store_true", help="Verify live HTTP 200 reachability of the report URL before sending")
    parser.add_argument("--db", default="", help="Optional path to jobs.db")
    parser.add_argument("--summary", default="", help="Optional path to latest_run_summary.json")
    parser.add_argument("--report-meta", default="", help="Optional path to report_meta.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    send_from_workspace(
        deploy_status=args.deploy_status,
        pipeline_status=args.pipeline_status,
        deployed_url=args.deployed_url,
        verify_url=args.verify_url,
        db_path=args.db or None,
        summary_path=args.summary or None,
        report_meta_path=args.report_meta or None,
    )


if __name__ == "__main__":
    main()