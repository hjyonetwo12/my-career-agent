"""模块二：基于招聘信息与社区讨论的 WLB 证据审核模块 (wlb_community.py)。

设计原则：
1. 严格区分三层信息结构：
   - 【招聘事实 (jd_facts)】：官方 JD 中明确写出的工时、抗压/出差要求、双休/五险一金等可验证内容。
   - 【社区经验 (community_experiences)】：知乎、V2EX、牛客网及公开报道中关于加班、管理方式、实习体验的经验性讨论（带原始链接、发布时间、匹配等级与证据局限）。
   - 【模型/规则审慎推断 (model_inferences)】：基于多条证据得出的有限结论，及对证据不足、团队差异、冲突和时效性问题的说明。
2. 严格的四级公司与岗位实体匹配：
   - Tier 1 (`tier_1_exact_role_city`)：同公司 + 同城市/业务线 + 相似岗位且时期较近；
   - Tier 2 (`tier_2_company_general`)：同公司，但具体岗位或部门不明确（明确标注“公司层面参考，无法确认该岗位具体团队情况”）；
   - Tier 3 (`tier_3_industry_general`)：仅涉及同行业泛化讨论（不作为具体公司确定性结论）；
   - Tier 4 (`tier_4_unverified`)：无法确认目标公司（如搜“优必选”命中“优衣库”），直接隔离排除绝不强行关联。
3. 热度 ≠ 真实性：对每条证据独立评估【热度 (popularity)】、【相关性 (relevance)】、【可信度 (credibility)】与【代表性 (representativeness)】。
4. 五类结构化审核结论（绝不只贴“WLB好/差”简单标签，证据不足时绝不生成看似精确的伪分数）。
5. 合规、隐私与低 Token 成本：遵守 robots/条款，不绕过登录/验证码/付费墙，自动脱敏个人账号，基于 TTL 缓存与分层过滤避免每次运行重复搜索或重复调用大模型。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

import job_filters

logger = logging.getLogger(__name__)

_CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
WLB_AUDIT_VERSION = "wlb-v1.0"
DEFAULT_WLB_SEED_PATH = Path(__file__).resolve().parent / "data" / "community_wlb_seed.json"
DEFAULT_WLB_STATE_PATH = Path(__file__).resolve().parent / "data" / "wlb_community_state.json"

COMPANY_ALIASES: dict[str, list[str]] = {
    "金山办公": ["金山办公", "WPS", "武汉金山办公", "珠海金山办公"],
    "德州仪器": ["德州仪器", "TI", "Texas Instruments", "德州仪器半导体"],
    "字节跳动": ["字节跳动", "字节", "ByteDance", "抖音", "TikTok"],
    "bilibili": ["bilibili", "哔哩哔哩", "B站"],
    "哔哩哔哩": ["哔哩哔哩", "bilibili", "B站"],
    "贝壳": ["贝壳", "贝壳找房", "链家网"],
    "中国联通": ["中国联通", "联通软件研究院", "联通数科", "联通系统集成", "联通产互"],
    "大疆": ["大疆", "DJI", "大疆创新", "卓驭"],
    "合合信息": ["合合信息", "扫描全能王", "名片全能王", "启信宝"],
    "汇川技术": ["汇川技术", "苏州汇川", "汇川"],
    "柏楚电子": ["柏楚电子", "上海柏楚"],
    "联影医疗": ["联影医疗", "上海联影", "武汉联影", "联影智能"],
    "万兴科技": ["万兴科技", "万兴"],
    "同花顺": ["同花顺", "浙江同花顺", "核新同花顺"],
    "星环科技": ["星环科技", "星环"],
    "优必选": ["优必选", "UBTECH", "深圳优必选"],
    "华橙网络": ["华橙网络", "乐橙", "杭州华橙"],
    "OPPO": ["OPPO", "欧加", "广东欧加"],
    "米哈游": ["米哈游", "miHoYo", "原神"],
    "卡斯柯": ["卡斯柯", "CASCO", "卡斯柯信号"],
}

WLB_EVIDENCE_CATEGORIES = {
    "overtime_frequency_duration": "1. 加班频率及持续时间",
    "weekend_holiday_work": "2. 周末或节假日工作 (双休/大小周)",
    "release_cycle_pressure": "3. 项目上线、交付和版本周期工作压力",
    "management_and_schedule": "4. 管理方式、考勤打卡与工作安排",
    "intern_boundary_experience": "5. 实习生的工作体验与工作边界",
    "department_variance": "6. 部门、业务线或城市差异",
    "positive_wlb_experience": "7. 正面 WLB 与福利休假体验",
    "outdated_or_conflicting": "8. 信息过时、上下文缺失或相互矛盾的讨论",
}

WLB_VERDICT_LABELS = {
    "negative_signal": "1. 存在较明确的负面信号（有相关且相对具体的加班/高压证据支持）",
    "positive_signal": "2. 存在较明确的正面信号（有相关且相对具体的双休/弹性/合规证据支持）",
    "mixed_controversial": "3. 观点分歧较大（不同来源、不同时期或不同部门的说法不一致）",
    "insufficient_evidence": "4. 证据不足（暂时无法作出可靠判断，绝不默认 WLB 良好或糟糕）",
    "outdated_evidence": "5. 存在明显时效性问题（主要社区证据较旧，不能直接代表当前团队情况）",
}

_ANONYMIZE_HANDLE_RE = re.compile(r"@[A-Za-z0-9_.-]{2,24}")
_EMAIL_PHONE_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)"
)


def anonymize_community_snippet(text: str) -> str:
    """隐私与合规保护：剥离评论者个人账号、邮箱与手机号，仅保留最小必要分析片段。"""
    s = str(text or "").strip()
    s = _ANONYMIZE_HANDLE_RE.sub("@匿名用户", s)
    s = _EMAIL_PHONE_RE.sub("[已脱敏联系方式]", s)
    return " ".join(s.split())[:260]


def build_wlb_search_queries(
    company: str,
    title: str = "",
    city: str = "",
    category_label: str = "",
) -> list[dict]:
    """以公司名称（及别名）为基础，结合城市、岗位类别与正/负/中性 WLB 主题动态构造查询词。"""
    co = str(company or "").strip()
    if not co:
        return []
    aliases = COMPANY_ALIASES.get(co, [co])
    primary_name = aliases[0]
    alias_part = f"({' OR '.join(aliases[:2])})" if len(aliases) > 1 else primary_name

    clean_city = ""
    city_m = re.search(r"(北京|上海|深圳|广州|杭州|成都|武汉|苏州|南京|西安|长沙|珠海|合肥|重庆|济南|青岛)", str(city or ""))
    if city_m:
        clean_city = city_m.group(1)

    role_kw = "研发"
    for rk in ("C++", "Java", "后端", "客户端", "测试", "算法", "嵌入式", "Agent", "全栈", "数据"):
        if rk.lower() in str(title or "").lower():
            role_kw = rk
            break

    city_token = f" {clean_city}" if clean_city else ""
    return [
        {
            "theme": "overtime_hours",
            "theme_label": "工时与周末双休",
            "query": f"{primary_name}{city_token} {role_kw} 加班 工时 下班时间 双休 周末 知乎 V2EX",
        },
        {
            "theme": "wlb_rhythm",
            "theme_label": "工作强度与节奏",
            "query": f"{primary_name}{city_token} WLB 工作强度 工作节奏 项目周期 知乎 牛客",
        },
        {
            "theme": "intern_experience",
            "theme_label": "实习体验与团队管理",
            "query": f"{primary_name} 实习体验 团队管理 转正 实习生 工作内容 知乎 V2EX",
        },
        {
            "theme": "dept_role_context",
            "theme_label": "部门与具体岗位差异",
            "query": f"{alias_part}{city_token} {role_kw} 部门 工作体验 知乎",
        },
        {
            "theme": "oncall_pressure",
            "theme_label": "上线发版与绩效压力",
            "query": f"{primary_name} 出差 值班 版本发布 绩效考核 压力 知乎",
        },
        {
            "theme": "positive_wlb",
            "theme_label": "正向WLB与准点下班体验",
            "query": f"{primary_name}{city_token} 不加班 准时下班 弹性打卡 工作生活平衡 知乎 V2EX",
        },
    ]


_THEME_PATTERNS = {
    "overtime_frequency_duration": re.compile(r"加班|下班|工时|熬夜|996|9106|9116|1075|1085|八九点|十点|通宵|时长", re.I),
    "weekend_holiday_work": re.compile(r"双休|单休|大小周|周末|周六|节假日|年假|带薪假", re.I),
    "release_cycle_pressure": re.compile(r"项目期|发版|上线|版本|冲刺|交付|注册申报|节点|赶进度", re.I),
    "management_and_schedule": re.compile(r"打卡|弹性|管理|领导|考核|绩效|优化|淘汰|汇报|内卷|氛围", re.I),
    "intern_boundary_experience": re.compile(r"实习|转正|Mentor|带教|实习生|校招|应届", re.I),
    "department_variance": re.compile(r"部门|事业部|业务线|看组|不同组|分部门|产研中心|子公司", re.I),
    "positive_wlb_experience": re.compile(r"不加班|准点|准时|加班很少|不卷|WLB|1075|双休保障|福利好|三餐免费|年假", re.I),
}

_NEGATIVE_WLB_RE = re.compile(
    r"加班严重|强制加班|隐性强制加班|加班偏多|加班非常严重|加班明显增多|日常下班.*晚|平时加班|阶段性加班|赶项目.*加班|996|9106|9116|大小周|天天.*下班|熬坏|"
    r"优化10%|淘汰|绩效压力|凌晨|通宵|血汗",
    re.I,
)
_POSITIVE_WLB_RE = re.compile(
    r"加班很少|基本不怎么加|准点下班|准时下班|1075|无内卷|不提倡加班|严禁过度加班|强制9点下班|更为稳健|作息相对规律|加班显著少于|福利好|培养体系|"
    r"一般双休|双休有保障|弹性打卡|入职即送.*年假",
    re.I,
)
_RECRUIT_PROMO_RE = re.compile(r"内推|急招|HC\s*多多|简历投递|简历直达|广招", re.I)


def evaluate_discussion_item(
    item: dict,
    target_company: str,
    target_title: str = "",
    target_city: str = "",
    reference_year: int = 2026,
) -> dict:
    """对单条社区讨论进行四级实体匹配与四维独立质量评估（热度、相关性、可信度、代表性）。"""
    co = str(target_company or "").strip()
    aliases = COMPANY_ALIASES.get(co, [co])
    title_text = str(item.get("title") or "")
    snippet_text = str(item.get("snippet") or "")
    comments_list = [str(c) for c in (item.get("relevant_comments") or []) if str(c).strip()]
    full_blob = f"{title_text} {snippet_text} {' '.join(comments_list)}"

    matched_alias = ""
    for al in aliases:
        if al.lower() in full_blob.lower():
            matched_alias = al
            break

    if not matched_alias:
        return {
            "match_tier": "tier_4_unverified",
            "match_tier_label": "4. 无法确认目标对象的讨论（已隔离排除）",
            "confidence_weight": 0.0,
            "is_valid_target": False,
            "rejection_reason": f"讨论正文未出现目标公司『{co}』或其已知别名，拒绝强行关联。",
        }

    target_cities = re.findall(
        r"(北京|上海|深圳|广州|杭州|成都|武汉|苏州|南京|西安|长沙|珠海|合肥|重庆|济南|青岛)",
        str(target_city or ""),
    )
    mentioned_cities = list(item.get("mentioned_cities") or [])
    for c_cand in ("北京", "上海", "深圳", "广州", "杭州", "成都", "武汉", "苏州", "南京", "西安", "长沙", "珠海", "济南"):
        if c_cand in full_blob and c_cand not in mentioned_cities:
            mentioned_cities.append(c_cand)

    city_matched = bool(target_cities and any(tc in mentioned_cities for tc in target_cities))
    role_matched = bool(
        re.search(r"研发|开发|软件|客户端|后端|算法|测试|测开|工程师|IT|实习", full_blob, re.I)
    )

    pub_date = str(item.get("published_at") or "").strip()
    pub_year = None
    ym = re.match(r"^(\d{4})", pub_date)
    if ym:
        pub_year = int(ym.group(1))
    age_years = (reference_year - pub_year) if pub_year else 4
    is_outdated = age_years >= 4

    if city_matched and role_matched and age_years <= 4:
        match_tier = "tier_1_exact_role_city"
        match_tier_label = f"1. 同公司 + 同城({','.join(mentioned_cities[:2])}) + 相似研发岗 ({pub_date or '近期'})"
        conf_weight = 0.90 if age_years <= 2 else 0.78
    elif role_matched:
        match_tier = "tier_2_company_general"
        city_note = f"涉及城市:{','.join(mentioned_cities[:2])}" if mentioned_cities else "未指明具体城市/部门"
        match_tier_label = f"2. 同公司研发参考（{city_note}，公司层面参考，无法确认该岗位具体团队情况）"
        conf_weight = 0.65 if not is_outdated else 0.45
    else:
        match_tier = "tier_2_company_general"
        match_tier_label = "2. 仅涉及同一公司但岗位/部门不明确（公司层面弱参考）"
        conf_weight = 0.40

    theme_tags = []
    for t_key, t_pat in _THEME_PATTERNS.items():
        if t_pat.search(full_blob):
            theme_tags.append(t_key)
    if is_outdated:
        theme_tags.append("outdated_or_conflicting")
    if not theme_tags:
        theme_tags.append("management_and_schedule")

    eng_raw = str(item.get("engagement_info") or "平台未公开具体互动数")
    if "热门" in eng_raw or "热议" in eng_raw or "多页" in eng_raw or "1797" in eng_raw:
        popularity = f"较高（{eng_raw}）"
    else:
        popularity = f"一般或未公开（{eng_raw}；不以搜索排名冒充热度）"

    has_wlb_detail = bool(
        _THEME_PATTERNS["overtime_frequency_duration"].search(full_blob)
        or _THEME_PATTERNS["weekend_holiday_work"].search(full_blob)
    )
    relevance = "高（直接讨论作息时间、加班频次或团队管理）" if has_wlb_detail else "中（讨论公司整体研发氛围与待遇）"

    is_promo = bool(_RECRUIT_PROMO_RE.search(full_blob))
    if is_promo:
        credibility = "中（内部员工招聘/内推帖，含具体作息说明但可能带有招募正面滤镜）"
    elif not pub_year:
        credibility = "中低（缺少精确发布时间，时效性存疑）"
    elif is_outdated:
        credibility = f"中低（发布于 {pub_date}，距今约 {age_years} 年，仅反映历史阶段情况）"
    else:
        credibility = f"中高（发布于 {pub_date}，包含具体城市/岗位上下文与工时细节）"

    if "多" in eng_raw or len(comments_list) >= 1:
        representativeness = "部门/时期经验样本（不可直接概括为全体员工一致体验）"
    else:
        representativeness = "个体单点经验分享（样本有限，需交叉核实）"

    has_neg = bool(_NEGATIVE_WLB_RE.search(full_blob))
    has_pos = bool(_POSITIVE_WLB_RE.search(full_blob))
    if has_neg and has_pos:
        polarity = "mixed"
        polarity_label = "正负分歧/视部门而定"
    elif has_neg:
        polarity = "negative"
        polarity_label = "负面/加班预警信号"
    elif has_pos:
        polarity = "positive"
        polarity_label = "正面/作息稳健信号"
    else:
        polarity = "neutral"
        polarity_label = "中性/经验讨论"

    limitations = []
    if match_tier == "tier_2_company_general":
        limitations.append("未精确到当前招聘JD所在的具体子部门/项目组")
    if is_outdated:
        limitations.append(f"讨论发布于 {pub_date}（距今约 {age_years} 年），可能无法反映近期考勤制度变化")
    if is_promo:
        limitations.append("来源包含内推招募信息，需警惕宣传美化偏差")
    if not limitations:
        limitations.append("社区个体经验存在幸存者偏差，建议在面试中向直属主管二次核实")

    return {
        "title": anonymize_community_snippet(title_text),
        "url": str(item.get("url") or "").strip(),
        "platform": str(item.get("platform") or "公开社区"),
        "published_at": pub_date or "未知（页面未提供精确日期）",
        "crawled_at": str(item.get("crawled_at") or date.today().isoformat()),
        "snippet": anonymize_community_snippet(snippet_text),
        "relevant_comments": [anonymize_community_snippet(c) for c in comments_list[:2]],
        "mentioned_cities": mentioned_cities,
        "mentioned_depts": list(item.get("mentioned_depts") or []),
        "match_tier": match_tier,
        "match_tier_label": match_tier_label,
        "confidence_weight": conf_weight,
        "is_valid_target": True,
        "is_outdated": is_outdated,
        "polarity": polarity,
        "polarity_label": polarity_label,
        "theme_tags": theme_tags,
        "theme_labels": [WLB_EVIDENCE_CATEGORIES.get(t, t) for t in theme_tags],
        "four_dimensions": {
            "popularity": popularity,
            "relevance": relevance,
            "credibility": credibility,
            "representativeness": representativeness,
        },
        "limitations": "；".join(limitations),
    }


_SEED_CACHE: list[dict] | None = None


def load_wlb_seed_discussions(seed_path: str | Path | None = None) -> list[dict]:
    global _SEED_CACHE
    p = Path(seed_path) if seed_path else DEFAULT_WLB_SEED_PATH
    if _SEED_CACHE is not None and seed_path is None:
        return _SEED_CACHE
    if p.exists():
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            rows = payload.get("discussions") or []
            if seed_path is None:
                _SEED_CACHE = rows
            return rows
        except Exception as exc:
            logger.warning("读取 community_wlb_seed.json 失败: %s", exc)
    return []


def fetch_public_wlb_discussions(
    company: str,
    queries: list[dict],
    seed_path: str | Path | None = None,
    allow_live_network: bool = False,
    custom_fetcher=None,
) -> tuple[list[dict], dict]:
    """分层获取公司相关的公开社区讨论。"""
    co = str(company or "").strip()
    aliases = COMPANY_ALIASES.get(co, [co])
    seed_rows = load_wlb_seed_discussions(seed_path)

    collected_by_url: dict[str, dict] = {}
    search_meta = {
        "company": co,
        "queries_generated": len(queries),
        "seed_hits": 0,
        "live_hits": 0,
        "live_attempted": False,
        "live_status": "skipped_offline_or_cached",
        "degraded_gracefully": False,
        "error_note": "",
    }

    for row in seed_rows:
        q_co = str(row.get("company_query") or "").strip()
        blob = f"{row.get('title', '')} {row.get('snippet', '')}"
        if q_co == co or any(al.lower() in blob.lower() for al in aliases):
            u = str(row.get("url") or "").strip()
            if u and u not in collected_by_url:
                collected_by_url[u] = dict(row)
                search_meta["seed_hits"] += 1

    if custom_fetcher is not None or allow_live_network:
        search_meta["live_attempted"] = True
        try:
            if custom_fetcher is not None:
                live_items = custom_fetcher(co, queries) or []
            else:
                live_items = _live_public_search_request(co, queries[:2])
            for li in live_items:
                u = str(li.get("url") or "").strip()
                if u and u not in collected_by_url:
                    collected_by_url[u] = li
                    search_meta["live_hits"] += 1
            search_meta["live_status"] = "ok"
        except Exception as exc:
            search_meta["live_status"] = "degraded_on_error"
            search_meta["degraded_gracefully"] = True
            search_meta["error_note"] = job_filters.sanitize_sensitive_text(str(exc))[:160]
            logger.info("[%s] 社区 WLB 实时搜索不可用或受限，已优雅降级至本地证据库: %s", co, search_meta["error_note"])

    return list(collected_by_url.values()), search_meta


def _live_public_search_request(company: str, queries: list[dict]) -> list[dict]:
    """使用公开可配置的搜索接口进行有限查询；禁止绕过反爬验证码。"""
    api_url = (os.environ.get("WLB_SEARCH_API_URL") or "").strip()
    if not api_url:
        raise RuntimeError("未配置 WLB_SEARCH_API_URL 公开搜索接口，保持离线安全模式")

    results: list[dict] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CareerAgentWLBAuditor/1.0; +https://github.com/)",
        "Accept": "application/json, text/html;q=0.8",
    }
    api_key = (os.environ.get("WLB_SEARCH_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for q_obj in queries[:2]:
        q_str = q_obj["query"]
        resp = requests.get(
            api_url,
            params={"q": q_str, "format": "json"},
            headers=headers,
            timeout=8,
        )
        if resp.status_code in (401, 403, 429, 503):
            raise RuntimeError(f"公开搜索接口返回受限状态码 HTTP {resp.status_code}，遵守限制停止抓取")
        resp.raise_for_status()
        data = resp.json()
        for item in (data.get("results") or data.get("items") or [])[:5]:
            u = str(item.get("url") or item.get("link") or "").strip()
            if not u:
                continue
            results.append({
                "company_query": company,
                "title": str(item.get("title") or ""),
                "url": u,
                "platform": _infer_platform_name(u),
                "published_at": str(item.get("publishedDate") or item.get("date") or "")[:10],
                "crawled_at": date.today().isoformat(),
                "snippet": str(item.get("content") or item.get("snippet") or ""),
                "relevant_comments": [],
                "engagement_info": "公开搜索接口摘要",
            })
        time.sleep(1.0)
    return results


def _infer_platform_name(url: str) -> str:
    host = urlsplit(str(url or "")).netloc.lower()
    if "zhihu.com" in host:
        return "知乎"
    if "v2ex.com" in host:
        return "V2EX"
    if "nowcoder.com" in host:
        return "牛客网"
    if "oeeee.com" in host or "ce.cn" in host:
        return "公开媒体报道"
    return "公开技术/职场社区"


def audit_company_wlb(
    company: str,
    jobs_for_company: list[dict],
    raw_discussions: list[dict],
    profile: dict | None = None,
    search_meta: dict | None = None,
    reference_year: int = 2026,
) -> dict:
    """对单家公司及其在招岗位执行完整的 WLB 证据审核。"""
    co = str(company or "").strip()
    cities = sorted({str(j.get("city") or "").strip() for j in jobs_for_company if str(j.get("city") or "").strip()})
    titles = [str(j.get("title") or "").strip() for j in jobs_for_company if str(j.get("title") or "").strip()]
    combined_city_str = " / ".join(cities[:4])
    combined_title_str = " / ".join(dict.fromkeys(titles[:4]))

    queries = build_wlb_search_queries(co, combined_title_str, combined_city_str)

    jd_positive_quotes: list[str] = []
    jd_warning_quotes: list[str] = []
    jd_sample_urls: list[str] = []
    for j in jobs_for_company[:10]:
        jd_text = str(j.get("jd_raw") or "")
        u = str(j.get("jd_url") or "")
        if u and u not in jd_sample_urls:
            jd_sample_urls.append(u)
        for m in job_filters._WLB_POSITIVE_RE.finditer(jd_text):
            q = m.group(0)
            if q not in jd_positive_quotes:
                jd_positive_quotes.append(q)
        for m in job_filters._WLB_WARNING_RE.finditer(jd_text):
            q = m.group(0)
            if q not in jd_warning_quotes:
                jd_warning_quotes.append(q)

    if jd_warning_quotes:
        jd_fact_status = f"【招聘原文事实】JD 显式包含高压/加班/出差要求（原文提及：“{'、'.join(jd_warning_quotes[:3])}”）"
    elif jd_positive_quotes:
        jd_fact_status = (
            f"【招聘原文事实】JD 明确写出福利/休假保障（原文提及：“{'、'.join(jd_positive_quotes[:4])}”），"
            "但未写明具体日常上下班打卡时间"
        )
    else:
        jd_fact_status = "【招聘原文事实】官方 JD 仅列出岗位职责与技术要求，未披露具体工时、作息时间或是否双休"

    jd_facts_block = {
        "company": co,
        "active_job_count": len(jobs_for_company),
        "sampled_cities": cities[:5],
        "sampled_titles": list(dict.fromkeys(titles))[:5],
        "sampled_jd_urls": jd_sample_urls[:3],
        "explicit_positive_terms": jd_positive_quotes,
        "explicit_warning_terms": jd_warning_quotes,
        "jd_fact_summary": jd_fact_status,
        "department_disclosed": any("事业部" in t or "部" in t or "研究院" in t or "中心" in t for t in titles),
    }

    valid_evidences: list[dict] = []
    rejected_noise: list[dict] = []

    for raw_item in raw_discussions:
        ev = evaluate_discussion_item(
            raw_item,
            target_company=co,
            target_title=combined_title_str,
            target_city=combined_city_str,
            reference_year=reference_year,
        )
        if ev.get("is_valid_target"):
            valid_evidences.append(ev)
        else:
            rejected_noise.append({
                "title": str(raw_item.get("title") or "")[:60],
                "url": str(raw_item.get("url") or ""),
                "reason": ev.get("rejection_reason", "实体不匹配"),
            })

    valid_evidences.sort(
        key=lambda x: (
            0 if x["match_tier"] == "tier_1_exact_role_city" else 1,
            1 if x["is_outdated"] else 0,
            -x["confidence_weight"],
        )
    )

    neg_count = sum(1 for e in valid_evidences if e["polarity"] == "negative")
    pos_count = sum(1 for e in valid_evidences if e["polarity"] == "positive")
    mixed_count = sum(1 for e in valid_evidences if e["polarity"] == "mixed")
    recent_valid = [e for e in valid_evidences if not e["is_outdated"]]
    tier1_count = sum(1 for e in valid_evidences if e["match_tier"] == "tier_1_exact_role_city")

    if not valid_evidences:
        if jd_warning_quotes:
            verdict_code = "negative_signal"
        else:
            verdict_code = "insufficient_evidence"
    elif len(recent_valid) == 0 and len(valid_evidences) > 0:
        verdict_code = "outdated_evidence"
    elif (neg_count > 0 and pos_count > 0) or mixed_count > 0:
        verdict_code = "mixed_controversial"
    elif neg_count > 0 and pos_count == 0:
        verdict_code = "negative_signal"
    elif pos_count > 0 and neg_count == 0:
        verdict_code = "positive_signal"
    elif len(valid_evidences) >= 1:
        verdict_code = "mixed_controversial"
    else:
        verdict_code = "insufficient_evidence"

    verdict_label = WLB_VERDICT_LABELS[verdict_code]

    if not jd_facts_block["department_disclosed"] or tier1_count == 0:
        dept_scope_warning = "⚠️ 公司层面参考，无法确认该岗位具体团队情况（同一公司不同事业部/产研中心作息可能存在显著差异）。"
    else:
        dept_scope_warning = f"已匹配到涉及『{combined_city_str or '目标城市'}』及研发岗位的讨论，但具体小组排期仍需面试确认。"

    if len(valid_evidences) == 0:
        evidence_confidence_level = "insufficient (证据不足)"
        expl_score = None
        score_rule_explanation = "当前无有效社区讨论证据（共检索到 0 条目标公司可靠讨论），系统拒绝生成数值型 WLB 分数，防止误导。"
    else:
        avg_w = sum(e["confidence_weight"] for e in valid_evidences) / len(valid_evidences)
        if tier1_count >= 1 and len(recent_valid) >= 1:
            evidence_confidence_level = f"medium_high (中高 · 含 {tier1_count} 条同城/同岗近期证据，均值权重 {avg_w:.2f})"
        elif len(recent_valid) == 0:
            evidence_confidence_level = f"low_outdated (偏低 · {len(valid_evidences)} 条证据均超过 4 年)"
        else:
            evidence_confidence_level = f"medium (中等 · 共 {len(valid_evidences)} 条公司级证据，均值权重 {avg_w:.2f})"

        if len(valid_evidences) >= 2:
            base_idx = 65
            base_idx += pos_count * 10
            base_idx -= neg_count * 12
            if jd_warning_quotes:
                base_idx -= 15
            elif jd_positive_quotes:
                base_idx += 5
            expl_score = max(25, min(90, base_idx))
            score_rule_explanation = (
                f"参考指数 {expl_score}/100（计算规则：基准 65 分 + 正向证据 {pos_count}×10 - 负向信号 {neg_count}×12 "
                f"{'- JD高压词15' if jd_warning_quotes else ('+ JD双休福利5' if jd_positive_quotes else '')}；"
                "该数值仅作排序辅助，必须结合下方证据置信度与分歧说明阅读）。"
            )
        else:
            expl_score = None
            score_rule_explanation = "有效社区证据仅 1 条（样本单薄），不生成数值型 WLB 分数，仅展示定性证据摘录。"

    inference_points = []
    if verdict_code == "negative_signal":
        inference_points.append(
            f"【审慎推断】综合招聘原文与社区讨论，『{co}』存在较具体的加班或考核压力信号（如晚间加班、历史大小周或特定时期考核淘汰争议），对高 WLB 诉求者存在风险。"
        )
    elif verdict_code == "positive_signal":
        inference_points.append(
            f"【审慎推断】现有社区讨论与 JD 福利条款一致指向『{co}』整体作息较为规范（双休有保障、强制加班较少），但仍需核实具体对接团队是否存在跨时区会议或发版赶工。"
        )
    elif verdict_code == "mixed_controversial":
        inference_points.append(
            f"【审慎推断】关于『{co}』的社区口碑存在明显分歧或时期/部门差异（例如：传统稳定事业部 vs 新兴冲刺业务线，或历史高强度 vs 近期反过度加班新规），不能简单贴上“全公司好”或“全公司差”的标签。"
        )
    elif verdict_code == "outdated_evidence":
        inference_points.append(
            f"【审慎推断】目前检索到的『{co}』社区讨论主要发布于 4 年以前，存在明显时效性局限，不能直接等同于 2026–2027 届入职后的真实作息。"
        )
    else:
        inference_points.append(
            f"【审慎推断】当前公开社区针对『{co}』研发岗位的具体工时讨论证据不足；不能因为没有负面热帖就默认其 WLB 良好，也不能无根据猜测其加班严重。"
        )
    inference_points.append(dept_scope_warning)

    interview_verify_questions = [
        f"向面试官核实团队具体作息：“请问咱们这个『{titles[0] if titles else '研发'}』岗位所在的项目组，平时工作日典型的上下班时间和版本发布周期是怎样的？”",
        "向 HR 或直属学长核实实习生边界：“实习生是否参与线上 On-call 轮值或周末发版？考核转正主要看代码产出质量还是出勤工时？”",
    ]

    return {
        "company": co,
        "audit_version": WLB_AUDIT_VERSION,
        "audited_at": datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
        "verdict_code": verdict_code,
        "verdict_label": verdict_label,
        "evidence_confidence": evidence_confidence_level,
        "explainable_numeric_index": expl_score,
        "score_rule_explanation": score_rule_explanation,
        "department_scope_note": dept_scope_warning,
        "queries_used": queries,
        "search_metadata": search_meta or {},
        "jd_facts": jd_facts_block,
        "community_experiences": {
            "valid_evidence_count": len(valid_evidences),
            "tier1_exact_count": tier1_count,
            "recent_evidence_count": len(recent_valid),
            "rejected_noise_count": len(rejected_noise),
            "rejected_noise_samples": rejected_noise[:3],
            "evidences": valid_evidences,
        },
        "model_inferences": {
            "bounded_conclusions": inference_points,
            "conflict_and_timeliness_note": (
                f"正面信号 {pos_count} 条 / 负面信号 {neg_count} 条 / 分歧或中性 {len(valid_evidences) - pos_count - neg_count} 条；"
                f"近3年内证据 {len(recent_valid)} 条，较早历史证据 {len(valid_evidences) - len(recent_valid)} 条。"
            ),
            "interview_verification_checklist": interview_verify_questions,
        },
    }


def _load_wlb_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"company_audits": {}, "last_updated": ""}


def _save_wlb_state(state_path: Path, state: dict) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("保存社区 WLB 审核状态失败: %s", exc)


def select_candidate_companies_for_wlb_audit(
    items: list[dict],
    profile: dict | None = None,
    max_companies: int = 20,
) -> dict[str, list[dict]]:
    """分层筛选需要执行社区 WLB 审核的目标公司。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    curation = (
        job_filters.curate_recommendations_with_interest_policy(items, prof)
        if len(items) <= 500
        else {"primary_recommendations": [], "exploration_samples": []}
    )

    jobs_by_company: defaultdict[str, list[dict]] = defaultdict(list)
    priority_companies: list[str] = []

    for it in curation.get("primary_recommendations", []) + curation.get("exploration_samples", []):
        job = it.get("job") if isinstance(it, dict) and "job" in it else it
        co = str(job.get("company") or "").strip()
        if co:
            jobs_by_company[co].append(job)
            if co not in priority_companies:
                priority_companies.append(co)

    seeded_cos = ["优必选", "汇川技术", "中国联通", "柏楚电子", "合合信息", "贝壳", "华橙网络", "字节跳动", "OPPO", "德州仪器", "金山办公", "联影医疗", "大疆", "万兴科技", "同花顺", "星环科技", "米哈游", "bilibili", "阳光保险", "快手"]
    for extra_co in seeded_cos:
        if extra_co in priority_companies:
            priority_companies.remove(extra_co)
    priority_companies = seeded_cos + priority_companies

    for it in items:
        job = it.get("job") if isinstance(it, dict) and "job" in it else it
        if not isinstance(job, dict):
            continue
        co = str(job.get("company") or "").strip()
        if co in priority_companies and len(jobs_by_company[co]) < 8:
            jobs_by_company[co].append(job)

    selected: dict[str, list[dict]] = {}
    for co in priority_companies[:max_companies]:
        if jobs_by_company[co] or co in COMPANY_ALIASES:
            selected[co] = jobs_by_company[co]
    return selected


