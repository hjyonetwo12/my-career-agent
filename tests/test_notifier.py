import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import notifier


def _make_item(company, title, score=None, rec="考虑", url=None,
               advantages=None, gaps=None, city="深圳",
               last_seen_at=None):
    return {
        "job": {
            "company": company,
            "title": title,
            "city": city,
            "jd_url": url or f"https://example.com/{company}/{title}",
            "last_seen_at": last_seen_at,
            "cohort": 2027,
            "cohort_status": "confirmed",
        },
        "analysis": {
            "match_score": score,
            "advantages": advantages or [],
            "gaps": gaps or [],
            "summary": "",
            "recommendation": rec,
        } if score is not None else None,
    }


def test_skip_when_no_webhook(monkeypatch):
    monkeypatch.delenv("FEISHU_WEBHOOK", raising=False)
    with patch("notifier.requests.post") as mock_post:
        notifier.send([], [], {"date": "2026-05-15"})
    mock_post.assert_not_called()


def test_intern_filter_does_not_match_international():
    assert notifier._is_intern_title("Software Engineer Intern")
    assert notifier._is_intern_title("推荐算法实习生")
    assert not notifier._is_intern_title("International E-commerce Engineer")


def test_payload_structure_and_new_jobs(monkeypatch):
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/dummy")

    new_jobs = [{
        "company": "宇树", "title": "视觉算法工程师",
        "city": "杭州", "jd_url": "https://www.unitree.com/position/123",
        "cohort": 2027, "cohort_status": "confirmed",
    }]
    all_jobs = [
        _make_item("宇树", "视觉算法工程师", score=88, rec="推荐",
                   url="https://www.unitree.com/position/123",
                   advantages=["ROS", "深度相机"], gaps=["CUDA"]),
    ]
    report_data = {"date": "2026-05-15"}

    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 0}
        notifier.send(new_jobs, all_jobs, report_data)

    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["msg_type"] == "post"
    post = payload["content"]["post"]["zh_cn"]
    assert "2026-05-15" in post["title"]
    flat = json.dumps(post["content"], ensure_ascii=False)
    assert "今日新增推荐 (1 个)" in flat
    assert "视觉算法工程师" in flat
    assert "ROS" in flat
    assert "CUDA" in flat


def test_payload_stays_below_feishu_limit_with_many_long_jobs():
    long_text = "机器学习岗位要求" * 100
    new_jobs = [
        {
            "company": f"公司{i}{long_text}",
            "title": f"岗位{i}{long_text}",
            "city": long_text,
            "jd_url": f"https://example.com/{i}",
        }
        for i in range(100)
    ]
    all_jobs = [
        _make_item(
            job["company"],
            job["title"],
            score=90,
            rec="推荐",
            url=job["jd_url"],
            advantages=[long_text],
            gaps=[long_text],
            city=job["city"],
        )
        for job in new_jobs
    ]

    payload = notifier._build_payload(new_jobs, all_jobs, {"date": "2026-05-15"})
    encoded_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    assert encoded_size < 30 * 1024


def test_previous_cohort_new_jobs_are_not_sent():
    old_job = {
        "company": "旧届公司",
        "title": "2026届校招-软件开发工程师",
        "city": "北京",
        "jd_url": "old-job",
    }
    current_job = {
        "company": "当前公司",
        "title": "软件开发工程师",
        "city": "北京",
        "jd_url": "current-job",
        "cohort": 2027,
        "cohort_status": "confirmed",
    }
    all_jobs = [
        _make_item("旧届公司", old_job["title"], score=90, rec="推荐", url="old-job"),
        _make_item("当前公司", current_job["title"], score=90, rec="推荐", url="current-job"),
    ]

    payload = notifier._build_payload([old_job, current_job], all_jobs, {"date": "2026-05-15"})
    flat = json.dumps(payload["content"]["post"]["zh_cn"]["content"], ensure_ascii=False)

    assert "当前公司" in flat
    assert "旧届公司" not in flat


