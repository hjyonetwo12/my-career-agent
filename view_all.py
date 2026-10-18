"""一次性查看全部已抓取/已分析的岗位，检测 profile.yaml 变化、同步五维评分并更新综合建议，生成累计报告并打开。

用法：
    python view_all.py
"""

import os
import webbrowser
from datetime import date
from pathlib import Path

import db as db_module
import job_filters
import reporter
from profile_config import load_profile


def fetch_all(conn) -> dict:
    """返回 DB 中所有活跃岗位 + 各自的分析（若有）+ 投递记录。"""
    items = db_module.get_all_jobs_with_analysis(conn)
    return {
        "date": "累计全部",
        "items": items,
        "applications": db_module.get_applications(conn),
    }


def main():
    db_path = Path(
        os.environ.get("JOBS_DB_PATH", Path(__file__).parent / "data" / "jobs.db")
    )
    if not db_path.exists():
        print(f"找不到数据库：{db_path}，请先运行 main.py")
        return

    profile_path = Path(
        os.environ.get("PROFILE_PATH", Path(__file__).parent / "profile.yaml")
    )
    try:
        profile = load_profile(profile_path)
    except ValueError as exc:
        print(f"[配置错误] {exc}")
        return

    conn = db_module.init_db(str(db_path))
    score_stats = db_module.sync_local_baseline_scores(conn, profile=profile)
    data = fetch_all(conn)
    conn.close()

    if not data["items"]:
        print("数据库里没有岗位记录")
        return

    force_refresh = os.environ.get("FORCE_ADVICE_REFRESH", "").strip().lower() in ("1", "true", "yes")
    advice = job_filters.update_or_get_comprehensive_advice(
        data["items"],
        profile=profile,
        force_refresh=force_refresh,
    )
    vinfo = advice.get("version_info") or {}
    print(
        f"[配置闭环] 个人画像版本 {vinfo.get('profile_version_id')} → "
        f"综合建议版本 {vinfo.get('advice_version_id')} ({vinfo.get('strategy_label')})"
    )

    today = date.today().isoformat()
    report_path = reporter.generate_report(today, data, "reports", out_name="index")
    rmeta = data.get("report_meta") or {}

    print(f"\n累计报告已生成：{report_path}")
    print(f"  报告版本：{rmeta.get('report_version_id', '—')} (生成时间: {rmeta.get('generated_at', '—')})")
    print(f"  活跃岗位总数：{len(data['items'])} 个（确认27届展示：{rmeta.get('current_27_jobs', 0)} 个）")
    analyzed = sum(1 for i in data["items"] if i.get("analysis"))
    print(
        f"  已评分岗位：{analyzed} 个（AI精析保留 {score_stats.get('ai_pro_kept', 0)} 个 / "
        f"五维规则评分 {score_stats.get('local_scored_upserted', 0) + score_stats.get('local_scored_cached', 0)} 个 / "
        f"JD待补全 {score_stats.get('jd_incomplete_skipped', 0)} 个 / "
        f"资格不符 {score_stats.get('ineligible_skipped', 0)} 个）"
    )

    if not os.environ.get("CI"):
        webbrowser.open(f"file:///{report_path.replace(os.sep, '/')}")


if __name__ == "__main__":
    main()
