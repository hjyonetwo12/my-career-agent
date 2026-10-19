"""第六阶段：全面审计修复后的自动化验收测试套件（覆盖静态检查、11项数据流集成测试、版本一致性验收与评分系统6状态验收）。"""

import json
import os
import py_compile
import sys
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

import analyzer
import db
import job_cohorts
import job_details
import job_filters
import notifier
import profile_config
import reporter

ROOT = Path(__file__).resolve().parent.parent

UNDERGRAD_PROFILE = {
    "degree": "本科",
    "job_type": "实习",
    "target_cohort": 2029,
    "directions": [
        "后端服务、企业级平台与分布式系统工程 (C++/Go/Python/Java)",
        "跨平台桌面客户端、办公软件与工业软件研发 (C++/Qt/SDK/Windows&Mac)",
        "测试开发 (SDET)、质量工程与研发效能工具链 (Python/C++/自动化测试/CI)",
        "AI 应用工程与企业级智能体落地 (Agent/RAG/AI全栈工程，非算法科研岗)",
    ],
    "optional_interest_branches": [
        "图形学与实时渲染 (OpenGL/GLSL/3D视景/工业CAD几何)",
    ],
    "skills": [
        "C++ (C++17，熟悉 STL 容器、RAII、智能指针与模块化设计)",
        "Python (掌握数据处理、网络爬虫、自动化脚本与 pytest 测试基础)",
        "数据结构与算法 (掌握线性表、栈队列、树、图、哈希、排序搜索)",
        "操作系统与计算机网络基础 (熟悉进程线程、内存管理、Linux 环境、TCP/IP)",
        "Git 版本控制与 CMake 构建工具",
    ],
    "project_evidence": [
        "已完成小型 OpenGL 图形程序（含着色器、三维模型加载与基础光照渲染）",
        "使用 Python、SQLite 与自动化测试维护 Career Agent 多源招聘采集与分析系统",
    ],
    "matching": {
        "primary_directions": ["后端服务", "跨平台桌面客户端", "测试开发", "AI应用工程"],
        "secondary_directions": ["图形学与实时渲染"],
        "project_evidence": [
            "已完成小型 OpenGL 图形程序",
            "使用 Python、SQLite 与自动化测试维护 Career Agent 系统",
        ],
        "supporting_skills": ["C++17", "Python", "Linux", "Git", "CMake"],
        "learning_targets": ["MySQL", "Redis", "Qt6"],
        "unverified_skills": [],
    },
    "score_component_limits": {
        "core_direction": 20,
        "required_skills": 25,
        "project_evidence": 20,
        "engineering_stack": 15,
        "basic_criteria": 20,
    },
    "score_thresholds": {
        "recommend": 75,
        "consider": 60,
    },
}


def test_static_syntax_and_workflow_yaml_validity():
    """A. 静态与基础测试：检查全部 Python 源码语法、配置文件 Schema 与 GitHub Actions YAML 结构。"""
    py_files = list(ROOT.glob("*.py")) + list((ROOT / "crawlers").glob("*.py")) + list((ROOT / "scripts").glob("*.py"))
    assert len(py_files) >= 25
    for pyf in py_files:
        py_compile.compile(str(pyf), doraise=True)

    # 校验 config.yaml 与 profile.yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(cfg.get("companies"), list) and len(cfg["companies"]) > 100
    prof = profile_config.load_profile(ROOT / "profile.yaml")
    assert sum(prof["score_component_limits"].values()) == 100

    # 校验 GitHub Actions YAML 与关键顺序（先部署 gh-pages，后发送飞书推送）
    daily_wf = yaml.safe_load((ROOT / ".github" / "workflows" / "daily_report.yml").read_text(encoding="utf-8"))
    steps = daily_wf["jobs"]["run"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    idx_run = next(i for i, n in enumerate(step_names) if "Run pipeline" in n)
    idx_deploy = next(i for i, n in enumerate(step_names) if "Deploy report to gh-pages" in n)
    idx_notify = next(i for i, n in enumerate(step_names) if "Send Feishu notification" in n)
    assert idx_run < idx_deploy < idx_notify
    assert steps[idx_run]["env"].get("SKIP_NOTIFY") == "1"


def test_scoring_subsystem_six_states_and_no_artificial_64_cap(tmp_path):
    """D. 评分验收：验证6种状态区分（尚未评分、评分成功、评分失败、资格不符、证据不足、低分）及解除64分误锁。"""
    conn = db.init_db(str(tmp_path / "scoring_states.db"))

    # 1. 高匹配岗位（AI Pro 评分 79 分，验证不再被错误锁死在 64 分）
    high_job = {
        "company": "大疆",
        "title": "2027校招-AI测试开发工程师",
        "city": "深圳",
        "job_type": "校招",
        "jd_url": "https://example.com/dji/ai-sdet",
        "jd_raw": (
            "岗位职责：负责 AI 应用与自动化测试工具链研发、性能评测与持续集成。"
            "任职要求：本科及以上学历，熟悉 C++ 或 Python，掌握 Linux、数据结构与算法、Git。" * 2
        ),
        "published_at": "2026-10-18",
        "source": "大疆",
        "cohort": 2027,
        "cohort_status": "confirmed",
    }
    _, jid_high = db.upsert_job(conn, high_job)
    stored_high = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (jid_high,)).fetchone())

    mock_pro_json = json.dumps({
        "score_breakdown": {
            "core_direction": 18,
            "required_skills": 20,
            "project_evidence": 20,
            "engineering_stack": 11,
            "basic_criteria": 10,
        },
        "evidence_level": "partial",
        "evidence": [
            {
                "jd_requirement": "Python与自动化测试工具开发",
                "profile_evidence": "Career Agent Python/pytest 工程实践",
                "relation": "direct",
                "requirement_type": "basic",
            }
        ],
        "missing_core_requirements": ["Jenkins CI平台", "专项压测框架"],
        "advantages": ["Python与自动化测试实践直接契合", "C++与Linux系统基础扎实"],
        "gaps": ["需补充企业级 CI/CD 平台经验"],
        "summary": "AI测试工具开发方向高度契合，本科可投。",
    }, ensure_ascii=False)

    with patch("analyzer.call_deepseek_api", return_value=mock_pro_json):
        pro_res = analyzer.analyze_job(stored_high, UNDERGRAD_PROFILE)
    assert pro_res["analysis_status"] == "complete"
    assert pro_res["match_score"] > 64  # 79 - 3 = 76，解除 64 分硬封顶！
    assert pro_res["recommendation"] == "推荐"
    db.save_analysis(conn, jid_high, pro_res)

    # 2. 本地七维规则评分岗位（完整 JD，本科可投、意向城市、含明确问题分析+系统构建+验证反馈职责）
    local_job = {
        "company": "金山办公",
        "title": "2027届-C++跨平台客户端开发工程师",
        "city": "武汉",
        "job_type": "校招",
        "jd_url": "https://example.com/wps/cpp",
        "jd_raw": (
            "岗位职责：1. 负责 WPS 跨平台桌面客户端核心模块的设计与实现；"
            "2. 参与复杂文档渲染与内存性能瓶颈分析、故障排查与稳定性调优；"
            "3. 编写单元测试与自动化回归测试脚本，参与代码评审与持续集成闭环。"
            "任职要求：2027届本科及以上学历，熟悉 C/C++、STL、数据结构与算法、操作系统、计算机网络与 Linux/Git/CMake，周末双休。"
        ),
        "published_at": "2026-10-18",
        "source": "金山办公",
        "cohort": 2027,
        "cohort_status": "confirmed",
    }
    _, jid_local = db.upsert_job(conn, local_job)

    # 3. 真实低分岗位（完整 JD，但属于纯前沿算法岗且缺口多 -> 评分为低分 <60）
    low_job = {
        "company": "某大模型公司",
        "title": "2027届-多模态预训练算法工程师",
        "city": "北京",
        "job_type": "校招",
        "jd_url": "https://example.com/llm/algo",
        "jd_raw": (
            "岗位职责：负责百亿参数多模态基座大模型预训练算法研发与顶会论文发表。"
            "任职要求：熟悉深度学习算法，需承受较强的工作压力与快节奏加班，熟练使用 Java、Golang、MySQL、Docker、Kubernetes。" * 2
        ),
        "published_at": "2026-10-18",
        "source": "某大模型公司",
        "cohort": 2027,
        "cohort_status": "confirmed",
    }
    _, jid_low = db.upsert_job(conn, low_job)

    # 4. 资格不符岗位（明确要求硕士或博士学历）
    ineligible_job = {
        "company": "某研究院",
        "title": "2027届-博士专项视觉算法研究员",
        "city": "上海",
        "job_type": "校招",
        "jd_url": "https://example.com/inst/phd",
        "jd_raw": (
            "岗位职责：负责前沿计算机视觉算法研究。任职要求：硕士研究生及以上学历或博士学历，发表过顶会论文。" * 2
        ),
        "published_at": "2026-10-18",
        "source": "某研究院",
        "cohort": 2027,
        "cohort_status": "confirmed",
    }
    _, jid_ineligible = db.upsert_job(conn, ineligible_job)
    stored_ineligible = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (jid_ineligible,)).fetchone())
    inel_res = analyzer.analyze_job(stored_ineligible, UNDERGRAD_PROFILE)
    assert inel_res["analysis_status"] == "ineligible"

    # 5. 证据不足岗位（JD 过短/待补全）
    sparse_job = {
        "company": "某车企",
        "title": "2027届-C++软件开发工程师",
        "city": "合肥",
        "job_type": "校招",
        "jd_url": "https://example.com/car/sparse",
        "jd_raw": "2027届-C++软件开发工程师 发布于 2026-10-18",
        "published_at": "2026-10-18",
        "source": "某车企",
        "cohort": 2027,
        "cohort_status": "confirmed",
    }
    _, jid_sparse = db.upsert_job(conn, sparse_job)

    # 6. 评分失败状态（API 异常返回 failed，不伪装成正常低分入库）
    with patch("analyzer.call_deepseek_api", side_effect=RuntimeError("API timeout")):
        fail_res = analyzer.analyze_job(stored_high, UNDERGRAD_PROFILE)
    assert fail_res["analysis_status"] == "failed"
    assert fail_res["summary"] == "分析失败，请检查API配置"

    # 执行数据库本地五维基准同步
    sync_stats = db.sync_local_baseline_scores(conn, profile=UNDERGRAD_PROFILE)
    assert sync_stats["ai_pro_kept"] == 1
    assert sync_stats["local_scored_upserted"] == 2  # local_job 与 low_job
    assert sync_stats["jd_incomplete_skipped"] == 1
    assert sync_stats["ineligible_skipped"] == 1

    # 验证报告 payload 中的状态与数值排序
    items = db.get_all_jobs_with_analysis(conn)
    shown, _, _, _ = reporter._filter_items(items)
    payload = reporter._jobs_payload(shown)
    by_id = {row["id"]: row for row in payload}

    assert by_id[jid_high]["e"] is True and by_id[jid_high]["st"] == "complete" and by_id[jid_high]["s"] >= 75
    assert by_id[jid_local]["e"] is True and by_id[jid_local]["st"] == "local_scored" and by_id[jid_local]["s"] >= 75
    assert by_id[jid_low]["e"] is True and by_id[jid_low]["st"] == "local_scored" and by_id[jid_low]["s"] < 60
    assert by_id[jid_sparse]["e"] is False and by_id[jid_sparse]["st"] == "jd_incomplete" and by_id[jid_sparse]["s"] is None
    assert by_id[jid_ineligible]["e"] is False and by_id[jid_ineligible]["st"] == "ineligible" and by_id[jid_ineligible]["s"] is None
    conn.close()


