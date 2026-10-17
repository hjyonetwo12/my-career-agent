import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import job_filters
import notifier
import profile_config
import reporter


def test_notifier_run_stats_summary_and_anomaly_classification(tmp_path, monkeypatch):
    """验证飞书推送包含运行时间、抓取/新增/更新/去重/筛选/评分成功失败统计、分阶段异常提醒及同日防重复折叠。"""
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/dummy123456")

    run_stats = {
        "started_at": "2026-10-17 06:00:00 CST",
        "finished_at": "2026-10-17 06:18:22 CST",
        "crawled_total": 1200,
        "new_inserted_count": 0,
        "updated_existing_count": 1150,
        "deduped_count": 50,
        "passed_filter_count": 480,
        "scored_success_count": 12,
        "scored_failed_count": 2,
        "failed_companies_count": 3,
        "crawl_failed": False,
        "advice_version_id": "adv-12345678",
        "advice_strategy": "无需更新（复用缓存）",
        "advice_error": "API error with key sk-secretkey1234567890abcd",
    }
    all_jobs = [
        {
            "job": {
                "company": "金山办公",
                "title": "C++客户端开发工程师",
                "city": "珠海",
                "jd_url": "https://example.com/wps/1",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "last_seen_at": "2026-10-17",
            },
            "analysis": {
                "match_score": 88,
                "recommendation": "推荐",
                "advantages": ["C++17", "CMake"],
                "gaps": ["Qt6"],
            },
        }
    ]
    report_data = {"date": "2026-10-17", "run_stats": run_stats, "dedup_push": True}
    state_file = tmp_path / "notify_state.json"

    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 0}
        # 第一次推送：完整包含运行摘要与高分岗位
        ok1 = notifier.send([], all_jobs, report_data, state_path=state_file)
        assert ok1 is True
        flat1 = json.dumps(mock_post.call_args.kwargs["json"], ensure_ascii=False)
        assert "抓取 1200 条" in flat1
        assert "新增 0 条" in flat1
        assert "更新 1150 条" in flat1
        assert "去重合并 50 条" in flat1
        assert "通过筛选 480 条" in flat1
        assert "AI评分成功 12 条 | 评分失败 2 条" in flat1
        assert "AI评分异常: 2 个岗位评分未完成" in flat1
        assert "本轮无新增岗位（爬虫与筛选链路运行正常）" in flat1
        # 密钥必须被脱敏
        assert "sk-secretkey" not in flat1
        assert "[REDACTED_SECRET]" in flat1
        assert "C++客户端开发工程师" in flat1

        # 同日第二次相同摘要推送：触发防重复刷屏折叠
        ok2 = notifier.send([], all_jobs, report_data, state_path=state_file)
        assert ok2 is True
        flat2 = json.dumps(mock_post.call_args.kwargs["json"], ensure_ascii=False)
        assert "本次自动折叠防刷屏" in flat2


def test_profile_config_validation_and_secret_guard(tmp_path):
    """验证 profile_config.py 规范化新字段，并拦截误写入的密钥。"""
    valid_p = tmp_path / "profile.yaml"
    valid_p.write_text(
        """
degree: 本科
job_type: 实习
target_cohort: 2029
directions:
  - 后端开发与分布式系统
optional_interest_branches:
  - 图形学与实时渲染
skills:
  - C++
  - Python
target_roles:
  - name: 后端
    keywords: [后端, C++]
score_component_limits:
  core_direction: 20
  required_skills: 25
  project_evidence: 20
  engineering_stack: 15
  basic_criteria: 20
score_thresholds:
  recommend: 75
  consider: 60
""",
        encoding="utf-8",
    )
    prof = profile_config.load_profile(valid_p)
    assert prof["education"]["graduation_year"] == 2029
    assert prof["optional_interest_branches"] == ["图形学与实时渲染"]
    assert prof["matching"]["secondary_directions"] == ["图形学与实时渲染"]
    assert prof["preferences"]["wlb_priority"] == "high"

    # 测试密钥泄露拦截
    leaked_p = tmp_path / "leaked_profile.yaml"
    leaked_p.write_text(
        valid_p.read_text(encoding="utf-8") + "\napi_key: sk-1234567890abcdef12345678\n",
        encoding="utf-8",
    )
    try:
        profile_config.load_profile(leaked_p)
        raise AssertionError("Should have rejected secret in profile.yaml")
    except ValueError as exc:
        assert "安全拦截" in str(exc)