def test_high_score_filter_and_sort(monkeypatch):
    """传入混合分数，断言只有 score>=60 的进推送，且按分数降序。"""
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/dummy")
    all_jobs = [
        _make_item("A", "低分岗", score=30, rec="不推荐"),     # 应被过滤
        _make_item("A", "中分岗", score=65, rec="考虑"),
        _make_item("A", "实习生", score=85, rec="推荐"),      # 实习应被过滤
        _make_item("A", "高分岗", score=85, rec="推荐"),
    ]
    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 0}
        notifier.send([], all_jobs, {"date": "2026-05-15"})

    flat = json.dumps(
        mock_post.call_args.kwargs["json"]["content"]["post"]["zh_cn"]["content"],
        ensure_ascii=False,
    )
    # 高分岗（85）应在中分岗（65）之前出现
    idx_high = flat.find("高分岗")
    idx_mid = flat.find("中分岗")
    assert idx_high != -1 and idx_mid != -1
    assert idx_high < idx_mid
    # 低分 + 实习不在
    assert "低分岗" not in flat
    assert "实习生" not in flat
    # 共 2 个高分岗位
    assert "共 2 个" in flat


def test_new_jobs_section_filters_irrelevant(monkeypatch):
    """今日新增段同样过滤：实习/不推荐/低分应被剔除。"""
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/dummy")
    # 5 个新增：1 个高分相关 + 1 个实习 + 1 个不推荐 + 1 个低分 + 1 个未分析
    new_jobs = [
        {"company": "字节", "title": "视觉算法工程师", "city": "北京", "jd_url": "u1",
         "cohort": 2027, "cohort_status": "confirmed"},
        {"company": "字节", "title": "推荐算法实习生", "city": "北京", "jd_url": "u2"},
        {"company": "字节", "title": "电商运营", "city": "上海", "jd_url": "u3"},
        {"company": "字节", "title": "服务器测试", "city": "深圳", "jd_url": "u4"},
        {"company": "字节", "title": "新岗位待分析", "city": "杭州", "jd_url": "u5"},
    ]
    all_jobs = [
        _make_item("字节", "视觉算法工程师", score=85, rec="推荐", url="u1"),
        _make_item("字节", "推荐算法实习生", score=80, rec="推荐", url="u2"),
        _make_item("字节", "电商运营", score=5, rec="不推荐", url="u3"),
        _make_item("字节", "服务器测试", score=40, rec="考虑", url="u4"),
        _make_item("字节", "新岗位待分析", url="u5"),  # analysis=None
    ]
    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 0}
        notifier.send(new_jobs, all_jobs, {"date": "2026-05-15"})

    flat = json.dumps(
        mock_post.call_args.kwargs["json"]["content"]["post"]["zh_cn"]["content"],
        ensure_ascii=False,
    )
    # 应展示：仅 视觉算法工程师
    assert "视觉算法工程师" in flat
    # 应过滤：
    assert "推荐算法实习生" not in flat  # 实习
    assert "电商运营" not in flat        # 不推荐
    assert "服务器测试" not in flat      # 低分
    assert "新岗位待分析" not in flat    # 未分析
    # header 应显示过滤数量
    assert "已过滤 4 个不相关" in flat
    # 至少有 "今日新增推荐 (1 个)"
    assert "今日新增推荐 (1 个)" in flat


def test_top_gaps_only_from_high_score_jobs():
    """低分岗位的 gaps 不应进入 Top5（只取 score>=60 的）。"""
    items = [
        _make_item("A", "高分岗", score=85, gaps=["CUDA", "TensorRT"]),
        _make_item("B", "中分岗", score=65, gaps=["CUDA", "ROS"]),
        _make_item("C", "低分岗", score=30, gaps=["秘书技能", "财务"]),  # 不应计入
    ]
    top = notifier._collect_top_gaps(items)
    gaps = {g for g, _ in top}
    assert "CUDA" in gaps and "TensorRT" in gaps and "ROS" in gaps
    assert "秘书技能" not in gaps and "财务" not in gaps
    # CUDA 应出现 2 次
    cuda_count = next(c for g, c in top if g == "CUDA")
    assert cuda_count == 2