def test_version_consistency_across_report_deploy_and_feishu(tmp_path, monkeypatch):
    """C. 版本一致性验收：验证生成的 HTML、report_meta.json 与飞书推送 URL 包含完全一致的 report_version_id，且部署失败时拦截旧链接。"""
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test_hook")
    monkeypatch.delenv("REPORT_BASE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "test-student/career-agent")

    report_dir = tmp_path / "reports"
    data = {
        "date": "2026-10-18",
        "items": [
            {
                "job": {
                    "id": 1,
                    "company": "金山办公",
                    "title": "2027届-C++研发工程师",
                    "city": "珠海",
                    "jd_url": "https://example.com/wps/1",
                    "job_type": "校招",
                    "cohort": 2027,
                    "cohort_status": "confirmed",
                    "crawled_at": "2026-10-18",
                    "last_seen_at": "2026-10-18",
                    "jd_raw": "负责WPS跨平台C++客户端研发，熟悉C++17、STL、Linux。" * 3,
                },
                "analysis": {
                    "match_score": 85,
                    "recommendation": "推荐",
                    "advantages": ["C++17直接匹配"],
                    "gaps": ["Qt6"],
                    "summary": "高匹配客户端研发岗",
                    "analysis_status": "complete",
                },
            }
        ],
        "applications": [],
    }

    html_path = reporter.generate_report("2026-10-18", data, reports_dir=str(report_dir), out_name="index")
    html_text = Path(html_path).read_text(encoding="utf-8")
    meta_path = report_dir / "report_meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    ver_id = meta["report_version_id"]
    assert ver_id.startswith("rep-")
    assert f'content="{ver_id}"' in html_text
    assert f"🔖 版本：{ver_id}" in html_text

    # 1. 部署成功时：飞书消息包含该版本号与带 ?v=rep-xxxx 的正式 GitHub Pages URL
    payload_ok = notifier._build_payload(
        [data["items"][0]["job"]],
        data["items"],
        {**data, "deploy_status": "success"},
        run_stats={"report_version_id": ver_id, "deploy_status": "success"},
    )
    flat_ok = json.dumps(payload_ok, ensure_ascii=False)
    assert ver_id in flat_ok
    assert f"https://test-student.github.io/career-agent/index.html?v={ver_id}" in flat_ok
    assert "career-agent-4z4.pages.dev" not in flat_ok

    # 2. 部署失败时：飞书消息明确报告部署失败，且绝不附带旧版网页链接
    payload_fail = notifier._build_payload(
        [data["items"][0]["job"]],
        data["items"],
        {**data, "deploy_status": "failure"},
        run_stats={"report_version_id": ver_id, "deploy_status": "failure"},
    )
    flat_fail = json.dumps(payload_fail, ensure_ascii=False)
    assert "网页部署失败" in flat_fail
    assert f"https://test-student.github.io/career-agent/index.html?v={ver_id}" not in flat_fail


def test_data_flow_scenarios_1_to_6_and_10_11(tmp_path):
    """B. 数据流集成测试（覆盖场景 1, 2, 3, 4, 5, 6, 10, 11）。"""
    conn = db.init_db(str(tmp_path / "integration_flow.db"))

    # 场景 5：插入一条过期历史岗位（last_seen_at = 2020-01-01）
    stale_job = {
        "company": "历史公司",
        "title": "2025届-C++开发工程师",
        "city": "北京",
        "job_type": "校招",
        "jd_url": "https://example.com/stale/1",
        "jd_raw": "负责旧系统维护。" * 10,
        "published_at": "2020-01-01",
        "source": "历史公司",
        "cohort": 2025,
        "cohort_status": "confirmed",
    }
    _, jid_stale = db.upsert_job(conn, stale_job)
    conn.execute("UPDATE jobs SET last_seen_at = '2020-01-01' WHERE id = ?", (jid_stale,))
    conn.commit()

    # 场景 1 & 4 & 6：插入正常岗位 A（高分）、正常岗位 B（中分）、缺失字段岗位 C（无 city 和短 JD）
    job_a = {
        "company": "金山办公",
        "title": "2027届-C++跨平台客户端研发工程师",
        "city": "珠海",
        "job_type": "校招",
        "jd_url": "https://example.com/wps/a",
        "jd_raw": "岗位职责：负责跨平台客户端研发与性能优化。任职要求：本科及以上学历，熟悉 C++17、STL、Linux、CMake、数据结构与算法，周末双休。" * 3,
        "published_at": "2026-10-18",
        "source": "金山办公",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "screening_tier": "A",
    }
    job_b = {
        "company": "测试企业",
        "title": "2027届-后端研发工程师",
        "city": "上海",
        "job_type": "校招",
        "jd_url": "https://example.com/test/b",
        "jd_raw": "岗位职责：负责企业级后端服务研发。任职要求：本科及以上学历，熟悉 C++ 或 Go，需补充 Java、MySQL、Redis、Docker、Kubernetes，抗压能力强。" * 3,
        "published_at": "2026-10-18",
        "source": "测试企业",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "screening_tier": "A",
    }
    job_missing_fields = {
        "company": "某初创公司",
        "title": "2027届-软件开发工程师",
        "city": "",
        "job_type": "校招",
        "jd_url": "https://example.com/startup/c",
        "jd_raw": "",
        "published_at": "",
        "source": "某初创公司",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "screening_tier": "B",
    }

    was_ins_a1, jid_a = db.upsert_job(conn, job_a)
    was_ins_a2, jid_a_dup = db.upsert_job(conn, job_a)
    assert was_ins_a1 is True and was_ins_a2 is False and jid_a == jid_a_dup

    _, jid_b = db.upsert_job(conn, job_b)
    _, jid_c = db.upsert_job(conn, job_missing_fields)

    stored_a = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (jid_a,)).fetchone())

    # 场景 2：AI 返回格式错误
    with patch("analyzer.call_deepseek_api", return_value="NOT A JSON {{{"):
        res_bad_json = analyzer.batch_analyze([stored_a], UNDERGRAD_PROFILE, conn, hydrate=False)
    assert res_bad_json == []
    assert not db.has_analysis(conn, jid_a)

    # 场景 3：评分服务超时/异常
    with patch("analyzer.call_deepseek_api", side_effect=TimeoutError("Read timed out")):
        res_timeout = analyzer.batch_analyze([stored_a], UNDERGRAD_PROFILE, conn, hydrate=False)
    assert res_timeout == []
    assert not db.has_analysis(conn, jid_a)

    # 场景 1 & 6：AI 服务恢复正常后评分成功入库，二次运行自动跳过已评分岗位
    valid_pro_a = json.dumps({
        "score_breakdown": {"core_direction": 19, "required_skills": 22, "project_evidence": 18, "engineering_stack": 13, "basic_criteria": 16},
        "evidence_level": "direct",
        "evidence": [
            {"jd_requirement": "C++17与STL", "profile_evidence": "C++17项目", "relation": "direct", "requirement_type": "core"},
            {"jd_requirement": "CMake与Linux", "profile_evidence": "Linux/CMake实践", "relation": "direct", "requirement_type": "basic"},
        ],
        "missing_core_requirements": [],
        "advantages": ["C++17与桌面工程高度吻合"],
        "gaps": [],
        "summary": "高度契合的客户端研发岗",
    }, ensure_ascii=False)
    with patch("analyzer.call_deepseek_api", return_value=valid_pro_a) as mock_api:
        res_ok = analyzer.batch_analyze([stored_a], UNDERGRAD_PROFILE, conn, hydrate=False)
        assert len(res_ok) == 1
        assert mock_api.call_count == 1
        res_repeat = analyzer.batch_analyze([stored_a], UNDERGRAD_PROFILE, conn, hydrate=False)
        assert res_repeat == []
        assert mock_api.call_count == 1

    sync_stats = db.sync_local_baseline_scores(conn, profile=UNDERGRAD_PROFILE)
    assert sync_stats["ai_pro_kept"] == 1
    assert sync_stats["local_scored_upserted"] == 1
    assert sync_stats["jd_incomplete_skipped"] == 1

    items = db.get_all_jobs_with_analysis(conn)
    assert all(it["job"]["id"] != jid_stale for it in items)
    shown, _, _, _ = reporter._filter_items(items)
    payload = reporter._jobs_payload(shown)
    sorted_payload = sorted(payload, key=lambda j: j["s"] if j["e"] else -1, reverse=True)
    assert [j["id"] for j in sorted_payload] == [jid_a, jid_b, jid_c]
    assert sorted_payload[0]["s"] == 88
    assert sorted_payload[1]["e"] is True and sorted_payload[1]["s"] < 88
    assert sorted_payload[2]["e"] is False and sorted_payload[2]["s"] is None and sorted_payload[2]["r"] == "JD待补全"
    conn.close()