def test_profile_version_diff_and_impact_classification():
    """验证个人需求版本差异检测（含重点探索 -> 一般兴趣支线迁移、新增技能、纯UI变更）与影响范围判断。"""
    base_prof = {
        "degree": "本科",
        "job_type": "实习",
        "target_cohort": 2029,
        "directions": ["后端服务与分布式系统", "AI 应用工程与智能体落地"],
        "optional_interest_branches": ["图形学与实时渲染"],
        "skills": ["C++", "Python"],
        "project_evidence": ["完成小型 OpenGL 项目"],
        "preferences": {"wlb_priority": "high"},
        "score_component_limits": {"core_direction": 20, "required_skills": 25, "project_evidence": 20, "engineering_stack": 15, "basic_criteria": 20},
        "score_thresholds": {"recommend": 75, "consider": 60},
    }

    # 1. 完全相同配置 -> no_change
    diff_same = job_filters.diff_profile_versions(base_prof, base_prof)
    assert diff_same["has_changes"] is False
    impact_same = job_filters.classify_profile_change_impact(diff_same, market_changed=False)
    assert impact_same["update_strategy"] == "no_change"
    assert impact_same["needs_ai_regeneration"] is False

    # 2. 将 AI 应用工程从重点探索改为一般兴趣支线 -> recompute_ranking_and_advice
    interest_shifted_prof = {
        **base_prof,
        "directions": ["后端服务与分布式系统"],
        "optional_interest_branches": ["图形学与实时渲染", "AI 应用工程与智能体落地"],
    }
    diff_interest = job_filters.diff_profile_versions(base_prof, interest_shifted_prof)
    assert diff_interest["has_changes"] is True
    assert any("从【重点探索】调整为【一般兴趣支线】" in t for t in diff_interest["direction_transitions"])
    impact_interest = job_filters.classify_profile_change_impact(diff_interest)
    assert impact_interest["update_strategy"] == "recompute_ranking_and_advice"
    assert impact_interest["needs_crawler_rerun"] is False

    # 3. 新增 C++ 并发与 MySQL 技能及新项目 -> regenerate_and_reanalyze
    skill_added_prof = {
        **base_prof,
        "skills": ["C++", "Python", "C++多线程并发", "MySQL与Redis"],
        "project_evidence": ["完成小型 OpenGL 项目", "完成高并发日志与数据库检索工具"],
    }
    diff_skill = job_filters.diff_profile_versions(base_prof, skill_added_prof)
    impact_skill = job_filters.classify_profile_change_impact(diff_skill)
    assert impact_skill["update_strategy"] == "regenerate_and_reanalyze"
    assert impact_skill["needs_job_rescore"] is True

    # 4. 纯 UI/自定义分类标签变更 -> ui_only (不触发 AI 重生成)
    ui_prof = {
        **base_prof,
        "custom_categories": {"custom_iot": {"label": "物联网工程", "keywords": ["IoT"]}},
    }
    diff_ui = job_filters.diff_profile_versions(base_prof, ui_prof)
    impact_ui = job_filters.classify_profile_change_impact(diff_ui)
    assert impact_ui["update_strategy"] == "ui_only"
    assert impact_ui["needs_ai_regeneration"] is False


