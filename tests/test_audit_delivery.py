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

    # 2. 本地五维规则评分岗位（完整 JD，本科可投 C++ 客户端岗）
    local_job = {
        "company": "金山办公",
        "title": "2027届-C++跨平台客户端开发工程师",
        "city": "珠海",
        "job_type": "校招",
        "jd_url": "https://example.com/wps/cpp",
        "jd_raw": (
            "岗位职责：负责 WPS 桌面客户端核心模块研发与性能优化。"
            "任职要求：本科及以上学历，熟悉 C/C++、STL、数据结构与算法、操作系统与 Linux/Windows 编程，双休福利完善。" * 2
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