def test_data_flow_scenarios_7_8_9_failures_and_isolation(tmp_path, monkeypatch):
    """B. 数据流集成测试（覆盖场景 7, 8, 9）：报告生成失败、网页部署失败与飞书推送失败隔离。"""
    bad_dir_file = tmp_path / "not_a_dir"
    bad_dir_file.write_text("file", encoding="utf-8")
    try:
        reporter.generate_report("2026-10-18", {"items": []}, reports_dir=str(bad_dir_file))
        raise AssertionError("Expected OSError when reports_dir is a file")
    except OSError:
        pass

    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/secret_token_xyz")
    monkeypatch.setenv("REPORT_BASE_URL", "https://my-custom-report.example.com")
    payload = notifier._build_payload(
        [], [], {"date": "2026-10-18", "deploy_status": "failure"},
        run_stats={"deploy_status": "failure", "report_version_id": "rep-failtest"},
    )
    flat = json.dumps(payload, ensure_ascii=False)
    assert "网页部署失败" in flat
    assert "https://my-custom-report.example.com" not in flat

    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 502
        mock_post.return_value.text = "Bad Gateway for https://open.feishu.cn/open-apis/bot/v2/hook/secret_token_xyz"
        ok = notifier.send([], [], {"date": "2026-10-18"}, retries=1)
        assert ok is False


def test_exploration_mode_cross_language_score_parity_and_independent_transition_cost():
    """验证核心反偏置要求：在探索模式下，其他条件相近时，仅因为用户正在学习 C++，
    不会自动导致 C++ 岗位获得更高的总体匹配分；学习成本独立影响转型评估，不替代职业适合度判断。"""
    prof = profile_config.load_profile(ROOT / "profile.yaml")
    assert prof["recommendation_mode"] == "exploration"
    assert prof["target_direction_locked"] is False
    assert prof["user_state"]["bias_guardrails"]["infer_cpp_direction_interest"] is False
    assert prof["user_state"]["bias_guardrails"]["equate_low_learning_cost_with_suitability"] is False

    base_jd_suffix = (
        "岗位职责：负责核心业务平台与分布式服务模块的设计、开发、单元测试与性能优化。"
        "任职要求：2027届本科及以上学历，计算机或软件工程相关专业；"
        "具备扎实的数据结构与算法、操作系统、计算机网络与 Linux 基础；"
        "熟悉 Git 版本控制与自动化测试流程，周末双休，提供完善的导师制培养。"
    )

    cpp_job = {
        "company": "基准科技",
        "title": "2027届-C++后端开发工程师",
        "city": "成都",
        "job_type": "校招",
        "jd_url": "https://example.com/bench/cpp",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": f"熟练掌握 C/C++ 编程语言与 STL 容器。{base_jd_suffix}",
    }
    java_job = {
        "company": "基准科技",
        "title": "2027届-Java后端开发工程师",
        "city": "成都",
        "job_type": "校招",
        "jd_url": "https://example.com/bench/java",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": f"熟悉 Java 编程语言与 Spring Boot 微服务生态。{base_jd_suffix}",
    }
    go_job = {
        "company": "基准科技",
        "title": "2027届-Go后端开发工程师",
        "city": "成都",
        "job_type": "校招",
        "jd_url": "https://example.com/bench/go",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": f"熟悉 Golang (Go语言) 编程与高并发微服务开发。{base_jd_suffix}",
    }
    python_job = {
        "company": "基准科技",
        "title": "2027届-Python后端开发工程师",
        "city": "成都",
        "job_type": "校招",
        "jd_url": "https://example.com/bench/python",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": f"熟悉 Python 编程语言与服务端开发。{base_jd_suffix}",
    }
    ts_job = {
        "company": "基准科技",
        "title": "2027届-TypeScript全栈开发工程师",
        "city": "成都",
        "job_type": "校招",
        "jd_url": "https://example.com/bench/ts",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": f"熟悉 TypeScript / JavaScript 与 React 现代 Web 工程化开发。{base_jd_suffix}",
    }

    res_cpp = job_filters.score_job_against_profile(cpp_job, prof)
    res_java = job_filters.score_job_against_profile(java_job, prof)
    res_go = job_filters.score_job_against_profile(go_job, prof)
    res_py = job_filters.score_job_against_profile(python_job, prof)
    res_ts = job_filters.score_job_against_profile(ts_job, prof)

    # 1. 验证总体匹配分平权：在其他条件相近的情况下，C++ 岗位绝不自动高于 Java / Go / Python / TS 岗位
    assert res_cpp["match_score"] == res_java["match_score"] == res_go["match_score"] == res_py["match_score"] == res_ts["match_score"]
    assert res_cpp["recommendation"] == res_java["recommendation"] == "推荐"

    # 2. 验证学习与转型成本独立评估（严格遵守新版 profile.yaml 中 Python "本人还没开始学习" 的边界声明）：
    #    C++ 为 low，Python 与 Go 为 medium_low，Java 与 TS 为 medium
    assert res_cpp["traceable_evidence"]["transition_cost"]["code"] == "low"
    assert res_py["traceable_evidence"]["transition_cost"]["code"] == "medium_low"
    assert res_go["traceable_evidence"]["transition_cost"]["code"] == "medium_low"
    assert res_java["traceable_evidence"]["transition_cost"]["code"] == "medium"
    assert res_ts["traceable_evidence"]["transition_cost"]["code"] == "medium"

    # 3. 验证探索模式下召回与分级平权：5 个跨语言岗位均进入 A 档，不被预筛排除
    for job_item in (cpp_job, java_job, go_job, python_job, ts_job):
        assert analyzer.local_screening_tier(job_item, prof) == "A"
        assert analyzer._merge_screening_tier(job_item, "C", prof) in ("A", "B")


def test_recommendation_mode_switching_and_traceable_evidence_fields():
    """验证四（探索模式 vs 定向求职模式切换）与五（5项可追踪证据与7维评估因素完整性）。"""
    prof_explore = profile_config.load_profile(ROOT / "profile.yaml")
    prof_targeted_java = {
        **prof_explore,
        "recommendation_mode": "targeted",
        "target_direction_locked": True,
        "locked_target_directions": ["Java后端"],
        "locked_target_languages": ["Java"],
    }

    common_jd = (
        "岗位职责：负责企业级核心服务研发与维护。任职要求：2027届本科及以上学历，"
        "掌握数据结构与算法、操作系统、计算机网络、Linux 与 Git，周末双休。" * 2
    )
    cpp_job = {
        "company": "测试科技",
        "title": "2027届-C++软件开发工程师",
        "city": "上海",
        "job_type": "校招",
        "jd_url": "https://example.com/mode/cpp",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": f"要求熟悉 C++ 与 STL。{common_jd}",
    }
    java_job = {
        "company": "测试科技",
        "title": "2027届-Java后端开发工程师",
        "city": "上海",
        "job_type": "校招",
        "jd_url": "https://example.com/mode/java",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": f"要求熟悉 Java 与 Spring Boot。{common_jd}",
    }

    # 定向求职模式锁定 Java 时，Java 岗位得分显著高于 C++ 岗位
    scored_java_targeted = job_filters.score_job_against_profile(java_job, prof_targeted_java)
    scored_cpp_targeted = job_filters.score_job_against_profile(cpp_job, prof_targeted_java)
    assert scored_java_targeted["recommendation_mode"] == "targeted"
    assert scored_java_targeted["match_score"] > scored_cpp_targeted["match_score"]

    # 验证 7 维解耦因素与 5 项可追踪证据完整性
    factors = scored_java_targeted["evaluation_factors"]
    expected_factor_keys = {
        "1_domain_and_role_fit",
        "2_hard_qualifications",
        "3_skill_gap",
        "4_transition_cost",
        "5_wlb_and_growth",
        "6_personal_conditions",
        "7_existing_skill_advantage",
    }
    assert expected_factor_keys.issubset(set(factors.keys()))

    trace = scored_java_targeted["traceable_evidence"]
    assert trace["why_worth_considering"]
    assert len(trace["met_requirements"]) >= 2
    assert len(trace["missing_capabilities"]) >= 1
    assert trace["cpp_transferability"]["transferable_to"]
    assert trace["cpp_transferability"]["cannot_replace"]
    assert "JVM" in trace["cpp_transferability"]["cannot_replace"][0] or "Spring" in trace["cpp_transferability"]["cannot_replace"][0]
    assert trace["transition_cost"]["level"]
    assert trace["transition_cost"]["basis"]
    assert trace["transition_cost"]["uncertainty"]



