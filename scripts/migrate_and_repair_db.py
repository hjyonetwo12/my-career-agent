"""数据库无损修复与评分同步脚本（兼容旧库升级与误删恢复）。

功能：
1. 确保 jobs 与 job_analysis 表包含最新 Schema 字段与索引。
2. 从 job_screening_cache 回填因旧版 update_job_cohort Bug 被清空的 screening_tier。
3. 对库内已有 DeepSeek Pro 评分记录（若有），按修复后的 _finalize_analysis 重新计算真实总分（消除 64 分硬截断 Bug）。
4. 对所有确认 27 届、符合计算机方向且具备有效 JD 的未 Pro 评分岗位，写入或更新本地五维基准评分（model='local-rule-v1'），
   同时保留 DeepSeek Pro 评分不被覆盖。
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analyzer
import db as db_module
import job_cohorts
from profile_config import load_profile


def migrate_and_repair(
    db_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    donor_db_path: str | Path | None = None,
) -> dict:
    target_db = Path(db_path or os.environ.get("JOBS_DB_PATH", ROOT / "data" / "jobs.db"))
    prof_file = Path(profile_path or os.environ.get("PROFILE_PATH", ROOT / "profile.yaml"))
    profile = load_profile(prof_file)
    conn = db_module.init_db(str(target_db))

    # 1. 从 job_screening_cache 回填被旧版 update_job_cohort 误清空的 screening_tier
    cache_rows = conn.execute(
        "SELECT company, title, tier FROM job_screening_cache WHERE screening_version = ?",
        (analyzer.SCREENING_VERSION,),
    ).fetchall()
    tier_by_title = {(str(r["company"] or "").strip(), str(r["title"] or "").strip()): r["tier"] for r in cache_rows if r["tier"] in ("A", "B", "C")}

    active_27 = conn.execute(
        "SELECT id, company, title, jd_raw, screening_tier FROM jobs WHERE cohort = 2027 AND cohort_status = 'confirmed'"
    ).fetchall()
    tier_updates = []
    for r in active_27:
        cur_tier = str(r["screening_tier"] or "").strip()
        if cur_tier in ("A", "B"):
            continue
        tkey = (str(r["company"] or "").strip(), str(r["title"] or "").strip())
        tier = tier_by_title.get(tkey) or analyzer.local_screening_tier(dict(r), profile)
        if tier in ("A", "B", "C"):
            tier_updates.append((tier, r["id"]))
    if tier_updates:
        conn.executemany("UPDATE jobs SET screening_tier = ? WHERE id = ?", tier_updates)
        conn.commit()

    # 2. 若提供 donor_db_path（例如备份库中含有被 2029 届 Bug 误删的 DeepSeek Pro 评分），按 jd_url 安全恢复
    restored_pro = 0
    if donor_db_path and Path(donor_db_path).exists():
        dconn = sqlite3.connect(str(donor_db_path))
        dconn.row_factory = sqlite3.Row
        donor_rows = dconn.execute("""
            SELECT j.jd_url, j.title, j.company, j.city, j.jd_raw,
                   a.score_breakdown, a.evidence, a.evidence_level,
                   a.advantages, a.gaps, a.summary, a.model, a.analyzed_at
            FROM job_analysis a
            JOIN jobs j ON j.id = a.job_id
            WHERE a.analysis_status = 'complete'
              AND (a.model LIKE '%pro%' OR a.model LIKE '%deepseek%')
        """).fetchall()
        for dr in donor_rows:
            target_job = conn.execute(
                "SELECT * FROM jobs WHERE jd_url = ? AND cohort = 2027 AND cohort_status = 'confirmed'",
                (dr["jd_url"],),
            ).fetchone()
            if not target_job:
                continue
            job_dict = dict(target_job)
            if not job_cohorts.is_confirmed_current(job_dict):
                continue
            try:
                raw_obj = {
                    "score_breakdown": json.loads(dr["score_breakdown"] or "{}"),
                    "evidence": json.loads(dr["evidence"] or "[]"),
                    "evidence_level": dr["evidence_level"] or "partial",
                    "advantages": json.loads(dr["advantages"] or "[]"),
                    "gaps": json.loads(dr["gaps"] or "[]"),
                    "summary": dr["summary"] or "",
                }
                recalc = analyzer._finalize_analysis(raw_obj, job_dict, profile, dr["model"] or "deepseek-v4-pro")
                db_module.save_analysis(conn, job_dict["id"], recalc)
                restored_pro += 1
            except Exception:
                continue
        dconn.close()

    # 3. 对当前库内已有的 DeepSeek Pro 评分重新核算（解除旧版 64 分上限截断）
    recalculated_pro = 0
    existing_pro = conn.execute("""
        SELECT a.*, j.company, j.title, j.city, j.jd_raw, j.cohort, j.cohort_status
        FROM job_analysis a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.analysis_status = 'complete'
          AND a.model != 'local-rule-v1'
    """).fetchall()
    for row in existing_pro:
        rdict = dict(row)
        try:
            raw_obj = {
                "score_breakdown": json.loads(rdict.get("score_breakdown") or "{}"),
                "evidence": json.loads(rdict.get("evidence") or "[]"),
                "evidence_level": rdict.get("evidence_level") or "partial",
                "advantages": json.loads(rdict.get("advantages") or "[]"),
                "gaps": json.loads(rdict.get("gaps") or "[]"),
                "summary": rdict.get("summary") or "",
            }
            if not raw_obj["score_breakdown"]:
                continue
            recalc = analyzer._finalize_analysis(
                raw_obj,
                rdict,
                profile,
                rdict.get("model") or "deepseek-v4-pro",
            )
            db_module.save_analysis(conn, rdict["job_id"], recalc)
            recalculated_pro += 1
        except Exception:
            continue

    # 4. 同步本地五维基准评分（覆盖所有确认27届、符合方向、JD完整的未Pro评分岗位）
    baseline_stats = db_module.sync_local_baseline_scores(conn, profile=profile)

    # 5. 统计修复后数据库状态
    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    confirmed_27 = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE cohort = 2027 AND cohort_status = 'confirmed'"
    ).fetchone()[0]
    total_analyses = conn.execute("SELECT COUNT(*) FROM job_analysis").fetchone()[0]
    max_score_row = conn.execute("SELECT MAX(match_score), MIN(match_score), AVG(match_score) FROM job_analysis").fetchone()
    ge_70 = conn.execute("SELECT COUNT(*) FROM job_analysis WHERE match_score >= 70").fetchone()[0]
    ge_75 = conn.execute("SELECT COUNT(*) FROM job_analysis WHERE match_score >= 75").fetchone()[0]

    conn.close()
    summary = {
        "db_path": str(target_db),
        "total_jobs": total_jobs,
        "confirmed_27_jobs": confirmed_27,
        "backfilled_tiers": len(tier_updates),
        "restored_pro_analyses": restored_pro,
        "recalculated_pro_analyses": recalculated_pro,
        "local_baseline_stats": baseline_stats,
        "total_analyses": total_analyses,
        "max_score": max_score_row[0] or 0,
        "min_score": max_score_row[1] or 0,
        "avg_score": round(float(max_score_row[2] or 0), 1),
        "score_ge_70_count": ge_70,
        "score_ge_75_count": ge_75,
    }
    return summary


if __name__ == "__main__":
    donor = sys.argv[1] if len(sys.argv) > 1 else None
    result = migrate_and_repair(donor_db_path=donor)
    print(json.dumps(result, ensure_ascii=False, indent=2))