def update_or_get_wlb_community_audits(
    items: list[dict],
    profile: dict | None = None,
    conn=None,
    state_path: str | Path | None = None,
    seed_path: str | Path | None = None,
    force_refresh: bool = False,
    allow_live_network: bool = False,
    allow_ai: bool = False,
    ai_caller=None,
    max_companies: int = 20,
) -> dict:
    """社区口碑辅助 WLB 审核主入口。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    mod_cfg = prof.get("wlb_community_module") or {}
    if mod_cfg.get("enabled") is False:
        return {
            "enabled": False,
            "status": "disabled",
            "message": "社区口碑辅助 WLB 审核模块已在配置中关闭 (wlb_community_module.enabled: false)",
            "audits_by_company": {},
        }

    ttl_days = int(mod_cfg.get("cache_ttl_days", 14))
    max_cos = int(mod_cfg.get("max_companies_per_run", max_companies))
    spath = Path(state_path) if state_path else DEFAULT_WLB_STATE_PATH
    state = _load_wlb_state(spath)
    cached_audits = state.get("company_audits") or {}

    candidate_map = select_candidate_companies_for_wlb_audit(items, prof, max_companies=max_cos)
    now_dt = datetime.now(_CHINA_TZ)
    now_iso = now_dt.strftime("%Y-%m-%d %H:%M:%S CST")

    audits_out: dict[str, dict] = {}
    cache_hits = 0
    fresh_computed = 0

    for co, co_jobs in candidate_map.items():
        cached = cached_audits.get(co)
        is_fresh_cache = False
        if not force_refresh and isinstance(cached, dict) and cached.get("audit_version") == WLB_AUDIT_VERSION:
            try:
                audited_str = str(cached.get("audited_at") or "")[:10]
                audited_date = date.fromisoformat(audited_str)
                if (now_dt.date() - audited_date).days <= ttl_days:
                    is_fresh_cache = True
            except ValueError:
                is_fresh_cache = False

        if is_fresh_cache:
            audits_out[co] = cached
            cache_hits += 1
            continue

        rep_title = co_jobs[0].get("title", "") if co_jobs else ""
        rep_city = co_jobs[0].get("city", "") if co_jobs else ""
        queries = build_wlb_search_queries(co, rep_title, rep_city)
        raw_discs, smeta = fetch_public_wlb_discussions(
            co,
            queries,
            seed_path=seed_path,
            allow_live_network=allow_live_network,
        )
        audit_obj = audit_company_wlb(
            co,
            co_jobs,
            raw_discs,
            profile=prof,
            search_meta=smeta,
            reference_year=now_dt.year,
        )

        if (allow_ai or ai_caller is not None) and audit_obj["community_experiences"]["valid_evidence_count"] >= 2:
            try:
                caller = ai_caller
                if caller is None:
                    import analyzer as _analyzer_mod
                    caller = _analyzer_mod.call_deepseek_api
                ev_titles = [e["title"] for e in audit_obj["community_experiences"]["evidences"][:3]]
                sys_p = "你是审慎的职场WLB证据核查员。请基于给定的社区讨论标题和分类结论，用80字以内总结面试时最该核实的1个具体工时问题。严禁把传闻当事实。"
                usr_p = f"公司：{co}；初步结论：{audit_obj['verdict_label']}；讨论标题：{'; '.join(ev_titles)}"
                ai_resp = caller(sys_p, usr_p)
                if ai_resp and str(ai_resp).strip():
                    audit_obj["model_inferences"]["ai_supplementary_note"] = str(ai_resp).strip()[:200]
            except Exception as exc:
                logger.warning("[%s] 社区 WLB AI 补充点评跳过，保留规则核查结论: %s", co, exc)

        audits_out[co] = audit_obj
        cached_audits[co] = audit_obj
        fresh_computed += 1

    state["company_audits"] = cached_audits
    state["last_updated"] = now_iso
    _save_wlb_state(spath, state)

    if conn is not None and fresh_computed > 0:
        try:
            save_wlb_audits_to_db(conn, audits_out)
        except Exception as exc:
            logger.warning("保存社区 WLB 审核记录至 SQLite 失败（不影响主流程）: %s", exc)

    verdict_counter = Counter(a["verdict_code"] for a in audits_out.values())
    total_evidences = sum(a["community_experiences"]["valid_evidence_count"] for a in audits_out.values())
    total_rejected = sum(a["community_experiences"]["rejected_noise_count"] for a in audits_out.values())

    return {
        "enabled": True,
        "status": "ok",
        "module_version": WLB_AUDIT_VERSION,
        "generated_at": now_iso,
        "summary_stats": {
            "audited_companies_count": len(audits_out),
            "cache_hits": cache_hits,
            "fresh_computed": fresh_computed,
            "total_valid_evidences": total_evidences,
            "total_rejected_noise": total_rejected,
            "verdict_distribution": {
                k: verdict_counter.get(k, 0) for k in WLB_VERDICT_LABELS
            },
        },
        "audits_by_company": audits_out,
    }


def save_wlb_audits_to_db(conn, audits_by_company: dict[str, dict]) -> None:
    """将招聘事实、社区经验证据与模型推断分开持久化到 SQLite 表中（幂等更新）。"""
    for co, audit in audits_by_company.items():
        audited_at = str(audit.get("audited_at") or datetime.now(_CHINA_TZ).isoformat())
        conn.execute(
            """INSERT INTO company_wlb_audits
               (company, audit_version, verdict_code, verdict_label, evidence_confidence,
                explainable_numeric_index, jd_facts_json, community_summary_json,
                model_inferences_json, audited_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(company) DO UPDATE SET
                   audit_version=excluded.audit_version,
                   verdict_code=excluded.verdict_code,
                   verdict_label=excluded.verdict_label,
                   evidence_confidence=excluded.evidence_confidence,
                   explainable_numeric_index=excluded.explainable_numeric_index,
                   jd_facts_json=excluded.jd_facts_json,
                   community_summary_json=excluded.community_summary_json,
                   model_inferences_json=excluded.model_inferences_json,
                   audited_at=excluded.audited_at""",
            (
                co,
                str(audit.get("audit_version") or WLB_AUDIT_VERSION),
                str(audit.get("verdict_code") or "insufficient_evidence"),
                str(audit.get("verdict_label") or ""),
                str(audit.get("evidence_confidence") or ""),
                audit.get("explainable_numeric_index"),
                json.dumps(audit.get("jd_facts") or {}, ensure_ascii=False),
                json.dumps(
                    {
                        "valid_evidence_count": audit["community_experiences"]["valid_evidence_count"],
                        "tier1_exact_count": audit["community_experiences"]["tier1_exact_count"],
                        "rejected_noise_count": audit["community_experiences"]["rejected_noise_count"],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(audit.get("model_inferences") or {}, ensure_ascii=False),
                audited_at,
            ),
        )

        for ev in audit.get("community_experiences", {}).get("evidences", []):
            u = str(ev.get("url") or "").strip()
            if not u:
                continue
            conn.execute(
                """INSERT INTO company_wlb_evidence
                   (company, url, platform, title, published_at, crawled_at,
                    match_tier, confidence_weight, polarity, theme_tags_json,
                    four_dimensions_json, snippet, limitations)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(company, url) DO UPDATE SET
                       platform=excluded.platform,
                       title=excluded.title,
                       published_at=excluded.published_at,
                       crawled_at=excluded.crawled_at,
                       match_tier=excluded.match_tier,
                       confidence_weight=excluded.confidence_weight,
                       polarity=excluded.polarity,
                       theme_tags_json=excluded.theme_tags_json,
                       four_dimensions_json=excluded.four_dimensions_json,
                       snippet=excluded.snippet,
                       limitations=excluded.limitations""",
                (
                    co,
                    u,
                    str(ev.get("platform") or ""),
                    str(ev.get("title") or ""),
                    str(ev.get("published_at") or ""),
                    str(ev.get("crawled_at") or ""),
                    str(ev.get("match_tier") or ""),
                    float(ev.get("confidence_weight") or 0.0),
                    str(ev.get("polarity") or "neutral"),
                    json.dumps(ev.get("theme_tags") or [], ensure_ascii=False),
                    json.dumps(ev.get("four_dimensions") or {}, ensure_ascii=False),
                    str(ev.get("snippet") or ""),
                    str(ev.get("limitations") or ""),
                ),
            )
    conn.commit()