def test_new_personal_profile_and_interest_filtering_parsed_and_versioned():
    """验证新版 profile.yaml 中的 personal_profile、interest_filtering、skill_proficiency 被完整读取并纳入版本指纹。"""
    prof = profile_config.load_profile(ROOT / "profile.yaml")
    pp = prof["personal_profile"]
    if_cfg = prof["interest_filtering"]
    sp_map = profile_config.extract_skill_proficiency_map(prof)

    assert len(pp["thinking_and_work_style"]) >= 4
    assert len(pp["hobbies_and_general_interests"]) >= 4
    assert len(pp["possible_work_content_preferences"]) >= 4
    assert len(pp["interest_caveats"]) >= 3
    assert if_cfg["enabled"] is True
    assert if_cfg["evaluate_actual_work_content"] is True
    assert if_cfg["use_hobbies_as_soft_evidence_only"] is True
    assert if_cfg["separate_interest_fit_from_skill_fit"] is True
    assert if_cfg["separate_interest_fit_from_learning_cost"] is True
    assert sp_map["python"]["not_learned_yet"] is True
    assert "本人还没开始学习" in sp_map["python"]["limitation"]
    assert "基础学习阶段" in sp_map["c++"]["level"]

    # 修改 personal_profile 中的偏好必须改变 profile_version_id 并被 diff_profile_versions 捕获
    vid_before = job_filters.profile_version_id(prof)
    prof_modified = {
        **prof,
        "personal_profile": {
            **pp,
            "liked_work_content": ["分布式存储引擎性能诊断"],
        },
    }
    vid_after = job_filters.profile_version_id(prof_modified)
    assert vid_before != vid_after
    diff = job_filters.diff_profile_versions(prof, prof_modified)
    assert diff["has_changes"] is True
    assert "personal_profile" in diff["changed_fields"]


def test_work_content_interest_evaluation_and_hobby_anti_bias():
    """验证 4.1 & 4.3：兴趣判断基于实际日常工作内容而非职位名/热门标签/爱好，且有效识别低兴趣重复岗与职责证据不足岗。"""
    prof = profile_config.load_profile(ROOT / "profile.yaml")

    # 案例 1：同名“AI Agent 工程师”，A 岗深入核心机制研发、问题排查与量化评测；B 岗主要是 Dify 低代码拖拽配置与整理文档/演示材料
    deep_agent_job = {
        "company": "甲科技",
        "title": "2027届-AI Agent研发工程师",
        "city": "上海",
        "job_type": "校招",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": (
            "岗位职责：1. 参与 Agent 系统核心模块的设计与实现，深入任务规划、上下文管理与工具调用底层机制；"
            "2. 排查并解决高并发场景下的响应延迟与状态一致性问题，开展性能优化；"
            "3. 搭建自动化评测基准与监控诊断闭环，用量化数据验证迭代效果。"
            "任职要求：2027届本科及以上学历，扎实掌握数据结构与算法、操作系统、计算机网络与 Linux，熟悉 C++/Go/Python 之一，周末双休。"
        ),
    }
    lowcode_agent_job = {
        "company": "甲科技",
        "title": "2027届-AI Agent开发工程师",
        "city": "上海",
        "job_type": "校招",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": (
            "岗位职责：1. 负责使用 Dify、FastGPT 等低代码搭建工具完成日常工作流简单配置；"
            "2. 负责数据录入、资料整理、台账整理以及整理项目文档与PPT演示材料。"
            "任职要求：2027届本科及以上学历，熟悉常用 OFFICE 办公软件，了解 Python 基础。"
        ),
    }
    res_deep = job_filters.score_job_against_profile(deep_agent_job, prof)
    res_lowcode = job_filters.score_job_against_profile(lowcode_agent_job, prof)

    assert res_deep["dimensions"]["interest_fit"]["status"] == "high"
    assert res_lowcode["dimensions"]["interest_fit"]["status"] == "low"
    assert res_deep["match_score"] >= 75 and res_deep["recommendation"] == "推荐"
    assert res_lowcode["match_score"] < 60 and res_lowcode["recommendation"] == "不推荐"
    assert "反偏置声明" in res_deep["dimensions"]["interest_fit"]["caveat_applied"]

    # 案例 2：游戏/图形与高强度纯算法科研反偏置校验
    pure_research_job = {
        "company": "乙研究院",
        "title": "2027届-【北斗】大模型算法研究员",
        "city": "北京",
        "job_type": "校招",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": (
            "岗位职责：负责前沿强化学习理论推导，以第一作者发表 NeurIPS / ICML / CVPR 顶会论文。"
            "任职要求：2027届毕业生，博士优先，具备顶尖数学推导能力。"
        ),
    }
    res_research = job_filters.score_job_against_profile(pure_research_job, prof)
    assert res_research["dimensions"]["interest_fit"]["status"] == "low"
    assert res_research["recommendation"] == "不推荐"

    # 案例 3：职责描述不足（仅有任职要求，无具体日常职责）→ 标记为 insufficient_evidence，绝不伪装成高分推荐
    vague_duty_job = {
        "company": "丙软件",
        "title": "2027届-C++软件开发工程师",
        "city": "成都",
        "job_type": "校招",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": (
            "任职要求：1. 2027届本科及以上学历，计算机或软件工程专业；"
            "2. 熟悉 C/C++ 编程语言、数据结构与算法、操作系统、计算机网络与 Linux；"
            "3. 熟悉 Git 与 CMake，周末双休，五险一金。" * 2
        ),
    }
    res_vague = job_filters.score_job_against_profile(vague_duty_job, prof)
    assert res_vague["dimensions"]["interest_fit"]["status"] == "insufficient_evidence"
    assert res_vague["match_score"] <= 68
    assert res_vague["recommendation"] != "推荐"


def test_seven_dimensions_strict_separation_and_hard_gates():
    """验证 4.2 & 五：7个评价维度独立存在，且硬性不合格、WLB风险、非意向城市不能被高兴趣分掩盖。"""
    prof = profile_config.load_profile(ROOT / "profile.yaml")
    expected_dims = {
        "eligibility",
        "interest_fit",
        "career_fit",
        "skill_fit",
        "learning_cost",
        "wlb_fit",
        "evidence_confidence",
    }

    # 高兴趣但要求硕士及以上学历 → 硬性资格一票否决
    master_job = {
        "company": "丁科技",
        "title": "2027届-分布式存储系统研发工程师",
        "city": "杭州",
        "job_type": "校招",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": (
            "岗位职责：负责分布式存储引擎核心模块架构设计、代码实现、性能瓶颈排查与单元测试闭环。"
            "任职要求：硕士研究生及以上学历，熟悉 C++/Go、Linux、数据结构与算法，周末双休。"
        ),
    }
    res_master = job_filters.score_job_against_profile(master_job, prof)
    assert res_master["analysis_status"] == "ineligible"
    assert res_master["dimensions"]["eligibility"]["passed"] is False
    assert res_master["match_score"] <= 30
    assert res_master["recommendation"] == "不推荐"

    # 非计算机硬件/IC/视觉设计岗位假阳性拦截验证
    for non_cs_title in ("数字IC工程师(前端方向)", "27届正式批-PCB工程师（机器人方向）", "【27届校招】视觉设计培训生", "安全管理/损耗控制"):
        assert job_filters.is_cs_software_role({"title": non_cs_title}) is False

    # 高兴趣但存在显式高压加班/驻场要求 → 触发 WLB 门禁，不得评为“推荐”
    overtime_job = {
        "company": "戊科技",
        "title": "2027届-后端研发工程师",
        "city": "深圳",
        "job_type": "校招",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "jd_raw": (
            "岗位职责：负责核心后端服务架构设计与开发、故障定位排查与自动化测试验证。"
            "任职要求：2027届本科及以上学历，熟悉 Java/Go/C++、Linux、数据结构与算法；"
            "需具备极强的抗压能力，适应高强度加班、大小周及长期出差驻场开发。"
        ),
    }
    res_ot = job_filters.score_job_against_profile(overtime_job, prof)
    assert expected_dims.issubset(set(res_ot["dimensions"].keys()))
    assert res_ot["dimensions"]["wlb_fit"]["status"] == "risk"
    assert res_ot["dimensions"]["wlb_fit"]["violates_constraint"] is True
    assert res_ot["match_score"] <= 58
    assert res_ot["recommendation"] == "不推荐"


def test_curation_policy_and_report_seven_dim_rendering(tmp_path):
    """验证 4.4 & 六：主推荐列表控制、探索样本保留、过滤统计抽查面板及报告七维详情渲染。"""
    prof = profile_config.load_profile(ROOT / "profile.yaml")
    items = [
        {
            "job": {
                "id": 201,
                "company": "优必选",
                "title": "2027届-Java后端开发工程师",
                "city": "深圳",
                "job_type": "校招",
                "jd_url": "https://example.com/ubtech/java",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "jd_raw": (
                    "岗位职责：1. 负责云平台后端服务架构设计与核心模块开发；"
                    "2. 排查并优化高并发接口性能瓶颈与故障定位；"
                    "3. 编写单元测试与接口自动化测试，参与持续集成与代码评审。"
                    "任职要求：2027届本科及以上学历，熟悉 Java/Spring Boot、数据结构与算法、操作系统、计算机网络与 Linux/Git，周末双休。"
                ),
            },
            "analysis": None,
        },
        {
            "job": {
                "id": 202,
                "company": "探索科技",
                "title": "2027届-软件开发工程师",
                "city": "成都",
                "job_type": "校招",
                "jd_url": "https://example.com/explore/sw",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "jd_raw": (
                    "任职要求：2027届本科及以上学历，熟悉 C++ 或 Go 语言，掌握数据结构与算法、操作系统、Linux 与 Git，周末双休。" * 2
                ),
            },
            "analysis": None,
        },
        {
            "job": {
                "id": 203,
                "company": "重复事务公司",
                "title": "2027届-IT技术支持与配置工程师",
                "city": "上海",
                "job_type": "校招",
                "jd_url": "https://example.com/low/ops",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "jd_raw": (
                    "岗位职责：负责日常数据录入、资料整理、桌面运维值守及售后客服电话回访。"
                    "任职要求：2027届本科及以上学历，熟悉 Office 软件。"
                ),
            },
            "analysis": None,
        },
    ]
    for it in items:
        it["analysis"] = job_filters.score_job_against_profile(it["job"], prof)

    curation = job_filters.curate_recommendations_with_interest_policy(items, prof)
    assert len(curation["primary_recommendations"]) == 1
    assert curation["primary_recommendations"][0]["job"]["id"] == 201
    assert len(curation["exploration_samples"]) == 1
    assert curation["exploration_samples"][0]["job"]["id"] == 202
    assert curation["total_filtered_or_demoted"] >= 2

    report_path = reporter.generate_report("2026-10-18", {"date": "2026-10-18", "items": items}, str(tmp_path))
    html_text = Path(report_path).read_text(encoding="utf-8")
    assert "展开七维独立评估详情" in html_text
    assert "跨方向代表性探索样本" in html_text
    assert "已从主推荐列表排除/降权的岗位统计与防误杀抽查样本" in html_text


