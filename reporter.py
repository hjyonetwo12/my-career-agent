"""HTML 报告生成器 — 单文件 SPA 风格，左侧导航（含顶部全局目录 + 底部综合建议）+ 多页面切换。

升级功能概览：
1. 阶段 B（可筛选的岗位分类体系）：支持一主多辅职业类别（游戏引擎、图形学与三维、系统软件与基础设施、
   后端与分布式、AI与机器学习、嵌入式与设备、测试质量与开发工具、数据与存储、安全工程、客户端与跨平台、
   其他技术岗、待分类），支持类别筛选栏、清除筛选、关联公司联动与去重统计。
2. 阶段 C（品牌名称与动态校招季节）：固定品牌名称为「AI校招情报」，基于 Asia/Shanghai 时区、配置规则与
   实际岗位数据动态展示招聘周期标签（秋招季/春招季/实习招聘/日常招聘/多招聘周期并行/招聘周期进行中）。
3. 阶段 D（左侧顶部全局目录模块）：支持「职业类别 → 公司 → 具体岗位」与「公司 → 类别 → 岗位」双视图切换、
   搜索过滤、展开折叠、按需延迟渲染、真实页面跳转定位及 URL Hash 状态恢复。
4. 阶段 E（左侧底部综合建议模块）：结合个人画像（技术栈、项目证据、大二阶段、WLB与稳定性诉求）与真实采集
   岗位数据，输出多方向 7 维对比、分阶段学习内容、可执行学习方法与本科至毕业后分层职业规划，严格区分
   【事实】【推断】【建议】【未知】。
"""
import html
import os
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import job_filters
import job_cohorts
import job_details
import yaml
import db as _db_mod
from db import APPLICATION_STAGES, KANBAN_COLUMNS, STAGE_TO_COLUMN

# 细阶段标签 + 看板列标签都并进来，并兜底老库可能存的旧 key（旧的 assessment/interview）
_STAGE_LABELS = dict(APPLICATION_STAGES)
for _k, _v in KANBAN_COLUMNS:
    _STAGE_LABELS.setdefault(_k, _v)
_STAGE_LABELS.setdefault("assessment", "笔试")
_DEFAULT_STAGE_KEY = APPLICATION_STAGES[0][0]  # "applied"
_STAGE_COLUMN = dict(STAGE_TO_COLUMN)
for _k, _v in KANBAN_COLUMNS:
    _STAGE_COLUMN.setdefault(_k, _k)


# ──────────────────────────────────────────────────────────────────────────
# 公用 helper
# ──────────────────────────────────────────────────────────────────────────

_PRIMARY = "#0B6E69"
_SUCCESS = "#10B981"
_WARN = "#F59E0B"
_GRAY = "#9CA3AF"
_HIGH_MATCH_THRESHOLD = 70

_AVATAR_COLORS = [
    "#EF4444", "#F97316", "#F59E0B", "#10B981", "#06B6D4",
    "#3B82F6", "#6366F1", "#8B5CF6", "#EC4899", "#14B8A6",
]


def _company_careers_urls() -> dict[str, str]:
    """Load the configured official campus entry for company-detail actions."""
    try:
        config_path = Path(__file__).with_name("config.yaml")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return {
            company["name"]: company["careers_url"]
            for company in config.get("companies", [])
            if company.get("name") and company.get("careers_url")
        }
    except (OSError, yaml.YAMLError):
        return {}


def _is_intern(title: str) -> bool:
    return job_filters.is_intern_title(title)


def _score_color(score) -> str:
    try:
        s = int(score)
    except (TypeError, ValueError):
        return _GRAY
    if s >= 80:
        return _SUCCESS
    if s >= 60:
        return _WARN
    return _GRAY


def _score_bg(score) -> str:
    try:
        s = int(score)
    except (TypeError, ValueError):
        return "#F3F4F6"
    if s >= 80:
        return "#ECFDF5"
    if s >= 60:
        return "#FFFBEB"
    return "#F3F4F6"


def _avatar_color(company: str) -> str:
    h = sum(ord(c) for c in (company or "")) % len(_AVATAR_COLORS)
    return _AVATAR_COLORS[h]


def _avatar_text(company: str) -> str:
    s = (company or "").strip()
    return s[:2] if s else "?"


def _city_short(city: str, max_len: int = 30) -> str:
    s = (city or "").strip()
    return s if len(s) <= max_len else s[:max_len] + "…"


_FAIL_SUMMARY = "分析失败，请检查API配置"
_UNEVAL_PREFIXES = ("未评估", "AI 粗筛", "Claude 粗筛")


def _is_unevaluated(analysis: dict | None) -> bool:
    """方向外、未细评分的「未评估」岗位。"""
    if not analysis:
        return False
    if analysis.get("recommendation") == "未评估":
        return True
    summary = analysis.get("summary") or ""
    return any(summary.startswith(p) for p in _UNEVAL_PREFIXES)


def _platform_of(jd_url: str) -> str:
    """从 jd_url 推断招聘平台（用于前端筛选）。"""
    u = (jd_url or "").lower()
    if "mokahr.com" in u:
        return "Moka"
    if "zhiye.com" in u:
        return "北森"
    if "feishu.cn" in u or "mioffice" in u:
        return "飞书"
    return "自建"


def _is_unresolved_beisen_url(jd_url: str) -> bool:
    u = (jd_url or "").lower()
    return "zhiye.com/campus/jobs#" in u


def _is_detail_link(job: dict) -> bool:
    return bool(job.get("jd_url")) and job.get("link_kind", "detail") != "list"


def _cohort_label(job: dict) -> str:
    try:
        year = int(job.get("cohort") or 0)
    except (TypeError, ValueError):
        year = 0
    status = str(job.get("cohort_status") or "")
    if not status:
        decision = job_cohorts.explicit_job_cohort(job)
        year = decision["cohort"]
        status = decision["cohort_status"]
    if status != "confirmed" or not year:
        return "届别待确认"
    return f"{str(year)[-2:]}届"


def _job_title_html(job: dict) -> str:
    title = html.escape(job.get("title", ""))
    badge = f'<span class="cohort-badge">{html.escape(_cohort_label(job))}</span>'
    if _is_detail_link(job):
        return f'<a href="{html.escape(job["jd_url"])}" target="_blank" rel="noopener" class="cell-job-link">{title}</a> {badge}'
    return f'<span class="cell-job-link">{title}</span> {badge}'


def _company_link(company: str, cohort: str = "current") -> str:
    safe_company = html.escape(company or "", quote=True)
    safe_cohort = html.escape(cohort, quote=True)
    return (
        f'<button type="button" class="company-link" '
        f'data-company-link="{safe_company}" '
        f'data-company-cohort="{safe_cohort}">{safe_company}</button>'
    )


def _job_action_html(job: dict) -> str:
    if not job.get("jd_url"):
        return ""
    is_detail = _is_detail_link(job)
    label = "去投递" if is_detail else "打开招聘列表"
    return (
        f'<a href="{html.escape(job["jd_url"])}" target="_blank" rel="noopener" '
        f'class="cell-action" title="{label}">'
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
        f'stroke="{_PRIMARY}" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/>'
        '<polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></a>'
    )


def _jobs_payload(items: list, app_index=None) -> list[dict]:
    """前端搜索/筛选/分页用的紧凑岗位数组（支持主类别 + 辅助类别多标签 + 分类依据）。

    c=公司 t=岗位(保留原始职位名) ct=城市 s=匹配分 r=推荐 p=平台 u=链接 e=是否已评分
    cat=主类别key cats=全部所属类别keys(含主类别、辅助类别与兼容别名)
    cat_label=主类别中文名 cat_labels=全部类别中文名列表 cat_reason=分类依据
    """
    app_index = app_index or {}
    prof = job_filters.load_active_profile()
    out = []
    for idx, it in enumerate(items):
        job = it["job"]
        a = it.get("analysis") or {}
        a_status = str(a.get("analysis_status") or ("complete" if a else "")).strip().lower()
        is_failed = a_status == "failed" or (a.get("summary") or "") == _FAIL_SUMMARY
        is_ineligible = a_status == "ineligible" or (
            not bool(a)
            and bool(it.get("_is_current"))
            and (
                ("本科" in str(prof.get("degree") or "本科") and job_filters.is_master_phd_only_job(job))
                or not job_filters.is_cs_software_role(job)
            )
        )
        jd_incomplete = a_status == "jd_incomplete" or (not bool(a) and job_details.is_jd_incomplete(job))
        evaluated = (
            bool(a)
            and a_status in {"complete", "local_scored"}
            and not is_failed
            and not is_ineligible
            and not _is_unevaluated(a)
        )
        pending = not bool(a)
        if evaluated:
            rec_label = a.get("recommendation") or "考虑"
            st_code = a_status
        elif not it.get("_is_current"):
            rec_label = "不评分"
            st_code = "cohort_unconfirmed"
        elif is_failed:
            rec_label = "评分失败"
            st_code = "failed"
        elif jd_incomplete:
            rec_label = "JD待补全"
            st_code = "jd_incomplete"
        elif is_ineligible:
            rec_label = "资格不符"
            st_code = "ineligible"
        elif pending:
            rec_label = "待评估"
            st_code = "unscored"
        else:
            rec_label = "未评估"
            st_code = "unevaluated"

        app = app_index.get(job.get("id"))
        ap = _STAGE_LABELS.get(app.get("current_stage"), app.get("current_stage") or "已投递") if app else ""
        cat_info = job_filters.classify_job_categories(job, prof)
        jid = job.get("id") if job.get("id") is not None else f"row-{idx + 1}"
        out.append({
            "c": job.get("company", ""),
            "t": job.get("title", ""),
            "ct": _city_short(job.get("city", "") or "", 40),
            "s": int(a.get("match_score") or 0) if evaluated else None,
            "r": rec_label,
            "st": st_code,
            "p": _platform_of(job.get("jd_url", "")),
            "u": job.get("jd_url", ""),
            "k": job.get("link_kind", "detail"),
            "cy": _cohort_label(job),
            "cat": cat_info["primary_category"],
            "cats": cat_info["categories"],
            "cat_label": cat_info["primary_label"],
            "cat_labels": cat_info["category_labels"],
            "cat_reason": cat_info["category_reason"],
            "ev": job.get("cohort_evidence") or job.get("cohort_source") or "官网未明确届别",
            "e": evaluated,
            "id": jid,
            "ap": ap,
        })
    return out


def _directory_payload(shown_items: list, previous_items: list, unknown_items: list) -> list[dict]:
    """生成用于左侧顶部「全局目录」的去重精简岗位索引数据。

    即便在 server_pagination=True 模式下，目录也能基于真实数据渲染，无需重复请求或采集。
    """
    prof = job_filters.load_active_profile()
    seen_keys = set()
    records = []

    def _add_group(items: list, cohort_key: str):
        for idx, it in enumerate(items):
            job = it.get("job") or {}
            company = (job.get("company") or "").strip()
            title = (job.get("title") or "").strip()
            if not company or not title:
                continue
            dedup_key = (company.casefold(), title.casefold(), cohort_key)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            cat_info = job_filters.classify_job_categories(job, prof)
            a = it.get("analysis") or {}
            evaluated = bool(a) and not _is_unevaluated(a)
            jid = job.get("id") if job.get("id") is not None else f"{cohort_key}-{idx + 1}"
            records.append({
                "id": jid,
                "c": company,
                "t": title,
                "ct": _city_short(job.get("city", "") or "", 24),
                "cat": cat_info["primary_category"],
                "cats": [c for c in [cat_info["primary_category"], *cat_info["secondary_categories"]] if c],
                "s": a.get("match_score") if evaluated else None,
                "u": job.get("jd_url", ""),
                "k": job.get("link_kind", "detail"),
                "ch": cohort_key,
            })

    _add_group(shown_items, "current")
    if not records:
        # 若当前确认届别为空，退回纳入待确认/往届真实岗位，确保目录树始终展示真实数据
        _add_group(unknown_items, "unknown")
        _add_group(previous_items, "previous")
    return records


def _company_summaries(items: list) -> list[dict]:
    """Return compact company ranking data without embedding every job."""
    grouped = {}
    for item in items:
        job = item["job"]
        analysis = item.get("analysis") or {}
        company = job.get("company", "")
        entry = grouped.setdefault(
            company,
            {
                "c": company,
                "total": 0,
                "scores": [],
                "topS": None,
                "topT": job.get("title", "") or "-",
            },
        )
        entry["total"] += 1
        if analysis and not _is_unevaluated(analysis):
            score = analysis.get("match_score") or 0
            entry["scores"].append(score)
            if entry["topS"] is None or score > entry["topS"]:
                entry["topS"] = score
                entry["topT"] = job.get("title", "")

    summaries = []
    for entry in grouped.values():
        scores = entry.pop("scores")
        entry["avg"] = sum(scores) / len(scores) if scores else None
        summaries.append(entry)
    summaries.sort(
        key=lambda item: (item["avg"] is None, -(item["avg"] or 0), -item["total"])
    )
    return summaries


def _filter_items(all_items: list) -> tuple[list, list, list, int]:
    """Split current, previous, and unknown cohorts while hiding invalid rows.

    原始岗位仍保留在数据库里；这里控制默认报告/本地看板的展示范围。
    返回 (确认27届, 确认往届, 届别待确认, 隐藏数)。
    """
    hidden = 0
    current, previous, unknown = [], [], []
    for it in all_items:
        a = it.get("analysis")
        if not job_filters.is_formal_campus_job(it["job"]):
            hidden += 1
            continue
        if job_filters.is_direction_out_job(it["job"]):
            hidden += 1
            continue
        if a and (a.get("summary") or "") == _FAIL_SUMMARY:
            hidden += 1
            continue
        if a is not None and _is_unevaluated(a):
            hidden += 1
            continue
        if _is_unresolved_beisen_url(it["job"].get("jd_url", "")):
            hidden += 1
            continue
        job = it["job"]
        try:
            year = int(job.get("cohort") or 0)
        except (TypeError, ValueError):
            year = 0
        status = str(job.get("cohort_status") or "")
        if not status:
            decision = job_cohorts.explicit_job_cohort(job)
            year = decision["cohort"]
            status = decision["cohort_status"]
        if status == "confirmed" and year == job_cohorts.CURRENT_COHORT:
            current.append({**it, "_is_current": True})
        elif status == "confirmed" and 0 < year <= 2026:
            previous.append(it)
        else:
            unknown.append(it)
    return current, previous, unknown, hidden


# ──────────────────────────────────────────────────────────────────────────
# 组件渲染
# ──────────────────────────────────────────────────────────────────────────

def _stat_card(value, label, icon_svg, accent_bg, accent_color, value_id: str = "") -> str:
    id_attr = f' id="{html.escape(value_id, quote=True)}"' if value_id else ""
    return f"""
<div class="stat-card">
  <div class="stat-icon" style="background:{accent_bg};color:{accent_color}">{icon_svg}</div>
  <div class="stat-text">
    <div class="stat-value"{id_attr}>{value}</div>
    <div class="stat-label">{html.escape(label)}</div>
  </div>
</div>"""


def _company_avatar(company: str) -> str:
    color = _avatar_color(company)
    text = _avatar_text(company)
    return f'<div class="avatar" style="background:{color}">{html.escape(text)}</div>'


def _app_badge(app: dict) -> str:
    if not app:
        return ""
    label = _STAGE_LABELS.get(app.get("current_stage"), app.get("current_stage") or "已投递")
    return f'<span class="app-badge">📋 {html.escape(label)}</span>'


def _apply_control(job: dict, app_index, editable: bool) -> str:
    job_id = job.get("id")
    app = (app_index or {}).get(job_id) if job_id is not None else None
    if app:
        return _app_badge(app)
    if editable and job_id is not None:
        return (
            f'<form class="apply-form" method="post" action="/apply">'
            f'<input type="hidden" name="job_id" value="{job_id}">'
            f'<input type="hidden" name="company" value="{html.escape(job.get("company","") or "", quote=True)}">'
            f'<input type="hidden" name="title" value="{html.escape(job.get("title","") or "", quote=True)}">'
            f'<input type="hidden" name="city" value="{html.escape(job.get("city","") or "", quote=True)}">'
            f'<button type="submit" class="mark-btn">✅ 我投了</button></form>'
        )
    return ""


def _big_job_card(item: dict, app_index=None, editable: bool = False) -> str:
    job = item["job"]
    a = item["analysis"]
    score = a.get("match_score", 0)
    summary = a.get("summary") or ""
    advantages = a.get("advantages") or []
    gaps = a.get("gaps") or []
    city = _city_short(job.get("city", "") or "—")
    cat_info = job_filters.classify_job_categories(job)
    cats_attr = ",".join(cat_info["categories"])
    cat_pills = "".join(
        f'<span class="cat-tag {"cat-tag-primary" if idx == 0 else "cat-tag-sec"}" '
        f'title="{html.escape(cat_info["category_reason"])}">{html.escape(lbl)}</span>'
        for idx, lbl in enumerate(cat_info["category_labels"])
    )
    try:
        _pct = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        _pct = 0

    adv_html = "".join(
        f'<li><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{_SUCCESS}" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>'
        f'<span>{html.escape(adv)}</span></li>'
        for adv in advantages
    ) or '<li class="empty">—</li>'

    gaps_html = "".join(
        f'<li><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{_WARN}" stroke-width="3"><circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
        f'<span>{html.escape(g)}</span></li>'
        for g in gaps
    ) or '<li class="empty">无明显短板</li>'

    jid = job.get("id") or ""
    return f"""
<article class="big-card" id="job-card-{html.escape(str(jid), quote=True)}" data-company="{html.escape(job.get('company',''), quote=True)}" data-title="{html.escape(job.get('title',''), quote=True)}" data-cats="{html.escape(cats_attr, quote=True)}">
  <div class="big-card-head">
    {_company_avatar(job['company'])}
    <div class="big-card-title">
      <h3>{html.escape(job['title'])}</h3>
      <div class="big-card-company">{_company_link(job['company'])} · {cat_pills}</div>
    </div>
    <div class="big-card-meta">
      <div class="location"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg> {html.escape(city)}</div>
      <div class="score-chip" style="background:{_score_bg(score)};color:{_score_color(score)}">
        <strong>{score}</strong> 匹配度
      </div>
      {_apply_control(job, app_index, editable)}
      {f'<a class="apply-btn" href="{html.escape(job["jd_url"])}" target="_blank" rel="noopener">立即投递</a>' if _is_detail_link(job) else '<span class="listing-hint">仅提供招聘列表</span>'}
    </div>
  </div>
  <p class="big-card-summary">{html.escape(summary) or '—'}</p>
  <div class="match-meter" title="匹配度 {score}/100">
    <div class="match-meter-marker" style="left:{_pct}%"></div>
  </div>
  <div class="big-card-grid">
    <div class="adv-block">
      <div class="block-title"><span style="color:{_SUCCESS}">●</span> 我的优势</div>
      <ul class="adv-list">{adv_html}</ul>
    </div>
    <div class="gap-block">
      <div class="block-title"><span style="color:{_WARN}">●</span> 缺失技能</div>
      <ul class="gap-list">{gaps_html}</ul>
    </div>
  </div>
  <div class="big-card-foot">
    {f'<a href="{html.escape(job["jd_url"])}" target="_blank" rel="noopener" class="detail-link">查看岗位详情 <span>›</span></a>' if _is_detail_link(job) else f'<a href="{html.escape(job["jd_url"])}" target="_blank" rel="noopener" class="detail-link">查看招聘列表 <span>›</span></a>'}
  </div>
</article>"""