def test_top_gaps_filters_noise_keywords():
    """缺口词是'无 XX'或包含'专业方向'等噪声词时应被过滤。"""
    items = [
        _make_item("A", "x", score=80, gaps=["无明显缺口", "CUDA"]),
        _make_item("B", "y", score=80, gaps=["专业方向不匹配", "TensorRT"]),
        _make_item("C", "z", score=80, gaps=["不限专业", "无", "学历要求不符"]),
    ]
    top = notifier._collect_top_gaps(items)
    gaps = {g for g, _ in top}
    assert gaps == {"CUDA", "TensorRT"}


def test_truncate_city_long_text():
    """城市超过 20 字符应截断追加 …。"""
    short = notifier._truncate("北京")
    assert short == "北京"
    long = notifier._truncate("深圳/上海/北京/广州/重庆/芜湖/长春/保定/合肥/武汉")
    assert long.endswith("…")
    assert len(long) == 21  # 20 + …


def test_top_gaps_rendered_as_separate_bullet_lines(monkeypatch):
    """高频缺口 Top5 每条单独一行，格式 '  • 技能 (次数)'。"""
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/dummy")
    items = [
        _make_item("A", "x", score=85, gaps=["CUDA", "TensorRT"]),
        _make_item("B", "y", score=80, gaps=["CUDA"]),
    ]
    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 0}
        notifier.send([], items, {"date": "2026-05-15"})

    content = mock_post.call_args.kwargs["json"]["content"]["post"]["zh_cn"]["content"]
    rows_text = [
        "".join(seg.get("text", "") for seg in row)
        for row in content
    ]
    # 标题行
    assert any(t == "高频缺口 Top5:" for t in rows_text)
    # CUDA 应出现 2 次（计数）和 TensorRT 应出现 1 次
    assert any(t == "  • CUDA (2)" for t in rows_text)
    assert any(t == "  • TensorRT (1)" for t in rows_text)
    # 不应再有合并的单行格式
    assert not any("CUDA(2)" in t and "TensorRT" in t for t in rows_text)


def test_high_score_section_inserts_blank_lines(monkeypatch):
    """高分岗位每条之间应有空行。"""
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/dummy")
    items = [
        _make_item("A", "岗1", score=85),
        _make_item("B", "岗2", score=80),
    ]
    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 0}
        notifier.send([], items, {"date": "2026-05-15"})

    content = mock_post.call_args.kwargs["json"]["content"]["post"]["zh_cn"]["content"]
    # 找两条高分岗位的行，它们之间应该有 1 行空文本
    titles = [
        i for i, row in enumerate(content)
        if any("岗" in seg.get("text", "") for seg in row)
    ]
    # 第二个岗位之前的那一行应该是空（分隔）
    sep_idx = titles[1] - 1
    sep_row = content[sep_idx]
    assert sep_row == [{"tag": "text", "text": ""}]


def test_report_url_resolves_explicit_or_github_pages_and_never_uses_hardcoded_author_domain(monkeypatch):
    """报告链接优先使用 REPORT_BASE_URL，次选由 GITHUB_REPOSITORY 自动推导 GitHub Pages，绝不回退原作者旧域名。"""
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/dummy")
    monkeypatch.delenv("REPORT_BASE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "student-user/career-agent")
    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 0}
        notifier.send([], [], {"date": "2026-05-15", "report_meta": {"report_version_id": "rep-1234abcd"}})

    flat = json.dumps(
        mock_post.call_args.kwargs["json"]["content"]["post"]["zh_cn"]["content"],
        ensure_ascii=False,
    )
    assert "https://student-user.github.io/career-agent/index.html?v=rep-1234abcd" in flat
    assert "career-agent-4z4.pages.dev" not in flat
    assert "2026-05-15.html" not in flat