def test_module1_skill_stack_extraction_and_project_recommendations(tmp_path):
    """验收 5.1：高频技能栈统计（按类别分组、同义词合并、重复发布去重、必需/加分/小众区分、样本不足标记）与实现项目推荐。"""
    import skill_projects
    prof = profile_config.load_profile(ROOT / "profile.yaml")

    # 构造含同义词、重复多城发布、不同类别与小样本类别的真实感测试集
    sample_jobs = []
    # Java/Go 后端岗（含重复多城发布 3 条，实际应计为 1 个独立岗位）
    for idx, city in enumerate(("深圳", "上海", "成都"), 1):
        sample_jobs.append({
            "id": 1000 + idx,
            "company": "甲云科技",
            "title": "2027届-后端开发工程师(J1001)",
            "city": city,
            "job_type": "校招",
            "jd_url": f"https://example.com/backend/{idx}",
            "crawled_at": "2026-10-18",
            "jd_raw": (
                "岗位职责：负责高并发微服务架构设计与核心接口开发，参与单元测试与代码评审。"
                "任职要求：1. 熟练掌握 Golang / Go语言 或 Java (Spring Boot)；"
                "2. 扎实掌握数据结构与算法、操作系统、TCP/IP 计算机网络与 Linux；"
                "3. 熟练掌握 MySQL 索引调优与 Redis 缓存设计；"
                "4. 有 Kubernetes (K8s) 或 Docker 容器经验者优先加分。"
            ),
        })
    # C++ 客户端/系统岗
    for idx in range(1, 16):
        sample_jobs.append({
            "id": 2000 + idx,
            "company": f"乙工业软件_{idx}",
            "title": "2027届-C++桌面客户端研发工程师",
            "city": "杭州",
            "job_type": "校招",
            "jd_url": f"https://example.com/cpp/{idx}",
            "crawled_at": "2026-10-18",
            "jd_raw": (
                "岗位职责：负责跨平台 C++17 / Qt6 客户端软件模块开发、内存泄漏排查与自动化测试。"
                "任职要求：熟练掌握 C/C++ 编程语言、STL、数据结构与算法、Linux/Windows 操作系统与 CMake/Git；"
                "熟悉 GDB/ASan 性能分析工具；有 OpenGL 渲染经验者优先。"
            ),
        })

    stats = skill_projects.aggregate_skills_by_category(sample_jobs, prof, min_category_samples=10)
    # 1. 验证单岗多城重复发布被正确识别并剔除（18条原始记录 -> 16个独立岗位，剔除2条重复）
    assert stats["dedup_summary"]["raw_posting_count"] == 18
    assert stats["dedup_summary"]["unique_job_count"] == 16
    assert stats["dedup_summary"]["duplicate_postings_removed"] == 2

    # 2. 验证不同岗位类别技能分开统计，不混为一谈；且样本不足类别明确标记
    cpp_cat = stats["by_category"]["software_cpp"]
    be_cat = stats["by_category"]["backend_dist"]
    assert cpp_cat["unique_job_count"] == 15
    assert cpp_cat["sample_sufficient"] is True
    assert be_cat["unique_job_count"] == 1
    assert be_cat["sample_sufficient"] is False
    assert "样本仅 1 个" in be_cat["sample_warning"]

    # 3. 验证必需技能(must)与加分项(bonus)区分、同义词合并
    single_skills = skill_projects.extract_job_skills(sample_jobs[0])
    assert single_skills["lang_golang"]["requirement_level"] == "must"
    assert single_skills["tool_docker_k8s"]["requirement_level"] == "bonus"

    # 4. 验证项目推荐包含完整 11 项要素、开源参考边界，且不局限于 C++/Python（包含 Go/Java、SQL/Redis、Qt、SDET、Agent、嵌入式仿真）
    state_file = tmp_path / "sp_state.json"
    conn = db.init_db(str(tmp_path / "sp_test.db"))
    rep1 = skill_projects.update_or_get_skill_projects_report(
        sample_jobs, profile=prof, conn=conn, state_path=state_file, force_refresh=True
    )
    assert rep1["enabled"] is True
    assert rep1["version_info"]["cache_hit"] is False
    projects = rep1["tier_b_projects"]
    assert len(projects) >= 6
    all_langs = " ".join(" ".join(p["primary_languages"]) for p in projects)
    assert "Go" in all_langs and "Java" in all_langs and "C++" in all_langs

    for p in projects:
        for req_key in (
            "project_id", "title", "skill_ids", "target_categories", "target_roles",
            "project_goal", "core_features", "engineering_tasks", "knowledge_mapping",
            "acceptance_criteria", "difficulty_level", "prerequisites", "estimated_effort",
            "resume_value", "staged_extensions", "reference_and_license", "separation_evaluation",
        ):
            assert p.get(req_key), f"Project {p.get('project_id')} missing {req_key}"
        assert p["reference_and_license"]["reuse_vs_self_impl"]
        assert p["separation_evaluation"]["current_skill_base"]
        assert p["separation_evaluation"]["learning_cost"]
        assert p["separation_evaluation"]["interest_alignment"]

    # 5. 验证缓存命中与数据库一致性（第二次运行 cache_hit=True，不重复计算）
    rep2 = skill_projects.update_or_get_skill_projects_report(
        sample_jobs, profile=prof, conn=conn, state_path=state_file, force_refresh=False
    )
    assert rep2["version_info"]["cache_hit"] is True
    assert rep2["version_info"]["report_version_id"] == rep1["version_info"]["report_version_id"]

    db_proj_cnt = conn.execute("SELECT COUNT(*) FROM skill_project_recommendations").fetchone()[0]
    db_snap_cnt = conn.execute("SELECT COUNT(*) FROM skill_stats_snapshots").fetchone()[0]
    conn.close()
    assert db_proj_cnt == len(projects)
    assert db_snap_cnt == 1