def _table_job_row(item: dict, app_index=None, editable: bool = False) -> str:
    job = item["job"]
    a = item.get("analysis") or {}
    score = a.get("match_score", "—")
    city = _city_short(job.get("city", "") or "—", 50)
    control = _apply_control(job, app_index, editable)
    cohort_label = _cohort_label(job)
    company_cohort = (
        "current" if cohort_label == "27届"
        else ("unknown" if cohort_label == "届别待确认" else "previous")
    )
    return f"""
<tr data-company="{html.escape(job['company'], quote=True)}" data-title="{html.escape(job['title'], quote=True)}">
  <td class="cell-company"><div class="cell-company-inner">{_company_avatar(job['company'])} {_company_link(job['company'], company_cohort)}</div></td>
  <td>{_job_title_html(job)} <span class="job-inline-actions">{control}</span></td>
  <td><span class="cell-score" style="background:{_score_bg(score)};color:{_score_color(score)}">{score}</span></td>
  <td class="cell-city">{html.escape(city)}</td>
  <td>{_job_action_html(job)}</td>
</tr>"""


# ──────────────────────────────────────────────────────────────────────────
# 页面构建
# ──────────────────────────────────────────────────────────────────────────

def _page_recommended(items: list, hidden_count: int, date_str: str,
                      app_index=None, editable: bool = False,
                      server_pagination: bool = False) -> tuple[str, dict]:
    """总体岗位页：职业类别筛选栏 + 关联公司联动条 + 高匹配大卡片 + 全部岗位表。"""
    items_with_a = [it for it in items if it.get("analysis")]
    evaluated = [it for it in items_with_a if not _is_unevaluated(it["analysis"])]
    recommended = sorted(
        [it for it in evaluated if (it["analysis"].get("match_score") or 0) >= _HIGH_MATCH_THRESHOLD],
        key=lambda x: x["analysis"].get("match_score") or 0,
        reverse=True,
    )
    uneval_count = sum(1 for it in items_with_a if _is_unevaluated(it["analysis"]))
    company_count = len({it["job"]["company"] for it in items})

    stats = {
        "active": len(items),
        "companies": company_count,
        "high_match": len(recommended),
        "unevaluated": uneval_count,
        "recommended": sum(1 for it in evaluated if it["analysis"].get("recommendation") == "推荐"),
        "hidden": hidden_count,
    }

    featured = recommended[:10] if server_pagination else recommended
    rec_html = (
        "\n".join(_big_job_card(it, app_index, editable) for it in featured)
        if recommended
        else f'<div class="empty-state">📭 暂无高匹配岗位（≥{_HIGH_MATCH_THRESHOLD} 分）</div>'
    )

    full_payload = _jobs_payload(items, app_index)
    payload = [] if server_pagination else full_payload
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    editable_js = "true" if editable else "false"
    companies = sorted({j["c"] for j in full_payload if j.get("c")})
    company_opts = "".join(f'<option value="{html.escape(c)}">{html.escape(c)}</option>' for c in companies)

    # 统计每个类别下的去重岗位数与关联公司列表（支持一主多辅类别，且总计不重复累加）
    cat_job_ids: dict[str, set] = defaultdict(set)
    cat_companies: dict[str, set] = defaultdict(set)
    for idx, j in enumerate(full_payload):
        uid = j.get("id") if j.get("id") is not None else idx
        primary_and_sec = [
            c for c in (j.get("cats") or [j.get("cat")])
            if c in job_filters.PRIMARY_CATEGORY_ORDER or c.startswith("custom_")
        ]
        if not primary_and_sec:
            primary_and_sec = [j.get("cat") or "unclassified"]
        for ck in primary_and_sec:
            cat_job_ids[ck].add(uid)
            if j.get("c"):
                cat_companies[ck].add(j["c"])

    # 构建可筛选的职业类别芯片栏与下拉选项
    ordered_cat_keys = list(job_filters.PRIMARY_CATEGORY_ORDER)
    for ck in sorted(cat_job_ids):
        if ck not in ordered_cat_keys:
            ordered_cat_keys.append(ck)

    cat_pills_html = [
        f'<button type="button" class="cat-filter-pill active" data-cat="" '
        f'title="查看全部类别岗位（不重复统计）">'
        f'<span class="cfp-name">全部类别</span>'
        f'<span class="cfp-count">{len(full_payload)}岗 · {len(companies)}企</span></button>'
    ]
    category_opt_list = []
    for ck in ordered_cat_keys:
        label = job_filters.JOB_CATEGORY_LABELS.get(ck, ck)
        j_cnt = len(cat_job_ids.get(ck, set()))
        c_cnt = len(cat_companies.get(ck, set()))
        category_opt_list.append(
            f'<option value="{html.escape(ck)}">{html.escape(label)} ({j_cnt})</option>'
        )
        cat_pills_html.append(
            f'<button type="button" class="cat-filter-pill" data-cat="{html.escape(ck)}" '
            f'data-count="{j_cnt}" data-companies="{c_cnt}" '
            f'title="筛选 {html.escape(label)}（共 {j_cnt} 个岗位，涉及 {c_cnt} 家公司）">'
            f'<span class="cfp-name">{html.escape(label)}</span>'
            f'<span class="cfp-count">{j_cnt}岗 · {c_cnt}企</span></button>'
        )
    # 兼容旧类别 key 在下拉中的程序化选择
    for leg_k, leg_lbl in job_filters._LEGACY_CATEGORY_LABELS.items():
        if leg_k not in ordered_cat_keys:
            category_opt_list.append(
                f'<option value="{html.escape(leg_k)}" hidden>{html.escape(leg_lbl)}</option>'
            )
    category_opts = "".join(category_opt_list)
    cat_meta_json = json.dumps(
        {
            ck: {
                "label": job_filters.JOB_CATEGORY_LABELS.get(ck, ck),
                "job_count": len(cat_job_ids.get(ck, set())),
                "companies": sorted(cat_companies.get(ck, set())),
            }
            for ck in ordered_cat_keys
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")

    page = f"""
<section class="page active" data-page="recommended">
  <div class="page-head">
    <div>
      <h1>27届校招岗位</h1>
      <p class="page-sub">仅展示有官方证据确认的 27 届正式校招岗位 · 支持按职业类别（含主类别与辅助类别）筛选岗位与关联企业 · ≥{_HIGH_MATCH_THRESHOLD} 分置顶推荐</p>
    </div>
    <div class="page-meta">
      <span class="meta-pill">📅 扫描日期：{html.escape(date_str)}</span>
      <span class="meta-pill muted">已隐藏 {hidden_count} 个过滤项</span>
    </div>
  </div>

  <div class="stat-grid">
    {_stat_card(stats['active'], '岗位总数（去重）', _ICON_BAG, '#EEF0FF', _PRIMARY, value_id='statActiveJobs')}
    {_stat_card(stats['companies'], '覆盖企业数', _ICON_BUILDING, '#E0F2FE', '#0284C7', value_id='statActiveCompanies')}
    {_stat_card(stats['high_match'], f'高匹配 (≥{_HIGH_MATCH_THRESHOLD})', _ICON_THUMBS, '#ECFDF5', _SUCCESS, value_id='statHighMatch')}
    {_stat_card(stats['hidden'], '已隐藏过滤项', _ICON_STAR, '#F3F4F6', _GRAY, value_id='statHiddenCount')}
  </div>

  <div class="category-explorer-panel" id="categoryExplorerPanel">
    <div class="cep-head">
      <div>
        <h2 class="section-title" style="margin:0">🗂️ 职业类别筛选体系（支持一主多辅归类 · 原始职位名称完整保留）</h2>
        <p class="cep-sub" id="categoryFilterStatus">当前展示：<b>全部类别</b>（共 {stats['active']} 个去重岗位，覆盖 {stats['companies']} 家公司；选择类别后岗位列表、公司列表与统计将同步更新）</p>
      </div>
      <button type="button" id="btnClearFilters" class="clear-filter-btn" hidden>✕ 清除筛选 · 返回全部岗位</button>
    </div>
    <div class="cat-filter-bar" id="categoryFilterBar" role="region" aria-label="职业类别筛选">
      {"".join(cat_pills_html)}
    </div>
    <div class="filtered-company-bar" id="filteredCompanyBar" hidden>
      <div class="fcb-title">🏢 当前筛选类别下的招聘公司（仅展示存在符合当前筛选条件岗位的企业，点击可按公司过滤或查看详情）：</div>
      <div class="fcb-list" id="filteredCompanyList"></div>
    </div>
  </div>

  <h2 class="section-title">🎯 高匹配岗位（≥{_HIGH_MATCH_THRESHOLD} 分）</h2>
  <div class="big-card-list" id="featuredCardList">
    {rec_html}
  </div>

  <h2 class="section-title">📋 全部岗位（搜索 / 筛选 / 分页）</h2>
  <div class="job-toolbar">
    <input type="search" id="jobSearch" class="jt-search" placeholder="搜索公司名称、原始岗位名称或技术关键词…">
    <select id="fCompany" class="jt-sel"><option value="">全部公司</option>{company_opts}</select>
    <select id="fCategory" class="jt-sel"><option value="">全部岗位类别</option>{category_opts}</select>
    <select id="fPlatform" class="jt-sel">
      <option value="">全部平台</option><option>Moka</option><option>北森</option><option>飞书</option><option>自建</option>
    </select>
    <select id="fEval" class="jt-sel">
      <option value="">全部已展示</option><option value="scored">仅已评分</option><option value="uneval">待评估</option>
    </select>
    <select id="fScore" class="jt-sel">
      <option value="">不限分数</option><option value="70">≥70</option><option value="60">60–69</option><option value="0">&lt;60</option>
    </select>
  </div>
  <div class="table-wrap">
    <table class="job-table">
      <thead>
        <tr><th>公司</th><th>原始岗位名称与所属类别</th><th>匹配度</th><th>工作地点</th><th>平台</th><th>操作</th></tr>
      </thead>
      <tbody id="jobTbody"></tbody>
    </table>
  </div>
  <div class="job-pager">
    <button id="jpPrev" class="jp-btn">‹ 上一页</button>
    <span id="jpInfo" class="jp-info"></span>
    <button id="jpNext" class="jp-btn">下一页 ›</button>
  </div>
  <script>window.__JOBS = {data_json}; window.__CATEGORY_META = {cat_meta_json}; window.__INITIAL_STATS = {json.dumps(stats, ensure_ascii=False)}; window.__EDITABLE = {editable_js}; window.__SERVER_PAGINATION = {str(server_pagination).lower()};</script>
</section>"""
    return page, stats


def _page_previous_cohort(items: list, app_index=None, editable: bool = False,
                          server_pagination: bool = False) -> str:
    items = sorted(
        items,
        key=lambda item: (item.get("analysis") or {}).get("match_score", -1),
        reverse=True,
    )
    payload = [] if server_pagination else _jobs_payload(items, app_index)
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    rows = (
        '<tr><td colspan="5" class="empty-state">正在加载岗位...</td></tr>'
        if server_pagination
        else "\n".join(_table_job_row(it, app_index, editable) for it in items)
    )
    body = f"""
<div class="job-toolbar">
  <input type="search" id="previousSearch" class="jt-search" placeholder="搜索公司名称或岗位名称…">
</div>
<div class="table-wrap"><table class="job-table"><thead>
  <tr><th>公司</th><th>岗位</th><th>匹配度</th><th>工作地点</th><th>操作</th></tr>
</thead><tbody id="previousTbody">{rows}</tbody></table></div>""" if rows else '<div class="empty-state big-empty">暂无明确标注的往届校招岗位</div>'
    pager = """<div class="job-pager">
  <button id="ppPrev" class="jp-btn">上一页</button>
  <span id="ppInfo" class="jp-info"></span>
  <button id="ppNext" class="jp-btn">下一页</button>
</div>""" if server_pagination else ""
    return f"""
<section class="page" data-page="previous-cohort">
  <div class="page-head"><div><h1>往届校招岗位</h1>
    <p class="page-sub">明确标注为 2026 届及更早届别的正式校招岗位 · 共 {len(items)} 个</p>
  </div></div>
  {body}
  {pager}
  <script>window.__PREVIOUS_JOBS = {data_json};</script>
</section>"""


def _page_unknown_cohort(items: list, app_index=None, editable: bool = False,
                         server_pagination: bool = False) -> str:
    payload = [] if server_pagination else _jobs_payload(items, app_index)
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    rows = '<tr><td colspan="5" class="empty-state">正在加载岗位...</td></tr>'
    body = f"""
<div class="job-toolbar">
  <input type="search" id="unknownSearch" class="jt-search" placeholder="搜索公司名称或岗位名称…">
</div>
<div class="table-wrap"><table class="job-table"><thead>
  <tr><th>公司</th><th>岗位</th><th>届别判断依据</th><th>工作地点</th><th>操作</th></tr>
</thead><tbody id="unknownTbody">{rows}</tbody></table></div>"""
    pager = """<div class="job-pager">
  <button id="upPrev" class="jp-btn">上一页</button>
  <span id="upInfo" class="jp-info"></span>
  <button id="upNext" class="jp-btn">下一页</button>
</div>"""
    return f"""
<section class="page" data-page="unknown-cohort">
  <div class="page-head"><div><h1>届别待确认</h1>
    <p class="page-sub">官方岗位信息和公司校招入口均未明确届别，不补抓 JD、不进行匹配评分 · 共 {len(items)} 个</p>
  </div></div>
  {body}
  {pager}
  <script>window.__UNKNOWN_JOBS = {data_json};</script>
</section>"""


def _page_today(items: list, date_str: str, app_index=None, editable: bool = False) -> str:
    """今日新增页（crawled_at == 最新批次日期）。"""
    latest = max(
        (it["job"].get("crawled_at") for it in items if it["job"].get("crawled_at")),
        default=date_str,
    )
    new_items = [
        it for it in items
        if it.get("analysis") and it["job"].get("crawled_at") == latest
    ]
    new_items.sort(
        key=lambda x: x["analysis"].get("match_score", 0),
        reverse=True,
    )
    if not new_items:
        body = '<div class="empty-state big-empty">📭 今日暂无新增岗位<p>明天 08:00 会自动重新扫描</p></div>'
    else:
        rows = "\n".join(_table_job_row(it, app_index, editable) for it in new_items)
        body = f"""
<div class="table-wrap">
  <table class="job-table">
    <thead>
      <tr><th>公司</th><th>岗位</th><th>匹配度</th><th>工作地点</th><th>操作</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""
    return f"""
<section class="page" data-page="today">
  <div class="page-head">
    <div>
      <h1>今日新增</h1>
      <p class="page-sub">本次扫描首次出现的岗位（共 {len(new_items)} 个）</p>
    </div>
    <div class="page-meta">
      <span class="meta-pill">📅 扫描日期：{html.escape(date_str)}</span>
    </div>
  </div>
  {body}
</section>"""


def _page_companies(items: list) -> str:
    """公司排行页 — 按届别切换聚合、分页与搜索。"""
    return """
<section class="page" data-page="companies">
  <div class="page-head">
    <div>
      <h1>公司排行</h1>
      <p class="page-sub">按届别独立统计 · 点击公司查看当前届别岗位</p>
    </div>
  </div>
  <div class="company-cohort-tabs" role="tablist" aria-label="公司排行届别">
    <button type="button" class="company-cohort-tab active" data-company-cohort="current" role="tab" aria-selected="true">
      27届 <span id="ccCurrentCount" class="company-cohort-count">0</span>
    </button>
    <button type="button" class="company-cohort-tab" data-company-cohort="previous" role="tab" aria-selected="false">
      往届 <span id="ccPreviousCount" class="company-cohort-count">0</span>
    </button>
    <button type="button" class="company-cohort-tab" data-company-cohort="unknown" role="tab" aria-selected="false">
      届别待确认 <span id="ccUnknownCount" class="company-cohort-count">0</span>
    </button>
  </div>
  <div class="job-toolbar">
    <input type="search" id="companySearch" class="jt-search" placeholder="搜索公司名…">
  </div>
  <div class="table-wrap">
    <table class="job-table">
      <thead>
        <tr><th>公司</th><th>岗位数</th><th id="companyAvgHead">平均匹配度</th>
          <th id="companyTopScoreHead">最高分</th><th id="companyTopJobHead">最高分岗位</th></tr>
      </thead>
      <tbody id="companyTbody"></tbody>
    </table>
  </div>
  <div class="job-pager">
    <button id="cpPrev" class="jp-btn">‹ 上一页</button>
    <span id="cpInfo" class="jp-info"></span>
    <button id="cpNext" class="jp-btn">下一页 ›</button>
  </div>
</section>"""


def _page_company_detail() -> str:
    """公司详情页（单个复用区块，内容由 JS 的 openCompany() 实时填充）。"""
    return """
<section class="page" data-page="company-detail">
  <div class="page-head">
    <div>
      <button id="cdBack" class="back-btn">← 返回公司排行</button>
      <div id="cdHead"></div>
    </div>
  </div>
  <div class="table-wrap">
    <table class="job-table">
      <thead>
        <tr><th>岗位</th><th>匹配度</th><th>工作地点</th><th>平台</th><th>操作</th></tr>
      </thead>
      <tbody id="cdTbody"></tbody>
    </table>
  </div>
</section>"""


_STAGE_RESULTS = ["待", "进行中", "通过", "挂"]
_RESULT_STYLE = {
    "待": ("#F3F4F6", "#6B7280"),
    "进行中": ("#EFF6FF", "#3B82F6"),
    "通过": ("#ECFDF5", "#10B981"),
    "挂": ("#FEF2F2", "#EF4444"),
}


def _result_chip(result: str) -> str:
    bg, fg = _RESULT_STYLE.get(result, _RESULT_STYLE["待"])
    return f'<span class="result-chip" style="background:{bg};color:{fg}">{html.escape(result or "待")}</span>'


_COL_STYLE = {
    "applied": ("#EEF0FF", "#6C63FF"),
    "written": ("#FEF3C7", "#B45309"),
    "interview": ("#E0E7FF", "#4338CA"),
    "offer": ("#DCFCE7", "#15803D"),
    "rejected": ("#F3F4F6", "#6B7280"),
}
_EVENT_TYPES = ["笔试", "测评", "一面", "二面", "三面", "HR面", "其他"]


def _event_sort_key(event: dict) -> tuple[str, str, str]:
    return (
        event.get("event_date") or "9999-12-31",
        event.get("event_time") or "99:99",
        event.get("company") or "",
    )


def _event_when(event: dict) -> str:
    date_part = event.get("event_date") or "日期待定"
    time_part = event.get("event_time") or ""
    return f"{date_part} {time_part}".strip()


def _collect_schedule_events(applications: list) -> list[dict]:
    events = []
    for app in applications:
        for event in app.get("events", []) or []:
            events.append({
                **event,
                "app_id": app.get("id"),
                "company": app.get("company", ""),
                "title": app.get("title", ""),
                "city": app.get("city", ""),
                "current_stage": app.get("current_stage") or _DEFAULT_STAGE_KEY,
            })
    return sorted(events, key=_event_sort_key)


def _event_form(app: dict) -> str:
    opts = "".join(f'<option>{html.escape(t)}</option>' for t in _EVENT_TYPES)
    return f"""
<form class="event-form" method="post" action="/application/{app['id']}/event">
  <select name="event_type">{opts}</select>
  <input type="date" name="event_date" required>
  <input type="time" name="event_time">
  <input name="note" placeholder="地点 / 链接 / 备注">
  <button type="submit">添加日程</button>
</form>"""


def _app_card(app: dict, job_index=None, editable: bool = False) -> str:
    company = app.get("company", "") or "—"
    title = app.get("title", "") or "—"
    city = _city_short(app.get("city", "") or "", 40)
    note = app.get("note", "") or ""
    job_id = app.get("job_id")
    jinfo = (job_index or {}).get(job_id) if job_id is not None else None

    cur_stage = app.get("current_stage") or _DEFAULT_STAGE_KEY
    col = _STAGE_COLUMN.get(cur_stage, "applied")
    stages = app.get("stages", [])
    cur_result = stages[-1].get("result") if stages else "待"

    score_html = ""
    jd_link = ""
    if jinfo:
        sc = jinfo.get("match_score")
        if sc is not None:
            score_html = f'<span class="cell-score" style="background:{_score_bg(sc)};color:{_score_color(sc)}">{sc}</span>'
        if jinfo.get("jd_url"):
            label = "回看岗位 ›" if jinfo.get("link_kind", "detail") != "list" else "打开招聘列表 ›"
            jd_link = f'<a href="{html.escape(jinfo["jd_url"])}" target="_blank" rel="noopener" class="detail-link">{label}</a>'

    round_chip = ""
    if col == "interview":
        round_chip = f'<span class="round-chip">{html.escape(_STAGE_LABELS.get(cur_stage, ""))}</span>'
    elif col == "rejected":
        cut = next((s.get("stage") for s in reversed(stages)
                    if s.get("stage") not in (None, "rejected", "applied")), None)
        if cut:
            round_chip = f'<span class="round-chip muted">{html.escape(_STAGE_LABELS.get(cut, cut))}挂</span>'

    hist_rows = "".join(
        f'<li>{html.escape(_STAGE_LABELS.get(s.get("stage"), s.get("stage") or ""))}'
        f' · {html.escape(s.get("result","") or "")}'
        f' · {html.escape(s.get("date","") or "")}'
        f'{(" · " + html.escape(s["note"])) if s.get("note") else ""}</li>'
        for s in stages
    )
    history = f'<details class="app-history"><summary>阶段历史 ({len(stages)})</summary><ul>{hist_rows}</ul></details>' if stages else ""
    events = sorted(app.get("events", []) or [], key=_event_sort_key)
    next_event = events[0] if events else None
    event_hint = ""
    if next_event:
        event_hint = (
            f'<div class="app-card-event">📅 {html.escape(_event_when(next_event))}'
            f' · {html.escape(next_event.get("event_type", "日程"))}'
            f'{(" · " + html.escape(next_event["note"])) if next_event.get("note") else ""}</div>'
        )

    edit_html = ""
    if editable:
        stage_opts = "".join(
            f'<option value="{k}"{" selected" if k == cur_stage else ""}>{html.escape(v)}</option>'
            for k, v in APPLICATION_STAGES
        )
        result_opts = "".join(
            f'<option{" selected" if r == cur_result else ""}>{html.escape(r)}</option>'
            for r in _STAGE_RESULTS
        )
        edit_html = f"""
<form class="stage-form" method="post" action="/application/{app['id']}/stage">
  <select name="stage">{stage_opts}</select>
  <select name="result">{result_opts}</select>
  <input name="note" placeholder="备注（可选）">
  <button type="submit">更新</button>
</form>
<form class="del-form" method="post" action="/application/{app['id']}/delete" onsubmit="return confirm('删除这条投递记录？')">
  <button type="submit" class="del-btn">删除</button>
</form>"""

    return f"""
<div class="app-card">
  <div class="app-card-head">
    {_company_avatar(company)}
    <div class="app-card-title">
      <div class="app-card-job">{html.escape(title)}</div>
      <div class="app-card-co">{html.escape(company)}{(" · " + html.escape(city)) if city else ""}</div>
    </div>
    {score_html}
  </div>
  <div class="app-card-status">{round_chip}{_result_chip(cur_result)}</div>
  {f'<div class="app-card-note">📝 {html.escape(note)}</div>' if note else ''}
  {event_hint}
  <div class="app-card-foot">
    <span class="app-card-links">{jd_link}{history}</span>
    {'<button type="button" class="app-card-toggle" aria-expanded="false" title="展开编辑">···</button>' if editable else ''}
  </div>
  {f'<div class="app-card-editor" hidden>{edit_html}</div>' if editable else ''}
</div>"""


def _page_applications(applications: list, job_index=None, editable: bool = False) -> str:
    """投递记录看板：5 列 + 顶部漏斗统计条 + 已挂列默认折叠。"""
    by_col: dict[str, list] = {k: [] for k, _ in KANBAN_COLUMNS}
    for app in applications:
        stage = app.get("current_stage") or _DEFAULT_STAGE_KEY
        col = _STAGE_COLUMN.get(stage, "applied")
        by_col.setdefault(col, []).append(app)

    funnel = "".join(
        f'<div class="funnel-item" style="border-top-color:{_COL_STYLE[k][1]}">'
        f'<div class="funnel-num" style="color:{_COL_STYLE[k][1]}">{len(by_col.get(k, []))}</div>'
        f'<div class="funnel-lbl">{html.escape(lbl)}</div></div>'
        for k, lbl in KANBAN_COLUMNS
    )
    funnel_bar = f'<div class="funnel-bar">{funnel}</div>'

    def _cards_html(cards):
        return ("\n".join(_app_card(a, job_index, editable) for a in cards)
                if cards else '<div class="kanban-empty">—</div>')

    main_cols = []
    rejected_col = ""
    for key, label in KANBAN_COLUMNS:
        cards = by_col.get(key, [])
        bg, fg = _COL_STYLE.get(key, ("#F9FAFB", "#374151"))
        if key == "rejected":
            rejected_col = f"""
<details class="kanban-col rejected-col">
  <summary class="kanban-col-head" style="background:{bg};color:{fg}">已挂 <span class="kanban-count">{len(cards)}</span></summary>
  <div class="kanban-body">{_cards_html(cards)}</div>
</details>"""
        else:
            main_cols.append(f"""
<div class="kanban-col" data-stage="{key}">
  <div class="kanban-col-head" style="background:{bg};color:{fg};border-bottom:2px solid {fg}">{html.escape(label)} <span class="kanban-count">{len(cards)}</span></div>
  <div class="kanban-body">{_cards_html(cards)}</div>
</div>""")

    manual_form = ""
    if editable:
        manual_form = """
<form class="manual-form" method="post" action="/application/new">
  <input name="company" placeholder="公司名" required>
  <input name="title" placeholder="岗位" required>
  <input name="city" placeholder="城市（可选）">
  <button type="submit">+ 手动添加库外投递</button>
</form>"""

    total = len(applications)
    return f"""
<section class="page" data-page="applications">
  <div class="page-head">
    <div>
      <h1>投递记录</h1>
      <p class="page-sub">步骤看所在列、卡面只看结果 · 共 {total} 条{'' if editable else '（只读，编辑请用本地"管理"模式）'}</p>
    </div>
  </div>
  {funnel_bar}
  {manual_form}
  <div class="kanban">
    {"".join(main_cols)}
    <div class="kanban-sep"></div>
    {rejected_col}
  </div>
</section>"""


def _page_schedule(applications: list, editable: bool = False) -> str:
    """投递日程：按日期聚合笔试/面试安排，并提供快速录入入口。"""
    events = _collect_schedule_events(applications)
    if events:
        days: dict[str, list] = defaultdict(list)
        for event in events:
            days[event.get("event_date") or "日期待定"].append(event)
        day_blocks = []
        for day, day_events in days.items():
            cards = []
            for event in day_events:
                note = event.get("note") or ""
                stage_label = _STAGE_LABELS.get(event.get("current_stage"), event.get("current_stage") or "")
                delete_form = ""
                if editable:
                    delete_form = (
                        f'<form method="post" action="/application/{event["app_id"]}/event/{event["id"]}/delete" '
                        f'class="event-delete" onsubmit="return confirm(\'删除这条日程？\')">'
                        f'<button type="submit">删除</button></form>'
                    )
                cards.append(f"""
<div class="schedule-card">
  <div class="schedule-time">{html.escape(event.get("event_time") or "全天")}</div>
  <div class="schedule-main">
    <div class="schedule-title">{html.escape(event.get("event_type") or "日程")} · {html.escape(event.get("company") or "—")}</div>
    <div class="schedule-job">{html.escape(event.get("title") or "—")}</div>
    <div class="schedule-meta">{html.escape(stage_label)}{(" · " + html.escape(event.get("city") or "")) if event.get("city") else ""}</div>
    {f'<div class="schedule-note">{html.escape(note)}</div>' if note else ''}
  </div>
  {delete_form}
</div>""")
            day_blocks.append(f"""
<section class="schedule-day">
  <div class="schedule-date">{html.escape(day)} <span>{len(day_events)} 项</span></div>
  <div class="schedule-stack">{"".join(cards)}</div>
</section>""")
        timeline = "".join(day_blocks)
    else:
        timeline = '<div class="empty-state big-empty">📭 暂无日程<p>在投递卡片或右侧表单中添加笔试、面试安排。</p></div>'

    quick_forms = ""
    if editable:
        form_cards = []
        for app in applications:
            form_cards.append(f"""
<div class="schedule-form-card">
  <div class="schedule-form-title">{html.escape(app.get("company", "") or "—")}</div>
  <div class="schedule-form-sub">{html.escape(app.get("title", "") or "—")}</div>
  {_event_form(app)}
</div>""")
        quick_forms = f"""
<aside class="schedule-side">
  <div class="schedule-side-title">快速添加</div>
  <div class="schedule-form-list">{"".join(form_cards) if form_cards else '<div class="kanban-empty">暂无投递记录</div>'}</div>
</aside>"""

    return f"""
<section class="page" data-page="schedule">
  <div class="page-head">
    <div>
      <h1>日程安排</h1>
      <p class="page-sub">按日期展示投递后的笔试、面试和其他后续安排 · 共 {len(events)} 项</p>
    </div>
  </div>
  <div class="schedule-layout">
    <div class="schedule-timeline">{timeline}</div>
    {quick_forms}
  </div>
</section>"""


def _format_epistemic_tags(text: str) -> str:
    """将文本中的【事实】【推断】【建议】【未知】转化为醒目的语义标签。"""
    escaped = html.escape(text or "")
    replacements = (
        ("【事实·WLB风险】", '<span class="ep-badge ep-risk">事实·WLB风险</span>'),
        ("【事实+推断】", '<span class="ep-badge ep-infer">事实+推断</span>'),
        ("【未知+行业推断】", '<span class="ep-badge ep-unknown">未知+行业推断</span>'),
        ("【未知/数据不足】", '<span class="ep-badge ep-unknown">未知/数据不足</span>'),
        ("【事实】", '<span class="ep-badge ep-fact">事实</span>'),
        ("【推断】", '<span class="ep-badge ep-infer">推断</span>'),
        ("【建议】", '<span class="ep-badge ep-advice">建议</span>'),
        ("【未知】", '<span class="ep-badge ep-unknown">未知</span>'),
    )
    for k, v in replacements:
        escaped = escaped.replace(k, v)
    return escaped


def _page_comprehensive_advice(all_items: list, date_str: str) -> str:
    """生成阶段 E：个人需求变化驱动的「综合建议」闭环页面（含版本管理、变化摘要、失败回退与手动刷新控制）。"""
    force_refresh = os.environ.get("FORCE_ADVICE_REFRESH", "").strip().lower() in ("1", "true", "yes")
    advice = job_filters.update_or_get_comprehensive_advice(
        all_items, force_refresh=force_refresh
    )
    tr = advice["data_time_range"]
    sm = advice["sample_summary"]
    ps = advice["profile_snapshot"]
    vinfo = advice.get("version_info") or {}
    vdiff = advice.get("version_diff_summary") or {}
    limitations = advice.get("data_limitations") or []
    watch_points = advice.get("next_watch_points") or []

    # 1. 学习方向建议 7 问面板
    qa_html = "".join(
        f'<div class="advice-qa-card">'
        f'<div class="advice-qa-q">{html.escape(q.get("q", ""))}</div>'
        f'<div class="advice-qa-a">{_format_epistemic_tags(q.get("a", ""))}</div>'
        f'</div>'
        for q in advice["skill_market_questions"]
    )

    # 2. 多方向 7 维结构化对比卡片
    dir_cards_html = []
    for d in advice["suggested_directions"]:
        cat_key = d["id"]
        if cat_key == "graphics_3d_game":
            cat_key = "graphics_3d"
        badge_text = d.get("priority_badge", "候选方向")
        badge_cls = "adc-badge-optional" if "一般兴趣支线" in badge_text else "adc-badge"
        dir_cards_html.append(f"""
<article class="advice-dir-card" data-advice-cat="{html.escape(cat_key)}">
  <div class="adc-head">
    <div>
      <span class="{badge_cls}">{html.escape(badge_text)}</span>
      <h3>1. 建议探索的方向：{html.escape(d['direction_name'])}</h3>
    </div>
    <div class="adc-actions">
      <span class="adc-stat">{html.escape(d.get('sample_stat', ''))}</span>
      <button type="button" class="adc-jump-btn" data-jump-cat="{html.escape(cat_key)}">查看该类别岗位 ›</button>
    </div>
  </div>
  <div class="adc-grid">
    <div class="adc-item">
      <div class="adc-label">2. 推荐理由</div>
      <div class="adc-val">{_format_epistemic_tags(d['recommendation_reason'])}</div>
    </div>
    <div class="adc-item">
      <div class="adc-label">3. 与当前技能的匹配情况</div>
      <div class="adc-val">{_format_epistemic_tags(d['skill_match'])}</div>
    </div>
    <div class="adc-item">
      <div class="adc-label">4. 需要补足的能力</div>
      <div class="adc-val">{_format_epistemic_tags(d['gaps_to_fill'])}</div>
    </div>
    <div class="adc-item">
      <div class="adc-label">5. 主要风险和不确定性</div>
      <div class="adc-val">{_format_epistemic_tags(d['risks_and_uncertainties'])}</div>
    </div>
    <div class="adc-item">
      <div class="adc-label">6. 与用户职业偏好的符合程度</div>
      <div class="adc-val">{_format_epistemic_tags(d['preference_alignment'])}</div>
    </div>
    <div class="adc-item">
      <div class="adc-label">7. 可以验证这一方向是否适合自己的实际行动</div>
      <div class="adc-val">{_format_epistemic_tags(d['validation_actions'])}</div>
    </div>
  </div>
</article>""")

    # 3. 分阶段学习内容建议表格
    lc_rows = "".join(
        f"<tr>"
        f"<td><b>{html.escape(st['stage'])}</b></td>"
        f"<td>{_format_epistemic_tags(st['foundations'])}</td>"
        f"<td>{_format_epistemic_tags(st['projects'])}</td>"
        f"<td>{_format_epistemic_tags(st['tools_and_debugging'])}</td>"
        f"<td>{_format_epistemic_tags(st['deliverable_proof'])}</td>"
        f"<td>{_format_epistemic_tags(st['jd_verification'])}</td>"
        f"<td><b>{html.escape(st['priority'])}</b><br>{_format_epistemic_tags(st['reason'])}</td>"
        f"</tr>"
        for st in advice["learning_content"]
    )

    # 4. 学习方法建议与“看过教程 vs 真正掌握”对照表
    lm = advice["learning_methods"]
    mastery_rows = "".join(
        f"<tr>"
        f"<td><b>{html.escape(m['dimension'])}</b></td>"
        f"<td style='color:#6B7280'>{html.escape(m['watched_tutorial'])}</td>"
        f"<td style='color:#065F46;font-weight:600'>{html.escape(m['truly_mastered'])}</td>"
        f"</tr>"
        for m in lm["tutorial_vs_mastery"]
    )

    # 5. 分层职业规划建议
    cp_cards = "".join(
        f'<div class="cp-layer-card">'
        f'<div class="cp-layer-title">{html.escape(layer["phase"])}</div>'
        f'<div style="font-size:12.5px;color:#0b6e69;font-weight:650;margin-bottom:6px">核心目标：{html.escape(layer["core_goal"])}</div>'
        f'<div style="font-size:13px;color:#40515e;line-height:1.65;white-space:pre-line">{_format_epistemic_tags(layer["action_points"])}</div>'
        f'</div>'
        for layer in advice["career_planning"]
    )

    skills_tags = "".join(
        f'<span class="prof-chip">{html.escape(str(s))}</span>' for s in (ps.get("skills") or [])
    )
    evidence_tags = "".join(
        f'<span class="prof-chip prof-chip-ev">{html.escape(str(e))}</span>'
        for e in (ps.get("project_evidence") or [])
    )

    # 6. 版本状态、变化摘要、数据不足限制与重点观察列表
    why_changed_lis = "".join(
        f"<li>{_format_epistemic_tags(str(w))}</li>" for w in (vdiff.get("why_changed") or [])
    )
    limitation_lis = "".join(
        f"<li>{_format_epistemic_tags(str(l))}</li>" for l in limitations
    )
    watch_lis = "".join(
        f"<li>{_format_epistemic_tags(str(wp))}</li>" for wp in watch_points
    )
    fallback_banner = ""
    if vinfo.get("last_error"):
        fallback_banner = (
            f'<div class="advice-fallback-alert" id="adviceFallbackAlert">'
            f'<b>🛡️ 失败回退保护已生效：</b>最近一次生成遇到异常（<code>{html.escape(str(vinfo["last_error"]))}</code>），'
            f'系统已保留最近成功生成的有效建议（生成于 {html.escape(str(vinfo.get("last_success_at", "—")))}），未覆盖或丢失任何数据。'
            f'</div>'
        )

    sync_badge_cls = "ep-fact" if vinfo.get("is_synced_with_profile", True) else "ep-unknown"
    sync_label = vinfo.get("sync_status_label") or "✅ 与当前个人配置版本一致"

    return f"""
<section class="page" data-page="comprehensive-advice">
  <div class="page-head">
    <div>
      <h1>🧭 个人画像驱动的综合职业与学习建议</h1>
      <p class="page-sub">结合 <code>profile.yaml</code> 个人能力画像与已采集的真实岗位需求 · 支持需求变化自动检测、版本追溯与按需刷新（建议而非替你决策）</p>
    </div>
    <div class="page-meta">
      <span class="meta-pill">📊 数据时间范围：{html.escape(tr['earliest_date'])} 至 {html.escape(tr['latest_date'])}</span>
      <span class="meta-pill">🧪 样本池：{sm['bachelor_cs_jobs']} 个本科计算机岗 / {sm['companies_count']} 家企业</span>
    </div>
  </div>

  <div class="advice-provenance-box" id="adviceVersionPanel">
    <div class="apb-header">
      <div>
        <h3>🔄 综合建议版本与配置同步状态（建议版本：<code id="advVerId">{html.escape(str(vinfo.get('advice_version_id', 'v-init')))}</code> · 画像版本：<code id="profVerId">{html.escape(str(vinfo.get('profile_version_id', 'v-init')))}</code>）</h3>
        <p class="apb-sub">
          <span class="ep-badge {sync_badge_cls}" id="adviceSyncBadge">{html.escape(sync_label)}</span>
          <span>🕒 最近生成时间：<b id="advGenTime">{html.escape(str(vinfo.get('generated_at', tr.get('generated_at', date_str))))}</b></span>
          <span> · 市场数据指纹：<code>{html.escape(str(vinfo.get('market_fingerprint', 'm-init')))}</code></span>
          <span> · 更新策略：<b>{html.escape(str(vinfo.get('strategy_label', '按需生成')))}</b></span>
        </p>
      </div>
      <div class="adc-actions">
        <button type="button" id="btnRegenerateAdvice" class="adc-jump-btn" title="检测 profile.yaml 与最新岗位数据并重新生成综合建议">
          🔄 重新生成综合建议
        </button>
      </div>
    </div>
    {fallback_banner}
    <div class="apb-meta-grid">
      <div><b>当前学历与阶段：</b>{html.escape(str(ps.get('degree','本科')))} · 目标 {html.escape(str(ps.get('target_cohort', 2029)))} 届（求职计划：{html.escape(str(ps.get('job_type','实习')))}）</div>
      <div><b>核心求职诉求：</b>本科直通就业（不读研）· 重视跨行业技术保值与 WLB（工作生活平衡）· 图形学作个人兴趣支线</div>
      <div><b>已掌握技能栈：</b>{skills_tags}</div>
      <div><b>已验证项目证据：</b>{evidence_tags}</div>
    </div>
    <div class="advice-diff-grid" id="adviceDiffSummary">
      <div class="adg-box">
        <div class="adg-title">📝 与上一版本相比，本次建议发生了什么变化及原因（版本对比 {html.escape(str(vdiff.get('previous_profile_version', '无')))} → {html.escape(str(vdiff.get('current_profile_version', '')))}）</div>
        <ul class="adg-list">{why_changed_lis}</ul>
      </div>
      <div class="adg-box">
        <div class="adg-title">⚠️ 数据不足限制与下次重点观察指标</div>
        <ul class="adg-list" id="adviceLimitations">{limitation_lis}</ul>
        <div class="adg-title" style="margin-top:8px">🔭 下次应重点观察的岗位需求或技能变化：</div>
        <ul class="adg-list" id="adviceWatchPoints">{watch_lis}</ul>
      </div>
    </div>
    <div class="advice-calibrator" id="adviceCalibrator">
      <div class="cal-title">⚡ 技能补充与偏好模拟器（勾选你计划新增的技能或偏好，实时重算下方各方向的准备度提示；永久修改请编辑 <code>profile.yaml</code>）：</div>
      <div class="cal-controls">
        <label class="cal-check"><input type="checkbox" value="sql" data-sim-skill="sql"> 补充 MySQL / Redis 数据持久化</label>
        <label class="cal-check"><input type="checkbox" value="qt" data-sim-skill="qt"> 补充 Qt6 桌面跨平台 GUI</label>
        <label class="cal-check"><input type="checkbox" value="go" data-sim-skill="go"> 补充 Go / FastAPI 并发网络服务</label>
        <label class="cal-check"><input type="checkbox" value="ci" data-sim-skill="ci"> 补充 pytest / Docker / CI 自动化</label>
        <label class="cal-check"><input type="checkbox" value="rag" data-sim-skill="rag"> 补充 RAG / Agent 结构化评测闭环</label>
        <span id="calSimOutput" class="cal-sim-output">当前按 <code>profile.yaml</code> 原始基线展示</span>
      </div>
    </div>
  </div>

  <h2 class="section-title">一、学习方向建议：当前技术栈与市场岗位关系 7 问精析</h2>
  <div class="advice-qa-grid">
    {qa_html}
  </div>

  <h2 class="section-title">二、多职业方向 7 维证据对比（展示多个有依据选项，理解取舍而非命令式单选）</h2>
  <div class="advice-dir-list" id="adviceDirList">
    {"".join(dir_cards_html)}
  </div>

  <h2 class="section-title">三、分阶段学习内容建议（结合大二阶段、可投入时间与求职目标）</h2>
  <div class="table-wrap" style="margin-bottom:28px">
    <table class="job-table advice-table">
      <thead>
        <tr>
          <th style="width:13%">学习阶段与目标</th>
          <th style="width:15%">需要补足的基础知识</th>
          <th style="width:16%">值得完成的工程项目</th>
          <th style="width:15%">开发工具、调试与工程实践</th>
          <th style="width:14%">能够证明能力的项目成果</th>
          <th style="width:13%">对应岗位要求与验证方式</th>
          <th style="width:14%">建议学习优先级及原因</th>
        </tr>
      </thead>
      <tbody>{lc_rows}</tbody>
    </table>
  </div>

  <h2 class="section-title">四、可执行学习方法建议（如何分配理论与实践、如何识别“看过教程”与“真正掌握”）</h2>
  <div class="advice-method-grid">
    <div class="am-card">
      <h3>1. 理论与工程实践分配</h3>
      <p>{_format_epistemic_tags(lm['time_allocation'])}</p>
    </div>
    <div class="am-card">
      <h3>2. 如何通过项目验证理解</h3>
      <p>{_format_epistemic_tags(lm['project_verification'])}</p>
    </div>
    <div class="am-card">
      <h3>3. 代码阅读、调试、测试与复盘安排</h3>
      <p>{_format_epistemic_tags(lm['debug_test_review_loop'])}</p>
    </div>
    <div class="am-card">
      <h3>4. 如何根据项目进展与招聘要求动态调整</h3>
      <p>{_format_epistemic_tags(lm['dynamic_adjustment'])}</p>
    </div>
  </div>
  <div class="table-wrap" style="margin-top:14px;margin-bottom:28px">
    <table class="job-table advice-table">
      <thead>
        <tr>
          <th style="width:20%">核心能力维度</th>
          <th style="width:40%">❌ “看过教程 / 跑通 Demo”状态（面试易被问穿）</th>
          <th style="width:40%">✅ “真正工程掌握”状态（可写入简历的硬证据）</th>
        </tr>
      </thead>
      <tbody>{mastery_rows}</tbody>
    </table>
  </div>

  <h2 class="section-title">五、分层职业规划建议（从本科大二到毕业后长期发展与 WLB 平衡）</h2>
  <div class="cp-layer-grid">
    {cp_cards}
  </div>
</section>"""


def _page_learning_guide(all_items: list, date_str: str) -> str:
    """保留原有的《技术要求与学习决策》实证统计页，与底部「综合建议」互补。"""
    insights = job_filters.aggregate_tech_and_learning_decisions(all_items)
    all_sub = insights["all_substantive_jd"]
    non_cs = insights["non_cs_filtered_out"]
    master_filtered = insights.get("master_phd_filtered_out", 0)
    cs_sub = insights["cs_substantive_jd"]

    dir_rows = "".join(
        f"<tr><td><b>{html.escape(k)}</b></td><td>{v}</td><td>{round(v / max(1, cs_sub) * 100, 1)}%</td></tr>"
        for k, v in insights["direction_distribution"].items()
    )

    def _render_eval_rows(eval_items: list[dict]) -> str:
        out = []
        for it in eval_items:
            ex_html = "<br>".join(
                f'<span style="color:#6B7280;font-size:12px">• <a href="{html.escape(e["url"])}" target="_blank" style="color:#0B6E69;text-decoration:underline">[{html.escape(e["company"])} - {html.escape(e["title"])}]</a> “{html.escape(e["snippet"])}”</span>'
                for e in it["examples"]
            )
            out.append(
                f"<tr>"
                f"<td><b>{html.escape(it['name'])}</b><br><span style='color:#0B6E69;font-size:12px'>{html.escape(it['group'])} · {html.escape(it['dim'])}</span></td>"
                f"<td style='font-size:12px'>"
                f"<b>本科CS岗提及：</b>{it['mention_total']} 岗 ({it['mention_pct']}%)<br>"
                f"• 明确要求词：{it['must_pct_of_mention']}%<br>"
                f"• 优先/加分词：{it['bonus_pct_of_mention']}%<br>"
                f"• 职责背景提及：{it['context_pct_of_mention']}%<br>"
                f"<span style='color:#4B5563'>主要分布：{html.escape(it['top_dirs_str'])}</span>"
                f"</td>"
                f"<td style='font-size:12px'>"
                f"<b>1.就业通用：</b>{html.escape(it['val_general'])}<br>"
                f"<b>2.方向专属：</b>{html.escape(it['val_domain'])}"
                f"</td>"
                f"<td style='font-size:12px'>"
                f"<b>3.可迁移与探索：</b>{html.escape(it['val_explore'])}<br>"
                f"<b>4.工程实践：</b>{html.escape(it['val_practice'])}"
                f"</td>"
                f"<td style='font-size:12px'>{html.escape(it['prereq_and_cost'])}<br>{ex_html}</td>"
                f"</tr>"
            )
        return "".join(out)

    eval_rows = _render_eval_rows(insights["tech_evaluations"])

    return f"""
<section class="page" data-page="learning-guide">
  <div class="page-head">
    <div>
      <h1>📊 技术要求实证统计与 JD 词频拆解</h1>
      <p class="page-sub">全库 {all_sub} 份完整 JD 中已剥离 {non_cs} 条非计算机岗与 {master_filtered} 条硕博专属岗，聚焦 <b>{cs_sub} 份本科可投的计算机真实 JD</b></p>
    </div>
    <div class="page-meta">
      <span class="meta-pill">📅 扫描日期：{html.escape(date_str)}</span>
    </div>
  </div>

  <h2 class="section-title">📊 四维技术学习价值与共性要求统计（基于 {cs_sub} 份本科可投计算机真实 JD）</h2>
  <div class="table-wrap" style="margin-bottom:28px">
    <table class="job-table">
      <thead>
        <tr>
          <th style="width:18%">技术领域与层级</th>
          <th style="width:18%">本科CS岗出现频率<br>(区分明确要求/加分/背景)</th>
          <th style="width:22%">1. 就业通用价值 &amp;<br>2. 方向专属价值</th>
          <th style="width:22%">3. 跨行业迁移价值 &amp;<br>4. 工程实践价值</th>
          <th style="width:20%">学习投入建议与真实JD溯源</th>
        </tr>
      </thead>
      <tbody>{eval_rows}</tbody>
    </table>
  </div>

  <h2 class="section-title">📂 本科可投计算机岗位方向供给分布（N = {cs_sub}）</h2>
  <div class="table-wrap" style="margin-bottom:28px">
    <table class="job-table">
      <thead><tr><th>职业类别</th><th>本科可投完整JD数</th><th>占本科计算机样本比例</th></tr></thead>
      <tbody>{dir_rows}</tbody>
    </table>
  </div>
</section>"""


# ──────────────────────────────────────────────────────────────────────────
# 图标 SVG（行内，避免外部依赖）
# ──────────────────────────────────────────────────────────────────────────

_ICON_BAG = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 7h-3V5a2 2 0 0 0-2-2H9a2 2 0 0 0-2 2v2H4a1 1 0 0 0-1 1v11a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a1 1 0 0 0-1-1z"/></svg>'
_ICON_BUILDING = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg>'
_ICON_THUMBS = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 0 0-6 0v4H4v11h13l3-8V9h-6z"/></svg>'
_ICON_STAR = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15 9 22 10 17 15 18 22 12 18 6 22 7 15 2 10 9 9 12 2"/></svg>'

_ICON_RANK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l-3 3 3 3M18 9l3 3-3 3M9 4l-2 16M15 4l2 16"/></svg>'
_ICON_CLOCK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><polyline points="12 6 12 12 16 14"/></svg>'
_ICON_BARS = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>'
_ICON_KANBAN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="6" height="18" rx="1"/><rect x="11" y="3" width="6" height="12" rx="1"/><rect x="19" y="3" width="2" height="9" rx="1"/></svg>'
_ICON_CALENDAR = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="2"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>'
_ICON_COMPASS = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>'

_NAV_GROUPS = [
    ("发现", [
        ("recommended", "27届校招", _ICON_RANK),
        ("today", "今日新增", _ICON_CLOCK),
        ("companies", "公司排行", _ICON_BARS),
        ("previous-cohort", "往届校招岗位", _ICON_CLOCK),
        ("unknown-cohort", "届别待确认", _ICON_CLOCK),
        ("learning-guide", "技术要求统计", _ICON_STAR),
    ]),
    ("我的", [
        ("applications", "投递记录", _ICON_KANBAN),
        ("schedule", "日程安排", _ICON_CALENDAR),
    ]),
]


def _render_sidebar(season_info: dict, dir_job_count: int, dir_company_count: int) -> str:
    """构建三段式左侧导航栏：
    1. 顶部固定区：固定品牌名「AI校招情报」+ 动态招聘周期标签。
    2. 中部独立滚动区：位于最顶部的「目录」树状模块 + 原有「发现 / 我的」导航组。
    3. 底部固定区：位于左侧导航栏最底部的「综合建议」模块入口（不被长目录挤出可视区）。
    """
    blocks = []
    first = True
    for group_label, items in _NAV_GROUPS:
        lis = []
        for slug, label, icon in items:
            active = " active" if first else ""
            first = False
            lis.append(
                f'<li class="nav-item{active}" data-target="{slug}">'
                f'<span class="nav-icon">{icon}</span><span class="nav-label">{label}</span>'
                f'</li>'
            )
        blocks.append(
            f'<div class="nav-group-label">{html.escape(group_label)}</div>'
            f'<ul class="nav-list">{"".join(lis)}</ul>'
        )

    brand_name = html.escape(season_info.get("brand_name") or job_filters.PRODUCT_BRAND_NAME)
    season_badge = html.escape(season_info.get("season_badge") or "招聘周期进行中")
    season_reason = html.escape(season_info.get("reason") or "")
    tz_label = html.escape(season_info.get("timezone") or "Asia/Shanghai")

    return f"""
<aside class="sidebar" aria-label="全局导航">
  <div class="sidebar-top">
    <div class="brand">
      <div class="brand-icon">🤖</div>
      <div class="brand-text">
        <div class="brand-title-row">
          <div class="brand-title">{brand_name}</div>
        </div>
        <div class="brand-sub">智能匹配 · 精准推荐</div>
      </div>
    </div>
    <div class="season-banner" id="seasonBanner" title="{season_reason}（时区：{tz_label}）">
      <span class="season-dot"></span>
      <span class="season-label" id="seasonBadgeText">{season_badge}</span>
      <span class="season-tz">{tz_label}</span>
    </div>
  </div>

  <div class="sidebar-scroll">
    <section class="dir-module" id="globalDirectory" aria-label="全局目录导航">
      <div class="dir-head">
        <button type="button" class="dir-toggle-btn" id="dirCollapseBtn" aria-expanded="true" title="展开或折叠全局目录">
          <span class="dir-chevron" id="dirChevron">▾</span>
          <span class="dir-title">📂 目录</span>
          <span class="dir-summary-badge" id="dirSummaryBadge">{dir_company_count}企 · {dir_job_count}岗</span>
        </button>
        <button type="button" class="dir-mode-btn" id="dirModeBtn" data-mode="cat-co-job" title="切换目录树结构（类别→公司→岗位 / 公司→类别→岗位）">
          按类别
        </button>
      </div>
      <div class="dir-body" id="dirBody">
        <div class="dir-search-wrap">
          <input type="search" id="dirSearch" class="dir-search-input" placeholder="定位类别 / 公司 / 具体岗位…">
        </div>
        <div class="dir-tree-container" id="dirTreeContainer" role="tree" aria-label="职业类别与公司岗位树">
        </div>
      </div>
    </section>

    <div class="sidebar-nav-groups">
      {"".join(blocks)}
    </div>
  </div>

  <div class="sidebar-bottom">
    <div class="nav-group-label" style="padding-top:2px">个人发展辅助</div>
    <ul class="nav-list" style="margin-bottom:0">
      <li class="nav-item nav-item-advice" data-target="comprehensive-advice" id="navComprehensiveAdvice">
        <span class="nav-icon">{_ICON_COMPASS}</span>
        <span class="nav-label">综合建议</span>
        <span class="advice-pill">画像驱动</span>
      </li>
    </ul>
  </div>
</aside>"""


# ──────────────────────────────────────────────────────────────────────────
# CSS / JS
# ──────────────────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: #F5F6FA; color: #1F2937; min-height: 100vh;
  font-size: 14px; line-height: 1.55;
}
a { color: inherit; text-decoration: none; }
ul { list-style: none; }

/* ── Layout ─────────────────────────────────────── */
.layout { display: flex; min-height: 100vh; }
.sidebar {
  width: 288px; background: #fbfcfc; border-right: 1px solid #dce4e8;
  padding: 16px 12px; flex-shrink: 0; position: sticky; top: 0;
  height: 100vh; display: flex; flex-direction: column; overflow: hidden;
}
.sidebar-top { flex-shrink: 0; padding-bottom: 10px; border-bottom: 1px solid #dce4e8; }
.sidebar-scroll {
  flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 10px 2px;
  scrollbar-width: thin;
}
.sidebar-bottom {
  flex-shrink: 0; padding-top: 10px; border-top: 1px solid #dce4e8;
  background: #fbfcfc;
}
.main { flex: 1; padding: 28px 34px 48px; max-width: 1640px; overflow-x: hidden; }

/* ── Brand & Dynamic Season Banner ──────────────── */
.brand { display: flex; align-items: center; gap: 10px; padding: 0 6px 10px; }
.brand-icon { width: 36px; height: 36px; border-radius: 6px;
              background: #17212b; color: #FFF;
              display: grid; place-items: center; font-size: 18px; flex-shrink: 0; }
.brand-title { font-size: 16px; font-weight: 750; color: #17212b; letter-spacing: 0; }
.brand-sub { font-size: 11px; color: #667582; margin-top: 1px; }
.season-banner {
  display: flex; align-items: center; gap: 6px; margin: 0 6px;
  padding: 5px 9px; border-radius: 6px; background: #e5f2f0;
  border: 1px solid #b8dcd7; color: #075b57; font-size: 12px; font-weight: 600;
}
.season-dot {
  width: 7px; height: 7px; border-radius: 50%; background: #10B981;
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.2); flex-shrink: 0;
}
.season-label { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.season-tz { font-size: 10px; color: #4e6b68; background: rgba(255,255,255,0.65); padding: 1px 5px; border-radius: 4px; }

/* ── Global Directory Tree (Top of Sidebar) ─────── */
.dir-module {
  background: #FFFFFF; border: 1px solid #dce4e8; border-radius: 6px;
  margin: 0 4px 12px; overflow: hidden;
}
.dir-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 8px; background: #f4f8f8; border-bottom: 1px solid #e9eef0;
}
.dir-toggle-btn {
  display: inline-flex; align-items: center; gap: 5px; border: 0;
  background: transparent; color: #17212b; font-size: 13px; font-weight: 700;
  cursor: pointer; padding: 2px 4px; border-radius: 4px;
}
.dir-toggle-btn:hover { background: #e5f2f0; color: #075b57; }
.dir-chevron { font-size: 11px; color: #667582; width: 12px; display: inline-block; text-align: center; }
.dir-summary-badge {
  font-size: 11px; font-weight: 600; color: #0b6e69; background: #e5f2f0;
  padding: 1px 6px; border-radius: 999px;
}
.dir-mode-btn {
  border: 1px solid #cfdadd; background: #FFFFFF; color: #4e5c67;
  font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 4px;
  cursor: pointer;
}
.dir-mode-btn:hover { border-color: #0b6e69; color: #0b6e69; background: #e5f2f0; }
.dir-body[hidden] { display: none; }
.dir-search-wrap { padding: 6px 8px; border-bottom: 1px solid #f0f4f5; }
.dir-search-input {
  width: 100%; padding: 5px 8px; font-size: 12px; border: 1px solid #dce4e8;
  border-radius: 4px; background: #fbfcfc; color: #17212b;
}
.dir-search-input:focus { outline: none; border-color: #0b6e69; background: #fff; }
.dir-tree-container {
  max-height: 34vh; overflow-y: auto; padding: 4px 4px 6px;
  font-size: 12px; scrollbar-width: thin;
}
.dir-node-l1, .dir-node-l2 { margin-bottom: 2px; }
.dir-row {
  display: flex; align-items: center; gap: 4px; width: 100%;
  padding: 4px 6px; border-radius: 4px; cursor: pointer;
  color: #283540; text-align: left; border: 0; background: transparent;
}
.dir-row:hover { background: #edf5f4; color: #075b57; }
.dir-row.active { background: #e5f2f0; color: #075b57; font-weight: 700; }
.dir-exp {
  width: 16px; height: 16px; display: inline-grid; place-items: center;
  font-size: 10px; color: #667582; border-radius: 3px; flex-shrink: 0;
}
.dir-exp:hover { background: #dce8e6; color: #17212b; }
.dir-lbl {
  flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.dir-cnt {
  font-size: 10px; color: #667582; background: #f0f4f5; padding: 0 5px;
  border-radius: 8px; flex-shrink: 0; font-variant-numeric: tabular-nums;
}
.dir-children-l2 { padding-left: 12px; border-left: 1px solid #e6ecee; margin-left: 8px; }
.dir-children-l3 { padding-left: 12px; border-left: 1px dashed #dce4e8; margin-left: 8px; }
.dir-job-btn {
  display: flex; align-items: center; justify-content: space-between; gap: 6px;
  width: 100%; padding: 3px 6px; border: 0; background: transparent;
  border-radius: 4px; color: #4a5965; font-size: 11.5px; text-align: left; cursor: pointer;
}
.dir-job-btn:hover { background: #eef6f5; color: #0b6e69; }
.dir-job-btn.active { background: #e5f2f0; color: #075b57; font-weight: 700; }
.dir-job-title { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dir-job-score { font-size: 10px; font-weight: 700; color: #0b6e69; flex-shrink: 0; }
.dir-more-btn {
  width: 100%; border: 0; background: #f4f8f8; color: #0b6e69;
  font-size: 11px; padding: 4px 6px; border-radius: 4px; cursor: pointer; margin-top: 2px;
}

/* ── Sidebar Nav Items ──────────────────────────── */
.nav-list { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
.nav-group-label {
  color: #8795a0; font-size: 11px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; padding: 8px 10px 4px;
}
.nav-item {
  position: relative; display: flex; align-items: center; gap: 10px;
  min-height: 38px; padding: 8px 10px; border-radius: 6px; cursor: pointer;
  color: #53616d; font-size: 13px; user-select: none;
  transition: background-color .16s ease, color .16s ease;
}
.nav-item:hover { background: #edf3f2; color: #075b57; }
.nav-item.active { background: #e5f2f0; color: #075b57; font-weight: 700; }
.nav-item-advice {
  background: #f0f7f6; border: 1px solid #c5e0dc; color: #075b57; font-weight: 650;
}
.nav-item-advice.active {
  background: #0b6e69; color: #FFFFFF !important; border-color: #0b6e69;
}
.advice-pill {
  margin-left: auto; font-size: 10px; padding: 1px 6px; border-radius: 999px;
  background: rgba(11, 110, 105, 0.14); color: inherit; font-weight: 600;
}
.nav-item-advice.active .advice-pill { background: rgba(255,255,255,0.22); color: #FFF; }
.nav-icon { display: inline-grid; place-items: center; flex-shrink: 0; }

/* ── Page Head & Stat Cards ─────────────────────── */
.page { display: none; animation: workspace-in .16s ease-out; }
.page.active { display: block; }
@keyframes workspace-in { from { opacity: 0; } to { opacity: 1; } }
.page-head {
  display: flex; justify-content: space-between; align-items: flex-end;
  gap: 20px; flex-wrap: wrap; margin-bottom: 22px; padding-bottom: 16px;
  border-bottom: 1px solid #dce4e8;
}
.page-head h1, .cd-title h1 { color: #17212b; font-size: 25px; font-weight: 750; line-height: 1.25; }
.page-sub { color: #667582; margin-top: 6px; font-size: 13px; }
.page-meta { display: flex; flex-direction: row; align-items: center; gap: 8px; flex-wrap: wrap; }
.meta-pill {
  background: #FFFFFF; border: 1px solid #dce4e8; padding: 6px 10px;
  border-radius: 999px; font-size: 12px; color: #4e5c67;
}
.meta-pill.muted { color: #9CA3AF; }

.stat-grid {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0;
  margin-bottom: 22px; border: 1px solid #dce4e8; border-radius: 6px;
  background: #FFFFFF; overflow: hidden;
}
.stat-card {
  min-height: 88px; gap: 12px; padding: 16px 18px; display: flex; align-items: center;
  border-right: 1px solid #dce4e8;
}
.stat-card:last-child { border-right: 0; }
.stat-icon { width: 36px; height: 36px; border-radius: 6px; display: grid; place-items: center; flex-shrink: 0; }
.stat-value { color: #17212b; font-size: 25px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.stat-label { color: #667582; font-size: 12px; margin-top: 4px; }

/* ── Category Explorer Panel (Stage B) ──────────── */
.category-explorer-panel {
  background: #FFFFFF; border: 1px solid #dce4e8; border-radius: 6px;
  padding: 16px 18px; margin-bottom: 24px;
}
.cep-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 12px; flex-wrap: wrap; margin-bottom: 12px;
}
.cep-sub { font-size: 12.5px; color: #667582; margin-top: 4px; }
.clear-filter-btn {
  padding: 6px 12px; border-radius: 4px; border: 1px solid #0b6e69;
  background: #e5f2f0; color: #075b57; font-size: 12px; font-weight: 650;
  cursor: pointer;
}
.clear-filter-btn:hover { background: #0b6e69; color: #FFFFFF; }
.cat-filter-bar {
  display: flex; flex-wrap: wrap; gap: 8px;
}
.cat-filter-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 11px; border-radius: 6px; border: 1px solid #dce4e8;
  background: #f8fbfb; color: #283540; font-size: 12.5px; cursor: pointer;
}
.cat-filter-pill:hover { border-color: #0b6e69; color: #075b57; background: #edf6f5; }
.cat-filter-pill.active {
  background: #0b6e69; color: #FFFFFF; border-color: #0b6e69; font-weight: 650;
}
.cfp-count {
  font-size: 11px; padding: 1px 6px; border-radius: 999px;
  background: rgba(0,0,0,0.06); color: inherit;
}
.cat-filter-pill.active .cfp-count { background: rgba(255,255,255,0.22); }
.filtered-company-bar {
  margin-top: 12px; padding-top: 12px; border-top: 1px dashed #dce4e8;
}
.fcb-title { font-size: 12px; font-weight: 650; color: #40515e; margin-bottom: 8px; }
.fcb-list { display: flex; flex-wrap: wrap; gap: 6px; max-height: 110px; overflow-y: auto; }
.fcb-chip {
  display: inline-flex; align-items: center; gap: 4px; padding: 4px 9px;
  border-radius: 4px; border: 1px solid #cfdadd; background: #FFFFFF;
  color: #283540; font-size: 12px; cursor: pointer;
}
.fcb-chip:hover, .fcb-chip.active {
  border-color: #0b6e69; background: #e5f2f0; color: #075b57; font-weight: 600;
}
.cat-tag {
  display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px;
  margin-right: 4px; font-weight: 500;
}
.cat-tag-primary { background: #e5f2f0; color: #075b57; }
.cat-tag-sec { background: #f0f3f4; color: #586775; }

/* ── Section Title & Big Cards ──────────────────── */
.section-title { font-size: 15px; font-weight: 700; color: #17212b; margin: 26px 0 12px; }
.big-card-list { display: grid; gap: 10px; }
.big-card {
  background: #FFFFFF; border: 1px solid #dce4e8; border-left: 3px solid #0b6e69;
  border-radius: 6px; padding: 20px 22px;
}
.big-card-head { display: flex; align-items: flex-start; gap: 14px; margin-bottom: 12px; flex-wrap: wrap; }
.big-card-title { flex: 1; min-width: 180px; }
.big-card-title h3 { font-size: 17px; font-weight: 700; color: #17212b; }
.big-card-company { color: #667582; font-size: 13px; margin-top: 4px; display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.big-card-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-left: auto; }
.location { color: #667582; font-size: 13px; display: inline-flex; align-items: center; gap: 4px; }
.score-chip { padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; white-space: nowrap; }
.score-chip strong { font-size: 16px; margin-right: 2px; }
.apply-btn, .mark-btn {
  background: #0b6e69; color: #FFF !important; padding: 7px 14px;
  border-radius: 4px; font-size: 13px; font-weight: 500; border: 0; cursor: pointer;
}
.apply-btn:hover, .mark-btn:hover { background: #075b57; }
.listing-hint { color: #667582; font-size: 12px; white-space: nowrap; }
.big-card-summary { color: #52616c; font-size: 14px; margin: 8px 0 14px; line-height: 1.65; }
.match-meter { position: relative; height: 6px; background: #dce8e6; margin: 2px 0 16px; }
.match-meter-marker {
  position: absolute; top: 50%; width: 12px; height: 12px; border-radius: 50%;
  background: #FFF; border: 3px solid #075b57; transform: translate(-50%,-50%);
}
.big-card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.block-title { font-weight: 600; color: #40515e; margin-bottom: 8px; font-size: 13px; display: flex; align-items: center; gap: 6px; }
.adv-list li, .gap-list li { display: flex; align-items: flex-start; gap: 8px; padding: 3px 0; font-size: 13px; color: #52616c; }
.adv-list li svg, .gap-list li svg { flex-shrink: 0; margin-top: 3px; }
.adv-list li.empty, .gap-list li.empty { color: #9CA3AF; padding-left: 22px; }
.big-card-foot { display: flex; justify-content: flex-end; margin-top: 12px; padding-top: 10px; border-top: 1px solid #e9eef0; }
.detail-link { color: #0b6e69; font-size: 13px; font-weight: 500; display: inline-flex; align-items: center; gap: 4px; }

/* ── Job Table & Highlight Flash ────────────────── */
.job-toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }
.jt-search, .jt-sel {
  min-height: 38px; border: 1px solid #dce4e8; border-radius: 4px;
  background: #FFFFFF; color: #17212b; padding: 8px 10px; font-size: 13px;
}
.jt-search { flex: 1 1 220px; min-width: 180px; }
#fCompany { width: 160px; }
#fCategory { width: 190px; }
.table-wrap { background: #FFFFFF; border: 1px solid #dce4e8; border-radius: 6px; overflow-x: auto; }
.job-table { width: 100%; min-width: 1040px; border-collapse: collapse; table-layout: fixed; }
.job-table thead { background: #f7f9f9; }
.job-table th { text-align: left; padding: 11px 14px; font-size: 12px; font-weight: 700; color: #586775; border-bottom: 1px solid #dce4e8; }
.job-table td { padding: 12px 14px; font-size: 13px; color: #283540; border-bottom: 1px solid #e9eef0; vertical-align: middle; }
.job-table tbody tr:hover, .company-row:hover { background: #f4faf9; }
.job-highlight-flash {
  animation: highlightPulse 2.4s ease-out;
  background: #fef9c3 !important;
}
@keyframes highlightPulse {
  0% { background: #fde047; }
  100% { background: transparent; }
}
.cell-company-inner { display: flex; align-items: center; gap: 8px; }
.company-link { border: 0; background: transparent; color: #33414b; font-weight: 650; cursor: pointer; text-align: left; }
.company-link:hover { color: #0b6e69; text-decoration: underline; }
.avatar { width: 28px; height: 28px; border-radius: 4px; color: #FFF; display: grid; place-items: center; font-size: 11px; font-weight: 600; flex-shrink: 0; }
.cell-job-link { color: #17212b; font-weight: 600; }
.cell-job-link:hover { color: #0b6e69; }
.cell-job-cats { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; }
.cell-score { display: inline-block; padding: 4px 10px; border-radius: 4px; font-weight: 600; font-size: 13px; min-width: 36px; text-align: center; }
.cell-score.uneval { background: #F3F4F6; color: #9CA3AF; font-weight: 500; }
.cell-plat { display: inline-block; padding: 2px 8px; border-radius: 4px; background: #eef3f4; color: #4e6069; font-size: 12px; }
.cell-action { display: inline-grid; place-items: center; width: 30px; height: 30px; border-radius: 4px; background: #eef5f4; color: #0b6e69; }
.no-detail { display: inline-grid; place-items: center; width: 30px; height: 30px; color: #9CA3AF; }
.cohort-badge { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 4px; background: #f0f3f4; color: #62717c; font-size: 11px; }
.app-badge { display: inline-block; background: #ECFDF5; color: #10B981; border-radius: 4px; padding: 2px 8px; font-size: 12px; font-weight: 600; }
.job-inline-actions { display: inline-flex; align-items: center; gap: 6px; margin-left: 6px; }
.job-pager { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin: 12px 0 0; }
.jp-btn, .back-btn { padding: 6px 14px; font-size: 13px; border: 1px solid #dce4e8; border-radius: 4px; background: #FFF; color: #41505a; cursor: pointer; }
.jp-btn:hover:not(:disabled), .back-btn:hover { border-color: #0b6e69; color: #0b6e69; background: #e5f2f0; }
.jp-btn:disabled { opacity: .45; cursor: not-allowed; }
.jp-info { font-size: 13px; color: #667582; }
.empty-state { border: 1px dashed #dce4e8; border-radius: 6px; padding: 28px; text-align: center; color: #667582; background: #FFFFFF; }
.empty-state.big-empty { padding: 54px 28px; }

/* ── Company Cohort Tabs & Detail ───────────────── */
.company-cohort-tabs { display: inline-flex; align-items: center; margin: 4px 0 14px; padding: 3px; border: 1px solid #DDE3E8; border-radius: 8px; background: #F4F7F7; }
.company-cohort-tab { min-height: 34px; padding: 6px 14px; border: 0; border-radius: 6px; background: transparent; color: #64748B; font-size: 13px; font-weight: 600; cursor: pointer; }
.company-cohort-tab.active { background: #FFF; color: #0F766E; box-shadow: 0 1px 3px rgba(15, 23, 42, .1); }
.company-cohort-count { display: inline-flex; min-width: 20px; height: 20px; margin-left: 5px; align-items: center; justify-content: center; padding: 0 5px; border-radius: 10px; background: #E2E8F0; color: #64748B; font-size: 11px; }
.company-row { cursor: pointer; }
.cd-title { display: flex; align-items: center; gap: 12px; }
.cd-stats { display: flex; gap: 18px; margin-top: 10px; flex-wrap: wrap; }
.cd-stat { font-size: 13px; color: #667582; }
.cd-stat b { font-size: 16px; color: #17212b; margin: 0 2px; }
.cd-actions { margin-top: 14px; }
.company-campus-link { display: inline-flex; align-items: center; gap: 6px; padding: 7px 11px; border: 1px solid #9bcac6; border-radius: 4px; background: #e5f2f0; color: #075b57; font-size: 13px; font-weight: 600; }

/* ── Comprehensive Advice Page (Stage E) ────────── */
.advice-provenance-box {
  background: #FFFFFF; border: 1px solid #dce4e8; border-left: 4px solid #0b6e69;
  border-radius: 6px; padding: 18px 20px; margin-bottom: 24px;
}
.apb-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.apb-header h3 { font-size: 16px; color: #17212b; }
.apb-sub { font-size: 12.5px; color: #52616c; margin-top: 4px; }
.ep-legend { display: flex; flex-wrap: wrap; gap: 6px; }
.ep-badge {
  display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 11px;
  font-weight: 700; margin-right: 4px; vertical-align: baseline;
}
.ep-fact { background: #DCFCE7; color: #166534; }
.ep-infer { background: #DBEAFE; color: #1E40AF; }
.ep-advice { background: #CCFBF1; color: #115E59; }
.ep-unknown { background: #FEF3C7; color: #92400E; }
.ep-risk { background: #FEE2E2; color: #991B1B; }
.apb-meta-grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 18px;
  padding: 12px; background: #f8fbfb; border-radius: 6px; font-size: 13px;
}
.prof-chip {
  display: inline-block; padding: 2px 8px; border-radius: 4px; background: #e5f2f0;
  color: #075b57; font-size: 12px; margin: 2px 4px 2px 0; font-weight: 600;
}
.prof-chip-ev { background: #EEF2FF; color: #3730A3; font-weight: 500; }

.adc-badge-optional {
  display: inline-block; font-size: 11px; font-weight: 700; color: #475569;
  background: #E2E8F0; padding: 2px 7px; border-radius: 4px; margin-bottom: 4px;
}
.advice-fallback-alert {
  margin: 8px 0 12px; padding: 10px 12px; border-radius: 6px;
  background: #FEF3C7; border: 1px solid #F59E0B; color: #92400E; font-size: 12.5px;
}
.advice-diff-grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px;
  margin-top: 12px;
}
.adg-box {
  background: #f8fbfb; border: 1px solid #dce4e8; border-radius: 6px; padding: 10px 12px;
}
.adg-title { font-size: 12.5px; font-weight: 700; color: #17212b; margin-bottom: 6px; }
.adg-list { padding-left: 16px; list-style: disc; font-size: 12.5px; color: #40515e; line-height: 1.55; }
.adg-list li { margin-bottom: 3px; }

.advice-calibrator { margin-top: 12px; padding-top: 12px; border-top: 1px dashed #dce4e8; }
.cal-title { font-size: 12.5px; font-weight: 650; color: #17212b; margin-bottom: 8px; }
.cal-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 12.5px; }
.cal-check { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; color: #283540; }
.cal-sim-output { margin-left: auto; font-size: 12px; color: #0b6e69; font-weight: 650; background: #e5f2f0; padding: 3px 9px; border-radius: 4px; }

.advice-qa-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 24px; }
.advice-qa-card { background: #FFFFFF; border: 1px solid #dce4e8; border-radius: 6px; padding: 14px 16px; }
.advice-qa-q { font-weight: 700; color: #17212b; font-size: 13.5px; margin-bottom: 6px; }
.advice-qa-a { color: #40515e; font-size: 13px; line-height: 1.6; }

.advice-dir-list { display: grid; gap: 16px; margin-bottom: 28px; }
.advice-dir-card {
  background: #FFFFFF; border: 1px solid #dce4e8; border-top: 3px solid #0b6e69;
  border-radius: 6px; padding: 18px 20px;
}
.adc-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 12px; flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid #e9eef0; margin-bottom: 14px;
}
.adc-badge {
  display: inline-block; font-size: 11px; font-weight: 700; color: #0b6e69;
  background: #e5f2f0; padding: 2px 7px; border-radius: 4px; margin-bottom: 4px;
}
.adc-head h3 { font-size: 17px; color: #17212b; }
.adc-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.adc-stat { font-size: 12.5px; color: #52616c; }
.adc-jump-btn {
  padding: 6px 12px; border-radius: 4px; border: 1px solid #0b6e69;
  background: #0b6e69; color: #FFFFFF; font-size: 12px; font-weight: 600; cursor: pointer;
}
.adc-jump-btn:hover { background: #075b57; }
.adc-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 18px; }
.adc-item { background: #f9fbfb; border: 1px solid #edf2f3; border-radius: 5px; padding: 10px 12px; }
.adc-label { font-size: 12px; font-weight: 700; color: #17212b; margin-bottom: 4px; }
.adc-val { font-size: 13px; color: #40515e; line-height: 1.6; }
.adc-foot { margin-top: 12px; padding-top: 10px; border-top: 1px dashed #dce4e8; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; font-size: 12px; }
.adc-foot-lbl { font-weight: 650; color: #52616c; }
.sample-job-chip {
  display: inline-block; padding: 2px 8px; border-radius: 4px; background: #eef5f4;
  color: #0b6e69; font-weight: 500;
}
.sample-job-chip:hover { text-decoration: underline; background: #dceee9; }

.advice-table { table-layout: auto; min-width: 980px; }
.advice-table td { vertical-align: top; line-height: 1.55; }
.advice-method-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.am-card { background: #FFFFFF; border: 1px solid #dce4e8; border-radius: 6px; padding: 14px 16px; }
.am-card h3 { font-size: 14px; color: #17212b; margin-bottom: 6px; }
.am-card p { font-size: 13px; color: #40515e; line-height: 1.6; }

.cp-layer-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-bottom: 24px; }
.cp-layer-card { background: #FFFFFF; border: 1px solid #dce4e8; border-left: 3px solid #0b6e69; border-radius: 6px; padding: 16px 18px; }
.cp-layer-title { font-size: 15px; font-weight: 700; color: #17212b; margin-bottom: 8px; }
.cp-layer-list li { font-size: 13px; color: #40515e; padding: 4px 0; line-height: 1.6; }

/* ── Applications Kanban & Schedule ─────────────── */
.funnel-bar { display: flex; gap: 0; margin-bottom: 18px; border: 1px solid #dce4e8; border-radius: 6px; overflow: hidden; background: #FFFFFF; }
.funnel-item { flex: 1; border-top: 2px solid #0b6e69; padding: 12px 8px; text-align: center; }
.funnel-item + .funnel-item { border-left: 1px solid #dce4e8; }
.funnel-num { font-size: 24px; font-weight: 700; line-height: 1; }
.funnel-lbl { font-size: 12px; color: #667582; margin-top: 5px; }
.kanban { display: grid; grid-template-columns: repeat(4, minmax(240px, 1fr)) minmax(200px, .82fr); gap: 14px; align-items: stretch; overflow-x: auto; padding-bottom: 8px; }
.kanban-col, .rejected-col { background: #eaf0ef; border: 1px solid #dce4e8; border-radius: 6px; overflow: hidden; }
.kanban-col-head { min-height: 44px; display: flex; align-items: center; justify-content: space-between; padding: 0 12px; font-size: 13px; font-weight: 700; }
.kanban-count { display: grid; min-width: 22px; height: 22px; padding: 0 6px; place-items: center; border-radius: 999px; background: rgba(0,0,0,0.07); font-size: 11px; }
.kanban-body { display: grid; align-content: start; gap: 10px; padding: 10px; }
.kanban-empty { display: grid; min-height: 100px; place-items: center; color: #87959d; font-size: 12px; }
.kanban-sep { display: none; }
.app-card { background: #FFFFFF; border: 1px solid #cfdadd; border-radius: 5px; padding: 12px; }
.app-card-head { display: flex; align-items: flex-start; gap: 8px; }
.app-card-title { flex: 1; min-width: 0; }
.app-card-job { font-weight: 600; font-size: 13px; color: #17212b; }
.app-card-co { font-size: 12px; color: #667582; margin-top: 2px; }
.app-card-status { display: flex; align-items: center; gap: 6px; margin-top: 10px; }
.round-chip { display: inline-block; padding: 2px 8px; border-radius: 4px; background: #6366F1; color: #FFF; font-size: 12px; font-weight: 700; }
.round-chip.muted { background: #E5E7EB; color: #6B7280; }
.result-chip { display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 12px; font-weight: 700; }
.app-card-note { font-size: 12px; color: #4B5563; margin-top: 8px; background: #FFFBEB; border-radius: 4px; padding: 6px 8px; }
.app-card-event { font-size: 12px; color: #17212b; margin-top: 8px; background: #f4f8f8; border-left: 2px solid #0b6e69; padding: 6px 8px; }
.app-card-foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 10px; padding-top: 8px; border-top: 1px solid #e9eef0; }
.app-card-toggle { border: 1px solid #dce4e8; border-radius: 4px; background: #fff; color: #61717b; padding: 2px 6px; cursor: pointer; }
.app-card-editor { display: grid; gap: 8px; padding-top: 10px; margin-top: 8px; border-top: 1px solid #dce4e8; }
.app-card-editor[hidden] { display: none; }
.stage-form, .manual-form, .event-form { display: flex; flex-wrap: wrap; gap: 6px; }
.stage-form select, .stage-form input, .manual-form input, .event-form select, .event-form input { border: 1px solid #dce4e8; border-radius: 4px; padding: 5px 8px; font-size: 12px; }
.stage-form button, .manual-form button, .event-form button { background: #0b6e69; color: #FFF; border: none; border-radius: 4px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
.manual-form { background: #FFFFFF; padding: 12px; border: 1px solid #dce4e8; border-radius: 6px; margin-bottom: 16px; }
.del-btn { background: none; border: none; color: #EF4444; font-size: 12px; cursor: pointer; }

.schedule-layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
.schedule-day { background: #FFFFFF; border: 1px solid #dce4e8; border-radius: 6px; margin-bottom: 12px; overflow: hidden; }
.schedule-date { display: flex; justify-content: space-between; padding: 10px 14px; background: #F9FAFB; font-weight: 700; }
.schedule-stack { padding: 10px; display: grid; gap: 10px; }
.schedule-card { display: grid; grid-template-columns: 72px minmax(0, 1fr) auto; gap: 12px; border: 1px solid #e9eef0; border-radius: 6px; padding: 10px; }
.schedule-time { color: #0b6e69; font-weight: 700; }
.schedule-side { background: #FFFFFF; border: 1px solid #dce4e8; border-radius: 6px; padding: 12px; }
.schedule-form-list { display: grid; gap: 10px; }
.schedule-form-card { border: 1px solid #e9eef0; border-radius: 6px; padding: 10px; }

@media (max-width: 1100px) {
  .main { padding: 22px 20px 36px; }
  .stat-grid, .advice-qa-grid, .adc-grid, .advice-method-grid, .cp-layer-grid, .apb-meta-grid { grid-template-columns: 1fr; }
  .stat-card { border-right: 0; border-bottom: 1px solid #dce4e8; }
}
@media (max-width: 768px) {
  .layout { flex-direction: column; }
  .sidebar { width: 100%; height: auto; max-height: 82vh; position: relative; border-right: 0; border-bottom: 1px solid #dce4e8; }
  .dir-tree-container { max-height: 24vh; }
  .main { padding: 16px; }
  .big-card-grid, .schedule-layout { grid-template-columns: 1fr; }
}
"""


_JS = """
document.addEventListener('DOMContentLoaded', () => {
  const navItems = document.querySelectorAll('.nav-item');
  const pages = document.querySelectorAll('.page');
  const el = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  function activate(target) {
    const page = document.querySelector(`.page[data-page="${target}"]`);
    if (!page) return;
    navItems.forEach(i => i.classList.toggle('active', i.dataset.target === target));
    pages.forEach(p => p.classList.remove('active'));
    page.classList.add('active');
  }
  window.activatePage = activate;

  navItems.forEach(item => {
    item.addEventListener('click', () => {
      activate(item.dataset.target);
      if (history.replaceState) history.replaceState(null, '', '#' + item.dataset.target);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });

  // ── 共享数据 + 渲染助手 ──
  const JOBS = window.__JOBS || [];
  const DIR_DATA = window.__DIRECTORY_DATA || JOBS;
  const CAT_LABELS = window.__CATEGORY_LABELS || {};
  const PRIMARY_CAT_ORDER = window.__PRIMARY_CATEGORY_ORDER || [];
  const SERVER_PAGINATION = Boolean(window.__SERVER_PAGINATION);
  const AVATAR_COLORS = ['#EF4444','#F97316','#F59E0B','#10B981','#06B6D4','#3B82F6','#6366F1','#8B5CF6','#EC4899','#14B8A6'];

  function avatar(name) {
    const s = String(name || '').trim();
    let h = 0; for (const ch of s) h += ch.codePointAt(0);
    const color = AVATAR_COLORS[h % AVATAR_COLORS.length];
    return '<div class="avatar" style="background:' + color + '">' + esc(s.slice(0, 2) || '?') + '</div>';
  }
  function companyLink(name, cohort) {
    return '<button type="button" class="company-link" data-company-link="' + esc(name) +
      '" data-company-cohort="' + esc(cohort || 'current') + '">' + esc(name) + '</button>';
  }
  function scoreCell(s) {
    let bg, fg;
    if (s >= 80) { bg = '#ECFDF5'; fg = '#10B981'; }
    else if (s >= 60) { bg = '#FFFBEB'; fg = '#F59E0B'; }
    else { bg = '#F3F4F6'; fg = '#9CA3AF'; }
    return '<span class="cell-score" style="background:' + bg + ';color:' + fg + '">' + s + '</span>';
  }
  function badge(j) {
    if (!j.e) return '<span class="cell-score uneval">' + esc(j.r || '未评估') + '</span>';
    return scoreCell(j.s || 0);
  }
  function opCell(j) {
    var link = j.k === 'list' ? '<span class="no-detail" title="无公开岗位详情页">—</span>' :
      (j.u ? '<a href="' + esc(j.u) + '" target="_blank" rel="noopener" class="cell-action" title="去投递">↗</a>' : '');
    if (j.ap) return '<span class="job-inline-actions"><span class="app-badge">📋 ' + esc(j.ap) + '</span> ' + link + '</span>';
    if (window.__EDITABLE && j.id != null) {
      var applied = '<form class="apply-form" method="post" action="/apply" style="display:inline-flex;align-items:center;gap:6px">' +
        '<input type="hidden" name="job_id" value="' + esc(j.id) + '">' +
        '<input type="hidden" name="company" value="' + esc(j.c) + '">' +
        '<input type="hidden" name="title" value="' + esc(j.t) + '">' +
        '<input type="hidden" name="city" value="' + esc(j.ct) + '">' +
        '<button type="submit" class="mark-btn">✅ 我投了</button></form>';
      return '<span class="job-inline-actions">' + applied + link + '</span>';
    }
    return link;
  }
  function jobName(j) {
    var name = esc(j.t) + ' <span class="cohort-badge">' + esc(j.cy || '届别待确认') + '</span>';
    var linkHtml = j.k === 'list' ? '<span class="cell-job-link">' + name + '</span>' :
      '<a href="' + esc(j.u) + '" target="_blank" rel="noopener" class="cell-job-link">' + name + '</a>';
    var labels = j.cat_labels || (j.cat_label ? [j.cat_label] : []);
    if (labels.length) {
      var tags = labels.map((lbl, idx) =>
        '<span class="cat-tag ' + (idx === 0 ? 'cat-tag-primary' : 'cat-tag-sec') + '" title="' + esc(j.cat_reason || '') + '">' + esc(lbl) + '</span>'
      ).join('');
      linkHtml += '<div class="cell-job-cats">' + tags + '</div>';
    }
    return linkHtml;
  }
  function jobMatchesCategory(j, cat) {
    if (!cat) return true;
    if (j.cat === cat) return true;
    if (Array.isArray(j.cats) && j.cats.indexOf(cat) >= 0) return true;
    return false;
  }

  // ── 全部岗位：搜索 / 类别筛选 / 公司联动 / 去重统计 / 分页 ──
  const tbody = el('jobTbody');
  let highlightedJobId = null;

  if (tbody) {
    const PAGE = 50;
    let page = 1;
    const fCompany = el('fCompany');
    const fCategory = el('fCategory');
    const fPlatform = el('fPlatform');
    const fEval = el('fEval');
    const fScore = el('fScore');
    const jobSearch = el('jobSearch');
    const btnClearFilters = el('btnClearFilters');
    const catFilterBar = el('categoryFilterBar');
    const catStatus = el('categoryFilterStatus');
    const filteredCoBar = el('filteredCompanyBar');
    const filteredCoList = el('filteredCompanyList');

    function filtered(ignoreCompanyFilter) {
      const q = (jobSearch.value || '').trim().toLowerCase();
      const co = ignoreCompanyFilter ? '' : fCompany.value;
      const cat = fCategory.value;
      const pl = fPlatform.value;
      const ev = fEval.value;
      const sc = fScore.value;
      let r = JOBS.filter(j => {
        if (co && j.c !== co) return false;
        if (cat && !jobMatchesCategory(j, cat)) return false;
        if (pl && j.p !== pl) return false;
        if (ev === 'scored' && !j.e) return false;
        if (ev === 'uneval' && j.e) return false;
        if (sc) {
          if (!j.e) return false;
          const s = j.s || 0;
          if (sc === '70' && s < 70) return false;
          if (sc === '60' && (s < 60 || s >= 70)) return false;
          if (sc === '0' && s >= 60) return false;
        }
        if (q) {
          const h = (j.c + ' ' + j.t + ' ' + (j.ct || '') + ' ' + (j.cat_label || '')).toLowerCase();
          if (h.indexOf(q) < 0) return false;
        }
        return true;
      });
      r.sort((a, b) => (b.e ? (b.s || 0) : -1) - (a.e ? (a.s || 0) : -1));
      return r;
    }

    function syncFilterUI(allMatchingRows, activeCompaniesForCat) {
      const cat = fCategory.value;
      const co = fCompany.value;
      const q = (jobSearch.value || '').trim();
      const hasAnyFilter = Boolean(cat || co || q || fPlatform.value || fEval.value || fScore.value);
      if (btnClearFilters) btnClearFilters.hidden = !hasAnyFilter;

      // 同步类别胶囊选中态
      if (catFilterBar) {
        catFilterBar.querySelectorAll('.cat-filter-pill').forEach(btn => {
          btn.classList.toggle('active', (btn.dataset.cat || '') === cat);
        });
      }

      // 动态更新公司下拉框（仅展示当前类别下有岗位的公司）
      if (fCompany && Array.isArray(activeCompaniesForCat)) {
        const currentCo = fCompany.value;
        fCompany.innerHTML = '<option value="">全部公司 (' + activeCompaniesForCat.length + ')</option>' +
          activeCompaniesForCat.map(c =>
            '<option value="' + esc(c) + '"' + (c === currentCo ? ' selected' : '') + '>' + esc(c) + '</option>'
          ).join('');
      }

      // 动态更新当前类别下的关联公司条
      if (filteredCoBar && filteredCoList) {
        if (cat && activeCompaniesForCat.length) {
          filteredCoBar.hidden = false;
          filteredCoList.innerHTML = activeCompaniesForCat.map(c =>
            '<button type="button" class="fcb-chip' + (c === co ? ' active' : '') + '" data-filter-co="' + esc(c) + '">' +
            '<span>' + esc(c) + '</span></button>'
          ).join('');
        } else {
          filteredCoBar.hidden = true;
          filteredCoList.innerHTML = '';
        }
      }

      // 同步顶部代表大卡片显示
      const featuredList = el('featuredCardList');
      if (featuredList) {
        featuredList.querySelectorAll('.big-card').forEach(card => {
          const cardCo = card.dataset.company || '';
          const cardTitle = card.dataset.title || '';
          const cardCats = (card.dataset.cats || '').split(',');
          let show = true;
          if (co && cardCo !== co) show = false;
          if (cat && cardCats.indexOf(cat) < 0) show = false;
          if (q && (cardCo + ' ' + cardTitle).toLowerCase().indexOf(q.toLowerCase()) < 0) show = false;
          card.hidden = !show;
        });
      }
    }

    async function render() {
      let rows, total, pages, uniqueCompanyCount, highMatchCount, activeCompaniesForCat;
      const cat = fCategory.value;
      const catMeta = (window.__CATEGORY_META || {})[cat];

      if (SERVER_PAGINATION) {
        const params = new URLSearchParams({
          cohort: 'current', page: String(page), per_page: String(PAGE),
          q: jobSearch.value || '', company: fCompany.value || '',
          category: fCategory.value || '',
          platform: fPlatform.value || '', evaluation: fEval.value || '',
          score: fScore.value || ''
        });
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">正在加载岗位...</td></tr>';
        try {
          const response = await fetch('/api/jobs?' + params.toString());
          if (!response.ok) throw new Error('load failed');
          const result = await response.json();
          rows = result.jobs; total = result.total; pages = result.pages;
          activeCompaniesForCat = result.companies || (catMeta ? catMeta.companies : []);
          uniqueCompanyCount = result.company_count != null ? result.company_count : activeCompaniesForCat.length;
          highMatchCount = result.high_match_count != null ? result.high_match_count : 0;
        } catch (_) {
          tbody.innerHTML = '<tr><td colspan="6" class="empty-state">岗位加载失败，请刷新重试</td></tr>';
          return;
        }
      } else {
        const rowsForCat = JOBS.filter(j => jobMatchesCategory(j, cat));
        activeCompaniesForCat = Array.from(new Set(rowsForCat.map(j => j.c).filter(Boolean))).sort();
        const allRows = filtered(false);
        // 严格按岗位 ID 去重统计，防止一岗多类被重复计入汇总
        const uniqueJobIds = new Set();
        const uniqueCoSet = new Set();
        let hm = 0;
        allRows.forEach((j, i) => {
          const uid = j.id != null ? j.id : (j.c + '|' + j.t + '|' + i);
          if (!uniqueJobIds.has(uid)) {
            uniqueJobIds.add(uid);
            if (j.c) uniqueCoSet.add(j.c);
            if (j.e && (j.s || 0) >= 70) hm++;
          }
        });
        total = uniqueJobIds.size;
        uniqueCompanyCount = uniqueCoSet.size;
        highMatchCount = hm;
        pages = Math.max(1, Math.ceil(allRows.length / PAGE));
        if (page > pages) page = pages;
        if (page < 1) page = 1;
        rows = allRows.slice((page - 1) * PAGE, page * PAGE);
      }

      // 更新顶部统计卡片与状态说明
      if (el('statActiveJobs')) el('statActiveJobs').textContent = String(total);
      if (el('statActiveCompanies')) el('statActiveCompanies').textContent = String(uniqueCompanyCount);
      if (el('statHighMatch')) el('statHighMatch').textContent = String(highMatchCount);
      if (catStatus) {
        const catName = cat ? (CAT_LABELS[cat] || cat) : '全部类别';
        const coPart = fCompany.value ? (' · 公司：<b>' + esc(fCompany.value) + '</b>') : '';
        catStatus.innerHTML = '当前展示：<b>' + esc(catName) + '</b>' + coPart +
          '（共 <b>' + total + '</b> 个去重岗位，覆盖 <b>' + uniqueCompanyCount + '</b> 家公司）';
      }

      syncFilterUI(rows, activeCompaniesForCat);

      tbody.innerHTML = rows.length ? rows.map(j =>
        '<tr id="job-row-' + esc(j.id) + '" class="' + (String(highlightedJobId) === String(j.id) ? 'job-highlight-flash' : '') + '">' +
        '<td class="cell-company"><div class="cell-company-inner">' + companyLink(j.c, 'current') + '</div></td>' +
        '<td>' + jobName(j) + '</td>' +
        '<td>' + badge(j) + '</td>' +
        '<td class="cell-city">' + esc(j.ct || '—') + '</td>' +
        '<td><span class="cell-plat">' + esc(j.p) + '</span></td>' +
        '<td class="cell-op">' + opCell(j) + '</td></tr>'
      ).join('') : '<tr><td colspan="6" class="empty-state">当前筛选条件下暂无匹配岗位，可点击「清除筛选」返回全部岗位</td></tr>';

      el('jpInfo').textContent = '第 ' + page + ' / ' + pages + ' 页 · 共 ' + total + ' 条';
      el('jpPrev').disabled = page <= 1;
      el('jpNext').disabled = page >= pages;
    }

    window.applyCategoryFilter = function(catKey, companyName, searchKw, targetJobId) {
      activate('recommended');
      if (fCategory) fCategory.value = catKey || '';
      if (fCompany) fCompany.value = companyName || '';
      if (jobSearch) jobSearch.value = searchKw || '';
      if (fPlatform) fPlatform.value = '';
      if (fEval) fEval.value = '';
      if (fScore) fScore.value = '';
      highlightedJobId = targetJobId || null;
      page = 1;
      render().then(() => {
        if (targetJobId) {
          const rowEl = el('job-row-' + targetJobId) || el('job-card-' + targetJobId);
          if (rowEl) {
            rowEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            rowEl.classList.add('job-highlight-flash');
          }
        }
      });
    };

    window.clearAllJobFilters = function() {
      if (jobSearch) jobSearch.value = '';
      if (fCompany) fCompany.value = '';
      if (fCategory) fCategory.value = '';
      if (fPlatform) fPlatform.value = '';
      if (fEval) fEval.value = '';
      if (fScore) fScore.value = '';
      highlightedJobId = null;
      page = 1;
      if (history.replaceState) history.replaceState(null, '', '#recommended');
      render();
    };

    if (btnClearFilters) {
      btnClearFilters.addEventListener('click', () => window.clearAllJobFilters());
    }
    if (catFilterBar) {
      catFilterBar.addEventListener('click', e => {
        const pill = e.target.closest('.cat-filter-pill');
        if (!pill) return;
        const targetCat = pill.dataset.cat || '';
        fCategory.value = targetCat;
        fCompany.value = '';
        page = 1;
        if (history.replaceState) {
          history.replaceState(null, '', targetCat ? ('#cat=' + encodeURIComponent(targetCat)) : '#recommended');
        }
        render();
      });
    }
    if (filteredCoList) {
      filteredCoList.addEventListener('click', e => {
        const chip = e.target.closest('[data-filter-co]');
        if (!chip) return;
        const coName = chip.dataset.filterCo || '';
        fCompany.value = (fCompany.value === coName) ? '' : coName;
        page = 1;
        render();
      });
    }

    let searchTimer;
    ['jobSearch', 'fCompany', 'fCategory', 'fPlatform', 'fEval', 'fScore'].forEach(id => {
      const inputEl = el(id);
      if (!inputEl) return;
      inputEl.addEventListener('input', () => {
        page = 1;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(render, SERVER_PAGINATION ? 180 : 0);
      });
      inputEl.addEventListener('change', () => {
        if (id === 'fCategory') fCompany.value = '';
        page = 1;
        render();
      });
    });
    el('jpPrev').addEventListener('click', () => { if (page > 1) { page--; render(); } });
    el('jpNext').addEventListener('click', () => { page++; render(); });
    render();
  }

  // ── 左侧顶部「目录」模块：支持双层级模式、搜索、折叠与延迟渲染 (Stage D) ──
  const dirTreeContainer = el('dirTreeContainer');
  const dirSearchInput = el('dirSearch');
  const dirModeBtn = el('dirModeBtn');
  const dirCollapseBtn = el('dirCollapseBtn');
  const dirBody = el('dirBody');
  const dirChevron = el('dirChevron');

  if (dirCollapseBtn && dirBody) {
    dirCollapseBtn.addEventListener('click', () => {
      const expanded = dirCollapseBtn.getAttribute('aria-expanded') === 'true';
      dirCollapseBtn.setAttribute('aria-expanded', String(!expanded));
      dirBody.hidden = expanded;
      if (dirChevron) dirChevron.textContent = expanded ? '▸' : '▾';
    });
  }

  if (dirTreeContainer) {
    let dirMode = 'cat-co-job'; // 'cat-co-job' 或 'co-cat-job'
    const expandedL1 = new Set();
    const expandedL2 = new Set();

    function buildDirectoryHierarchy(query) {
      const q = (query || '').trim().toLowerCase();
      const filteredJobs = DIR_DATA.filter(j => {
        if (!q) return true;
        const catName = CAT_LABELS[j.cat] || j.cat || '';
        return (j.c + ' ' + j.t + ' ' + catName).toLowerCase().indexOf(q) >= 0;
      });

      if (dirMode === 'cat-co-job') {
        const byCat = new Map();
        PRIMARY_CAT_ORDER.forEach(ck => byCat.set(ck, new Map()));
        filteredJobs.forEach(j => {
          const cats = (Array.isArray(j.cats) && j.cats.length)
            ? j.cats.filter(c => PRIMARY_CAT_ORDER.indexOf(c) >= 0 || String(c).indexOf('custom_') === 0)
            : [j.cat || 'unclassified'];
          const effectiveCats = cats.length ? cats : [j.cat || 'unclassified'];
          effectiveCats.forEach(ck => {
            if (!byCat.has(ck)) byCat.set(ck, new Map());
            const coMap = byCat.get(ck);
            if (!coMap.has(j.c)) coMap.set(j.c, []);
            coMap.get(j.c).push(j);
          });
        });
        const l1Nodes = [];
        byCat.forEach((coMap, ck) => {
          if (coMap.size === 0) return;
          let jobCount = 0;
          const l2Nodes = [];
          Array.from(coMap.keys()).sort().forEach(coName => {
            const jobs = coMap.get(coName);
            jobCount += jobs.length;
            l2Nodes.push({ key: ck + '::' + coName, label: coName, company: coName, cat: ck, count: jobs.length, jobs });
          });
          l1Nodes.push({
            key: ck,
            label: CAT_LABELS[ck] || ck,
            cat: ck,
            count: jobCount,
            subCount: l2Nodes.length,
            children: l2Nodes
          });
        });
        return l1Nodes;
      } else {
        const byCo = new Map();
        filteredJobs.forEach(j => {
          if (!byCo.has(j.c)) byCo.set(j.c, new Map());
          const catMap = byCo.get(j.c);
          const ck = j.cat || 'unclassified';
          if (!catMap.has(ck)) catMap.set(ck, []);
          catMap.get(ck).push(j);
        });
        const l1Nodes = [];
        Array.from(byCo.keys()).sort().forEach(coName => {
          const catMap = byCo.get(coName);
          let jobCount = 0;
          const l2Nodes = [];
          catMap.forEach((jobs, ck) => {
            jobCount += jobs.length;
            l2Nodes.push({
              key: coName + '::' + ck,
              label: CAT_LABELS[ck] || ck,
              company: coName,
              cat: ck,
              count: jobs.length,
              jobs
            });
          });
          l1Nodes.push({
            key: coName,
            label: coName,
            company: coName,
            count: jobCount,
            subCount: l2Nodes.length,
            children: l2Nodes
          });
        });
        return l1Nodes;
      }
    }

    function renderDirectoryTree() {
      const q = dirSearchInput ? dirSearchInput.value.trim() : '';
      const tree = buildDirectoryHierarchy(q);
      if (!tree.length) {
        dirTreeContainer.innerHTML = '<div style="padding:10px;color:#8795a0;text-align:center">无匹配目录项</div>';
        return;
      }
      // 搜索词存在时默认展开前 3 个一级节点方便直接点击
      const forceExpand = Boolean(q);
      const htmlParts = tree.map((n1, idx1) => {
        const isOpen1 = expandedL1.has(n1.key) || (forceExpand && idx1 < 3);
        let childrenHtml = '';
        if (isOpen1) {
          childrenHtml = '<div class="dir-children-l2">' + n1.children.map((n2, idx2) => {
            const isOpen2 = expandedL2.has(n2.key) || (forceExpand && idx2 < 2);
            let jobsHtml = '';
            if (isOpen2) {
              const maxShow = 25;
              const slice = n2.jobs.slice(0, maxShow);
              jobsHtml = '<div class="dir-children-l3">' + slice.map(j =>
                '<button type="button" class="dir-job-btn" data-dir-job-id="' + esc(j.id) + '" ' +
                'data-dir-co="' + esc(j.c) + '" data-dir-title="' + esc(j.t) + '" data-dir-cat="' + esc(n2.cat) + '" data-dir-ch="' + esc(j.ch || 'current') + '">' +
                '<span class="dir-job-title">' + esc(j.t) + '</span>' +
                (j.s != null ? '<span class="dir-job-score">' + j.s + '</span>' : '') +
                '</button>'
              ).join('') +
              (n2.jobs.length > maxShow
                ? '<button type="button" class="dir-more-btn" data-dir-co="' + esc(n2.company) + '" data-dir-cat="' + esc(n2.cat) + '">查看全部 ' + n2.jobs.length + ' 个岗位 ›</button>'
                : '') +
              '</div>';
            }
            return '<div class="dir-node-l2">' +
              '<div class="dir-row" data-l2-key="' + esc(n2.key) + '" data-dir-co="' + esc(n2.company) + '" data-dir-cat="' + esc(n2.cat) + '">' +
              '<span class="dir-exp" data-toggle-l2="' + esc(n2.key) + '">' + (isOpen2 ? '▾' : '▸') + '</span>' +
              '<span class="dir-lbl">' + esc(n2.label) + '</span>' +
              '<span class="dir-cnt">' + n2.count + '</span>' +
              '</div>' + jobsHtml + '</div>';
          }).join('') + '</div>';
        }
        return '<div class="dir-node-l1">' +
          '<div class="dir-row" data-l1-key="' + esc(n1.key) + '" data-dir-cat="' + esc(n1.cat || '') + '" data-dir-co="' + esc(n1.company || '') + '">' +
          '<span class="dir-exp" data-toggle-l1="' + esc(n1.key) + '">' + (isOpen1 ? '▾' : '▸') + '</span>' +
          '<span class="dir-lbl">' + esc(n1.label) + '</span>' +
          '<span class="dir-cnt">' + n1.count + '岗</span>' +
          '</div>' + childrenHtml + '</div>';
      });
      dirTreeContainer.innerHTML = htmlParts.join('');
    }

    dirTreeContainer.addEventListener('click', e => {
      const t1 = e.target.closest('[data-toggle-l1]');
      if (t1) {
        e.stopPropagation();
        const k = t1.dataset.toggleL1;
        if (expandedL1.has(k)) expandedL1.delete(k); else expandedL1.add(k);
        renderDirectoryTree();
        return;
      }
      const t2 = e.target.closest('[data-toggle-l2]');
      if (t2) {
        e.stopPropagation();
        const k = t2.dataset.toggleL2;
        if (expandedL2.has(k)) expandedL2.delete(k); else expandedL2.add(k);
        renderDirectoryTree();
        return;
      }
      const jobBtn = e.target.closest('.dir-job-btn');
      if (jobBtn) {
        const jid = jobBtn.dataset.dirJobId;
        const co = jobBtn.dataset.dirCo;
        const title = jobBtn.dataset.dirTitle;
        const cat = jobBtn.dataset.dirCat;
        const ch = jobBtn.dataset.dirCh || 'current';
        if (history.replaceState) {
          history.replaceState(null, '', '#job=' + encodeURIComponent(jid) + '&company=' + encodeURIComponent(co) + '&title=' + encodeURIComponent(title));
        }
        if (ch === 'current' && window.applyCategoryFilter) {
          window.applyCategoryFilter(cat, co, title, jid);
        } else if (window.openCompany) {
          window.openCompany(co, ch);
        }
        return;
      }
      const moreBtn = e.target.closest('.dir-more-btn');
      if (moreBtn) {
        const co = moreBtn.dataset.dirCo;
        const cat = moreBtn.dataset.dirCat;
        if (window.applyCategoryFilter) window.applyCategoryFilter(cat, co, '', null);
        return;
      }
      const l2Row = e.target.closest('[data-l2-key]');
      if (l2Row) {
        const k = l2Row.dataset.l2Key;
        const co = l2Row.dataset.dirCo;
        const cat = l2Row.dataset.dirCat;
        expandedL2.add(k);
        renderDirectoryTree();
        if (dirMode === 'cat-co-job' && window.openCompany) {
          if (history.replaceState) history.replaceState(null, '', '#company=' + encodeURIComponent(co));
          window.openCompany(co, 'current');
        } else if (window.applyCategoryFilter) {
          if (history.replaceState) history.replaceState(null, '', '#cat=' + encodeURIComponent(cat));
          window.applyCategoryFilter(cat, co, '', null);
        }
        return;
      }
      const l1Row = e.target.closest('[data-l1-key]');
      if (l1Row) {
        const k = l1Row.dataset.l1Key;
        const cat = l1Row.dataset.dirCat;
        const co = l1Row.dataset.dirCo;
        if (expandedL1.has(k)) expandedL1.delete(k); else expandedL1.add(k);
        renderDirectoryTree();
        if (dirMode === 'cat-co-job' && cat && window.applyCategoryFilter) {
          if (history.replaceState) history.replaceState(null, '', '#cat=' + encodeURIComponent(cat));
          window.applyCategoryFilter(cat, '', '', null);
        } else if (dirMode === 'co-cat-job' && co && window.openCompany) {
          if (history.replaceState) history.replaceState(null, '', '#company=' + encodeURIComponent(co));
          window.openCompany(co, 'current');
        }
      }
    });

    if (dirSearchInput) {
      dirSearchInput.addEventListener('input', () => renderDirectoryTree());
    }
    if (dirModeBtn) {
      dirModeBtn.addEventListener('click', () => {
        dirMode = dirMode === 'cat-co-job' ? 'co-cat-job' : 'cat-co-job';
        dirModeBtn.dataset.mode = dirMode;
        dirModeBtn.textContent = dirMode === 'cat-co-job' ? '按类别' : '按公司';
        expandedL1.clear();
        expandedL2.clear();
        renderDirectoryTree();
      });
    }
    renderDirectoryTree();
  }

  // ── 综合建议页交互：点击方向卡片跳转到对应类别筛选 + 技能模拟器 (Stage E) ──
  document.addEventListener('click', e => {
    const jumpBtn = e.target.closest('[data-jump-cat]');
    if (jumpBtn && window.applyCategoryFilter) {
      const cat = jumpBtn.dataset.jumpCat;
      if (history.replaceState) history.replaceState(null, '', '#cat=' + encodeURIComponent(cat));
      window.applyCategoryFilter(cat, '', '', null);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });

  
  const btnRegen = el('btnRegenerateAdvice');
  if (btnRegen) {
    btnRegen.addEventListener('click', async () => {
      const origText = btnRegen.textContent;
      btnRegen.disabled = true;
      btnRegen.textContent = '⏳ 正在检测配置与重新生成...';
      try {
        const resp = await fetch('/api/advice/regenerate', { method: 'POST' });
        if (resp.ok) {
          const data = await resp.json();
          if (data.ok) {
            window.location.hash = '#comprehensive-advice';
            window.location.reload();
            return;
          }
        }
        alert('当前为静态只读报告预览。若你已修改 profile.yaml，请双击根目录「查看.bat」（或运行 python view_all.py）即可在数秒内刷新综合建议版本！');
      } catch (_) {
        alert('当前为静态报告模式。若你已修改 profile.yaml，请双击根目录「查看.bat」（或运行 python view_all.py）即可立即刷新综合建议！');
      } finally {
        btnRegen.disabled = false;
        btnRegen.textContent = origText;
      }
    });
  }

  const calibrator = el('adviceCalibrator');
  if (calibrator) {
    const simOut = el('calSimOutput');
    const checks = calibrator.querySelectorAll('input[data-sim-skill]');
    try {
      const saved = JSON.parse(localStorage.getItem('__ca_sim_skills') || '[]');
      checks.forEach(c => { if (saved.indexOf(c.value) >= 0) c.checked = true; });
    } catch (_) {}

    function updateSim() {
      const selected = Array.from(checks).filter(c => c.checked).map(c => c.value);
      try { localStorage.setItem('__ca_sim_skills', JSON.stringify(selected)); } catch (_) {}
      if (!selected.length) {
        if (simOut) simOut.innerHTML = '当前按 <code>profile.yaml</code> 原始基线展示';
        return;
      }
      const unlocked = [];
      if (selected.indexOf('sql') >= 0) unlocked.push('后端/数据平台短板已消除');
      if (selected.indexOf('qt') >= 0) unlocked.push('跨平台桌面客户端契合度提升至高');
      if (selected.indexOf('go') >= 0) unlocked.push('云原生与高并发服务端可投面扩大');
      if (selected.indexOf('ci') >= 0) unlocked.push('SDET测试开发与工程效能直接达标');
      if (selected.indexOf('rag') >= 0) unlocked.push('AI应用工程/Agent评测闭环成形');
      if (simOut) simOut.textContent = '模拟增益：' + unlocked.join(' · ');
    }
    checks.forEach(c => c.addEventListener('change', updateSim));
    updateSim();
  }

  // ── 公司排行：前端聚合 + 搜索 + 分页；行可点击进入公司详情 ──
  const cTbody = el('companyTbody');
  if (cTbody) {
    function aggregateCompanies(source) {
      const agg = {};
      source.forEach(j => {
        const a = agg[j.c] || (agg[j.c] = {
          total: 0, scores: [], topS: null, topT: j.t || '—'
        });
        a.total++;
        if (j.e) {
          const s = j.s || 0;
          a.scores.push(s);
          if (a.topS === null || s > a.topS) { a.topS = s; a.topT = j.t; }
        }
      });
      return Object.keys(agg).map(c => {
        const a = agg[c];
        const avg = a.scores.length ? a.scores.reduce((x, y) => x + y, 0) / a.scores.length : null;
        return { c, total: a.total, avg, topS: a.topS, topT: a.topT };
      });
    }
    const embeddedSummaries = window.__COMPANY_SUMMARIES || {};
    const companySets = {
      current: embeddedSummaries.current || aggregateCompanies(JOBS),
      previous: embeddedSummaries.previous || aggregateCompanies(window.__PREVIOUS_JOBS || []),
      unknown: embeddedSummaries.unknown || aggregateCompanies(window.__UNKNOWN_JOBS || [])
    };
    Object.values(companySets).forEach(rows => rows.sort((x, y) =>
      ((x.avg === null) - (y.avg === null)) || ((y.avg || 0) - (x.avg || 0)) || (y.total - x.total)));

    let companyCohort = 'current';
    let companies = companySets.current;
    el('ccCurrentCount').textContent = companySets.current.length;
    el('ccPreviousCount').textContent = companySets.previous.length;
    el('ccUnknownCount').textContent = companySets.unknown.length;

    const CPAGE = 30;
    let cpage = 1;
    function cfiltered() {
      const q = (el('companySearch').value || '').trim().toLowerCase();
      return q ? companies.filter(o => o.c.toLowerCase().indexOf(q) >= 0) : companies;
    }
    function crender() {
      const rows = cfiltered(), total = rows.length;
      const pages = Math.max(1, Math.ceil(total / CPAGE));
      if (cpage > pages) cpage = pages;
      if (cpage < 1) cpage = 1;
      const slice = rows.slice((cpage - 1) * CPAGE, cpage * CPAGE);
      const scoresEnabled = companyCohort === 'current';
      cTbody.innerHTML = slice.length ? slice.map(o =>
        '<tr class="company-row" data-company="' + esc(o.c) + '" data-cohort="' + companyCohort + '">' +
        '<td class="cell-company"><div class="cell-company-inner">' + avatar(o.c) + ' <span>' + esc(o.c) + '</span></div></td>' +
        '<td class="cell-num">' + o.total + '</td>' +
        '<td>' + (!scoresEnabled ? '<span class="cell-score uneval">不评分</span>' :
          (o.avg === null ? '<span class="cell-score uneval">待评估</span>' : scoreCell(Math.round(o.avg)))) + '</td>' +
        '<td>' + (!scoresEnabled || o.topS === null ? '—' : scoreCell(o.topS)) + '</td>' +
        '<td class="cell-job-name">' + esc(o.topT) + '</td></tr>'
      ).join('') : '<tr><td colspan="5" class="empty-state">无匹配公司</td></tr>';
      el('cpInfo').textContent = '第 ' + cpage + ' / ' + pages + ' 页 · 共 ' + total + ' 家';
      el('cpPrev').disabled = cpage <= 1;
      el('cpNext').disabled = cpage >= pages;
    }
    el('companySearch').addEventListener('input', () => { cpage = 1; crender(); });
    el('cpPrev').addEventListener('click', () => { if (cpage > 1) { cpage--; crender(); } });
    el('cpNext').addEventListener('click', () => { cpage++; crender(); });
    document.querySelectorAll('.company-cohort-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        companyCohort = tab.dataset.companyCohort;
        companies = companySets[companyCohort] || [];
        cpage = 1;
        document.querySelectorAll('.company-cohort-tab').forEach(item => {
          const active = item === tab;
          item.classList.toggle('active', active);
          item.setAttribute('aria-selected', String(active));
        });
        el('companyAvgHead').textContent = companyCohort === 'current' ? '平均匹配度' : '评分状态';
        el('companyTopScoreHead').textContent = companyCohort === 'current' ? '最高分' : '匹配度';
        el('companyTopJobHead').textContent = companyCohort === 'current' ? '最高分岗位' : '代表岗位';
        crender();
      });
    });
    cTbody.addEventListener('click', e => {
      const tr = e.target.closest('.company-row');
      if (tr) window.openCompany(tr.dataset.company, tr.dataset.cohort);
    });
    crender();

    window.openCompany = async function openCompany(name, cohort) {
      companyCohort = cohort || companyCohort || 'current';
      let list;
      if (SERVER_PAGINATION) {
        el('cdTbody').innerHTML = '<tr><td colspan="5" class="empty-state">正在加载岗位...</td></tr>';
        activate('company-detail');
        const params = new URLSearchParams({
          cohort: companyCohort, company: name, page: '1', per_page: '5000'
        });
        try {
          const response = await fetch('/api/jobs?' + params.toString());
          if (!response.ok) throw new Error('load failed');
          list = (await response.json()).jobs;
        } catch (_) {
          el('cdTbody').innerHTML = '<tr><td colspan="5" class="empty-state">岗位加载失败，请返回重试</td></tr>';
          return;
        }
      } else {
        const source = companyCohort === 'previous' ? (window.__PREVIOUS_JOBS || []) :
          (companyCohort === 'unknown' ? (window.__UNKNOWN_JOBS || []) : JOBS);
        list = source.filter(j => j.c === name)
          .sort((a, b) => (b.e ? (b.s || 0) : -1) - (a.e ? (a.s || 0) : -1));
      }
      const scores = list.filter(j => j.e).map(j => j.s || 0);
      const avg = scores.length ? Math.round(scores.reduce((x, y) => x + y, 0) / scores.length) : null;
      const topS = scores.length ? Math.max.apply(null, scores) : null;
      const campusUrl = (window.__COMPANY_CAREERS || {})[name];
      const campusButton = campusUrl
        ? '<div class="cd-actions"><a class="company-campus-link" href="' + esc(campusUrl) + '" target="_blank" rel="noopener">公司校招 <span>↗</span></a></div>'
        : '';
      const cohortLabel = companyCohort === 'current' ? '27届' :
        (companyCohort === 'previous' ? '往届' : '届别待确认');
      el('cdHead').innerHTML =
        '<div class="cd-title">' + avatar(name) + '<h1>' + esc(name) + '</h1>' +
        '<span class="cohort-badge">' + cohortLabel + '</span></div>' +
        '<div class="cd-stats">' +
        '<span class="cd-stat"><b>' + list.length + '</b> 个岗位</span>' +
        '<span class="cd-stat">平均匹配 <b>' + (companyCohort === 'current' ?
          (avg === null ? '待评估' : avg) : '不评分') + '</b></span>' +
        '<span class="cd-stat">最高分 <b>' + (companyCohort === 'current' && topS !== null ? topS : '—') + '</b></span>' +
        '</div>' + campusButton;
      el('cdTbody').innerHTML = list.length ? list.map(j =>
        '<tr><td>' + jobName(j) + '</td>' +
        '<td>' + badge(j) + '</td>' +
        '<td class="cell-city">' + esc(j.ct || '—') + '</td>' +
        '<td><span class="cell-plat">' + esc(j.p) + '</span></td>' +
        '<td class="cell-op">' + opCell(j) + '</td></tr>'
      ).join('') : '<tr><td colspan="5" class="empty-state">暂无岗位</td></tr>';
      activate('company-detail');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    };
    const cdBack = el('cdBack');
    if (cdBack) cdBack.addEventListener('click', () => {
      activate('companies');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  document.addEventListener('click', e => {
    const trigger = e.target.closest('[data-company-link]');
    if (trigger && window.openCompany) {
      window.openCompany(trigger.dataset.companyLink, trigger.dataset.companyCohort || 'current');
    }
  });

  document.addEventListener('click', e => {
    const toggle = e.target.closest('.app-card-toggle');
    if (!toggle) return;
    const card = toggle.closest('.app-card');
    const editor = card && card.querySelector('.app-card-editor');
    if (!editor) return;
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    editor.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('title', open ? '收起编辑' : '展开编辑');
    toggle.textContent = open ? '收起' : '···';
  });

  const previousSearch = el('previousSearch');
  const previousTbody = el('previousTbody');
  if (previousSearch && previousTbody) {
    if (SERVER_PAGINATION) {
      let previousPage = 1;
      let previousTimer;
      const renderPrevious = async () => {
        const params = new URLSearchParams({
          cohort: 'previous', page: String(previousPage), per_page: '50',
          q: previousSearch.value || ''
        });
        previousTbody.innerHTML = '<tr><td colspan="5" class="empty-state">正在加载岗位...</td></tr>';
        try {
          const response = await fetch('/api/jobs?' + params.toString());
          if (!response.ok) throw new Error('load failed');
          const result = await response.json();
          previousTbody.innerHTML = result.jobs.length ? result.jobs.map(j =>
            '<tr><td class="cell-company"><div class="cell-company-inner">' + companyLink(j.c, 'previous') + '</div></td>' +
            '<td>' + jobName(j) + '</td><td>' + badge(j) + '</td>' +
            '<td class="cell-city">' + esc(j.ct || '-') + '</td>' +
            '<td class="cell-op">' + opCell(j) + '</td></tr>'
          ).join('') : '<tr><td colspan="5" class="empty-state">无匹配岗位</td></tr>';
          el('ppInfo').textContent = '第 ' + result.page + ' / ' + result.pages + ' 页 · 共 ' + result.total + ' 条';
          el('ppPrev').disabled = result.page <= 1;
          el('ppNext').disabled = result.page >= result.pages;
        } catch (_) {
          previousTbody.innerHTML = '<tr><td colspan="5" class="empty-state">岗位加载失败，请刷新重试</td></tr>';
        }
      };
      previousSearch.addEventListener('input', () => {
        previousPage = 1; clearTimeout(previousTimer);
        previousTimer = setTimeout(renderPrevious, 180);
      });
      el('ppPrev').addEventListener('click', () => { if (previousPage > 1) { previousPage--; renderPrevious(); } });
      el('ppNext').addEventListener('click', () => { previousPage++; renderPrevious(); });
      renderPrevious();
    } else {
      previousSearch.addEventListener('input', () => {
        const query = previousSearch.value.trim().toLowerCase();
        previousTbody.querySelectorAll('tr[data-company]').forEach(row => {
          const haystack = (row.dataset.company + ' ' + row.dataset.title).toLowerCase();
          row.hidden = Boolean(query) && !haystack.includes(query);
        });
      });
    }
  }

  const unknownSearch = el('unknownSearch');
  const unknownTbody = el('unknownTbody');
  if (unknownSearch && unknownTbody) {
    if (SERVER_PAGINATION) {
      let unknownPage = 1;
      let unknownTimer;
      const renderUnknown = async () => {
        const params = new URLSearchParams({
          cohort: 'unknown', page: String(unknownPage), per_page: '50',
          q: unknownSearch.value || ''
        });
        unknownTbody.innerHTML = '<tr><td colspan="5" class="empty-state">正在加载岗位...</td></tr>';
        try {
          const response = await fetch('/api/jobs?' + params.toString());
          if (!response.ok) throw new Error('load failed');
          const result = await response.json();
          unknownTbody.innerHTML = result.jobs.length ? result.jobs.map(j =>
            '<tr><td class="cell-company">' + companyLink(j.c, 'unknown') + '</td>' +
            '<td>' + jobName(j) + '</td><td>' + esc(j.ev || '官网未明确届别') + '</td>' +
            '<td class="cell-city">' + esc(j.ct || '-') + '</td>' +
            '<td class="cell-op">' + opCell(j) + '</td></tr>'
          ).join('') : '<tr><td colspan="5" class="empty-state">无匹配岗位</td></tr>';
          el('upInfo').textContent = '第 ' + result.page + ' / ' + result.pages + ' 页 · 共 ' + result.total + ' 条';
          el('upPrev').disabled = result.page <= 1;
          el('upNext').disabled = result.page >= result.pages;
        } catch (_) {
          unknownTbody.innerHTML = '<tr><td colspan="5" class="empty-state">岗位加载失败，请刷新重试</td></tr>';
        }
      };
      unknownSearch.addEventListener('input', () => {
        unknownPage = 1; clearTimeout(unknownTimer);
        unknownTimer = setTimeout(renderUnknown, 180);
      });
      el('upPrev').addEventListener('click', () => { if (unknownPage > 1) { unknownPage--; renderUnknown(); } });
      el('upNext').addEventListener('click', () => { unknownPage++; renderUnknown(); });
      renderUnknown();
    } else {
      let unknownPage = 1;
      const UNKNOWN_PAGE_SIZE = 50;
      const renderUnknownLocal = () => {
        const query = unknownSearch.value.trim().toLowerCase();
        const filtered = (window.__UNKNOWN_JOBS || []).filter(j =>
          !query || (j.c + ' ' + j.t + ' ' + (j.ct || '')).toLowerCase().includes(query)
        );
        const pages = Math.max(1, Math.ceil(filtered.length / UNKNOWN_PAGE_SIZE));
        unknownPage = Math.min(unknownPage, pages);
        const start = (unknownPage - 1) * UNKNOWN_PAGE_SIZE;
        const rows = filtered.slice(start, start + UNKNOWN_PAGE_SIZE);
        unknownTbody.innerHTML = rows.length ? rows.map(j =>
          '<tr><td class="cell-company">' + companyLink(j.c, 'unknown') + '</td>' +
          '<td>' + jobName(j) + '</td><td>' + esc(j.ev || '官网未明确届别') + '</td>' +
          '<td class="cell-city">' + esc(j.ct || '-') + '</td>' +
          '<td class="cell-op">' + opCell(j) + '</td></tr>'
        ).join('') : '<tr><td colspan="5" class="empty-state">无匹配岗位</td></tr>';
        el('upInfo').textContent = '第 ' + unknownPage + ' / ' + pages + ' 页 · 共 ' + filtered.length + ' 条';
        el('upPrev').disabled = unknownPage <= 1;
        el('upNext').disabled = unknownPage >= pages;
      };
      unknownSearch.addEventListener('input', () => {
        unknownPage = 1;
        renderUnknownLocal();
      });
      el('upPrev').addEventListener('click', () => {
        if (unknownPage > 1) { unknownPage--; renderUnknownLocal(); }
      });
      el('upNext').addEventListener('click', () => {
        unknownPage++; renderUnknownLocal();
      });
      renderUnknownLocal();
    }
  }

  // ── URL Hash 状态恢复（支持页面路由、类别筛选、公司详情与具体岗位定位）──
  function restoreFromHash() {
    const rawHash = (window.location.hash || '').replace(/^#/, '');
    if (!rawHash) return;
    if (rawHash.indexOf('=') >= 0) {
      const params = new URLSearchParams(rawHash);
      if (params.has('company') && !params.has('job') && window.openCompany) {
        window.openCompany(params.get('company'), params.get('cohort') || 'current');
        return;
      }
      if ((params.has('cat') || params.has('job')) && window.applyCategoryFilter) {
        window.applyCategoryFilter(
          params.get('cat') || '',
          params.get('company') || '',
          params.get('title') || '',
          params.get('job') || null
        );
        return;
      }
    }
    activate(rawHash);
  }
  restoreFromHash();
  window.addEventListener('hashchange', restoreFromHash);
});
"""


# ──────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────

def render_html(date_str: str, report_data: dict, editable: bool = False,
                server_pagination: bool = False) -> str:
    """组装整页 HTML（不落盘）。

    report_data 可含 "items"（岗位+分析）与 "applications"（投递记录列表）。
    editable=True 时在岗位卡/看板注入编辑表单（供本地 webapp 复用）。
    """
    all_items = report_data.get("items", [])
    shown_items, previous_items, unknown_items, hidden_count = _filter_items(all_items)

    applications = report_data.get("applications", []) or []
    app_index = {a["job_id"]: a for a in applications if a.get("job_id") is not None}
    job_index = {}
    for it in all_items:
        job = it.get("job", {})
        jid = job.get("id")
        if jid is not None:
            job_index[jid] = {
                "jd_url": job.get("jd_url", ""),
                "link_kind": job.get("link_kind", "detail"),
                "match_score": (it.get("analysis") or {}).get("match_score"),
            }

    season_info = job_filters.determine_recruitment_season(all_items, reference_date=date_str)
    dir_records = _directory_payload(shown_items, previous_items, unknown_items)
    dir_company_count = len({r["c"] for r in dir_records if r.get("c")})

    page_rec_html, _stats = _page_recommended(
        shown_items, hidden_count, date_str, app_index, editable, server_pagination
    )
    page_today_html = _page_today(shown_items, date_str, app_index, editable)
    page_companies_html = _page_companies(shown_items)
    page_previous_html = _page_previous_cohort(
        previous_items, app_index, editable, server_pagination
    )
    page_unknown_html = _page_unknown_cohort(
        unknown_items, app_index, editable, server_pagination
    )
    page_learning_html = _page_learning_guide(all_items, date_str)
    page_advice_html = _page_comprehensive_advice(all_items, date_str)
    page_company_detail_html = _page_company_detail()
    page_apps_html = _page_applications(applications, job_index, editable)
    page_schedule_html = _page_schedule(applications, editable)

    sidebar = _render_sidebar(season_info, len(dir_records), dir_company_count)
    careers_urls_json = json.dumps(_company_careers_urls(), ensure_ascii=False).replace("</", "<\\/")
    company_summaries_json = json.dumps(
        {
            "current": _company_summaries(shown_items),
            "previous": _company_summaries(previous_items),
            "unknown": _company_summaries(unknown_items),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    dir_json = json.dumps(dir_records, ensure_ascii=False).replace("</", "<\\/")
    cat_labels_json = json.dumps(job_filters.JOB_CATEGORY_LABELS, ensure_ascii=False).replace("</", "<\\/")
    cat_order_json = json.dumps(list(job_filters.PRIMARY_CATEGORY_ORDER), ensure_ascii=False)
    season_json = json.dumps(season_info, ensure_ascii=False).replace("</", "<\\/")

    import hashlib
    from datetime import datetime, timedelta, timezone
    _cn_tz = timezone(timedelta(hours=8))
    prof_vid = job_filters.profile_version_id()
    raw_ver_seed = json.dumps({
        "date": str(date_str),
        "total": len(all_items),
        "current": _stats.get("active", 0),
        "high": _stats.get("high_match", 0),
        "prof": prof_vid,
    }, sort_keys=True)
    report_version_id = "rep-" + hashlib.sha256(raw_ver_seed.encode("utf-8")).hexdigest()[:8]
    report_meta = {
        "report_version_id": report_version_id,
        "scan_date": str(date_str),
        "generated_at": datetime.now(_cn_tz).strftime("%Y-%m-%d %H:%M:%S CST"),
        "profile_version_id": prof_vid,
        "total_items": len(all_items),
        "current_27_jobs": _stats.get("active", 0),
        "active_companies": _stats.get("companies", 0),
        "high_match_jobs": _stats.get("high_match", 0),
        "hidden_jobs": _stats.get("hidden", 0),
    }
    if isinstance(report_data, dict):
        report_data["report_meta"] = report_meta
    report_meta_json = json.dumps(report_meta, ensure_ascii=False).replace("</", "<\\/")
    old_pill = f'<span class="meta-pill">📅 扫描日期：{html.escape(date_str)}</span>'
    new_pill = (
        old_pill
        + f'\n      <span class="meta-pill" id="reportVersionBadge" title="报告版本标识（用于核对本地、GitHub Pages与飞书链接一致性）">🔖 版本：{html.escape(report_version_id)}</span>'
    )
    page_rec_html = page_rec_html.replace(old_pill, new_pill, 1)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="career-agent-report-version" content="{html.escape(report_version_id)}">
<title>{html.escape(job_filters.PRODUCT_BRAND_NAME)} · {html.escape(season_info['season_badge'])} · {html.escape(date_str)}</title>
<style>{_CSS}</style>
</head>
<body>
<script>
window.__REPORT_META = {report_meta_json};
window.__COMPANY_CAREERS = {careers_urls_json};
window.__COMPANY_SUMMARIES = {company_summaries_json};
window.__DIRECTORY_DATA = {dir_json};
window.__CATEGORY_LABELS = {cat_labels_json};
window.__PRIMARY_CATEGORY_ORDER = {cat_order_json};
window.__SEASON_INFO = {season_json};
</script>
<div class="layout">
  {sidebar}
  <main class="main">
    {page_rec_html}
    {page_today_html}
    {page_companies_html}
    {page_previous_html}
    {page_unknown_html}
    {page_learning_html}
    {page_advice_html}
    {page_company_detail_html}
    {page_apps_html}
    {page_schedule_html}
  </main>
</div>
<script>{_JS}</script>
</body>
</html>"""


def generate_report(date_str: str, report_data: dict, reports_dir: str = "reports",
                    out_name: str | None = None) -> str:
    """唯一权威静态 HTML 报告生成入口：生成报告并同步写入 report_meta.json 与 outputs/index.html。"""
    import shutil
    rdir = Path(reports_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    html_content = render_html(date_str, report_data, editable=False)

    output_path = rdir / f"{out_name or date_str}.html"
    output_path.write_text(html_content, encoding="utf-8")

    meta = (report_data.get("report_meta") if isinstance(report_data, dict) else None) or {}
    if meta:
        try:
            (rdir / "report_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # 当生成正式主页 index.html 到默认 reports 目录时，同步镜像至 outputs/index.html，彻底消除新旧报告分叉
    default_rdir = Path(__file__).resolve().parent / "reports"
    if (out_name == "index") and (rdir.resolve() == default_rdir.resolve()):
        try:
            out_dir = Path(__file__).resolve().parent / "outputs"
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output_path, out_dir / "index.html")
            if meta:
                (out_dir / "report_meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except OSError:
            pass

    return str(output_path.resolve())
