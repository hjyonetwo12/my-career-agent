from __future__ import annotations

import re
from pathlib import Path

import importlib.machinery as _im_mach
_orig_fill = _im_mach.FileFinder._fill_cache
def _patched_fill_cache(self):
    _orig_fill(self)
    for _extra in ('skill_projects.py', 'wlb_community.py'):
        if (Path(self.path) / _extra).is_file():
            self._path_cache.add(_extra)
_im_mach.FileFinder._fill_cache = _patched_fill_cache
import importlib as _il; _il.invalidate_caches()
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


def _as_clean_str_list(val) -> list[str]:
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if str(x).strip()]


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


def normalize_personal_profile(raw_pp: dict | None) -> dict:
    """规范化四-B个人画像 (personal_profile)，支持用户后续追加体验反馈。"""
    pp = dict(raw_pp) if isinstance(raw_pp, dict) else {}
    return {
        "thinking_and_work_style": _as_clean_str_list(pp.get("thinking_and_work_style")),
        "hobbies_and_general_interests": _as_clean_str_list(pp.get("hobbies_and_general_interests")),
        "possible_work_content_preferences": _as_clean_str_list(pp.get("possible_work_content_preferences")),
        "interest_caveats": _as_clean_str_list(pp.get("interest_caveats")),
        # 支持 profile_update_policy 中定义的个人真实体验记录（最高优先级覆盖推断）
        "liked_work_content": _as_clean_str_list(pp.get("liked_work_content")),
        "disliked_work_content": _as_clean_str_list(pp.get("disliked_work_content")),
        "unverified_work_content": _as_clean_str_list(pp.get("unverified_work_content")),
    }


def normalize_interest_filtering(raw_if: dict | None) -> dict:
    """规范化四-C岗位兴趣过滤策略 (interest_filtering)。"""
    cfg = dict(raw_if) if isinstance(raw_if, dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "evaluate_actual_work_content": bool(cfg.get("evaluate_actual_work_content", True)),
        "use_hobbies_as_soft_evidence_only": bool(cfg.get("use_hobbies_as_soft_evidence_only", True)),
        "separate_interest_fit_from_skill_fit": bool(cfg.get("separate_interest_fit_from_skill_fit", True)),
        "separate_interest_fit_from_learning_cost": bool(cfg.get("separate_interest_fit_from_learning_cost", True)),
        "screening_rules": _as_clean_str_list(cfg.get("screening_rules")),
        "result_policy": _as_clean_str_list(cfg.get("result_policy")),
        "profile_update_policy": _as_clean_str_list(cfg.get("profile_update_policy")),
        "exploration_sample_per_category": int(cfg.get("exploration_sample_per_category", 2)),
        "max_exploration_samples": int(cfg.get("max_exploration_samples", 12)),
    }


def normalize_skill_proficiency(raw_sp: dict | None) -> dict:
    """规范化五-skill_proficiency，保留每项技能的真实熟练度(level)、证据(evidence)与能力边界(limitation)。"""
    sp = dict(raw_sp) if isinstance(raw_sp, dict) else {}
    out: dict[str, list[dict]] = {
        "main_languages": [],
        "engineering_basics": [],
        "interest_explorations": [],
    }
    for group_key in ("main_languages", "engineering_basics", "interest_explorations"):
        items = sp.get(group_key)
        if not isinstance(items, list):
            continue
        cleaned_group = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            level = str(item.get("level") or "").strip()
            evidence = str(item.get("evidence") or "").strip()
            limitation = str(item.get("limitation") or "").strip()
            not_learned_yet = bool(
                re.search(r"还没开始学习|尚未开始学习|未开始学习|未学习|零基础", f"{level} {limitation}")
            )
            cleaned_group.append({
                "name": name,
                "level": level,
                "evidence": evidence,
                "limitation": limitation,
                "not_learned_yet": not_learned_yet,
            })
        out[group_key] = cleaned_group
    return out