def test_module2_wlb_community_audit_matching_four_dims_and_degradation(tmp_path):
    """验收 5.2：社区 WLB 搜索词生成、四级实体匹配（拦截同音噪音）、四维分离、五类结论、三层数据分离、缓存与异常降级。"""
    import wlb_community
    prof = profile_config.load_profile(ROOT / "profile.yaml")

    # 1. 验证动态搜索词构造（覆盖正/负/中性/实习/部门上下文）
    queries = wlb_community.build_wlb_search_queries("汇川技术", "2027届-C++上位机软件开发工程师", "苏州")
    assert len(queries) == 6
    themes = {q["theme"] for q in queries}
    assert {"overtime_hours", "wlb_rhythm", "intern_experience", "dept_role_context", "oncall_pressure", "positive_wlb"} == themes
    assert any("苏州" in q["query"] and "不加班" in q["query"] for q in queries)

    # 2. 验证实体匹配：拦截将“优必选”误匹配到“优衣库”的噪音，同时正确分级同城同岗 vs 同公司未知部门
    noise_item = {
        "company_query": "优必选",
        "title": "在优衣库工作是种怎样的体验？店长排班与加班情况",
        "url": "https://www.zhihu.com/question/noise_uniqlo",
        "platform": "知乎",
        "published_at": "2025-05-01",
        "snippet": "优衣库门店每周排班，节假日比较忙。",
    }
    ev_noise = wlb_community.evaluate_discussion_item(noise_item, "优必选", "Java开发工程师", "深圳")
    assert ev_noise["is_valid_target"] is False
    assert ev_noise["match_tier"] == "tier_4_unverified"

    tier1_item = {
        "company_query": "金山办公",
        "title": "金山办公(WPS)武汉研发中心C++客户端日常作息与加班情况",
        "url": "https://www.zhihu.com/question/wps_wuhan_cpp",
        "platform": "知乎",
        "published_at": "2025-08-12",
        "snippet": "武汉WPS C++客户端研发平时晚上9点左右下班，周末双休，发版周偶有加班。",
        "engagement_info": "知乎回答 · 18赞同 · 6条评论",
        "relevant_comments": ["武汉研发中心确实比互联网大厂温和一些，但晚8:30-9:00还是常态。"],
    }
    ev_t1 = wlb_community.evaluate_discussion_item(tier1_item, "金山办公", "C++客户端开发工程师", "武汉")
    assert ev_t1["is_valid_target"] is True
    assert ev_t1["match_tier"] == "tier_1_exact_role_city"
    # 验证热度、相关性、可信度、代表性四维独立存在且不混为一谈
    four_d = ev_t1["four_dimensions"]
    assert set(four_d.keys()) == {"popularity", "relevance", "credibility", "representativeness"}
    assert "不可直接概括为全体员工" in four_d["representativeness"]

    # 3. 验证无证据时不编造数值分数、不默认 WLB 好或差，且三层结构（jd_facts / community_experiences / model_inferences）严格分离
    empty_audit = wlb_community.audit_company_wlb(
        "未知新创公司",
        [{"company": "未知新创公司", "title": "C++开发工程师", "city": "成都", "jd_raw": "负责C++后端服务开发，要求熟悉Linux与算法。"}],
        [],
        profile=prof,
    )
    assert empty_audit["verdict_code"] == "insufficient_evidence"
    assert empty_audit["explainable_numeric_index"] is None
    assert "jd_facts" in empty_audit and "community_experiences" in empty_audit and "model_inferences" in empty_audit
    assert "公司层面参考，无法确认该岗位具体团队情况" in empty_audit["department_scope_note"]

    # 4. 验证外部搜索接口报错/受限时优雅降级，不中断流程
    def _failing_fetcher(co, qs):
        raise ConnectionError("HTTP 403 Forbidden: simulated anti-crawler block")

    discs, smeta = wlb_community.fetch_public_wlb_discussions(
        "合合信息", queries, allow_live_network=True, custom_fetcher=_failing_fetcher
    )
    assert smeta["degraded_gracefully"] is True
    assert smeta["live_status"] == "degraded_on_error"
    assert len(discs) >= 1  # 仍成功从本地公开证据库回退拿到合合信息讨论

    # 5. 验证批量审核、SQLite 持久化与缓存命中
    conn = db.init_db(str(tmp_path / "wlb_test.db"))
    state_file = tmp_path / "wlb_state.json"
    test_items = [
        {"job": {"id": 1, "company": "汇川技术", "title": "C++软件开发工程师", "city": "苏州", "cohort": 2027, "cohort_status": "confirmed", "jd_raw": "负责工业自动化软件开发，双休，五险一金。" * 3}},
        {"job": {"id": 2, "company": "优必选", "title": "Java开发工程师", "city": "深圳", "cohort": 2027, "cohort_status": "confirmed", "jd_raw": "负责云平台后端服务开发，双休。" * 3}},
    ]
    res1 = wlb_community.update_or_get_wlb_community_audits(
        test_items, profile=prof, conn=conn, state_path=state_file, force_refresh=True
    )
    assert res1["summary_stats"]["fresh_computed"] > 0
    assert res1["audits_by_company"]["汇川技术"]["verdict_code"] == "mixed_controversial"
    # 优必选的“优衣库”同音噪音被拦截后应为 insufficient_evidence
    assert res1["audits_by_company"]["优必选"]["community_experiences"]["rejected_noise_count"] >= 1
    assert res1["audits_by_company"]["优必选"]["verdict_code"] == "insufficient_evidence"

    # 第二次运行命中缓存
    res2 = wlb_community.update_or_get_wlb_community_audits(
        test_items, profile=prof, conn=conn, state_path=state_file, force_refresh=False
    )
    assert res2["summary_stats"]["cache_hits"] == res1["summary_stats"]["audited_companies_count"]
    assert res2["summary_stats"]["fresh_computed"] == 0
    conn.close()


def test_module3_and_5_end_to_end_synergy_and_html_consistency(tmp_path):
    """验收 5.3：端到端验证岗位筛选、高频技能统计、实现项目推荐、社区WLB审核、数据库更新、HTML报告独立页面与卡片协同锚点的一致性。"""
    import skill_projects
    import wlb_community

    prof = profile_config.load_profile(ROOT / "profile.yaml")
    conn = db.init_db(str(tmp_path / "e2e_synergy.db"))

    raw_jobs = [
        {
            "company": "合合信息",
            "title": "【27校招】Agent开发工程师",
            "city": "上海",
            "job_type": "校招",
            "jd_url": "https://example.com/intsig/agent1",
            "published_at": "2026-10-18",
            "source": "合合信息",
            "crawled_at": "2026-10-18",
            "cohort": 2027,
            "cohort_status": "confirmed",
            "jd_raw": (
                "岗位职责：1. 负责建设智能体的任务规划、上下文管理、记忆、工具调用、评测等核心基础设施；"
                "2. 保障高并发场景下的低延迟、高可用，编写单元测试与自动化评测脚本。"
                "任职要求：2027届本科及以上学历，熟悉 Python/C++/Go 之一，掌握数据结构与算法、操作系统、计算机网络、Linux、MySQL、Redis 与 Git，周末双休。"
            ),
        },
        {
            "company": "汇川技术",
            "title": "2027届-C++软件开发工程师",
            "city": "苏州",
            "job_type": "校招",
            "jd_url": "https://example.com/inovance/cpp1",
            "published_at": "2026-10-18",
            "source": "汇川技术",
            "crawled_at": "2026-10-18",
            "cohort": 2027,
            "cohort_status": "confirmed",
            "jd_raw": (
                "岗位职责：1. 负责工业自动化上位机与控制软件的需求拆解、架构设计、C++/Qt6 编码实现与性能调优；"
                "2. 参与自动化测试工具开发与现场问题排查定位。"
                "任职要求：2027届本科及以上学历，熟悉 C/C++、STL、Qt、数据结构与算法、操作系统与 CMake/Git，五险一金。"
            ),
        },
    ]
    for j in raw_jobs:
        db.upsert_job(conn, j)

    db.sync_local_baseline_scores(conn, profile=prof)
    all_items = db.get_all_jobs_with_analysis(conn)
    assert len(all_items) == 2

    sp_res = skill_projects.update_or_get_skill_projects_report(
        all_items, profile=prof, conn=conn, state_path=tmp_path / "sp.json", force_refresh=True
    )
    wlb_res = wlb_community.update_or_get_wlb_community_audits(
        all_items, profile=prof, conn=conn, state_path=tmp_path / "wlb.json", force_refresh=True
    )
    conn.close()

    assert sp_res["enabled"] is True and len(sp_res["tier_b_projects"]) >= 6
    assert wlb_res["enabled"] is True and "合合信息" in wlb_res["audits_by_company"]

    report_file = reporter.generate_report(
        "2026-10-18",
        {"date": "2026-10-18", "items": all_items, "applications": []},
        str(tmp_path / "reports"),
        out_name="index",
    )
    html_out = Path(report_file).read_text(encoding="utf-8")

    # 验证两个新增独立模块页面及岗位卡片协同栏均正确渲染在最终 HTML 报告中
    assert "data-page=\"skill-projects\"" in html_out
    assert "层级 A：真实招聘高频技能概览" in html_out
    assert "层级 B：「技能栈 → 实现项目」工程训练方案" in html_out
    assert "层级 C：分阶段工程实践建议与防坑守则" in html_out
    assert "data-page=\"wlb-audit\"" in html_out
    assert "① 【招聘事实 (Official JD Facts)】" in html_out
    assert "② 【社区经验 (Community Experiences)】" in html_out
    assert "③ 【模型审慎推断与面试核实清单 (Model Inferences & Caveats)】" in html_out
    assert "🛠️ 对应工程训练项目：" in html_out
    assert "⚖️ 社区WLB证据：" in html_out


# ==============================================================================
# 十一、「技能栈与实现项目」独立模块 (v2.0) 专项验收测试套件
# ==============================================================================

def test_v2_navigation_and_skill_projects_page_regions(tmp_path):
    """验证 11.1 导航与页面：左侧菜单真实入口、点击进入独立页面、6大功能区域、操作按钮与刷新后状态恢复数据包。"""
    prof = profile_config.load_profile(ROOT / "profile.yaml")
    sample_items = [
        {
            "job": {
                "id": 3001 + i,
                "company": f"测试科技_{i}",
                "title": "2027届-C++/Python后端与客户端开发工程师",
                "city": "上海",
                "job_type": "校招",
                "jd_url": f"https://example.com/sp_nav/{i}",
                "crawled_at": "2026-10-18",
                "cohort": 2027,
                "cohort_status": "confirmed",
                "jd_raw": (
                    "岗位职责：负责核心服务与跨平台桌面客户端开发、性能调优与单元测试。"
                    "任职要求：2027届本科及以上学历，熟悉 C/C++、Python、Go、数据结构与算法、操作系统、计算机网络、MySQL、Redis、CMake、Git，双休。"
                ),
            },
            "analysis": None,
        }
        for i in range(16)
    ]

    report_file = reporter.generate_report(
        "2026-10-19",
        {"date": "2026-10-19", "items": sample_items, "applications": []},
        str(tmp_path / "reports"),
        out_name="index",
    )
    html_text = Path(report_file).read_text(encoding="utf-8")

    # 1. 左侧导航菜单必须包含真实可点击的 nav-item 入口 (data-target="skill-projects")
    assert '<li class="nav-item" data-target="skill-projects">' in html_text
    assert '<span class="nav-label">技能栈与实现项目</span>' in html_text

    # 2. 独立页面视图与 6 大核心区域齐全
    assert '<section class="page" data-page="skill-projects" id="pageSkillProjects">' in html_text
    for required_region_id in (
        'id="spStatusBanner"',
        'id="spActionToolbar"',
        'id="spWorkspaceSection"',
        'id="spProjectListSection"',
        'id="spProjectDetailSection"',
        'id="spSkillOverviewSection"',
    ):
        assert required_region_id in html_text, f"Missing region {required_region_id}"

    # 3. 关键操作按钮齐全，明确区分「仅更新技能统计」与「重新生成候选项目方案」
    for btn_id in (
        'id="btnRefreshSkillStats"',
        'id="btnRegenerateProjects"',
        'id="btnGeneratePlan"',
        'id="btnSubmitFeedback"',
        'id="btnCompareTwo"',
    ):
        assert btn_id in html_text, f"Missing button {btn_id}"

    # 4. 页面内嵌可恢复状态数据包 window.__SKILL_PROJECTS_DATA
    assert "window.__SKILL_PROJECTS_DATA =" in html_text
    assert "initSkillProjectsInteractive" in html_text


