"""本地投递记录管理器（可编辑）。

部署到 gh-pages 的报告是只读的；本地双击「管理.bat」起这个 Flask 服务，
同一套页面会冒出编辑控件（我投了 / 改阶段 / 手动添加 / 删除），写回 data/applications.json。
纯本地运行、永不部署，不影响 CI 与静态报告。

录入/改动投递后会**自动**把 data/applications.json 提交并推送到 origin/main，
触发 sync_report 工作流，手机端几分钟内即可看到（设 WEBAPP_AUTO_SYNC=0 可关闭）。

用法：
    python webapp.py
"""

import os
import shutil
import subprocess
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path

from flask import Flask, jsonify, redirect, request

import db as db_module
import reporter

_ROOT = Path(__file__).parent
DB_PATH = str(_ROOT / "data" / "jobs.db")
_PORT = int(os.environ.get("WEBAPP_PORT", "5000"))
_BUILD_ID = "company-cohort-ranking-v3"

# 自动把投递记录推到远端（让手机端的报告同步）。设 WEBAPP_AUTO_SYNC=0 关闭。
_AUTO_SYNC = os.environ.get("WEBAPP_AUTO_SYNC", "1") != "0"
_APPS_REL = "data/applications.json"
_sync_lock = threading.Lock()
_page_cache_lock = threading.RLock()
_page_cache = {"signature": None, "snapshot": None, "html": None}

app = Flask(__name__)


def _conn():
    return db_module.init_db(DB_PATH)


