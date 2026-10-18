from __future__ import annotations

import re
from pathlib import Path
import yaml


REQUIRED_FIELDS = (
    "degree",
    "job_type",
    "directions",
    "skills",
    "target_roles",
    "score_component_limits",
    "score_thresholds",
)

_EXPECTED_SCORE_COMPONENTS = {
    "core_direction",
    "required_skills",
    "project_evidence",
    "engineering_stack",
    "basic_criteria",
}

_VALID_RECOMMENDATION_MODES = {"exploration", "targeted"}

_SECRET_LEAK_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+",
    re.I,
)


def resolve_recommendation_mode(profile: dict | None) -> str:
    """解析当前推荐模式：'exploration'（探索模式，默认）或 'targeted'（定向求职模式）。

    规则：
    1. 若显式配置了 recommendation_mode ('exploration' / 'targeted')，优先采用；
    2. 若 target_direction_locked 为 True 或配置了非空 locked_target_directions，则为 'targeted'；
    3. 兼容旧版单元测试夹具：若未配置 directions 列表且未配置 recommendation_mode，但配置了单一
       direction 字符串（如旧测试中的 '机器人视觉 / 机械臂控制'），视为 'targeted'；
    4. 其余情况（用户尚未确认主线）一律默认使用 'exploration' 探索模式。
    """
    if not isinstance(profile, dict) or not profile:
        return "exploration"
    explicit = str(profile.get("recommendation_mode") or "").strip().lower()
    if explicit in _VALID_RECOMMENDATION_MODES:
        if bool(profile.get("target_direction_locked")) or bool(profile.get("locked_target_directions")):
            return "targeted"
        return explicit
    if bool(profile.get("target_direction_locked")) or bool(profile.get("locked_target_directions")):
        return "targeted"
    if not profile.get("directions") and profile.get("direction"):
        return "targeted"
    return "exploration"


def load_profile(path: str | Path = "profile.yaml") -> dict:
    """读取、校验并规范化个人画像配置 (profile.yaml)。

    提供向后兼容的默认值补全与清晰的中文错误提示。
    """
    profile_path = Path(path)
    if not profile_path.exists():
        raise ValueError(
            f"找不到用户画像配置: {profile_path}（请确认项目根目录下存在 profile.yaml）"
        )

    raw_text = profile_path.read_text(encoding="utf-8")
    if _SECRET_LEAK_RE.search(raw_text):
        raise ValueError(
            "安全拦截：检测到 profile.yaml 中疑似包含 API Key 或飞书 Webhook 密钥！"
            "请将密钥移至 .env 或 GitHub Secrets，切勿写入 profile.yaml。"
        )

    try:
        profile = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(
            f"profile.yaml 格式解析失败（请检查冒号后是否有空格、缩进是否使用了空格而非 Tab）: {exc}"
        ) from exc

    if not isinstance(profile, dict):
        raise ValueError("profile.yaml 顶层结构必须是键值对字典 (YAML Mapping)")

    return validate_and_normalize_profile(profile)