def test_verify_deployed_report_url_and_feishu_404_prevention(monkeypatch, tmp_path):
    """验证部署后在线 URL 的真实 HTTP 校验（200 最新版本、200 缓存刷新、404 未开启 Pages 拦截）及显式「打开最新校招情报」链接文案。"""
    import reporter

    # 1. HTTP 200 且包含最新版本号 -> verified_latest
    with patch("notifier.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = '<html><head><meta name="career-agent-report-version" content="rep-99887766"></head><body>AI校招情报</body></html>'
        res_ok = notifier.verify_deployed_report_url(
            "https://student-user.github.io/career-agent/index.html?v=rep-99887766",
            expected_version_id="rep-99887766",
            retries=1,
            wait_seconds=0,
        )
    assert res_ok["reachable"] is True
    assert res_ok["version_matched"] is True
    assert res_ok["check_status"] == "verified_latest"

    # 2. HTTP 404（仓库未开启 Pages 或 Private 仓库不支持免费 Pages） -> http_404_pages_not_enabled，绝不误报部署成功
    with patch("notifier.requests.get") as mock_get_404:
        mock_get_404.return_value.status_code = 404
        mock_get_404.return_value.text = "404 There isn't a GitHub Pages site here."
        res_404 = notifier.verify_deployed_report_url(
            "https://student-user.github.io/career-agent/index.html?v=rep-99887766",
            expected_version_id="rep-99887766",
            retries=1,
            wait_seconds=0,
        )
    assert res_404["reachable"] is False
    assert res_404["check_status"] == "http_404_pages_not_enabled"

    payload_404 = notifier._build_payload(
        [],
        [],
        {"date": "2026-10-19", "deploy_status": "success"},
        run_stats={
            "deploy_status": "success",
            "report_version_id": "rep-99887766",
            "url_verify_status": res_404["check_status"],
            "url_verify_message": res_404["message"],
        },
    )
    flat_404 = json.dumps(payload_404, ensure_ascii=False)
    assert "HTTP 404" in flat_404
    assert "✅ 网页部署成功" not in flat_404

    # 3. 正常部署成功且校验通过时：包含明确的「打开最新校招情报」链接与动态 deployed_url 优先级
    payload_live = notifier._build_payload(
        [],
        [],
        {"date": "2026-10-19", "deploy_status": "success", "deployed_url": "https://custom-pages.github.io/my-agent/"},
        run_stats={
            "deploy_status": "success",
            "deployed_url": "https://custom-pages.github.io/my-agent/",
            "report_version_id": "rep-99887766",
            "url_verify_status": "verified_latest",
            "url_verify_message": res_ok["message"],
        },
    )
    flat_live = json.dumps(payload_live, ensure_ascii=False)
    assert "打开最新校招情报" in flat_live
    assert "https://custom-pages.github.io/my-agent/index.html?v=rep-99887766" in flat_live

    # 4. 验证公开报告脱敏模式 (PUBLIC_REPORT_MODE=1) 不泄露私人投递记录与学校名称
    monkeypatch.setenv("PUBLIC_REPORT_MODE", "1")
    pub_dir = tmp_path / "pub_reports"
    sample_data = {
        "date": "2026-10-19",
        "items": [],
        "applications": [
            {"id": 1, "job_id": 1, "company": "私密投递公司", "title": "私密岗位", "current_stage": "interview_1", "note": "私人面试薪资备注12345", "events": []}
        ],
    }
    pub_html_path = reporter.generate_report("2026-10-19", sample_data, reports_dir=str(pub_dir), out_name="index")
    pub_html = Path(pub_html_path).read_text(encoding="utf-8")
    assert "私人面试薪资备注12345" not in pub_html
    assert "东北大学" not in pub_html
    assert (pub_dir / ".nojekyll").exists()
    monkeypatch.delenv("PUBLIC_REPORT_MODE", raising=False)