def _data_signature():
    """Return a cheap cache key that changes whenever report state changes."""
    paths = (Path(DB_PATH), db_module.APPLICATIONS_PATH, _ROOT / "profile.yaml", _ROOT / "data" / "advice_state.json", _ROOT / "data" / "skill_projects_state.json")
    signature = [date.today().isoformat()]
    for path in paths:
        try:
            stat = path.stat()
            signature.append((stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            signature.append((0, 0))
    return tuple(signature)


def _get_snapshot():
    signature = _data_signature()
    with _page_cache_lock:
        if _page_cache["signature"] == signature and _page_cache["snapshot"] is not None:
            return _page_cache["snapshot"], signature

        conn = _conn()
        try:
            items = db_module.get_all_jobs_with_analysis(conn)
            applications = db_module.get_applications(conn)
        finally:
            conn.close()
        current, previous, unknown, hidden = reporter._filter_items(items)
        app_index = {
            item["job_id"]: item
            for item in applications
            if item.get("job_id") is not None
        }
        snapshot = {
            "items": items,
            "current": current,
            "previous": previous,
            "unknown": unknown,
            "hidden": hidden,
            "applications": applications,
            "app_index": app_index,
        }
        _page_cache.update(signature=signature, snapshot=snapshot, html=None)
        return snapshot, signature


def _run_git(args, timeout=120):
    """跑一条 git 子命令（非交互、不弹编辑器/凭据框）。"""
    env = {**os.environ, "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"}
    # 强制 UTF-8 解码：Windows 默认 locale 是 GBK，git 输出含 UTF-8 字节会解码崩溃。
    return subprocess.run(
        ["git", *args], cwd=str(_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def _git_sync():
    """提交并推送 data/applications.json 到 origin/main。

    严格遵守 CLAUDE.md「Git 工作流」：用 git pull(merge) + push，**不 rebase**。
    只 `git add` applications.json——投递记录已与 jobs.db 解耦，正常情况下不碰 db、
    不会覆盖云端当天爬取（仅当你本地另跑过 main.py 改了 db 才会触发 db 的 merge=ours）。
    任何失败都只打印提示、保留本地提交，绝不做破坏性操作。
    """
    if not _AUTO_SYNC:
        return
    if shutil.which("git") is None:
        print("[sync] 未找到 git，跳过自动推送（可手动 push data/applications.json）")
        return
    with _sync_lock:
        try:
            if _run_git(["rev-parse", "--is-inside-work-tree"]).returncode != 0:
                print("[sync] 不在 git 仓库内，跳过自动推送")
                return
            _run_git(["add", _APPS_REL])
            if _run_git(["diff", "--cached", "--quiet"]).returncode == 0:
                return  # 无改动，无需提交
            msg = f"chore(apps): 更新投递记录 {datetime.now():%Y-%m-%d %H:%M}"
            r = _run_git(["commit", "-m", msg])
            if r.returncode != 0:
                print("[sync] commit 失败：", r.stderr.strip())
                return
            r = _run_git(["pull", "--no-rebase", "--no-edit", "origin", "main"])
            if r.returncode != 0:
                print("[sync] pull 失败（已本地提交，稍后手动 push）：", (r.stderr or r.stdout).strip())
                return
            r = _run_git(["push", "origin", "main"])
            if r.returncode != 0:
                print("[sync] push 失败（已本地提交，检查网络/凭据后手动 push）：", (r.stderr or r.stdout).strip())
                return
            print("[sync] ✅ 已推送投递记录，手机端几分钟内更新")
        except subprocess.TimeoutExpired:
            print("[sync] git 超时（已本地提交，稍后手动 push）")
        except Exception as e:  # noqa: BLE001 — 后台线程，任何异常都不应崩进程
            print("[sync] 异常（已本地提交，稍后手动 push）：", e)


def _schedule_sync():
    """后台线程跑 git 同步，不阻塞页面响应。"""
    threading.Thread(target=_git_sync, daemon=True).start()


@app.route("/")
def index():
    snapshot, signature = _get_snapshot()
    with _page_cache_lock:
        if _page_cache["signature"] == signature and _page_cache["html"] is not None:
            return _page_cache["html"]
        data = {
            "items": snapshot["items"],
            "applications": snapshot["applications"],
            "date": date.today().isoformat(),
        }
        page = reporter.render_html(
            date.today().isoformat(), data, editable=True, server_pagination=True
        )
        _page_cache["html"] = page
        return page




@app.route("/api/advice/regenerate", methods=["POST", "GET"])
def regenerate_advice_api():
    """手动触发重新生成综合建议（支持是否调用 AI 的显式控制）。"""
    import job_filters
    from profile_config import load_profile
    snapshot, _ = _get_snapshot()
    use_ai = request.args.get("ai", "0") == "1"
    try:
        prof = load_profile(_ROOT / "profile.yaml")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    advice = job_filters.update_or_get_comprehensive_advice(
        snapshot["items"],
        profile=prof,
        force_refresh=True,
        allow_ai=use_ai,
    )
    with _page_cache_lock:
        _page_cache["html"] = None
    return jsonify({
        "ok": True,
        "version_info": advice.get("version_info", {}),
        "version_diff_summary": advice.get("version_diff_summary", {}),
    })


_DB_PATH = DB_PATH
_skill_proj_lock = threading.Lock()


@app.route("/api/skill-projects/state", methods=["GET"])
def get_skill_projects_state_api():
    """获取「技能栈与实现项目」模块当前完整状态（含技能统计、约束清单、候选项目、已选项目与实施计划）。"""
    import skill_projects
    from profile_config import load_profile
    snapshot, _ = _get_snapshot()
    try:
        prof = load_profile(_ROOT / "profile.yaml")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    conn = _conn()
    try:
        res = skill_projects.update_or_get_skill_projects_report(
            snapshot["items"], profile=prof, conn=conn, force_refresh=False
        )
    finally:
        conn.close()
    return jsonify({
        "ok": True,
        "version_info": res.get("version_info", {}),
        "user_workspace": res.get("user_workspace", {}),
        "skill_constraints_count": len(res.get("skill_constraints") or []),
        "projects_count": len(res.get("tier_b_projects") or []),
    })


@app.route("/api/skill-projects/refresh-stats", methods=["POST", "GET"])
def refresh_skill_stats_only_api():
    """仅刷新技能统计与约束清单（不重新生成项目方案，不覆盖用户已选项目和实施计划）。"""
    import skill_projects
    from profile_config import load_profile
    if not _skill_proj_lock.acquire(blocking=False):
        return jsonify({"ok": False, "busy": True, "error": "已有技能统计或项目生成任务正在进行中，请勿重复点击"}), 429
    try:
        snapshot, _ = _get_snapshot()
        try:
            prof = load_profile(_ROOT / "profile.yaml")
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        conn = _conn()
        try:
            res = skill_projects.refresh_skill_stats_only(
                snapshot["items"], profile=prof, conn=conn
            )
        finally:
            conn.close()
        with _page_cache_lock:
            _page_cache["html"] = None
        return jsonify({
            "ok": True,
            "action": "refresh_stats_only",
            "version_info": res.get("version_info", {}),
            "user_workspace": res.get("user_workspace", {}),
            "skill_constraints_count": len(res.get("skill_constraints") or []),
            "projects_count": len(res.get("tier_b_projects") or []),
        })
    finally:
        _skill_proj_lock.release()


@app.route("/api/skill-projects/refresh", methods=["POST", "GET"])
@app.route("/api/skill-projects/regenerate", methods=["POST", "GET"])
def refresh_skill_projects_api():
    """手动触发重新生成候选项目方案（带防重复点击锁与已选项目防无提示覆盖保护）。"""
    import skill_projects
    from profile_config import load_profile
    if not _skill_proj_lock.acquire(blocking=False):
        return jsonify({"ok": False, "busy": True, "error": "正在生成候选项目方案，请勿重复点击"}), 429
    try:
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get("mode") or request.args.get("mode") or "regenerate").strip()
        confirm_overwrite = bool(payload.get("confirm_overwrite") or request.args.get("confirm_overwrite") == "1")
        use_ai = bool(payload.get("allow_ai") or request.args.get("ai") == "1")
        snapshot, _ = _get_snapshot()
        try:
            prof = load_profile(_ROOT / "profile.yaml")
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        conn = _conn()
        try:
            if mode == "stats_only":
                res = skill_projects.refresh_skill_stats_only(
                    snapshot["items"], profile=prof, conn=conn
                )
            else:
                res = skill_projects.update_or_get_skill_projects_report(
                    snapshot["items"],
                    profile=prof,
                    conn=conn,
                    force_refresh=True,
                    allow_ai=use_ai,
                    confirm_overwrite=confirm_overwrite,
                )
        finally:
            conn.close()
        with _page_cache_lock:
            _page_cache["html"] = None
        return jsonify({
            "ok": True,
            "action": mode,
            "version_info": res.get("version_info", {}),
            "user_workspace": res.get("user_workspace", {}),
            "projects_count": len(res.get("tier_b_projects") or []),
        })
    finally:
        _skill_proj_lock.release()


@app.route("/api/skill-projects/select", methods=["POST"])
def select_skill_project_api():
    """用户选择某个感兴趣的候选项目并持久化保存（尚未点击生成实施计划前不自动塞入冗长计划）。"""
    import skill_projects
    payload = request.get_json(silent=True) or {}
    project_id = str(payload.get("project_id") or request.form.get("project_id") or "").strip()
    if not project_id:
        return jsonify({"ok": False, "error": "缺少 project_id 参数"}), 400
    conn = _conn()
    try:
        res = skill_projects.select_candidate_project(project_id, conn=conn)
    finally:
        conn.close()
    if not res.get("ok"):
        return jsonify(res), 400
    with _page_cache_lock:
        _page_cache["html"] = None
    return jsonify(res)


@app.route("/api/skill-projects/generate-plan", methods=["POST"])
def generate_skill_project_plan_api():
    """针对用户已选择的候选项目生成 10 维分阶段实施计划并持久化绑定。"""
    import skill_projects
    from profile_config import load_profile
    payload = request.get_json(silent=True) or {}
    project_id = str(payload.get("project_id") or request.form.get("project_id") or "").strip() or None
    custom_focus = str(payload.get("custom_focus") or request.form.get("custom_focus") or "").strip()
    try:
        prof = load_profile(_ROOT / "profile.yaml")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    conn = _conn()
    try:
        res = skill_projects.build_and_save_implementation_plan(
            project_id=project_id,
            custom_focus=custom_focus,
            profile=prof,
            conn=conn,
        )
    finally:
        conn.close()
    if not res.get("ok"):
        return jsonify(res), 400
    with _page_cache_lock:
        _page_cache["html"] = None
    return jsonify(res)


@app.route("/api/skill-projects/iterate", methods=["POST"])
def iterate_skill_project_api():
    """用户对当前候选项目或已选项目提出迭代反馈（缩小 MVP、强化某项技能、双项目对比或自定义修改）。"""
    import skill_projects
    from profile_config import load_profile
    payload = request.get_json(silent=True) or {}
    project_id = str(payload.get("project_id") or request.form.get("project_id") or "").strip()
    action_type = str(payload.get("action_type") or request.form.get("action_type") or "custom").strip()
    feedback_text = str(payload.get("feedback_text") or request.form.get("feedback_text") or "").strip()
    compare_with_id = str(payload.get("compare_with_id") or request.form.get("compare_with_id") or "").strip()
    try:
        prof = load_profile(_ROOT / "profile.yaml")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    conn = _conn()
    try:
        res = skill_projects.submit_project_iteration(
            project_id=project_id,
            action_type=action_type,
            feedback_text=feedback_text,
            compare_with_id=compare_with_id,
            profile=prof,
            conn=conn,
        )
    finally:
        conn.close()
    if not res.get("ok"):
        return jsonify(res), 400
    with _page_cache_lock:
        _page_cache["html"] = None
    return jsonify(res)


@app.route("/api/wlb-audit/refresh", methods=["POST", "GET"])
def refresh_wlb_audit_api():
    """手动触发模块二（社区讨论 WLB 证据审核）刷新。"""
    import wlb_community
    from profile_config import load_profile
    snapshot, _ = _get_snapshot()
    try:
        prof = load_profile(_ROOT / "profile.yaml")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    conn = db_module.init_db(str(_DB_PATH))
    try:
        res = wlb_community.update_or_get_wlb_community_audits(
            snapshot["items"], profile=prof, conn=conn, force_refresh=True
        )
    finally:
        conn.close()
    with _page_cache_lock:
        _page_cache["html"] = None
    return jsonify({
        "ok": True,
        "version_info": res.get("version_info", {}),
        "audited_companies_count": (res.get("summary_stats") or {}).get("audited_companies_count", 0),
        "total_evidence_items": (res.get("summary_stats") or {}).get("total_valid_evidences", 0),
    })


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "build": _BUILD_ID})


@app.route("/api/jobs")
def jobs_page():
    """Serve filtered job pages without embedding the full database in HTML."""
    snapshot, _ = _get_snapshot()
    cohort = request.args.get("cohort", "current")
    if cohort == "previous":
        items = snapshot["previous"]
    elif cohort == "unknown":
        items = snapshot["unknown"]
    elif cohort == "all":
        items = snapshot["current"] + snapshot["previous"] + snapshot["unknown"]
    else:
        items = snapshot["current"]

    rows = reporter._jobs_payload(items, snapshot["app_index"])
    query = request.args.get("q", "").strip().lower()
    company = request.args.get("company", "")
    category = request.args.get("category", "")
    platform = request.args.get("platform", "")
    evaluation = request.args.get("evaluation", "")
    score_filter = request.args.get("score", "")

    def matches(job):
        if company and job["c"] != company:
            return False
        if category and job["cat"] != category and category not in (job.get("cats") or []):
            return False
        if platform and job["p"] != platform:
            return False
        if evaluation == "scored" and not job["e"]:
            return False
        if evaluation == "uneval" and job["e"]:
            return False
        if score_filter:
            if not job["e"]:
                return False
            score = job["s"] or 0
            if score_filter == "70" and score < 70:
                return False
            if score_filter == "60" and not 60 <= score < 70:
                return False
            if score_filter == "0" and score >= 60:
                return False
        if query:
            haystack = f'{job["c"]} {job["t"]} {job["ct"]}'.lower()
            if query not in haystack:
                return False
        return True

    rows = [job for job in rows if matches(job)]
    rows.sort(key=lambda job: job["s"] if job["e"] else -1, reverse=True)
    per_page = max(1, min(request.args.get("per_page", 50, type=int), 5000))
    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(request.args.get("page", 1, type=int), pages))
    start = (page - 1) * per_page
    active_companies = sorted({job["c"] for job in rows if job.get("c")})
    high_match_count = sum(1 for job in rows if job.get("e") and (job.get("s") or 0) >= 70)
    return jsonify({
        "jobs": rows[start:start + per_page],
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "total": total,
        "company_count": len(active_companies),
        "high_match_count": high_match_count,
        "companies": active_companies,
    })