def test_advice_closed_loop_cache_reuse_version_diff_and_ai_fallback(tmp_path):
    """验证综合建议闭环：首次生成 -> 相同输入缓存复用 -> 配置修改触发新版本 -> AI失败安全回退与密钥脱敏。"""
    state_file = tmp_path / "advice_state.json"
    items = [
        {
            "job": {
                "id": 1,
                "company": "金山办公",
                "title": "C++跨平台客户端开发工程师（2027届）",
                "city": "珠海",
                "jd_url": "https://example.com/wps/1",
                "job_type": "校招",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "crawled_at": "2026-10-16",
                "jd_raw": "本科及以上学历，熟悉 C++17、Qt6、多线程、CMake 与 Linux/Windows 开发，周末双休。" * 2,
            },
            "analysis": {"match_score": 88, "recommendation": "推荐"},
        },
        {
            "job": {
                "id": 2,
                "company": "合合信息",
                "title": "AI应用开发工程师（Agent方向-2027届）",
                "city": "上海",
                "jd_url": "https://example.com/intsig/2",
                "job_type": "校招",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "crawled_at": "2026-10-16",
                "jd_raw": "本科及以上学历，熟悉 Python、FastAPI、MySQL、RAG 与 Agent 工具调用开发。" * 2,
            },
            "analysis": {"match_score": 82, "recommendation": "推荐"},
        },
    ]

    prof_v1 = {
        "degree": "本科",
        "job_type": "实习",
        "target_cohort": 2029,
        "directions": ["跨平台桌面客户端", "AI 应用工程与智能体落地"],
        "optional_interest_branches": ["图形学与实时渲染"],
        "skills": ["C++基础", "Python"],
        "project_evidence": ["完成小型 OpenGL 项目"],
        "preferences": {"wlb_priority": "high"},
    }

    # 1. 首次生成建议版本 v1
    adv1 = job_filters.update_or_get_comprehensive_advice(
        items, profile=prof_v1, state_path=state_file
    )
    v1_id = adv1["version_info"]["advice_version_id"]
    p1_id = adv1["version_info"]["profile_version_id"]
    assert adv1["version_info"]["cache_hit"] is False
    assert adv1["version_info"]["is_synced_with_profile"] is True
    assert len(adv1["suggested_directions"]) >= 6
    assert adv1["data_limitations"]
    assert adv1["next_watch_points"]

    # 2. 相同配置与数据再次调用 -> 直接命中缓存，不重复生成
    adv1_cached = job_filters.update_or_get_comprehensive_advice(
        items, profile=prof_v1, state_path=state_file
    )
    assert adv1_cached["version_info"]["cache_hit"] is True
    assert adv1_cached["version_info"]["advice_version_id"] == v1_id

    # 3. 用户更新个人配置：将 AI 应用工程移入一般兴趣支线，并新增 Qt6 与 MySQL 技能
    prof_v2 = {
        **prof_v1,
        "directions": ["跨平台桌面客户端"],
        "optional_interest_branches": ["图形学与实时渲染", "AI 应用工程与智能体落地"],
        "skills": ["C++基础", "Python", "Qt6跨平台GUI", "MySQL与Redis"],
    }
    adv2 = job_filters.update_or_get_comprehensive_advice(
        items, profile=prof_v2, state_path=state_file
    )
    v2_id = adv2["version_info"]["advice_version_id"]
    p2_id = adv2["version_info"]["profile_version_id"]
    assert v2_id != v1_id
    assert p2_id != p1_id
    assert adv2["version_info"]["cache_hit"] is False
    why_changed_str = " ".join(adv2["version_diff_summary"]["why_changed"])
    assert "从【重点探索】调整为【一般兴趣支线】" in why_changed_str
    # 验证客户端方向识别到了新增的 Qt 能力，且 AI 方向被标记为一般兴趣支线但依然保留在对比列表中（未丢失！）
    dir_by_id = {d["id"]: d for d in adv2["suggested_directions"]}
    assert "Qt跨平台GUI" in dir_by_id["software_cpp"]["skill_match"]
    assert "一般兴趣支线" in dir_by_id["ai_ml"]["priority_badge"]
    assert len(adv2["suggested_directions"]) >= 6

    # 4. 模拟配置再次变更但 AI 生成失败（且异常中包含 API Key）：必须保留上一份有效建议 adv2 且脱敏错误
    prof_v3 = {**prof_v2, "preferred_cities": ["成都", "苏州"]}
    def failing_ai_caller(_sys, _usr):
        raise RuntimeError("DeepSeek 503 failure for token sk-99887766554433221100aabbcc")

    adv_fallback = job_filters.update_or_get_comprehensive_advice(
        items, profile=prof_v3, state_path=state_file, allow_ai=True, ai_caller=failing_ai_caller
    )
    assert adv_fallback["version_info"]["advice_version_id"] == v2_id
    assert adv_fallback["version_info"]["sync_status"] == "fallback_previous_valid"
    assert "sk-99887766" not in adv_fallback["version_info"]["last_error"]
    assert "[REDACTED_SECRET]" in adv_fallback["version_info"]["last_error"]
    # 有效建议内容完好无损
    assert len(adv_fallback["suggested_directions"]) >= 6


def test_open_job_discovery_preserved_across_profile_interest_changes():
    """验证开放式岗位发现原则：个人兴趣与技能变化不会删除其他职业类别或相邻岗位。"""
    diverse_items = [
        {"job": {"id": 1, "company": "米哈游", "title": "UE5游戏客户端开发工程师（27届）", "city": "上海", "jd_url": "https://e.com/1", "cohort": 2027, "cohort_status": "confirmed", "jd_raw": "负责UE5游戏引擎与C++开发"}},
        {"job": {"id": 2, "company": "深信服", "title": "网络安全研发工程师（27届）", "city": "深圳", "jd_url": "https://e.com/2", "cohort": 2027, "cohort_status": "confirmed", "jd_raw": "负责Linux系统安全与漏洞攻防研发"}},
        {"job": {"id": 3, "company": "平Cap", "title": "分布式数据库内核研发工程师（27届）", "city": "北京", "jd_url": "https://e.com/3", "cohort": 2027, "cohort_status": "confirmed", "jd_raw": "负责分布式存储引擎与数据库内核开发"}},
        {"job": {"id": 4, "company": "某创新院", "title": "2027届特种技术星火储备生", "city": "杭州", "jd_url": "https://e.com/4", "cohort": 2027, "cohort_status": "confirmed", "jd_raw": "培养复合型技术人才"}},
    ]
    html_out = reporter.render_html("2026-10-17", {"items": diverse_items})
    # 所有类别（游戏开发、安全工程、数据库与存储、待分类）的真实岗位全部保留在报告与目录树中
    for title in ("UE5游戏客户端开发工程师（27届）", "网络安全研发工程师（27届）", "分布式数据库内核研发工程师（27届）", "2027届特种技术星火储备生"):
        assert title in html_out
    assert 'id="adviceVersionPanel"' in html_out
    assert 'id="btnRegenerateAdvice"' in html_out