def test_v2_auto_input_assembly_and_traceable_skill_constraints(tmp_path):
    """验证 11.2 数据与技能统计：自动读取岗位与 profile.yaml、构造可追溯技能约束清单、样本不足不编造统计值、类别不混合。"""
    import skill_projects
    prof = profile_config.load_profile(ROOT / "profile.yaml")

    # 验证小样本 (<15) 时明确标记数据不足，绝不伪造高置信结论
    tiny_jobs = [
        {
            "id": 1,
            "company": "小样本企",
            "title": "2027届-后端开发工程师",
            "city": "深圳",
            "jd_url": "https://example.com/tiny/1",
            "crawled_at": "2026-10-18",
            "jd_raw": "负责后端接口开发。要求熟悉 Golang、MySQL、Redis、Linux 与数据结构算法。" * 2,
        }
    ]
    assembled_tiny = skill_projects.assemble_skill_project_inputs(tiny_jobs, prof)
    assert assembled_tiny["data_sufficient"] is False
    assert any("低于最小稳健统计阈值" in w for w in assembled_tiny["data_warnings"])
    assert assembled_tiny["profile_context"]["recommendation_mode"] == "exploration"
    assert assembled_tiny["profile_context"]["skill_proficiency_map"]["python"]["not_learned_yet"] is True

    # 验证充足样本下的可追溯技能约束清单 (Section 5.2)
    rich_jobs = []
    for i in range(18):
        rich_jobs.append({
            "id": 4000 + i,
            "company": f"样本公司_{i}",
            "title": "2027届-后端与系统研发工程师" if i % 2 == 0 else "2027届-C++客户端研发工程师",
            "city": "上海",
            "jd_url": f"https://example.com/rich/{i}",
            "crawled_at": "2026-10-18",
            "jd_raw": (
                "岗位职责：负责核心软件模块设计、代码编写、多线程并发调优与单元测试。"
                "任职要求：熟练掌握 C/C++ 或 Python/Go；扎实掌握数据结构与算法、操作系统、计算机网络、Linux；"
                "熟悉 MySQL 数据库索引与 Redis 缓存；掌握 Git 与 CMake 构建；有 Docker 容器经验者优先。"
            ),
        })
    stats = skill_projects.aggregate_skills_by_category(rich_jobs, prof)
    constraints = skill_projects.build_skill_constraints_checklist(stats)
    assert len(constraints) >= 6
    for c in constraints:
        assert c["constraint_id"].startswith("SC-")
        assert c["skill_name"]
        assert isinstance(c["source_categories"], list)
        assert c["unique_job_count"] > 0 and c["sample_size"] == 18
        assert c["requirement_type"] in ("must", "bonus", "optional")
        assert c["engineering_capability"]
        assert c["required_training_depth"]
        assert len(c["evidence_quotes"]) >= 1
        assert c["evidence_quotes"][0]["jd_url"].startswith("https://example.com/rich/")


def test_v2_interest_driven_candidate_projects_and_coverage_audit_table(tmp_path):
    """验证 11.3 项目生成：6个实质差异项目、12项必填要素、3档技能覆盖审计表、保留硬性技能要求、跨语言开放推荐与AI边界。"""
    import skill_projects
    prof = profile_config.load_profile(ROOT / "profile.yaml")

    jobs = [
        {
            "id": 5000 + i,
            "company": f"综合科技_{i}",
            "title": "2027届-软件研发工程师",
            "city": "成都",
            "jd_url": f"https://example.com/proj/{i}",
            "crawled_at": "2026-10-18",
            "jd_raw": (
                "岗位职责：负责软件系统架构设计、编码实现、性能分析与自动化测试验证。"
                "任职要求：2027届本科及以上学历，熟悉 C/C++、Python、Go 或 Java；"
                "掌握数据结构与算法、Linux 操作系统、TCP/IP 网络、MySQL、Redis、Qt6、OpenGL、Git、CMake 与 pytest。"
            ),
        }
        for i in range(20)
    ]
    rep = skill_projects.update_or_get_skill_projects_report(
        jobs, profile=prof, conn=None, state_path=tmp_path / "sp_proj.json", force_refresh=True
    )
    projects = rep["candidate_projects"]
    assert 4 <= len(projects) <= 6

    # 验证不同兴趣切入点与跨语言覆盖（绝不仅推荐 C++ 项目）
    themes = {p["interest_theme_tag"] for p in projects}
    assert len(themes) == len(projects)
    langs_flat = " ".join(p["primary_language_stack"] for p in projects)
    for lang in ("C++", "Python", "Go", "Java"):
        assert lang in langs_flat

    valid_coverage_levels = {"完整覆盖", "部分覆盖", "未覆盖"}
    for p in projects:
        # 验证 Section 6.4 要求的 12 项核心说明全部存在且非空
        assert p["title"] and p["core_purpose"]
        assert p["why_matches_interest"]
        assert p["target_categories"] and p["target_roles"]
        assert p["natural_tech_stack_usage"]
        assert p["skill_training_locations"]
        assert p["mvp_scope"]["must_have"] and p["mvp_scope"]["out_of_scope_initially"]
        assert p["engineering_challenges"]
        assert p["acceptance_criteria"]
        assert p["difficulty_level"] and p["estimated_effort"] and p["main_risks"]
        assert p["final_deliverables"]
        assert p["staged_extensions"]
        assert p["data_vs_inference_boundary"]["data_backed_facts"]
        assert p["data_vs_inference_boundary"]["reasonable_inferences"]

        # 验证 Section 6.5 技能覆盖审计表：覆盖程度只能使用 完整覆盖/部分覆盖/未覆盖，且诚实暴露未覆盖缺口
        cov_table = p["skill_coverage_table"]
        assert len(cov_table) >= len(rep["skill_constraints"])
        constraint_ids_in_table = {r["constraint_id"] for r in cov_table}
        for sc in rep["skill_constraints"]:
            assert sc["constraint_id"] in constraint_ids_in_table
        levels_seen = {row["coverage_level"] for row in cov_table}
        assert levels_seen.issubset(valid_coverage_levels)
        assert "完整覆盖" in levels_seen
        assert "未覆盖" in levels_seen
        for row in cov_table:
            if row["coverage_level"] == "完整覆盖":
                assert row["project_task_mapping"] != "未覆盖（本项目不主攻该技术栈）"
                assert row["verification_criteria"]
            else:
                assert row["supplementary_advice"]

        # 验证 Section 6.6 AI 协同与个人成长边界三分离
        ai_b = p["ai_collab_boundary"]
        assert ai_b["user_must_do"] and ai_b["ai_can_accelerate"] and ai_b["verification_proof"]


def test_v2_project_selection_implementation_plan_iteration_and_persistence(tmp_path):
    """验证 11.4 交互流程与持久化：未选前不自动锁定、选定后生成实施计划、迭代反馈、区分刷新统计与重新生成、防无提示覆盖及 Flask API 闭环。"""
    import skill_projects
    import webapp
    prof = profile_config.load_profile(ROOT / "profile.yaml")
    state_file = tmp_path / "sp_interactive.json"
    db_file = tmp_path / "sp_interactive.db"
    conn = db.init_db(str(db_file))

    jobs = [
        {
            "id": 6000 + i,
            "company": f"持久化科技_{i}",
            "title": "2027届-后端与系统研发工程师",
            "city": "深圳",
            "jd_url": f"https://example.com/persist/{i}",
            "crawled_at": "2026-10-18",
            "jd_raw": (
                "岗位职责：负责核心服务开发、数据库索引优化与自动化测试。"
                "任职要求：2027届本科及以上学历，熟悉 C++/Python/Go、数据结构与算法、操作系统、网络、MySQL、Redis、Git。"
            ),
        }
        for i in range(18)
    ]

    # 1. 第一次进入模块：未选择前绝不自动锁定最终项目或自动生成完整开发计划 (Section 7.2)
    rep_init = skill_projects.update_or_get_skill_projects_report(
        jobs, profile=prof, conn=conn, state_path=state_file, force_refresh=True
    )
    assert rep_init["user_workspace"]["selected_project_id"] is None
    assert rep_init["user_workspace"]["active_implementation_plan"] is None

    # 2. 用户选择候选项目一 (Python/Go 跨媒介收藏系统)
    sel_res = skill_projects.select_candidate_project(
        "proj_anime_4d_collection_vault", state_path=state_file, conn=conn
    )
    assert sel_res["ok"] is True
    assert sel_res["selected_project_id"] == "proj_anime_4d_collection_vault"
    assert sel_res["user_workspace"]["active_implementation_plan"] is None

    # 3. 用户点击「生成实施计划」：验证包含 Section 7.3 全部 10 项要素（且针对 Python 未学自动注入阶段 0）
    plan_res = skill_projects.build_and_save_implementation_plan(
        project_id="proj_anime_4d_collection_vault",
        custom_focus="重点强化 MySQL 复合索引与 Redis 缓存一致性",
        profile=prof,
        state_path=state_file,
        conn=conn,
    )
    assert plan_res["ok"] is True
    plan = plan_res["implementation_plan"]
    assert plan["project_id"] == "proj_anime_4d_collection_vault"
    assert len(plan["stages"]) >= 4
    assert "阶段 0" in plan["stages"][0]["stage_no"]
    assert plan["deliverables"]
    assert plan["knowledge_and_materials"]
    assert plan["problem_driven_learning_path"]
    assert plan["user_core_tasks"]
    assert plan["ai_assisted_tasks"]
    assert plan["engineering_and_release_standards"]
    assert plan["stage_acceptance_summary"]
    assert plan["fallback_scope_reduction"]["reduced_mvp_plan"]
    assert plan["skill_coverage_and_gaps_audit"]["covered_items"]

    # 4. 用户提交迭代反馈：缩小 MVP + 强化网络并发 + 双项目对比 (Section 7.4)
    iter1 = skill_projects.submit_project_iteration(
        project_id="proj_anime_4d_collection_vault",
        action_type="shrink_mvp",
        feedback_text="课业较忙，希望压缩到2周内完成核心闭环",
        profile=prof,
        state_path=state_file,
        conn=conn,
    )
    assert iter1["ok"] is True
    assert len(iter1["user_workspace"]["iteration_history"]) == 1

    iter_cmp = skill_projects.submit_project_iteration(
        project_id="proj_anime_4d_collection_vault",
        action_type="compare_two",
        compare_with_id="proj_live_shader_particle_desk",
        profile=prof,
        state_path=state_file,
        conn=conn,
    )
    assert iter_cmp["ok"] is True
    assert iter_cmp["comparison"]["project_a"]["project_id"] == "proj_anime_4d_collection_vault"
    assert iter_cmp["comparison"]["project_b"]["project_id"] == "proj_live_shader_particle_desk"

    # 5. 验证「仅刷新技能统计」不会覆盖用户已选项目、实施计划与迭代历史 (Section 3.2.6 & 8.5)
    jobs.append({
        "id": 6999,
        "company": "新增岗位企",
        "title": "2027届-Go后端开发工程师",
        "city": "上海",
        "jd_url": "https://example.com/persist/new999",
        "crawled_at": "2026-10-19",
        "jd_raw": "负责分布式微服务研发，要求熟悉 Golang、MySQL、Redis、Linux、Docker、Git。" * 2,
    })
    stats_refreshed = skill_projects.refresh_skill_stats_only(
        jobs, profile=prof, state_path=state_file, conn=conn
    )
    assert stats_refreshed["user_workspace"]["selected_project_id"] == "proj_anime_4d_collection_vault"
    assert stats_refreshed["user_workspace"]["active_implementation_plan"] is not None
    assert len(stats_refreshed["user_workspace"]["iteration_history"]) == 2

    # 6. 验证「重新生成项目方案（未勾选重置 confirm_overwrite=False）」同样保护用户已选项目并归档历史快照
    regen_protected = skill_projects.update_or_get_skill_projects_report(
        jobs, profile=prof, conn=conn, state_path=state_file, force_refresh=True, confirm_overwrite=False
    )
    assert regen_protected["user_workspace"]["selected_project_id"] == "proj_anime_4d_collection_vault"
    assert regen_protected["user_workspace"]["active_implementation_plan"] is not None
    assert len(regen_protected["user_workspace"]["archived_workspaces"]) >= 1
    assert "自动为您保留了已选项目" in regen_protected["user_workspace"]["pending_update_notice"]

    # 验证 SQLite 表 skill_project_user_workspace 中持久化一致
    db_ws_row = conn.execute(
        "SELECT selected_project_id, plan_generated_at FROM skill_project_user_workspace WHERE workspace_key='default'"
    ).fetchone()
    assert db_ws_row is not None
    assert db_ws_row[0] == "proj_anime_4d_collection_vault"
    assert db_ws_row[1] != ""
    conn.close()


