import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import job_filters
import notifier
import reporter


def test_stage_b_multi_category_classification_from_title_and_jd():
    job = {
        "id": 101,
        "company": "米哈游",
        "title": "客户端引擎开发工程师",
        "jd_url": "https://jobs.mihoyo.com/position/101",
        "jd_raw": (
            "岗位职责：负责 Unity / Unreal 游戏引擎渲染管线研发与 Shader 性能优化，"
            "使用 C++ 与 OpenGL / Vulkan 开发三维图形渲染模块。"
        ),
    }
    res = job_filters.classify_job_categories(job)
    assert res["primary_category"] in ("game_engine", "graphics_3d")
    assert "game_engine" in res["categories"]
    assert "graphics_3d" in res["categories"]
    assert len(res["secondary_categories"]) >= 1
    assert "游戏开发与游戏引擎" in res["category_labels"]
    assert "图形学、渲染与三维技术" in res["category_labels"]
    assert res["category_reason"]
    assert job["title"] == "客户端引擎开发工程师"
    assert job["jd_url"] == "https://jobs.mihoyo.com/position/101"


def test_stage_b_unclassified_fallback_preserves_ambiguous_jobs():
    ambiguous_job = {
        "id": 102,
        "company": "某科技公司",
        "title": "2027届特种技术星火储备生",
        "jd_url": "https://example.com/job/102",
        "jd_raw": "面向2027届毕业生，培养复合型创新技术人才，提供轮岗机会。",
    }
    res = job_filters.classify_job_categories(ambiguous_job)
    assert res["primary_category"] == "unclassified"
    assert res["primary_label"] == "待分类"
    assert "unclassified" in res["categories"]


def test_stage_b_custom_category_extension_via_profile():
    custom_profile = {
        "custom_categories": [
            {
                "key": "custom_quantum",
                "label": "量子计算与新型计算",
                "keywords": ["量子计算", "量子比特", "Qubit"],
            }
        ]
    }
    job = {
        "company": "本源量子",
        "title": "量子软件研发工程师",
        "jd_raw": "负责量子计算测控系统与 Qubit 编译器开发。",
    }
    res = job_filters.classify_job_categories(job, profile=custom_profile)
    assert res["primary_category"] == "custom_quantum"
    assert res["primary_label"] == "量子计算与新型计算"


def test_stage_b_report_category_filter_and_non_duplicated_stats():
    data = {
        "date": "2026-10-17",
        "items": [
            {
                "job": {
                    "id": 1,
                    "company": "库洛游戏",
                    "title": "UE5引擎渲染工程师（2027届）",
                    "city": "广州",
                    "jd_url": "https://example.com/kuro/1",
                    "job_type": "校招",
                    "cohort": 2027,
                    "cohort_status": "confirmed",
                    "jd_raw": "负责UE5游戏引擎与OpenGL/Vulkan图形渲染管线开发。",
                },
                "analysis": {
                    "match_score": 82,
                    "advantages": ["C++", "OpenGL"],
                    "gaps": ["UE5"],
                    "summary": "引擎渲染岗",
                    "recommendation": "推荐",
                },
            },
            {
                "job": {
                    "id": 2,
                    "company": "金山办公",
                    "title": "C++跨平台客户端开发工程师（2027届）",
                    "city": "珠海",
                    "jd_url": "https://example.com/wps/2",
                    "job_type": "校招",
                    "cohort": 2027,
                    "cohort_status": "confirmed",
                    "jd_raw": "负责WPS跨平台桌面客户端C++/Qt研发。",
                },
                "analysis": {
                    "match_score": 88,
                    "advantages": ["C++17", "CMake"],
                    "gaps": ["Qt6"],
                    "summary": "桌面客户端岗",
                    "recommendation": "推荐",
                },
            },
        ],
    }
    html_out = reporter.render_html("2026-10-17", data)
    assert "id=\"statActiveJobs\">2<" in html_out
    assert "id=\"statActiveCompanies\">2<" in html_out
    assert "id=\"categoryFilterBar\"" in html_out
    assert "id=\"btnClearFilters\"" in html_out
    assert "id=\"filteredCompanyBar\"" in html_out
    assert "data-cat=\"game_engine\"" in html_out
    assert "data-cat=\"graphics_3d\"" in html_out
    assert "data-cat=\"software_cpp\"" in html_out