@app.route("/apply", methods=["POST"])
def apply():
    job_id = request.form.get("job_id", type=int)
    conn = _conn()
    try:
        db_module.upsert_application(
            conn,
            job_id,
            request.form.get("company", ""),
            request.form.get("title", ""),
            request.form.get("city", ""),
        )
    finally:
        conn.close()
    _schedule_sync()  # 后台自动提交+推送 applications.json → 手机端同步
    return redirect("/#applications")


@app.route("/application/new", methods=["POST"])
def application_new():
    conn = _conn()
    try:
        db_module.add_manual_application(
            conn,
            request.form.get("company", "").strip(),
            request.form.get("title", "").strip(),
            request.form.get("city", "").strip(),
        )
    finally:
        conn.close()
    _schedule_sync()  # 后台自动提交+推送 applications.json → 手机端同步
    return redirect("/#applications")


@app.route("/application/<int:app_id>/stage", methods=["POST"])
def application_stage(app_id):
    conn = _conn()
    try:
        db_module.update_application_stage(
            conn,
            app_id,
            request.form.get("stage", ""),
            request.form.get("result", "待"),
            request.form.get("note", ""),
        )
    finally:
        conn.close()
    _schedule_sync()  # 后台自动提交+推送 applications.json → 手机端同步
    return redirect("/#applications")


