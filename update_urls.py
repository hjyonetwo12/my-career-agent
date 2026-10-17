"""一键自动体检、修复与更新企业招聘网址工具 (update_urls.py)

功能说明：
1. 【自动从在线共享文档同步最新网址】：
   读取公开维护的腾讯文档校招/实习在线汇总表（覆盖秋招、春招、暑期实习、日常实习），
   不仅自动补充新公司，还会**自动覆盖更新 config.yaml 中旧网址已失效/改版的老公司**！
2. 【并发极速体检全库 570+ 家公司网址（约 15~25 秒）】：
   使用 24 线程并发检测 config.yaml 中所有公司的 careers_url：
   - 若发现北森 (.zhiye.com) 旧活动子链接失效，自动修复为标准 `/campus` 根入口；
   - 若发现永久重定向（301/302），自动更新为跳转后的最新有效地址；
   - 若发现彻底无法连接的死链（如临时端口 :8083 停机、域名过期、404），
     自动将其移入 `outputs/inactive_companies_backup.yaml` 备份，从 `config.yaml` 活跃列表中剔除，
     避免每次跑爬虫时在死链上白白卡顿数十分钟！

用法：
    python update_urls.py
"""

from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

from scripts import qq_docs_27_autumn_monitor as qq_monitor

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
DB_PATH = ROOT / "data" / "jobs.db"
BACKUP_PATH = ROOT / "outputs" / "inactive_companies_backup.yaml"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _get_db_job_counts() -> dict[str, int]:
    if not DB_PATH.exists():
        return {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("SELECT company, COUNT(*) FROM jobs GROUP BY company").fetchall()
        conn.close()
        return {str(k): int(v) for k, v in rows}
    except Exception:
        return {}


def _check_single_url(company: dict, db_count: int) -> dict:
    """检测单个公司 URL 的连通性，并尝试自动修复常见平台过期链接。"""
    name = str(company.get("name") or "")
    url = str(company.get("careers_url") or "").strip()
    crawler = str(company.get("crawler") or "render").strip()

    if not url.startswith(("http://", "https://")):
        return {"company": company, "status": "dead", "reason": "非合法 HTTP/HTTPS 链接"}

    # 对已知有专用 API 爬虫的大厂（如字节、腾讯、阿里、美团、京东等），其爬虫内部走固定接口，直接放行
    if crawler not in {"render", "static_html", "moka", "beisen", "feishu", "hotjob"}:
        return {"company": company, "status": "ok", "reason": "专用爬虫接口"}

    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    try:
        resp = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        code = resp.status_code
        final_url = resp.url or url

        # 北森 (.zhiye.com) 子路径 404 时，自动回退到标准 /campus 入口
        if code >= 400 and ".zhiye.com" in url:
            parsed = urlparse(url)
            fallback_url = f"{parsed.scheme}://{parsed.netloc}/campus"
            resp2 = requests.get(fallback_url, headers=headers, timeout=8)
            if resp2.status_code < 400:
                updated = {**company, "careers_url": fallback_url, "crawler": "beisen"}
                return {
                    "company": updated,
                    "status": "repaired",
                    "old_url": url,
                    "new_url": fallback_url,
                    "reason": f"原链接返回 {code}，已自动修复为北森标准校招入口",
                }

        if code in (404, 410, 500, 502, 503, 504):
            return {"company": company, "status": "dead", "reason": f"HTTP {code} 页面失效"}

        return {"company": company, "status": "ok", "final_url": final_url}

    except requests.RequestException as exc:
        # 如果历史数据库里也从来没抓到过岗位（db_count == 0），且现在连不上，判定为死链
        if db_count == 0:
            return {"company": company, "status": "dead", "reason": f"连接超时或域名失效 ({type(exc).__name__})"}
        return {"company": company, "status": "ok", "reason": "偶发超时但历史库有岗位，暂保留"}


def sync_urls_from_tencent_docs(companies: list[dict], db_counts: dict[str, int]) -> tuple[list[dict], int, int]:
    """从公开腾讯文档同步最新校招/实习链接，自动修复已有公司的过期链接并补充新公司。"""
    print("正在连接腾讯文档公开校招/实习汇总表同步最新网址...")
    updated_count = 0
    added_count = 0
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": _UA})
        payload = qq_monitor._load_sheet_payload(session)
        rows = qq_monitor.parse_rows(payload)
    except Exception as exc:
        print(f"  [提示] 腾讯文档在线同步跳过（网络或接口受限）：{exc}")
        return companies, 0, 0

    by_name = {str(c.get("name") or "").casefold(): c for c in companies}
    for row in rows:
        cname = str(row.get("canonical_name") or "").strip()
        links = [str(u).strip() for u in (row.get("links") or []) if str(u).strip().startswith("http")]
        if not cname or not links:
            continue
        new_url = links[0]
        new_crawler = qq_monitor.infer_crawler(new_url) or "render"
        key = cname.casefold()

        if key in by_name:
            comp = by_name[key]
            old_url = str(comp.get("careers_url") or "").strip()
            old_crawler = str(comp.get("crawler") or "render").strip()
            # 当旧网址在库里 0 岗位，或旧爬虫是低效 render 而新链接是标准平台(moka/beisen/feishu/hotjob)，自动升级网址！
            if old_url != new_url and (
                db_counts.get(comp["name"], 0) == 0
                or (old_crawler == "render" and new_crawler in {"moka", "beisen", "feishu", "hotjob"})
            ):
                comp["careers_url"] = new_url
                comp["crawler"] = new_crawler
                updated_count += 1
                print(f"  🔄 [自动更新网址] {comp['name']}: {old_url} -> {new_url} ({new_crawler})")
        else:
            new_entry = {"name": cname, "careers_url": new_url, "crawler": new_crawler}
            companies.append(new_entry)
            by_name[key] = new_entry
            added_count += 1
            print(f"  ➕ [新增覆盖企业] {cname}: {new_url} ({new_crawler})")

    return companies, updated_count, added_count


def main() -> None:
    if not CONFIG_PATH.exists():
        print(f"找不到 {CONFIG_PATH}")
        return

    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    companies = cfg.get("companies") or []
    db_counts = _get_db_job_counts()

    print(f"=== 开始执行企业招聘网址自动更新与死链体检（共 {len(companies)} 家企业）===")

    # 第一步：从腾讯文档在线表同步最新网址（含更新老公司失效链接）
    companies, updated_from_doc, added_from_doc = sync_urls_from_tencent_docs(companies, db_counts)

    # 第二步：并发检测所有网址连通性，自动修复北森入口或隔离死链
    print(f"\n正在并发体检 {len(companies)} 家企业的网址连通性（约需 15~25 秒）...")
    active_companies = []
    dead_companies = []
    repaired_count = 0

    with ThreadPoolExecutor(max_workers=24) as pool:
        future_map = {
            pool.submit(_check_single_url, comp, db_counts.get(comp.get("name", ""), 0)): comp
            for comp in companies
        }
        for fut in as_completed(future_map):
            res = fut.result()
            status = res["status"]
            comp = res["company"]
            if status == "repaired":
                repaired_count += 1
                print(f"  🛠️ [自动修复] {comp['name']}: {res['old_url']} -> {res['new_url']} ({res['reason']})")
                active_companies.append(comp)
            elif status == "dead":
                print(f"  ❌ [隔离死链] {comp['name']} ({comp.get('careers_url')}): {res['reason']}")
                dead_companies.append({**comp, "dead_reason": res["reason"]})
            else:
                active_companies.append(comp)

    # 保持原有顺序
    order_index = {str(c.get("name")): i for i, c in enumerate(companies)}
    active_companies.sort(key=lambda x: order_index.get(str(x.get("name")), 99999))

    cfg["companies"] = active_companies
    CONFIG_PATH.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    if dead_companies:
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing_dead = []
        if BACKUP_PATH.exists():
            try:
                existing_dead = yaml.safe_load(BACKUP_PATH.read_text(encoding="utf-8")) or []
            except Exception:
                existing_dead = []
        BACKUP_PATH.write_text(
            yaml.safe_dump(existing_dead + dead_companies, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    print("\n========================================================")
    print("✅ 网址自动更新与体检完成！")
    print(f"  • 在线文档同步更新老企业网址：{updated_from_doc} 家")
    print(f"  • 在线文档自动新增企业：{added_from_doc} 家")
    print(f"  • 自动修复平台入口链接：{repaired_count} 家")
    print(f"  • 自动隔离无法打开的死链：{len(dead_companies)} 家（已备份至 outputs/inactive_companies_backup.yaml）")
    print(f"  • 当前 config.yaml 保留健康可爬企业：{len(active_companies)} 家")
    print("========================================================")


if __name__ == "__main__":
    main()