def test_stage_c_brand_name_and_dynamic_recruitment_season():
    assert job_filters.PRODUCT_BRAND_NAME == "AI校招情报"

    empty_season = job_filters.determine_recruitment_season([], reference_date="2026-10-17")
    assert empty_season["brand_name"] == "AI校招情报"
    assert empty_season["season_label"] == "招聘周期进行中"
    assert "Asia/Shanghai" in empty_season["timezone"]

    mixed_items = [
        {"job": {"title": f"2027届秋招研发工程师{i}", "job_type": "秋招", "cohort": 2027, "cohort_status": "confirmed", "last_seen_at": "2026-10-15"}}
        for i in range(6)
    ] + [
        {"job": {"title": f"C++日常实习生{i}", "job_type": "实习", "jd_raw": "面向在读学生日常实习", "last_seen_at": "2026-10-15"}}
        for i in range(5)
    ]
    mixed_season = job_filters.determine_recruitment_season(mixed_items, reference_date="2026-10-17")
    assert mixed_season["brand_name"] == "AI校招情报"
    assert mixed_season["season_label"] == "多招聘周期并行"
    assert "秋招季" in mixed_season["active_cycles"]
    assert "实习招聘" in mixed_season["active_cycles"]

    feishu_payload = notifier._build_payload([], [], {"date": "2026-10-17"})
    assert "AI校招情报" in feishu_payload["content"]["post"]["zh_cn"]["title"]


def test_stage_d_top_left_global_directory_and_layout():
    items = [
        {
            "job": {
                "id": 10,
                "company": "中望软件",
                "title": "三维几何算法工程师（27届）",
                "city": "广州",
                "jd_url": "https://example.com/zw/10",
                "job_type": "校招",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "jd_raw": "负责工业CAD三维几何内核与OpenGL渲染开发。",
            },
            "analysis": {"match_score": 84, "recommendation": "推荐"},
        },
        {
            "job": {
                "id": 11,
                "company": "中望软件",
                "title": "三维几何算法工程师（27届）",
                "city": "广州",
                "jd_url": "https://example.com/zw/10-dup",
                "job_type": "校招",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "jd_raw": "负责工业CAD三维几何内核与OpenGL渲染开发。",
            },
            "analysis": {"match_score": 84, "recommendation": "推荐"},
        },
    ]
    dir_records = reporter._directory_payload(items, [], [])
    assert len(dir_records) == 1
    assert dir_records[0]["c"] == "中望软件"
    assert dir_records[0]["t"] == "三维几何算法工程师（27届）"

    html_out = reporter.render_html("2026-10-17", {"items": items})
    idx_brand = html_out.index("class=\"brand-title\">AI校招情报</div>")
    idx_dir = html_out.index("id=\"globalDirectory\"")
    idx_nav = html_out.index("data-target=\"recommended\"")
    idx_advice_nav = html_out.index("id=\"navComprehensiveAdvice\"")
    assert idx_brand < idx_dir < idx_nav < idx_advice_nav


def test_stage_e_comprehensive_advice_multi_direction_and_epistemic_separation():
    items = [
        {
            "job": {
                "id": 1,
                "company": "金山办公",
                "title": "C++客户端开发工程师（2027届）",
                "city": "珠海",
                "jd_url": "https://example.com/wps/1",
                "job_type": "校招",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "crawled_at": "2026-10-15",
                "jd_raw": "本科及以上学历，熟悉 C++17、STL、多线程、CMake 与 Linux/Windows 桌面开发，周末双休。" * 2,
            },
            "analysis": {"match_score": 86, "recommendation": "推荐"},
        }
    ]
    advice = job_filters.generate_comprehensive_advice(items)
    assert advice["data_time_range"]["earliest_date"] == "2026-10-15"
    assert advice["sample_summary"]["total_jobs_in_db"] == 1
    directions = advice["suggested_directions"]
    assert len(directions) >= 4
    required_keys = {
        "direction_name",
        "recommendation_reason",
        "skill_match",
        "gaps_to_fill",
        "risks_and_uncertainties",
        "preference_alignment",
        "validation_actions",
    }
    for d in directions:
        assert required_keys.issubset(set(d.keys()))
    assert len(advice["skill_market_questions"]) == 7
    assert len(advice["learning_content"]) >= 3
    assert "tutorial_vs_mastery" in advice["learning_methods"]
    assert len(advice["career_planning"]) >= 4

    html_out = reporter.render_html("2026-10-17", {"items": items})
    assert "data-page=\"comprehensive-advice\"" in html_out
    assert "1. 建议探索的方向：" in html_out
    assert "2. 推荐理由" in html_out
    assert "3. 与当前技能的匹配情况" in html_out
    assert "4. 需要补足的能力" in html_out
    assert "5. 主要风险和不确定性" in html_out
    assert "6. 与用户职业偏好的符合程度" in html_out
    assert "7. 可以验证这一方向是否适合自己的实际行动" in html_out
    assert "ep-fact" in html_out
    assert "ep-infer" in html_out
    assert "ep-advice" in html_out
    assert "ep-unknown" in html_out