def extract_skill_proficiency_map(profile: dict | None) -> dict[str, dict]:
    """按规范化技能名返回对应的熟练度与边界信息字典，供评分器与提示词精确查表。"""
    if not isinstance(profile, dict):
        return {}
    sp = normalize_skill_proficiency(profile.get("skill_proficiency"))
    lookup: dict[str, dict] = {}
    for group_key, items in sp.items():
        for it in items:
            entry = {**it, "group": group_key}
            raw_name = it["name"].lower()
            lookup[raw_name] = entry
            for token in re.split(r"[/\s、,，]+", raw_name):
                tok = token.strip()
                if tok:
                    lookup.setdefault(tok, entry)
    return lookup


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

    directions = _as_clean_str_list(profile.get("directions"))
    optional_branches = _as_clean_str_list(
        profile.get("optional_interest_branches") or profile.get("secondary_directions")
    )
    skills = _as_clean_str_list(profile.get("skills"))
    project_evidence = _as_clean_str_list(profile.get("project_evidence"))
    learning_targets = _as_clean_str_list(profile.get("learning_targets"))
    unverified_skills = _as_clean_str_list(profile.get("unverified_skills"))

    normalized = dict(profile)
    normalized["directions"] = directions
    normalized["optional_interest_branches"] = optional_branches
    normalized["skills"] = skills
    normalized["project_evidence"] = project_evidence
    normalized["learning_targets"] = learning_targets
    normalized["unverified_skills"] = unverified_skills
    normalized["direction"] = " / ".join(directions)

    # 推荐模式与主线锁定状态规范化（未确认主线时默认 exploration）
    locked_dirs = _as_clean_str_list(normalized.get("locked_target_directions"))
    locked_langs = _as_clean_str_list(normalized.get("locked_target_languages"))
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

    # 规范化新增的个人画像 (personal_profile)、岗位兴趣过滤策略 (interest_filtering) 与技能熟练度边界 (skill_proficiency)
    normalized["personal_profile"] = normalize_personal_profile(normalized.get("personal_profile"))
    normalized["interest_filtering"] = normalize_interest_filtering(normalized.get("interest_filtering"))
    normalized["skill_proficiency"] = normalize_skill_proficiency(normalized.get("skill_proficiency"))

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

    normalized["preferred_cities"] = _as_clean_str_list(normalized.get("preferred_cities"))
    normalized["excluded_title_keywords"] = _as_clean_str_list(normalized.get("excluded_title_keywords"))
    normalized.setdefault("long_term_goals", {})

    matching_base = {
        "recommendation_mode": normalized["recommendation_mode"],
        "target_direction_locked": normalized["target_direction_locked"],
        "primary_directions": locked_dirs if normalized["recommendation_mode"] == "targeted" and locked_dirs else directions,
        "secondary_directions": optional_branches,
        "project_evidence": project_evidence,
        "supporting_skills": skills,
        "skill_proficiency": normalized["skill_proficiency"],
        "learning_targets": learning_targets,
        "unverified_skills": unverified_skills,
        "personal_profile": normalized["personal_profile"],
        "interest_filtering": normalized["interest_filtering"],
        "preferences": normalized["preferences"],
        "constraints": normalized["constraints"],
        "preferred_cities": normalized["preferred_cities"],
    }
    if not normalized.get("matching"):
        normalized["matching"] = matching_base
    else:
        m = dict(normalized["matching"])
        for k, v in matching_base.items():
            m.setdefault(k, v)
        normalized["matching"] = m

    sp_mod = dict(normalized.get("skill_project_module") or {})
    sp_mod.setdefault("enabled", True)
    sp_mod.setdefault("min_category_samples", 15)
    sp_mod.setdefault("min_overall_samples", 30)
    sp_mod.setdefault("cache_enabled", True)
    sp_mod.setdefault("use_ai_for_project_insights", False)
    normalized["skill_project_module"] = sp_mod

    wlb_mod = dict(normalized.get("wlb_community_module") or {})
    wlb_mod.setdefault("enabled", True)
    wlb_mod.setdefault("cache_ttl_days", 14)
    wlb_mod.setdefault("max_companies_per_run", 20)
    wlb_mod.setdefault("allow_live_public_search", False)
    wlb_mod.setdefault("use_ai_for_wlb_synthesis", False)
    normalized["wlb_community_module"] = wlb_mod

    return normalized
