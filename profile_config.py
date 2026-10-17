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

_SECRET_LEAK_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+",
    re.I,
)


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
            "primary_directions": directions,
            "secondary_directions": optional_branches,
            "project_evidence": project_evidence,
            "supporting_skills": skills,
            "learning_targets": learning_targets,
            "unverified_skills": unverified_skills,
        }
    else:
        m = dict(normalized["matching"])
        m.setdefault("primary_directions", directions)
        m.setdefault("secondary_directions", optional_branches)
        m.setdefault("project_evidence", project_evidence)
        m.setdefault("supporting_skills", skills)
        m.setdefault("learning_targets", learning_targets)
        m.setdefault("unverified_skills", unverified_skills)
        normalized["matching"] = m

    return normalized
