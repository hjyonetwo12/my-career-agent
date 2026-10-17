"""一次性查看全部已抓取/已分析的岗位，检测 profile.yaml 变化并更新综合建议，生成累计报告并打开。

用法：
    python view_all.py
"""

import os
import shutil
import webbrowser
from datetime import date
from pathlib import Path

import db as db_module
import job_filters
import reporter
from profile_config import load_profile


def fetch_all(conn) -> dict:
    """返回 DB 中所有岗位 + 各自的分析（若有）。"""
    items = db_module.get_all_jobs_with_analysis(conn)
    return {
        "date": "累计全部",
        "items": items,
        "applications": db_module.get_applications(conn),
    }


def main():
    db_path = Path(__file__).parent / "data" / "jobs.db"
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
    try:
        out_copy = Path(__file__).parent / "outputs" / "index.html"
        out_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report_path, out_copy)
    except Exception:
        pass

    print(f"\n累计报告已生成：{report_path}")
    print(f"  共 {len(data['items'])} 个岗位")
    analyzed = sum(1 for i in data["items"] if i["analysis"])
    print(f"  其中 {analyzed} 个已分析、{len(data['items']) - analyzed} 个未分析")

    if not os.environ.get("CI"):
        webbrowser.open(f"file:///{report_path.replace(os.sep, '/')}")


if __name__ == "__main__":
    main()