def validate_and_normalize_profile(profile: dict) -> dict:
    """验证并补全个人配置字典，确保旧版与新版字段结构均可安全使用。"""
    missing = [field for field in REQUIRED_FIELDS if field not in profile]
    if missing:
        raise ValueError(f"profile.yaml 缺少必要字段: {', '.join(missing)}")

    limits = profile["score_component_limits"]
    if (
        not isinstance(limits, dict)
        or set(limits) != _EXPECTED_SCORE_COMPONENTS
        or sum(int(v) for v in limits.values()) != 100
        or any(int(v) < 0 for v in limits.values())
    ):
        raise ValueError(
            "profile.yaml 的 score_component_limits 必须包含五个评分项且合计 100"
        )

    thresholds = profile["score_thresholds"]
    if not isinstance(thresholds, dict):
        raise ValueError("profile.yaml 的 score_thresholds 必须是包含 recommend 和 consider 的字典")
    recommend = int(thresholds.get("recommend", 80))
    consider = int(thresholds.get("consider", 60))
    if not 0 <= consider <= recommend <= 100:
        raise ValueError("评分阈值必须满足 0 <= consider <= recommend <= 100")

    raw_mode = str(profile.get("recommendation_mode") or "").strip().lower()
    if raw_mode and raw_mode not in _VALID_RECOMMENDATION_MODES:
        raise ValueError(
            f"profile.yaml 的 recommendation_mode 必须是 'exploration' 或 'targeted'，当前为: {raw_mode}"
        )

    directions = [
        str(item).strip() for item in (profile.get("directions") or [])
        if str(item).strip()
    ]
    optional_branches = [
        str(item).strip() for item in (
            profile.get("optional_interest_branches")
            or profile.get("secondary_directions")
            or []
        )
        if str(item).strip()
    ]
    skills = [
        str(item).strip() for item in (profile.get("skills") or [])
        if str(item).strip()
    ]
    project_evidence = [
        str(item).strip() for item in (profile.get("project_evidence") or [])
        if str(item).strip()
    ]
    learning_targets = [
        str(item).strip() for item in (profile.get("learning_targets") or [])
        if str(item).strip()
    ]
    unverified_skills = [
        str(item).strip() for item in (profile.get("unverified_skills") or [])
        if str(item).strip()
    ]

    normalized = dict(profile)
    normalized["directions"] = directions
    normalized["optional_interest_branches"] = optional_branches
    normalized["skills"] = skills
    normalized["project_evidence"] = project_evidence
    normalized["learning_targets"] = learning_targets
    normalized["unverified_skills"] = unverified_skills
    normalized["direction"] = " / ".join(directions)

    # 推荐模式与主线锁定状态规范化（未确认主线时默认 exploration）
    locked_dirs = [
        str(x).strip() for x in (normalized.get("locked_target_directions") or [])
        if str(x).strip()
    ]
    locked_langs = [
        str(x).strip() for x in (normalized.get("locked_target_languages") or [])
        if str(x).strip()
    ]
    normalized["locked_target_directions"] = locked_dirs
    normalized["locked_target_languages"] = locked_langs
    normalized["target_direction_locked"] = bool(
        normalized.get("target_direction_locked", False) or locked_dirs
    )
    normalized["recommendation_mode"] = resolve_recommendation_mode(normalized)

    # 用户状态与反偏置守卫默认值
    user_state = dict(normalized.get("user_state") or {})
    user_state.setdefault("current_learning_languages", ["C++", "Python"])
    user_state.setdefault("career_direction_determined", normalized["target_direction_locked"])
    user_state.setdefault(
        "exploration_goal",
        "先了解中国大陆不同计算机岗位的真实招聘要求，再决定重点投入的方向",
    )
    user_state.setdefault(
        "core_priorities",
        ["工作与生活平衡 (WLB)", "稳定成长", "长期技术积累"],
    )
    guardrails = dict(user_state.get("bias_guardrails") or {})
    guardrails.setdefault("infer_cpp_direction_interest", False)
    guardrails.setdefault("equate_low_learning_cost_with_suitability", False)
    guardrails.setdefault("use_cpp_as_default_direction_filter", False)
    user_state["bias_guardrails"] = guardrails
    normalized["user_state"] = user_state

    # 补全教育与求职阶段默认值
    edu = dict(normalized.get("education") or {})
    edu.setdefault("degree", str(normalized.get("degree") or "本科"))
    edu.setdefault("graduation_year", int(normalized.get("target_cohort") or 2029))
    edu.setdefault("current_stage", "本科在读")
    normalized["education"] = edu
    normalized.setdefault("target_cohort", edu["graduation_year"])

    # 补全偏好与约束默认值
    prefs = dict(normalized.get("preferences") or {})
    prefs.setdefault("wlb_priority", "high")
    prefs.setdefault("tech_growth_priority", "high")
    prefs.setdefault("stability_priority", "high")
    prefs.setdefault("overtime_tolerance", "low")
    prefs.setdefault("prefer_bachelor_friendly", "本科" in str(normalized.get("degree") or ""))
    normalized["preferences"] = prefs

    constraints = dict(normalized.get("constraints") or {})
    constraints.setdefault("reject_master_phd_only", "本科" in str(normalized.get("degree") or ""))
    constraints.setdefault("reject_high_overtime_signals", prefs.get("overtime_tolerance") == "low")
    normalized["constraints"] = constraints

    normalized.setdefault("preferred_cities", [])
    normalized.setdefault("excluded_title_keywords", [])
    normalized.setdefault("long_term_goals", {})

    if not normalized.get("matching"):
        normalized["matching"] = {
            "recommendation_mode": normalized["recommendation_mode"],
            "target_direction_locked": normalized["target_direction_locked"],
            "primary_directions": locked_dirs if normalized["recommendation_mode"] == "targeted" and locked_dirs else directions,
            "secondary_directions": optional_branches,
            "project_evidence": project_evidence,
            "supporting_skills": skills,
            "learning_targets": learning_targets,
            "unverified_skills": unverified_skills,
        }
    else:
        m = dict(normalized["matching"])
        m.setdefault("recommendation_mode", normalized["recommendation_mode"])
        m.setdefault("target_direction_locked", normalized["target_direction_locked"])
        m.setdefault("primary_directions", directions)
        m.setdefault("secondary_directions", optional_branches)
        m.setdefault("project_evidence", project_evidence)
        m.setdefault("supporting_skills", skills)
        m.setdefault("learning_targets", learning_targets)
        m.setdefault("unverified_skills", unverified_skills)
        normalized["matching"] = m

    return normalized