def test_v2_cost_control_ai_failure_fallback_and_secret_safety(tmp_path):
    """验证 11.5 成本与异常：普通进入页面命中缓存不调模型、模型失败保留已有结果并显示脱敏错误、密钥不泄露。"""
    import skill_projects
    prof = profile_config.load_profile(ROOT / "profile.yaml")
    state_file = tmp_path / "sp_cost.json"

    jobs = [
        {
            "id": 7000 + i,
            "company": f"成本测试企_{i}",
            "title": "2027届-C++研发工程师",
            "city": "武汉",
            "jd_url": f"https://example.com/cost/{i}",
            "crawled_at": "2026-10-18",
            "jd_raw": "负责C++系统研发，要求熟悉 C/C++、Linux、数据结构与算法、MySQL、CMake、Git。" * 2,
        }
        for i in range(16)
    ]

    ai_call_counter = {"count": 0}
    def _counting_ai_caller(sys_p, usr_p):
        ai_call_counter["count"] += 1
        return "AI定制化建议：优先实现核心存储与索引模块。"

    # 首次生成（不开启 allow_ai 时 0 模型调用）
    r1 = skill_projects.update_or_get_skill_projects_report(
        jobs, profile=prof, state_path=state_file, force_refresh=True, allow_ai=False
    )
    assert ai_call_counter["count"] == 0
    assert r1["version_info"]["cache_hit"] is False

    # 普通进入页面（force_refresh=False）：直接读取缓存，不重复计算也不调用模型
    r2 = skill_projects.update_or_get_skill_projects_report(
        jobs, profile=prof, state_path=state_file, force_refresh=False
    )
    assert r2["version_info"]["cache_hit"] is True
    assert ai_call_counter["count"] == 0

    # 模型调用失败且抛出的异常包含敏感 API Key 时：必须脱敏错误、保留全部 6 个有效候选方案
    def _failing_ai_with_secret(sys_p, usr_p):
        raise RuntimeError("DeepSeek API Error with key sk-1234567890abcdefSECRETKEY")

    r_fail = skill_projects.update_or_get_skill_projects_report(
        jobs,
        profile=prof,
        state_path=state_file,
        force_refresh=True,
        allow_ai=True,
        ai_caller=_failing_ai_with_secret,
    )
    assert r_fail["version_info"]["generation_status"] == "fallback_kept_valid"
    assert "sk-1234567890abcdef" not in r_fail["version_info"]["last_error"]
    assert "[REDACTED_SECRET]" in r_fail["version_info"]["last_error"]
    assert len(r_fail["candidate_projects"]) == 6


def test_v2_webapp_skill_projects_api_endpoints_and_concurrency_lock(tmp_path, monkeypatch):
    """验证 11.1 & 11.5：Flask 本地服务端 /api/skill-projects/* 全套交互接口与重复点击并发锁保护。"""
    import skill_projects
    import webapp

    test_db = tmp_path / "webapp_sp.db"
    test_state = tmp_path / "webapp_sp_state.json"
    conn = db.init_db(str(test_db))
    for i in range(16):
        db.upsert_job(conn, {
            "company": f"接口测试企_{i}",
            "title": "2027届-C++/Go后端开发工程师",
            "city": "上海",
            "job_type": "校招",
            "jd_url": f"https://example.com/webapp_sp/{i}",
            "published_at": "2026-10-18",
            "source": "接口测试",
            "crawled_at": "2026-10-18",
            "cohort": 2027,
            "cohort_status": "confirmed",
            "jd_raw": "负责后端与客户端模块研发，要求熟悉 C/C++、Go、Python、MySQL、Redis、Linux、数据结构与算法、Git。" * 2,
        })
    conn.close()

    monkeypatch.setattr(webapp, "DB_PATH", str(test_db))
    monkeypatch.setattr(webapp, "_DB_PATH", str(test_db))
    monkeypatch.setattr(skill_projects, "DEFAULT_SKILL_PROJECT_STATE_PATH", test_state)
    webapp._page_cache.update(signature=None, snapshot=None, html=None)

    client = webapp.app.test_client()

    # 1. GET /api/skill-projects/state
    r_state = client.get("/api/skill-projects/state")
    assert r_state.status_code == 200
    d_state = r_state.get_json()
    assert d_state["ok"] is True
    assert d_state["projects_count"] == 6
    assert d_state["user_workspace"]["selected_project_id"] is None

    # 2. POST /api/skill-projects/select
    r_sel = client.post("/api/skill-projects/select", json={"project_id": "proj_live_shader_particle_desk"})
    assert r_sel.status_code == 200
    d_sel = r_sel.get_json()
    assert d_sel["ok"] is True
    assert d_sel["selected_project_id"] == "proj_live_shader_particle_desk"

    # 3. POST /api/skill-projects/generate-plan
    r_plan = client.post("/api/skill-projects/generate-plan", json={
        "project_id": "proj_live_shader_particle_desk",
        "custom_focus": "强化 RenderDoc GPU 帧分析与 Qt6 多线程通信",
    })
    assert r_plan.status_code == 200
    d_plan = r_plan.get_json()
    assert d_plan["ok"] is True
    assert d_plan["implementation_plan"]["project_id"] == "proj_live_shader_particle_desk"
    assert len(d_plan["implementation_plan"]["stages"]) >= 3

    # 4. POST /api/skill-projects/iterate
    r_iter = client.post("/api/skill-projects/iterate", json={
        "project_id": "proj_live_shader_particle_desk",
        "action_type": "boost_testing_ci",
        "feedback_text": "增加 GTest 与 ASan 自动化回归测试",
    })
    assert r_iter.status_code == 200
    d_iter = r_iter.get_json()
    assert d_iter["ok"] is True
    assert len(d_iter["user_workspace"]["iteration_history"]) == 1

    # 5. POST /api/skill-projects/refresh-stats (仅更新技能统计，不冲掉已选项目与实施计划)
    r_ref = client.post("/api/skill-projects/refresh-stats")
    assert r_ref.status_code == 200
    d_ref = r_ref.get_json()
    assert d_ref["ok"] is True
    assert d_ref["action"] == "refresh_stats_only"
    assert d_ref["user_workspace"]["selected_project_id"] == "proj_live_shader_particle_desk"
    assert d_ref["user_workspace"]["active_implementation_plan"] is not None

    # 6. 验证重复点击并发锁保护：当 _skill_proj_lock 被占用时，返回 429 busy，绝不启动重复任务
    webapp._skill_proj_lock.acquire()
    try:
        r_busy = client.post("/api/skill-projects/regenerate", json={"mode": "regenerate"})
        assert r_busy.status_code == 429
        assert r_busy.get_json()["busy"] is True
    finally:
        webapp._skill_proj_lock.release()