@app.route("/application/<int:app_id>/event", methods=["POST"])
def application_event(app_id):
    conn = _conn()
    try:
        db_module.add_application_event(
            conn,
            app_id,
            request.form.get("event_type", ""),
            request.form.get("event_date", ""),
            request.form.get("event_time", ""),
            request.form.get("note", ""),
        )
    finally:
        conn.close()
    _schedule_sync()  # 后台自动提交+推送 applications.json → 手机端同步
    return redirect("/#schedule")


@app.route("/application/<int:app_id>/event/<int:event_id>/delete", methods=["POST"])
def application_event_delete(app_id, event_id):
    conn = _conn()
    try:
        db_module.delete_application_event(conn, app_id, event_id)
    finally:
        conn.close()
    _schedule_sync()  # 后台自动提交+推送 applications.json → 手机端同步
    return redirect("/#schedule")


@app.route("/application/<int:app_id>/delete", methods=["POST"])
def application_delete(app_id):
    conn = _conn()
    try:
        db_module.delete_application(conn, app_id)
    finally:
        conn.close()
    _schedule_sync()  # 后台自动提交+推送 applications.json → 手机端同步
    return redirect("/#applications")


def main():
    url = f"http://localhost:{_PORT}/"
    # 开自动重载：改任何 .py（含 reporter.py）服务自动重启，无需手动关窗重开。
    # 重载器把脚本跑两次（监视父进程 + 真正服务的子进程）；只在子进程
    # （WERKZEUG_RUN_MAIN=true，即真正绑定端口的那个）里打印+弹一次浏览器。
    # Keep a single server process so file watching cannot reopen the browser.
    if os.environ.get("WERKZEUG_RUN_MAIN", "true") == "true":
        print(f"投递记录管理器已启动：{url}")
        sync_state = "开" if _AUTO_SYNC else "关（WEBAPP_AUTO_SYNC=0）"
        print(f"录入/更新投递会自动推送到远端、同步到手机端，无需手动 commit。自动同步：{sync_state}")
        print("关闭此窗口即停止。")
        if os.environ.get("WEBAPP_OPEN_BROWSER", "1") == "1":
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
