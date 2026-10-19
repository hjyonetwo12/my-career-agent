"""模块一：高频技能栈提取与可实现工程项目推荐模块 (skill_projects.py)。

设计原则：
1. 知识转化为可实现、可验证、可使用的工程成果（拒绝“只列书单”或“做一个博客/聊天机器人”式的空泛建议）。
2. 从真实有效招聘数据 (data/jobs.db) 按岗位类别提取高频技能，区分【必需技能(must)】、【加分项(bonus)】与【少量岗位特殊要求(niche)】。
3. 统一同义词写法，严格区分【去重后独立岗位数(unique_jobs)】与【含多城/重复发布的记录数(raw_postings)】，样本不足时显式标记。
4. 结合 profile.yaml 与 exploration 探索模式：当前技能、学习成本与工作内容兴趣适配度三者解耦，绝不只推荐 C++/Python 项目。
5. 低成本持续更新：程序完成规范化与聚合，基于指纹检测实质变化并持久化缓存（SQLite + JSON），避免无条件重复消耗 Token。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import job_filters
from profile_config import extract_skill_proficiency_map

logger = logging.getLogger(__name__)

_CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
SKILL_PROJECT_VERSION = "sp-v1.0"
DEFAULT_SKILL_PROJECT_STATE_PATH = Path(__file__).resolve().parent / "data" / "skill_projects_state.json"

MIN_CATEGORY_UNIQUE_SAMPLES = 15
MIN_OVERALL_UNIQUE_SAMPLES = 30

SKILL_DIMENSION_LABELS = {
    "programming_language": "编程语言",
    "framework_library": "框架与开发库",
    "ds_algorithm": "数据结构与算法",
    "cs_foundation": "数据库、网络与操作系统基础",
    "eng_quality_tool": "测试、构建、部署、调试与性能分析工具",
    "domain_specific": "特定岗位方向专有技术栈",
}

SKILL_SYNONYM_CATALOG: tuple[tuple[str, str, str, list[str], re.Pattern], ...] = (
    (
        "lang_cpp",
        "C/C++",
        "programming_language",
        ["C++", "C/C++", "C++11/14/17/20", "C语言", "STL"],
        re.compile(r"C\+\+|C/C\+\+|(?<![A-Za-z0-9_])C语言|\bSTL\b|\bRAII\b", re.I),
    ),
    (
        "lang_python",
        "Python",
        "programming_language",
        ["Python", "Python3"],
        re.compile(r"\bPython\d?\b", re.I),
    ),
    (
        "lang_java",
        "Java",
        "programming_language",
        ["Java", "JDK", "JVM"],
        re.compile(r"\bJava\b(?!Script)|\bJVM\b|\bJDK\b|\bJ2EE\b", re.I),
    ),
    (
        "lang_golang",
        "Go (Golang)",
        "programming_language",
        ["Go", "Golang", "Go语言"],
        re.compile(r"\bGolang\b|\bGo\s*语言\b|\bGo\s*开发\b|(?<![A-Za-z])Go(?=\s*[、/，,或及与])", re.I),
    ),
    (
        "lang_js_ts",
        "TypeScript / JavaScript",
        "programming_language",
        ["TypeScript", "JavaScript", "ES6", "TS", "JS"],
        re.compile(r"\bTypeScript\b|\bJavaScript\b|\bES6\b", re.I),
    ),
    (
        "lang_rust",
        "Rust",
        "programming_language",
        ["Rust"],
        re.compile(r"\bRust\b", re.I),
    ),
    (
        "lang_csharp",
        "C# / .NET",
        "programming_language",
        ["C#", ".NET", "WPF", "WinForm"],
        re.compile(r"C#|\.NET\b|\bWPF\b|\bWinForm\b", re.I),
    ),
    (
        "lang_shell",
        "Shell / Bash 脚本",
        "programming_language",
        ["Shell", "Bash", "Linux脚本"],
        re.compile(r"\bShell\b|\bBash\b", re.I),
    ),
    (
        "fw_spring",
        "Spring Boot / Spring Cloud",
        "framework_library",
        ["Spring", "SpringBoot", "Spring Boot", "SpringCloud", "MyBatis"],
        re.compile(r"\bSpring\s*Boot\b|\bSpring\s*Cloud\b|\bSpring\b|\bMyBatis\b", re.I),
    ),
    (
        "fw_qt",
        "Qt / QML 跨平台 GUI",
        "framework_library",
        ["Qt", "Qt5", "Qt6", "QML", "上位机界面"],
        re.compile(r"\bQt[56]?\b|\bQML\b", re.I),
    ),
    (
        "fw_py_web",
        "FastAPI / Flask / Django",
        "framework_library",
        ["FastAPI", "Flask", "Django"],
        re.compile(r"\bFastAPI\b|\bFlask\b|\bDjango\b", re.I),
    ),
    (
        "fw_fe_modern",
        "React / Vue / Node.js",
        "framework_library",
        ["React", "Vue", "Vue3", "Node.js", "Electron"],
        re.compile(r"\bReact\b|\bVue[23]?\b|\bNode\.?js\b|\bElectron\b", re.I),
    ),
    (
        "fw_rpc_microservice",
        "gRPC / Protobuf / 微服务架构",
        "framework_library",
        ["gRPC", "Protobuf", "RPC", "微服务", "Dubbo"],
        re.compile(r"\bgRPC\b|\bProtobuf\b|\bDubbo\b|微服务|\bRPC\b", re.I),
    ),
    (
        "fw_dl_pytorch",
        "PyTorch / TensorFlow 深度学习框架",
        "framework_library",
        ["PyTorch", "TensorFlow", "ONNX", "TensorRT", "vLLM"],
        re.compile(r"\bPyTorch\b|\bTensorFlow\b|\bONNX\b|\bTensorRT\b|\bvLLM\b", re.I),
    ),
    (
        "algo_ds_core",
        "数据结构与常用算法 (树/图/哈希/排序/复杂度)",
        "ds_algorithm",
        ["数据结构", "算法基础", "常用算法", "时间复杂度", "空间复杂度", "动态规划"],
        re.compile(r"数据结构|算法基础|常用算法|时间复杂度|空间复杂度|二叉树|哈希表|动态规划|图论", re.I),
    ),
    (
        "algo_concurrency",
        "多线程并发与无锁/同步控制",
        "ds_algorithm",
        ["多线程", "并发编程", "锁机制", "线程池", "异步编程", "协程"],
        re.compile(r"多线程|并发编程|高并发|线程池|异步编程|协程|互斥锁|原子操作", re.I),
    ),
    (
        "algo_geom_math",
        "线性代数、计算几何与数值优化",
        "ds_algorithm",
        ["计算几何", "线性代数", "矩阵运算", "数值计算", "几何算法", "最优化"],
        re.compile(r"计算几何|几何算法|线性代数|数值计算|数值分析|最优化|空间索引|\bBVH\b", re.I),
    ),
    (
        "cs_linux_os",
        "Linux 环境与操作系统原理 (进程/内存/I/O)",
        "cs_foundation",
        ["Linux", "操作系统", "Unix", "虚拟内存", "epoll", "进程间通信", "IPC"],
        re.compile(r"\bLinux\b|操作系统|\bUnix\b|虚拟内存|\bepoll\b|进程间通信|\bIPC\b|内核态", re.I),
    ),
    (
        "cs_network_tcp",
        "计算机网络与协议栈 (TCP/IP/HTTP/Socket)",
        "cs_foundation",
        ["计算机网络", "TCP/IP", "HTTP", "HTTPS", "Socket", "网络编程", "WebSocket"],
        re.compile(r"计算机网络|TCP/IP|\bTCP\b|\bUDP\b|\bHTTP\b|\bHTTPS\b|\bSocket\b|网络编程|\bWebSocket\b", re.I),
    ),
    (
        "cs_sql_rdbms",
        "SQL 与关系型数据库 (MySQL / PostgreSQL)",
        "cs_foundation",
        ["SQL", "MySQL", "PostgreSQL", "Oracle", "SQLite", "关系型数据库", "索引优化", "事务"],
        re.compile(r"\bSQL\b|\bMySQL\b|\bPostgreSQL\b|\bSQLite\b|\bOracle\b|关系型数据库|数据库设计|索引优化", re.I),
    ),
    (
        "cs_cache_mq",
        "缓存与消息中间件 (Redis / Kafka / RabbitMQ)",
        "cs_foundation",
        ["Redis", "Kafka", "RabbitMQ", "RocketMQ", "消息队列", "分布式缓存"],
        re.compile(r"\bRedis\b|\bKafka\b|\bRabbitMQ\b|\bRocketMQ\b|消息队列|消息中间件|分布式缓存", re.I),
    ),
    (
        "cs_distributed",
        "分布式系统基础 (一致性/容错/负载均衡)",
        "cs_foundation",
        ["分布式系统", "分布式存储", "分布式一致性", "Raft", "负载均衡", "高可用"],
        re.compile(r"分布式(?:系统|存储|架构|计算|事务|一致性)|\bRaft\b|\bPaxos\b|负载均衡|高可用", re.I),
    ),
    (
        "tool_git_collab",
        "Git 版本控制与代码评审规范",
        "eng_quality_tool",
        ["Git", "GitHub", "GitLab", "SVN", "版本控制", "Code Review"],
        re.compile(r"\bGit\b|\bGitHub\b|\bGitLab\b|版本控制|代码评审|Code\s*Review", re.I),
    ),
    (
        "tool_cmake_build",
        "CMake / Makefile 编译构建工具链",
        "eng_quality_tool",
        ["CMake", "Makefile", "GCC", "Clang", "MSVC", "编译构建"],
        re.compile(r"\bCMake\b|\bMakefile\b|\bGCC\b|\bClang\b|\bMSVC\b|交叉编译", re.I),
    ),
    (
        "tool_test_auto",
        "单元测试、接口测试与自动化测试框架 (pytest/GTest/JUnit)",
        "eng_quality_tool",
        ["单元测试", "自动化测试", "接口测试", "测试框架", "pytest", "GTest", "JUnit", "Selenium", "Playwright"],
        re.compile(r"单元测试|自动化测试|接口测试|集成测试|测试框架|测试用例|\bpytest\b|\bGTest\b|\bJUnit\b|\bPlaywright\b|\bSelenium\b", re.I),
    ),
    (
        "tool_docker_k8s",
        "Docker 容器化与 Kubernetes 云原生部署",
        "eng_quality_tool",
        ["Docker", "Kubernetes", "K8s", "容器化", "云原生"],
        re.compile(r"\bDocker\b|\bKubernetes\b|\bK8s\b|容器化|云原生", re.I),
    ),
    (
        "tool_cicd_devops",
        "CI/CD 持续集成与工程效能流水线 (Jenkins/GitHub Actions)",
        "eng_quality_tool",
        ["CI/CD", "持续集成", "持续交付", "Jenkins", "GitHub Actions", "DevOps"],
        re.compile(r"CI/CD|持续集成|持续交付|\bJenkins\b|GitHub\s*Actions|\bDevOps\b", re.I),
    ),
    (
        "tool_debug_profile",
        "调试排障与性能/内存分析 (GDB/ASan/Valgrind/perf/抓包)",
        "eng_quality_tool",
        ["GDB", "Valgrind", "AddressSanitizer", "ASan", "perf", "Wireshark", "tcpdump", "性能调优", "性能测试", "内存泄漏"],
        re.compile(r"\bGDB\b|\bValgrind\b|\bASan\b|AddressSanitizer|\bperf\b|\bWireshark\b|\btcpdump\b|性能分析|性能调优|性能测试|压测|内存泄漏|故障定位|问题排查", re.I),
    ),
    (
        "dom_llm_agent_rag",
        "LLM 应用工程、RAG 检索增强与 Agent 工具调用",
        "domain_specific",
        ["大模型应用", "LLM", "Agent", "智能体", "RAG", "LangChain", "Prompt工程", "向量数据库", "Function Calling"],
        re.compile(r"\bAgent\b|智能体|\bRAG\b|\bLangChain\b|\bLangGraph\b|向量数据库|知识库检索|Function\s*Calling|工具调用|大模型应用|\bPrompt\b", re.I),
    ),
    (
        "dom_graphics_3d_cad",
        "图形渲染管线、三维几何与 CAD/仿真 (OpenGL/Vulkan/Shader/UE/Unity)",
        "domain_specific",
        ["OpenGL", "GLSL", "Vulkan", "DirectX", "Shader", "图形学", "渲染管线", "CAD", "CAE", "Unity", "UE4/UE5"],
        re.compile(r"\bOpenGL\b|\bGLSL\b|\bVulkan\b|\bDirectX\b|\bShader\b|渲染管线|图形学|\bCAD\b|\bCAE\b|\bUnity\b|\bUE[45]?\b|\bUnreal\b", re.I),
    ),
    (
        "dom_embedded_rtos",
        "嵌入式系统、RTOS、驱动与工业/车载总线 (MCU/ARM/RTOS/CAN/ROS)",
        "domain_specific",
        ["嵌入式Linux", "RTOS", "FreeRTOS", "MCU", "STM32", "ARM", "CAN总线", "UART/SPI/I2C", "ROS/ROS2", "SLAM"],
        re.compile(r"嵌入式|\bRTOS\b|\bFreeRTOS\b|\bMCU\b|\bSTM32\b|\bARM\b|\bCAN\b总线|\bAUTOSAR\b|\bROS2?\b|\bSLAM\b|驱动开发|固件", re.I),
    ),
    (
        "dom_bigdata_etl",
        "大数据处理、数仓建模与流批计算 (Spark/Flink/Hadoop/Hive/ClickHouse)",
        "domain_specific",
        ["Spark", "Flink", "Hadoop", "Hive", "ClickHouse", "ETL", "数据仓库", "数仓"],
        re.compile(r"\bSpark\b|\bFlink\b|\bHadoop\b|\bHive\b|\bClickHouse\b|\bETL\b|数据仓库|数仓|流计算|批处理", re.I),
    ),
    (
        "dom_security",
        "网络与系统安全工程 (漏洞分析/渗透测试/加密协议/逆向)",
        "domain_specific",
        ["渗透测试", "漏洞挖掘", "逆向工程", "安全攻防", "密码学", "WAF", "零信任"],
        re.compile(r"渗透测试|漏洞挖掘|逆向工程|安全攻防|加密算法|\bWAF\b|安全审计|威胁情报", re.I),
    ),
)

_SKILL_BY_ID = {row[0]: row for row in SKILL_SYNONYM_CATALOG}

_BONUS_SENTENCE_RE = re.compile(
    r"优先|加分|更佳|者佳|了解.*即可|有.*经验者|bonus|preferred|plus|nice\s+to\s+have",
    re.I,
)
_MUST_SENTENCE_RE = re.compile(
    r"精通|熟练|掌握|熟悉|扎实|必须|必备|具备|要求|负责|能够独立|良好的",
    re.I,
)


def _normalize_job_dedup_key(job: dict) -> str:
    """生成岗位去重签名（按公司 + 规范化职位名），用于区分独立岗位与同一岗位多城/多渠道重复发布。"""
    company = re.sub(r"\s+", "", str(job.get("company") or "").strip().lower())
    title = str(job.get("title") or "").strip().lower()
    title = re.sub(r"[\(（][jJ]?\d{3,10}[\)）]", "", title)
    title = re.sub(r"\s+", "", title)
    return f"{company}::{title}"


def extract_job_skills(job: dict) -> dict[str, dict]:
    """从单条招聘岗位中提取规范化技能，并判定其在该岗位中属于 must（必需）还是 bonus（加分项）。"""
    title = str(job.get("title") or "").strip()
    jd_raw = str(job.get("jd_raw") or "").strip()
    text = f"{title}\n{jd_raw}"
    if not text.strip():
        return {}

    clauses = [c.strip() for c in re.split(r"[\r\n；;。！!]+", text) if c.strip()]
    found: dict[str, dict] = {}

    for skill_id, canon_name, dim_key, synonyms, pattern in SKILL_SYNONYM_CATALOG:
        if not pattern.search(text):
            continue
        req_level = "must"
        matched_term = ""
        evidence_clause = ""

        tm = pattern.search(title)
        if tm:
            req_level = "must"
            matched_term = tm.group(0)
            evidence_clause = title[:80]
        else:
            clause_levels = []
            for cl in clauses:
                cm = pattern.search(cl)
                if not cm:
                    continue
                if not matched_term:
                    matched_term = cm.group(0)
                    evidence_clause = cl[:100]
                if _BONUS_SENTENCE_RE.search(cl):
                    clause_levels.append("bonus")
                elif _MUST_SENTENCE_RE.search(cl):
                    clause_levels.append("must")
                else:
                    clause_levels.append("must")
            if clause_levels:
                req_level = "must" if "must" in clause_levels else "bonus"

        found[skill_id] = {
            "skill_id": skill_id,
            "canonical_name": canon_name,
            "dimension": dim_key,
            "dimension_label": SKILL_DIMENSION_LABELS.get(dim_key, dim_key),
            "requirement_level": req_level,
            "matched_term": matched_term,
            "evidence_clause": evidence_clause,
        }
    return found


_SKILL_STATS_MEM_CACHE: dict[tuple, dict] = {}

def aggregate_skills_by_category(
    items: list[dict],
    profile: dict | None = None,
    min_category_samples: int = MIN_CATEGORY_UNIQUE_SAMPLES,
) -> dict:
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    first_id = (items[0].get("job", items[0]).get("id") if items and isinstance(items[0], dict) else None)
    last_id = (items[-1].get("job", items[-1]).get("id") if items and isinstance(items[-1], dict) else None)
    mem_key = (len(items), first_id, last_id, min_category_samples, job_filters.profile_version_id(prof))
    if mem_key in _SKILL_STATS_MEM_CACHE:
        return _SKILL_STATS_MEM_CACHE[mem_key]
    """对真实岗位数据按岗位类别提取高频技能栈（含去重校正、必需/加分/小众区分、时间趋势与样本充足性标记）。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()

    valid_jobs: list[dict] = []
    for it in items:
        job = it.get("job") if isinstance(it, dict) and "job" in it else it
        if not isinstance(job, dict) or job_filters.is_job_record_noise(job):
            continue
        if not job_filters.is_cs_software_role(job) or job_filters.is_direction_out_job(job):
            continue
        if job_filters.is_master_phd_only_job(job):
            continue
        jd_raw = str(job.get("jd_raw") or "").strip()
        if len(jd_raw) < 80:
            continue
        valid_jobs.append(job)

    overall_raw_count = len(valid_jobs)
    unique_jobs_by_key: dict[str, dict] = {}
    posting_counts_by_key: Counter = Counter()

    for job in valid_jobs:
        ukey = _normalize_job_dedup_key(job)
        posting_counts_by_key[ukey] += 1
        if ukey not in unique_jobs_by_key or len(str(job.get("jd_raw") or "")) > len(str(unique_jobs_by_key[ukey].get("jd_raw") or "")):
            unique_jobs_by_key[ukey] = job

    unique_jobs = list(unique_jobs_by_key.values())
    overall_unique_count = len(unique_jobs)
    duplicate_postings_removed = max(0, overall_raw_count - overall_unique_count)

    all_dates: list[str] = []
    platform_counter: Counter = Counter()
    for job in unique_jobs:
        for df in ("crawled_at", "last_seen_at", "published_at"):
            dv = str(job.get(df) or "").strip()[:10]
            if re.match(r"^\d{4}-\d{2}-\d{2}$", dv):
                all_dates.append(dv)
                break
        u = str(job.get("jd_url") or "").lower()
        if "zhiye.com" in u:
            platform_counter["北森(Beisen)"] += 1
        elif "mokahr.com" in u:
            platform_counter["Moka"] += 1
        elif "feishu.cn" in u or "mioffice" in u:
            platform_counter["飞书招聘(Feishu)"] += 1
        elif "hotjob.cn" in u:
            platform_counter["HotJob"] += 1
        else:
            platform_counter["企业官网自建站"] += 1

    earliest_date = min(all_dates) if all_dates else date.today().isoformat()
    latest_date = max(all_dates) if all_dates else date.today().isoformat()
    try:
        latest_dt = date.fromisoformat(latest_date)
    except ValueError:
        latest_dt = date.today()
    recent_cutoff = (latest_dt - timedelta(days=7)).isoformat()

    def _new_bucket() -> dict:
        return {
            "raw_posting_count": 0,
            "unique_job_count": 0,
            "companies": Counter(),
            "recent_unique_count": 0,
            "older_unique_count": 0,
            "skill_unique_hits": Counter(),
            "skill_raw_hits": Counter(),
            "skill_must_hits": Counter(),
            "skill_bonus_hits": Counter(),
            "skill_recent_hits": Counter(),
            "skill_older_hits": Counter(),
            "skill_companies": defaultdict(Counter),
            "skill_examples": defaultdict(list),
            "co_occurrence_pairs": Counter(),
        }

    overall_bucket = _new_bucket()
    category_buckets: dict[str, dict] = defaultdict(_new_bucket)

    for job in valid_jobs:
        cinfo = job_filters.classify_job_categories(job, prof)
        pcat = cinfo["primary_category"]
        overall_bucket["raw_posting_count"] += 1
        category_buckets[pcat]["raw_posting_count"] += 1

    for ukey, job in unique_jobs_by_key.items():
        raw_mult = posting_counts_by_key[ukey]
        cinfo = job_filters.classify_job_categories(job, prof)
        pcat = cinfo["primary_category"]
        co = str(job.get("company") or "").strip()
        jdate = str(job.get("crawled_at") or job.get("last_seen_at") or "")[:10]
        is_recent = bool(jdate and jdate >= recent_cutoff)

        skills_found = extract_job_skills(job)
        skill_ids = sorted(skills_found.keys())

        for target_b in (overall_bucket, category_buckets[pcat]):
            target_b["unique_job_count"] += 1
            if co:
                target_b["companies"][co] += 1
            if is_recent:
                target_b["recent_unique_count"] += 1
            else:
                target_b["older_unique_count"] += 1

            for sid, sdata in skills_found.items():
                target_b["skill_unique_hits"][sid] += 1
                target_b["skill_raw_hits"][sid] += raw_mult
                if sdata["requirement_level"] == "must":
                    target_b["skill_must_hits"][sid] += 1
                else:
                    target_b["skill_bonus_hits"][sid] += 1
                if is_recent:
                    target_b["skill_recent_hits"][sid] += 1
                else:
                    target_b["skill_older_hits"][sid] += 1
                if co:
                    target_b["skill_companies"][sid][co] += 1
                if len(target_b["skill_examples"][sid]) < 3 and job.get("jd_url"):
                    target_b["skill_examples"][sid].append({
                        "company": co,
                        "title": str(job.get("title") or ""),
                        "city": str(job.get("city") or ""),
                        "url": str(job.get("jd_url") or ""),
                        "requirement_level": sdata["requirement_level"],
                        "evidence_clause": sdata["evidence_clause"],
                    })

            for i in range(len(skill_ids)):
                for j in range(i + 1, len(skill_ids)):
                    if skill_ids[i] != "tool_git_collab" and skill_ids[j] != "tool_git_collab":
                        target_b["co_occurrence_pairs"][(skill_ids[i], skill_ids[j])] += 1

    def _format_bucket(bucket: dict, cat_key: str, cat_label: str, min_samples: int) -> dict:
        u_total = bucket["unique_job_count"]
        r_total = bucket["raw_posting_count"]
        sample_ok = u_total >= min_samples
        warning_msg = (
            ""
            if sample_ok
            else f"⚠️ 当前类别独立岗位样本仅 {u_total} 个（低于最小统计阈值 {min_samples} 个），以下频率仅供零星参考，不可视为行业普遍趋势。"
        )

        skills_out = []
        for sid, u_cnt in bucket["skill_unique_hits"].most_common():
            _, cname, dim_key, syns, _ = _SKILL_BY_ID[sid]
            r_cnt = bucket["skill_raw_hits"][sid]
            must_cnt = bucket["skill_must_hits"][sid]
            bonus_cnt = bucket["skill_bonus_hits"][sid]
            u_pct = round(u_cnt / max(1, u_total) * 100, 1)
            must_ratio = round(must_cnt / max(1, u_cnt) * 100)

            if u_cnt < 5 or (u_cnt < 25 and u_pct < 3.5):
                tier_role = "niche"
                tier_role_label = "少量岗位特殊要求"
            elif must_ratio >= 60:
                tier_role = "must"
                tier_role_label = "高频必需技能"
            else:
                tier_role = "bonus"
                tier_role_label = "常见加分项"

            rec_total = max(1, bucket["recent_unique_count"])
            old_total = max(1, bucket["older_unique_count"])
            rec_pct = round(bucket["skill_recent_hits"][sid] / rec_total * 100, 1) if bucket["recent_unique_count"] >= 5 else u_pct
            old_pct = round(bucket["skill_older_hits"][sid] / old_total * 100, 1) if bucket["older_unique_count"] >= 5 else u_pct
            delta_pct = round(rec_pct - old_pct, 1)
            if not sample_ok or (bucket["recent_unique_count"] < 5 or bucket["older_unique_count"] < 5):
                trend_str = f"统计周期 ({earliest_date}~{latest_date}) 均值 {u_pct}%"
            elif delta_pct >= 3.0:
                trend_str = f"近7日占比 +{delta_pct}%（近期需求上升）"
            elif delta_pct <= -3.0:
                trend_str = f"近7日占比 {delta_pct}%（近期波动回调）"
            else:
                trend_str = f"占比稳定（波动 {delta_pct:+.1f}%）"

            dup_inflation = r_cnt - u_cnt
            skills_out.append({
                "skill_id": sid,
                "canonical_name": cname,
                "synonyms_merged": syns[:4],
                "dimension": dim_key,
                "dimension_label": SKILL_DIMENSION_LABELS.get(dim_key, dim_key),
                "unique_job_count": u_cnt,
                "unique_job_pct": u_pct,
                "raw_posting_count": r_cnt,
                "duplicate_posting_count": dup_inflation,
                "must_count": must_cnt,
                "bonus_count": bonus_cnt,
                "must_ratio_pct": must_ratio,
                "tier_role": tier_role,
                "tier_role_label": tier_role_label,
                "company_count": len(bucket["skill_companies"][sid]),
                "top_companies": [c for c, _ in bucket["skill_companies"][sid].most_common(5)],
                "trend_summary": trend_str,
                "examples": bucket["skill_examples"][sid],
            })

        by_dimension: dict[str, list[dict]] = defaultdict(list)
        for s_item in skills_out:
            by_dimension[s_item["dimension"]].append(s_item)

        top_pairs = []
        for (s1, s2), p_cnt in bucket["co_occurrence_pairs"].most_common(6):
            if p_cnt < 2:
                continue
            n1 = _SKILL_BY_ID[s1][1]
            n2 = _SKILL_BY_ID[s2][1]
            top_pairs.append({
                "pair_ids": [s1, s2],
                "pair_label": f"{n1} + {n2}",
                "co_unique_jobs": p_cnt,
                "co_pct": round(p_cnt / max(1, u_total) * 100, 1),
            })

        return {
            "category_key": cat_key,
            "category_label": cat_label,
            "unique_job_count": u_total,
            "raw_posting_count": r_total,
            "duplicate_postings_filtered": max(0, r_total - u_total),
            "company_count": len(bucket["companies"]),
            "top_companies": [c for c, _ in bucket["companies"].most_common(8)],
            "sample_sufficient": sample_ok,
            "min_sample_threshold": min_samples,
            "sample_warning": warning_msg,
            "top_skills": skills_out[:12],
            "must_skills": [s for s in skills_out if s["tier_role"] == "must"][:8],
            "bonus_skills": [s for s in skills_out if s["tier_role"] == "bonus"][:6],
            "niche_skills": [s for s in skills_out if s["tier_role"] == "niche"][:6],
            "skills_by_dimension": dict(by_dimension),
            "common_combinations": top_pairs,
        }

    overall_formatted = _format_bucket(
        overall_bucket, "overall", "全库计算机研发岗位总体", MIN_OVERALL_UNIQUE_SAMPLES
    )

    categories_formatted: dict[str, dict] = {}
    ordered_cats = [
        "backend_dist",
        "software_cpp",
        "testing_quality",
        "ai_ml",
        "sys_infra",
        "data_platform",
        "embedded_device",
        "graphics_3d",
        "game_engine",
        "security",
    ]
    for ckey in ordered_cats:
        clabel = job_filters.JOB_CATEGORY_LABELS.get(ckey, ckey)
        b = category_buckets.get(ckey) or _new_bucket()
        categories_formatted[ckey] = _format_bucket(b, ckey, clabel, min_category_samples)

    fp_payload = {
        "unique_bucket": round(overall_unique_count, -1),
        "cat_top_skills": {
            ck: [s["skill_id"] for s in cdata["top_skills"][:5]]
            for ck, cdata in categories_formatted.items()
            if cdata["sample_sufficient"]
        },
    }
    stats_fp = "sk-" + hashlib.sha256(
        json.dumps(fp_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]

    _res_stat = {
        "stats_fingerprint": stats_fp,
        "generated_at": datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
        "time_range": {
            "earliest_date": earliest_date,
            "latest_date": latest_date,
            "recent_cutoff_date": recent_cutoff,
        },
        "dedup_summary": {
            "raw_posting_count": overall_raw_count,
            "unique_job_count": overall_unique_count,
            "duplicate_postings_removed": duplicate_postings_removed,
            "dedup_explanation": (
                f"已从 {overall_raw_count} 条原始有效岗位中合并剔除 {duplicate_postings_removed} 条同公司同职位多城/重复发布记录，"
                f"以 {overall_unique_count} 个独立岗位为基准计算技能占比，防止单一大厂刷屏造成伪趋势。"
            ),
        },
        "platform_sources": dict(platform_counter.most_common()),
        "overall": overall_formatted,
        "by_category": categories_formatted,
    }
    _SKILL_STATS_MEM_CACHE[mem_key] = _res_stat
    return _res_stat


PROJECT_BLUEPRINT_CATALOG: tuple[dict, ...] = (
    {
        "project_id": "proj_sql_redis_service",
        "title": "项目一：多级缓存与索引调优的校招情报检索 REST 服务 (FastAPI/Go + MySQL + Redis)",
        "tier_priority": "P0_prioritize_now",
        "priority_badge": "🌟 建议先做 P0 · 补齐跨方向最大公约数缺口",
        "difficulty_level": "入门 → 进阶 (Entry to Intermediate)",
        "primary_languages": ["Python 或 Go"],
        "skill_ids": ["cs_sql_rdbms", "cs_cache_mq", "cs_network_tcp", "tool_test_auto", "tool_docker_k8s"],
        "target_categories": ["backend_dist", "data_platform", "testing_quality", "ai_ml"],
        "target_roles": ["后端开发工程师", "数据工程/平台研发工程师", "测试开发工程师(SDET)", "AI应用全栈工程师"],
        "project_goal": (
            "构建一个支持百万级岗位与技能标签检索、具备 MySQL 复合索引优化、Redis 热点缓存防击穿、接口幂等控制与自动化压测对比的可运行后端服务。"
        ),
        "core_features": [
            "1. 结构化关系型存储：将岗位、公司、技能标签拆分为规范化 MySQL 表（含外键约束与 FULLTEXT/B+树复合索引），支持多条件分页查询（城市+类别+技能栈+最低分）；",
            "2. Redis 多级缓存与一致性：实现 Cache-Aside 模式，对高频条件查询做带 TTL 抖动的 Redis 缓存，使用互斥锁/布隆过滤思路防止缓存击穿与穿透；",
            "3. 幂等写入与批量同步 API：提供带幂等 Token 校验的岗位增量入库与投递状态变更 RESTful 接口，支持事务回滚；",
            "4. 慢查询诊断与指标暴露：内置 `/metrics` 端点统计接口 P50/P95/P99 延迟、Redis 缓存命中率与 MySQL `EXPLAIN` 扫描行数对比。",
        ],
        "engineering_tasks": [
            "需求拆解与数据库建模：设计 E-R 图，对比单表大 JSON 字段与规范化关联表在多条件检索下的性能差异；",
            "接口设计与契约定义：使用 OpenAPI 3.0 规范定义请求参数校验、统一错误码与分页游标结构；",
            "错误处理与容错降级：当 Redis 节点不可用或超时时，自动熔断并降级回源至 MySQL，保证服务不崩溃且记录结构化告警日志；",
            "测试与性能分析：编写 pytest 接口回归测试，使用 `locust` 或 `wrk` 在 100 并发下对比“无索引 vs 复合索引 vs Redis 缓存”三组吞吐量(QPS)与 P99 延迟。",
        ],
        "knowledge_mapping": [
            "数据库系统原理：B+ 树聚簇索引与非聚簇索引、最左前缀原则、事务 ACID 隔离级别（Read Committed vs Repeatable Read）、乐观锁/悲观锁；",
            "计算机网络：HTTP 状态码语义、TCP 连接池复用 (Keep-Alive)、超时重试与幂等性设计；",
            "数据结构与算法：LRU 缓存淘汰算法（哈希表+双向链表）、布隆过滤器位数组与哈希冲突分析。",
        ],
        "acceptance_criteria": [
            "✅ 功能可复现：通过 `docker compose up` 一键拉起 App + MySQL + Redis，执行 `pytest` 全部 20+ 条正常与异常边界用例 100% 通过；",
            "✅ 索引优化证据：提供 `EXPLAIN` 截图与实测记录，证明在 5 万条模拟岗位数据下，添加复合索引后查询扫描行数下降 90% 以上、单次查询耗时从 >150ms 降至 <15ms；",
            "✅ 缓存与容错验证：压测报告显示开启 Redis 后读接口 QPS 提升 5 倍以上，且手动停止 Redis 容器后接口仍能正常返回 200（触发降级标志）。",
        ],
        "prerequisites": "掌握基础 SQL 语法或任意一门后端语言（Python 或 Go 基础语法），了解 HTTP 请求基本概念。",
        "estimated_effort": "约 30–45 小时（按本科生课余每周投入 10–12 小时估算，约 3–4 周完成 MVP 基础版；若从零学习 Python/Go Web 框架需额外加 10 小时）。",
        "resume_value": (
            "直接弥补本科生最常见的“只会写单机脚本/课程作业、缺乏 MySQL 索引调优与 Redis 缓存实战”短板；"
            "面试时可拿出实测的 EXPLAIN 执行计划对比表、缓存降级方案与压测 P99 曲线，在后端、数据、测开、AI 工程面试中均极具说服力。"
        ),
        "staged_extensions": [
            "阶段 1（MVP · 第1-2周）：完成 MySQL 表结构设计、复合索引优化与核心检索 REST API；",
            "阶段 2（进阶 · 第3周）：接入 Redis 缓存、实现缓存失效策略与故障自动降级，补齐 pytest 单元测试；",
            "阶段 3（挑战 · 第4周+）：引入消息队列（Redis Stream 或 Kafka）实现异步评测任务分发与死信重试。",
        ],
        "reference_and_license": {
            "references": [
                "FastAPI 官方教程 (MIT License, https://fastapi.tiangolo.com/)",
                "Go Gin 官方文档 (MIT License, https://gin-gonic.com/)",
                "MySQL 8.0 Reference Manual - Optimization & Indexes",
            ],
            "reuse_vs_self_impl": (
                "允许正常使用 FastAPI/Gin、SQLAlchemy/GORM、redis-py 等成熟开源库；"
                "必须由本人独立完成：数据库表结构与复合索引设计、Cache-Aside 防击穿/降级逻辑、慢查询对比实验及自动化测试脚本，严禁直接套用现成后台管理模板。"
            ),
        },
    },
    {
        "project_id": "proj_cpp_epoll_kv",
        "title": "项目二：基于 C++17 Reactor 模式与 WAL 日志的轻量级并发键值存储与网络诊断服务",
        "tier_priority": "P0_prioritize_now",
        "priority_badge": "🌟 建议先做 P0 · 深化现有 C++ 与系统网络底座",
        "difficulty_level": "进阶 (Intermediate)",
        "primary_languages": ["C++ (C++17)"],
        "skill_ids": ["lang_cpp", "cs_linux_os", "cs_network_tcp", "algo_concurrency", "tool_cmake_build", "tool_debug_profile"],
        "target_categories": ["software_cpp", "backend_dist", "sys_infra", "embedded_device", "security"],
        "target_roles": ["C++软件开发工程师", "Linux系统研发工程师", "后端研发工程师(C++)", "基础架构/存储工程师"],
        "project_goal": (
            "使用现代 C++17 在 Linux 环境下从零实现一个支持多线程非阻塞网络通信、预写式日志 (WAL) 崩溃恢复与跳表/哈希内存索引的轻量级键值存储服务。"
        ),
        "core_features": [
            "1. 非阻塞网络引擎：基于 Linux `epoll` (Edge/Level Triggered) + 固定大小线程池实现 Reactor 事件循环，支持自定义二进制长度前缀或 RESP 协议解析；",
            "2. 内存存储与并发安全：手写跳表 (SkipList) 或分段锁哈希表 (ConcurrentHashMap)，支持 `PUT`、`GET`、`DEL`、`SCAN` 范围查询与毫秒级键过期清理；",
            "3. 持久化与崩溃恢复：实现追加写日志 (Write-Ahead Log, WAL) 与 CRC32 校验，服务重启时自动重放 WAL 恢复状态；",
            "4. 内置诊断指令：支持 `INFO STATS` 返回当前活跃连接数、内存占用、命令延迟直方图与慢命令记录。",
        ],
        "engineering_tasks": [
            "接口与协议设计：设计零拷贝或环形缓冲区 (`RingBuffer`) 处理 TCP 粘包与半包问题；",
            "内存与并发排障：使用 RAII 智能指针管理连接生命周期，在开启 `AddressSanitizer (ASan)` 与 `ThreadSanitizer (TSan)` 下排查数据竞争与内存泄漏；",
            "构建与自动化测试：使用 CMake 组织多目录工程（`src/`、`include/`、`tests/`、`bench/`），编写 GoogleTest 单元测试覆盖断连、半包、损坏 WAL 恢复等异常场景；",
            "性能剖析：编写多线程压测客户端，使用 Linux `perf` 生成火焰图定位锁竞争或系统调用瓶颈。",
        ],
        "knowledge_mapping": [
            "操作系统：I/O 多路复用 (`select`/`poll`/`epoll`)、非阻塞文件描述符、页缓存 (`fsync`/`fdatasync`)、线程同步原语（互斥锁/读写锁/条件变量）；",
            "计算机网络：TCP 状态机、粘包与拆包、半关闭连接 (`SHUT_WR`)、Socket 缓冲区管理；",
            "数据结构与算法：跳表 (SkipList) 概率平衡多层链表、LRU 定时轮或小根堆过期键淘汰、CRC 校验。",
        ],
        "acceptance_criteria": [
            "✅ 编译与内存安全：CMake 一键编译通过，在开启 `-fsanitize=address,undefined` 模式下运行全部 GTest 单元测试与 10 万次并发请求实现 0 内存泄漏、0 非法访问；",
            "✅ 崩溃一致性验证：写入 1 万条记录后强制 `kill -9` 进程，重启服务后数据 100% 完整恢复，人为篡改 WAL 末尾字节能准确告警并跳过损坏帧；",
            "✅ 性能基准报告：附上单机 50 并发连接下的 QPS 与 P99 延迟实测数据，以及至少 1 次使用 `perf`/`gdb` 定位并修复性能卡点的复盘记录。",
        ],
        "prerequisites": "已掌握 C++ 基础语法（类、STL 容器、智能指针）与 Linux 基本命令。",
        "estimated_effort": "约 40–55 小时（按每周 10–12 小时计，约 4 周；直接建立在你已有的 C++ 与系统课程基础上，学习曲线平滑）。",
        "resume_value": (
            "把你在课堂上学的 C++、数据结构、操作系统和计算机网络真正串成一个可验证的工业级系统作品；"
            "相比单纯罗列“熟悉 C++/STL/Linux”，面试官看到你在 ASan/TSan 下的排障记录、TCP 半包处理和 WAL 崩溃恢复实验，会直接认可你的工程扎实度。"
        ),
        "staged_extensions": [
            "阶段 1（MVP · 第1-2周）：单线程内存跳表/哈希表 + WAL 文件持久化与 GoogleTest 单测；",
            "阶段 2（进阶 · 第3周）：接入 Linux `epoll` + 环形缓冲区协议解析 + 线程池并发控制；",
            "阶段 3（挑战 · 第4周+）：实现后台定时快照压缩 (Compaction) 或主从复制状态同步。",
        ],
        "reference_and_license": {
            "references": [
                "LevelDB 架构文档与 WAL 格式说明 (BSD-3-Clause License, https://github.com/google/leveldb)",
                "Redis RESP 协议规范文档 (BSD-3-Clause License, https://redis.io/docs/reference/protocol-spec/)",
            ],
            "reuse_vs_self_impl": (
                "允许复用 GoogleTest、spdlog（可选）与 C++ 标准库；"
                "核心的 SkipList/分段哈希表、WAL 编解码、RingBuffer 半包解析与 epoll 事件分发必须亲手编写，不得直接复制 muduo 或 LevelDB 源码。"
            ),
        },
    },
    {
        "project_id": "proj_qt_cpp_visualizer",
        "title": "项目三：基于 C++17 与 Qt6 的跨平台工业日志/三维模型检视与实时性能诊断桌面软件",
        "tier_priority": "P1_high_value_option",
        "priority_badge": "🎯 核心方向进阶 P1 · 承接 C++/OpenGL 切入高 WLB 工业/办公客户端",
        "difficulty_level": "进阶 (Intermediate)",
        "primary_languages": ["C++ (C++17)"],
        "skill_ids": ["lang_cpp", "fw_qt", "dom_graphics_3d_cad", "algo_concurrency", "tool_cmake_build", "tool_debug_profile"],
        "target_categories": ["software_cpp", "graphics_3d", "embedded_device", "game_engine"],
        "target_roles": ["C++客户端开发工程师", "工业软件/CAD研发工程师", "医疗影像/三维可视化软件工程师", "上位机软件工程师"],
        "project_goal": (
            "结合你已有的 OpenGL 基础与 C++ 能力，使用 Qt6 开发一款集“三维网格模型检视 (`QOpenGLWidget`) + 大体量设备日志异步解析 + 实时帧率/传感器曲线监控”于一体的跨平台桌面工具软件。"
        ),
        "core_features": [
            "1. 多线程解耦架构：采用工作线程异步流式读取并解析 100MB+ 工业运行日志或 OBJ/STL 三维网格文件，通过 Qt6 信号槽与环形队列更新 UI，确保主界面 60FPS 零卡顿；",
            "2. 三维视口与拾取交互：在 `QOpenGLWidget` 中集成你已有的着色器渲染逻辑，新增包围盒 (AABB/BVH) 射线求交鼠标点击拾取、线框/实体模式切换与法线可视化；",
            "3. 实时性能与数据面板：实时绘制帧耗时 (Frame Time ms)、顶点/面片统计、内存占用曲线，并支持按时间轴拖动回放关键帧状态；",
            "4. 状态导出与批处理命令行模式：支持将诊断报告一键导出为 JSON/CSV，并提供无界面 (`--headless`) 批处理校验模式供自动化测试调用。",
        ],
        "engineering_tasks": [
            "架构解耦：严格分离数据模型层 (`Core`)、渲染算法层 (`Renderer`) 与界面表现层 (`UI`)，使核心解析与几何计算模块可脱离 Qt GUI 独立运行单元测试；",
            "性能分析与渲染调试：使用 RenderDoc 抓取渲染帧核验 Uniform/VAO 状态，使用 ASan/Valgrind 排查模型反复加载卸载时的显存与堆内存泄漏；",
            "跨平台构建：编写 CMakeLists.txt 兼容 Windows (MSVC/MinGW) 与 Linux (GCC) 构建，配置 GitHub Actions 自动编译验证。",
        ],
        "knowledge_mapping": [
            "面向对象与设计模式：观察者模式（Qt 信号槽）、MVC/MVVM 界面解耦、RAII OpenGL 资源管理；",
            "计算机图形学与计算几何：MVP 坐标变换矩阵、射线与三角形求交 (Möller–Trumbore 算法)、AABB 层次包围盒加速；",
            "操作系统：GUI 事件循环机制 (Event Loop)、生产者-消费者多线程同步与内存映射文件 (`mmap`)。",
        ],
        "acceptance_criteria": [
            "✅ 界面响应度验证：加载 50 万面片 OBJ 模型或 200MB 日志文件时，进度条平滑更新且窗口拖动无“未响应”假死；",
            "✅ 算法与内存验证：核心几何求交与日志解析模块拥有独立单元测试且通过率 100%，连续加载/卸载模型 50 次内存占用无持续爬升；",
            "✅ 可展示交付物：仓库附带 3 张高清功能截图/GIF、RenderDoc 帧分析截图及清晰的架构分层图。",
        ],
        "prerequisites": "已具备 C++17 基础与入门级 OpenGL 着色器程序经验（你目前已满足！只需补充 Qt6 信号槽与界面布局）。",
        "estimated_effort": "约 35–50 小时（你在 OpenGL 部分已有现成代码基础，主要投入在 Qt6 多线程 UI 架构与 BVH 拾取算法，约 3–4 周课余时间）。",
        "resume_value": (
            "将你现有的小型 OpenGL 练习升级为“具备完整 GUI 交互、多线程解耦与性能剖析面板”的准工业级桌面软件；"
            "极度契合金山办公(WPS)、万兴科技、联影医疗、柏楚电子、中望软件、卡斯柯等高 WLB 办公/工业/医疗软件企业的招聘要求。"
        ),
        "staged_extensions": [
            "阶段 1（MVP · 第1-2周）：搭建 Qt6 主窗口 + `QOpenGLWidget` 迁入现有模型渲染 + 基础属性面板；",
            "阶段 2（进阶 · 第3周）：加入后台异步加载线程、AABB 射线点击拾取与实时帧耗时曲线；",
            "阶段 3（挑战 · 第4周+）：引入 BVH 树将百万面片射线拾取耗时从 O(N) 降至 O(log N)，并输出对比评测表。",
        ],
        "reference_and_license": {
            "references": [
                "Qt 6 Official Documentation - QOpenGLWidget & Threads (LGPLv3 / Commercial, https://doc.qt.io/qt-6/)",
                "LearnOpenGL 教程 (CC BY-NC 4.0, https://learnopengl.com/)",
            ],
            "reuse_vs_self_impl": (
                "允许动态链接使用 Qt6 开源库 (遵守 LGPLv3)、glm 数学库 (MIT) 与 tinyobjloader (MIT)；"
                "多线程任务调度、AABB/BVH 空间拾取算法与性能监控面板必须独立编码实现。"
            ),
        },
    },
    {
        "project_id": "proj_sdet_quality_gate",
        "title": "项目四：分布式接口契约测试、混沌故障注入与 CI 质量门禁平台 (Python + pytest + Docker)",
        "tier_priority": "P1_high_value_option",
        "priority_badge": "🎯 核心方向进阶 P1 · 切入高 WLB 外企/大厂测试开发 (SDET)",
        "difficulty_level": "入门 → 进阶 (Entry to Intermediate)",
        "primary_languages": ["Python"],
        "skill_ids": ["lang_python", "tool_test_auto", "tool_cicd_devops", "tool_docker_k8s", "cs_network_tcp", "tool_debug_profile"],
        "target_categories": ["testing_quality", "backend_dist", "sys_infra"],
        "target_roles": ["测试开发工程师 (SDET)", "质量工程研发工程师", "工程效能/DevOps工程师", "自动化测试工程师"],
        "project_goal": (
            "开发一套针对 Web/RPC 服务的自动化质量保障框架，支持数据驱动接口契约校验、网络延迟/超时/脏数据故障注入、并发性能基准测试，并集成进 GitHub Actions 实现自动阻断劣质代码的 CI 质量门禁。"
        ),
        "core_features": [
            "1. 声明式接口契约测试引擎：基于 `pytest` + `pydantic`/`jsonschema` 读取 YAML/JSON 用例集，自动校验 HTTP 状态码、响应 Schema、数据库落盘副作用与幂等性；",
            "2. 轻量级故障注入代理 (Fault Injection Proxy)：支持在测试夹具 (Fixture) 中模拟下游 API 超时、HTTP 500/429 限流、截断 JSON 与慢响应，自动验证被测系统是否按预期降级；",
            "3. 性能回归对比器：自动采集每次提交在并发压测下的 QPS、错误率与 P95 延迟，若较基线退化超过 15% 则自动标记 CI 失败；",
            "4. 可视化测试报告生成：输出含失败请求重放命令 (`curl`)、耗时分布与覆盖率统计的独立 HTML 诊断报告。",
        ],
        "engineering_tasks": [
            "测试框架设计：使用 pytest 插件机制 (`conftest.py` / 自定义 Hook) 实现测试环境自动初始化、脏数据自动回滚与失败自动重试定位；",
            "容器化编排：编写 `Dockerfile` 与 `docker-compose.yml`，在隔离容器中一键启动被测服务与测试执行器；",
            "CI/CD 流水线搭建：配置 `.github/workflows/`，在每次 Pull Request 时自动运行单测、契约测试、故障注入测试与覆盖率门禁（要求核心模块覆盖率 ≥ 80%）。",
        ],
        "knowledge_mapping": [
            "软件工程与质量保障：等价类划分、边界值分析、契约测试 (Contract Testing)、混沌工程容错验证、回归测试基线；",
            "计算机网络：HTTP 代理拦截原理、超时控制、状态码语义与重试退避策略；",
            "操作系统与容器：Linux 进程信号、Docker 网络命名空间与退出码传递。",
        ],
        "acceptance_criteria": [
            "✅ 自动化门禁闭环：GitHub Actions 流水线真实绿灯通过，且故意注入 1 处接口字段缺失或超时未捕获 Bug 时，流水线能准确标红拦截并输出复现日志；",
            "✅ 故障注入覆盖：至少包含 6 类真实异常注入场景（下游超时、502/429、非法 JSON、重复提交、并发抢占、空库冷启动）；",
            "✅ 量化产出：输出结构化 HTML/Markdown 测试与性能基线报告。",
        ],
        "prerequisites": "掌握基础命令与函数编写概念；若尚未系统学习 Python，可先花 1 周掌握 Python 基础语法与 `requests`/`pytest`。",
        "estimated_effort": "约 25–35 小时（按每周 10 小时计，约 2.5–3.5 周；甚至可直接以本 Career Agent 仓库为被测对象进行升级）。",
        "resume_value": (
            "外企（德州仪器、博世、恩智浦、施耐德）及大厂测试开发 (SDET) 岗最忌讳候选人只会“手工点点点”；"
            "该项目直接展示你具备“开发自动化测试工具、设计故障注入实验与搭建 CI 质量门禁”的硬核工程能力。"
        ),
        "staged_extensions": [
            "阶段 1（MVP · 第1周）：基于 pytest + YAML 数据驱动完成 30+ 条接口正常与边界契约测试；",
            "阶段 2（进阶 · 第2周）：实现 Mock 故障注入夹具（超时/429/畸形响应）与覆盖率门禁；",
            "阶段 3（挑战 · 第3周+）：加入并发压测性能基线自动比对与 Docker Compose 一键回归。",
        ],
        "reference_and_license": {
            "references": [
                "pytest Official Documentation (MIT License, https://docs.pytest.org/)",
                "Chaos Engineering Principles & Toxiproxy Design (MIT License)",
            ],
            "reuse_vs_self_impl": (
                "正常使用 pytest、requests/httpx、coverage.py 等开源测试库；"
                "测试夹具编排、故障注入模拟逻辑、性能退化判定规则与报告生成器由本人独立设计实现。"
            ),
        },
    },
    {
        "project_id": "proj_agent_rag_eval_sandbox",
        "title": "项目五：可追溯证据链的混合检索 (BM25+向量) 与 Agent 工具调用量化评测沙盒",
        "tier_priority": "P1_high_value_option",
        "priority_badge": "🎯 核心方向进阶 P1 · 将现有 Agent 好奇心转化为可量化的工程闭环",
        "difficulty_level": "进阶 (Intermediate)",
        "primary_languages": ["Python"],
        "skill_ids": ["lang_python", "dom_llm_agent_rag", "cs_sql_rdbms", "tool_test_auto", "tool_debug_profile"],
        "target_categories": ["ai_ml", "backend_dist", "testing_quality", "data_platform"],
        "target_roles": ["AI应用开发工程师", "Agent研发工程师", "大模型评测/数据工程师", "AI全栈工程师"],
        "project_goal": (
            "不依赖重型黑盒低代码平台，亲手实现一个支持“BM25 关键词 + 向量语义混合检索 + RRF 重排 + 严格 JSON Schema 工具调用 + 黄金测试集自动化评测”的轻量级 Agent 工程系统。"
        ),
        "core_features": [
            "1. 混合检索与证据溯源引擎：实现文档分块 (Chunking)、倒排索引 BM25 检索与向量检索，通过倒数排名融合 (RRF) 输出 Top-K 证据片段，并强制每个生成结论绑定精确的原文块 ID 与字符偏移量；",
            "2. 可靠工具调用状态机：实现带参数 Schema 校验、超时控制、指数退避重试、循环调用熔断与 Token 预算统计的 Function Calling 调度器；",
            "3. 事实核查与幻觉拦截器：自动比对模型输出中的数字/链接是否真实存在于检索上下文中，发现未授权虚构链接或数字时自动拦截重写；",
            "4. 黄金评测集 (Golden Benchmark) 看板：构建 50+ 条标注问答与工具调用评测集，一键跑分输出召回率 (Recall@K)、工具选择准确率、幻觉率、P95 延迟与单次 Token 成本。",
        ],
        "engineering_tasks": [
            "数据处理与算法实现：手写 BM25 评分公式与 RRF 融合算法，对比纯关键词检索、纯向量检索与混合检索在专业术语（如 C++/Qt6/届别）上的召回差异；",
            "容错与缓存设计：实现基于输入指纹的确定性本地缓存，当外部 LLM API 超时或限流时优雅降级至确定性规则提取；",
            "量化评测闭环：每修改一次 Prompt 或分块大小，必须运行评测脚本生成量化对比表格，用数据而非主观感觉指导迭代。",
        ],
        "knowledge_mapping": [
            "信息检索与数据结构：倒排索引 (Inverted Index)、TF-IDF / BM25 算法、余弦相似度、倒数排名融合 (RRF)；",
            "软件工程：有限状态机 (FSM)、JSON Schema 语法树校验、幂等缓存与熔断器模式；",
            "概率与统计：准确率 (Precision)、召回率 (Recall)、F1-Score、分位数延迟 (P50/P95/P99)。",
        ],
        "acceptance_criteria": [
            "✅ 零黑盒核心逻辑：混合检索 RRF、工具调用状态机与幻觉校验器代码清晰可读，不依赖 Dify/Coze 等拖拽工作流；",
            "✅ 评测数据可复现：运行 `python run_eval.py` 可在终端与 HTML 输出完整的对比实验表（展示单路检索 vs 混合检索的 Recall@5 提升幅度）；",
            "✅ 异常防御测试：通过模拟 API 429、畸形 JSON 返回与提示词注入攻击的单元测试。",
        ],
        "prerequisites": "需掌握 Python 基础语法、JSON 数据处理与 SQLite 基础使用。",
        "estimated_effort": "约 35–45 小时（可直接基于你当前维护的 AI 校招情报 Agent 仓库进行深度演进，复用率极高）。",
        "resume_value": (
            "彻底把你与只会“调 OpenAI 接口或用 Dify 拖工作流”的普通求职者区分开；"
            "合合信息、金山办公、字节跳动、腾讯等企业的 AI 应用/Agent 研发岗极度看重候选人是否做过**混合检索调优、幻觉拦截与量化评测集 (Evaluation)**。"
        ),
        "staged_extensions": [
            "阶段 1（MVP · 第1-2周）：实现倒排索引 BM25 + 向量余弦检索与 RRF 融合模块，附带 30 条评测样本；",
            "阶段 2（进阶 · 第3周）：实现带熔断、重试、预算控制与幻觉校验的工具调用状态机；",
            "阶段 3（挑战 · 第4周+）：构建可视化 A/B 实验对比看板，对比不同分块策略与重排权重的准确率-成本帕累托前沿。",
        ],
        "reference_and_license": {
            "references": [
                "Anthropic Building Effective Agents Engineering Guide",
                "Okapi BM25 & Reciprocal Rank Fusion (RRF) 标准信息检索论文与公式",
            ],
            "reuse_vs_self_impl": (
                "允许使用 requests/httpx、sqlite3、numpy 等基础库；"
                "BM25 计分、RRF 融合、工具调用状态机、幻觉校验器与评测统计脚本必须亲手独立实现，拒绝直接套壳 LangChain 高层封装。"
            ),
        },
    },
    {
        "project_id": "proj_explore_go_java_microservice",
        "title": "项目六：[探索模式 · 跨语言代表] 分布式定时任务调度与幂等执行引擎 (Go 或 Java Spring Boot)",
        "tier_priority": "P2_exploration_compare",
        "priority_badge": "🧭 探索对比项目 P2 · 低成本试水主流企业级服务端生态 (Go / Java)",
        "difficulty_level": "进阶 (Intermediate)",
        "primary_languages": ["Go (Golang) 或 Java 17"],
        "skill_ids": ["lang_golang", "lang_java", "fw_spring", "fw_rpc_microservice", "cs_sql_rdbms", "cs_cache_mq", "cs_distributed"],
        "target_categories": ["backend_dist", "sys_infra", "data_platform"],
        "target_roles": ["Java后端开发工程师", "Golang后端研发工程师", "分布式系统研发工程师", "金融科技/电信云平台研发工程师"],
        "project_goal": (
            "用 Go（语法接近 C 且极易上手）或 Java Spring Boot 实现一个支持 Cron 表达式解析、数据库行锁/Redis 分布式锁防重复触发、失败指数退避重试与执行节点健康心跳的轻量级任务调度服务。"
        ),
        "core_features": [
            "1. 任务注册与生命周期管理：支持动态创建、暂停、立即触发定时任务，持久化任务元数据与历史执行流水至 MySQL；",
            "2. 分布式防重调度：在多实例部署下，通过 MySQL 乐观锁版本号或 Redis `SET NX EX` 租约锁保证同一时刻同一任务只被调度一次；",
            "3. 异步执行与超时熔断：工作节点通过 HTTP/gRPC 接收调度指令，支持任务级超时强制取消、失败自动重试（最多 N 次）与死信告警记录；",
            "4. 时间轮 (Timing Wheel) 或最小堆调度器：实现高效内存到期检测结构，避免每秒全表扫描数据库。",
        ],
        "engineering_tasks": [
            "跨语言对照体验：通过实现这一紧凑的后端核心组件，亲身体会企业级服务端开发（事务、锁、幂等、RPC、监控）的真实日常工作内容；",
            "并发与容错测试：编写多实例并发争抢锁的集成测试，验证不会出现重复执行或任务漏调；",
            "工程构建：使用 Go Modules 或 Maven/Gradle 规范管理依赖，提供 Docker 一键启动编排。",
        ],
        "knowledge_mapping": [
            "分布式系统：分布式锁租约机制、幂等性设计 (Idempotency)、心跳检测与故障转移 (Failover)；",
            "数据结构与算法：最小堆 (Min-Heap) 优先队列、时间轮 (Hierarchical Timing Wheel)；",
            "数据库与网络：事务乐观锁 (`UPDATE ... WHERE version = ?`)、gRPC/HTTP 长连接与超时控制。",
        ],
        "acceptance_criteria": [
            "✅ 并发不重不漏：同时启动 3 个调度器实例运行 500 个短周期任务，数据库执行记录验证 0 重复触发、0 漏触发；",
            "✅ 故障恢复验证：人为杀死正在执行任务的 Worker 节点，主调度器能在租约过期后自动重派任务并记录重试次数；",
            "✅ 对比复盘文档：撰写一页对比总结（记录自己是否喜欢此类服务端并发与状态一致性工程问题）。",
        ],
        "prerequisites": "已具备 C/C++、操作系统与网络基础；Go 语言约 1 周即可入门，Java + Spring Boot 约需 3–4 周基础准备。",
        "estimated_effort": "Go 版本约 30–40 小时（约 3 周）；Java Spring Boot 版本约 45–60 小时（约 4–5 周，含框架生态熟悉时间）。",
        "resume_value": (
            "在全库计算机岗位中，Java 与 Go 后端占了极高比例（遍布银行科技、电信云、国央企数字化、外企与互联网）；"
            "完成此项目能让你瞬间打通主流服务端岗位投递通道，同时用真实编码体感验证自己是否喜欢服务端架构工作。"
        ),
        "staged_extensions": [
            "阶段 1（MVP）：单节点基于最小堆与 MySQL 的任务注册、定时触发与执行日志记录；",
            "阶段 2（进阶）：引入 Redis/MySQL 分布式锁支持多实例高可用防重调度与超时重试；",
            "阶段 3（挑战）：拆分 Scheduler 与 Worker，使用 gRPC 通信并支持分片广播任务。",
        ],
        "reference_and_license": {
            "references": [
                "XXL-JOB 调度架构设计文档 (GPLv3, 仅参考架构思想，严禁抄袭代码)",
                "Kafka Purgatory 分层时间轮论文与设计解析 (Apache-2.0 License)",
            ],
            "reuse_vs_self_impl": (
                "允许使用标准 Web/RPC 框架与 Cron 表达式解析库；"
                "核心的时间轮/最小堆调度循环、分布式抢锁状态流转与幂等重试状态机必须独立编写。"
            ),
        },
    },
    {
        "project_id": "proj_explore_embedded_can_sim",
        "title": "项目七：[探索模式 · 设备/工控代表] 工业/车载控制器报文仿真、超时容错与回放诊断器 (C/C++ + Linux)",
        "tier_priority": "P2_exploration_compare",
        "priority_badge": "🧭 探索对比项目 P2 · 零硬件板卡成本体验工业控制/车载嵌入式软件",
        "difficulty_level": "进阶 (Intermediate)",
        "primary_languages": ["C / C++"],
        "skill_ids": ["lang_cpp", "dom_embedded_rtos", "cs_linux_os", "algo_concurrency", "tool_cmake_build", "tool_test_auto"],
        "target_categories": ["embedded_device", "software_cpp", "testing_quality"],
        "target_roles": ["嵌入式软件工程师(Linux)", "工业控制/自动化软件工程师", "车载软件研发工程师", "机器人软件工程师"],
        "project_goal": (
            "无需购买昂贵开发板，直接基于 Linux 虚拟 SocketCAN / UDP 环回接口，用 C/C++ 实现一套模拟电机/传感器高频周期报文发送、CRC 校验、丢包超时安全状态机切换与离线黑匣子回放的设备软件系统。"
        ),
        "core_features": [
            "1. 高精度周期报文仿真器：基于 Linux `timerfd` + `epoll` 实现毫秒级 (10ms/50ms) 传感器与控制器数据帧打包、字节序转换与 CRC8/CRC16 校验码生成；",
            "2. 确定性安全状态机 (Safety FSM)：实现 `INIT -> STANDBY -> RUNNING -> DEGRADED -> FAULT_STOP` 状态机，当连续 3 帧校验失败或心跳超时 (>150ms) 时自动进入安全停机状态；",
            "3. 零动态分配环形缓冲区：在实时收发路径中使用预分配静态内存池与无锁单生产者单消费者 (SPSC) 环形队列，杜绝运行时内存碎片；",
            "4. 黑匣子录制与倍速回放器：将通信帧按二进制紧凑格式落盘，支持按 0.5x / 1x / 4x 速度重放历史故障帧以复现现场问题。",
        ],
        "engineering_tasks": [
            "通信协议定义：设计定长二进制帧结构（帧头、消息ID、序列号、负载、CRC、时间戳），编写位域解析与大小端转换函数；",
            "异常注入测试：编写自动化测试脚本人为注入丢包、乱序、比特翻转和延迟抖动，验证状态机是否 100% 触发安全保护；",
            "实时性与内存审计：统计周期任务的触发抖动 (Jitter)，确保热路径上无 `malloc`/`new` 动态分配。",
        ],
        "knowledge_mapping": [
            "嵌入式与计算机组成：大小端字节序 (Endianness)、位运算、CRC 循环冗余校验、定点数与浮点数编码；",
            "操作系统：高精度定时器 (`timerfd_create`/`clock_nanosleep`)、静态内存池、SPSC 无锁环形队列；",
            "软件工程：有限状态机表驱动法 (Table-Driven FSM)、看门狗 (Watchdog) 容错机制。",
        ],
        "acceptance_criteria": [
            "✅ 协议与状态机单测：位解包、CRC 校验与状态机全部状态迁移路径通过 100% 单元测试覆盖；",
            "✅ 故障注入保护验证：注入 5% 帧损坏或 200ms 断连时，系统在 1 个控制周期内准确切换至 `FAULT_STOP` 并生成故障快照文件；",
            "✅ 实时性报告：输出 10,000 次 10ms 周期调度的延迟分布统计（平均误差 < 0.5ms）。",
        ],
        "prerequisites": "掌握 C/C++ 基础（指针、结构体、位运算）与 Linux 基础环境。",
        "estimated_effort": "约 30–40 小时（约 3 周课余时间，完全在 Linux PC/虚拟机内完成，零硬件采购成本）。",
        "resume_value": (
            "汇川技术、卡斯柯、四方继保、博世、德州仪器、联影医疗等高 WLB 工业自动化、轨交电力与外企硬件龙头大量招聘此类 C/C++ 设备与控制软件岗；"
            "该项目让你无需买板子就能展示“定长二进制协议解析、CRC校验、表驱动安全状态机与实时性调优”的硬核能力。"
        ),
        "staged_extensions": [
            "阶段 1（MVP）：实现二进制报文封包/解包、CRC16 校验与表驱动安全状态机；",
            "阶段 2（进阶）：接入 Linux `timerfd` + UDP/SocketCAN 实现双进程实时收发与故障注入测试；",
            "阶段 3（挑战）：对接项目三的 Qt6 桌面端，实时绘制传感器波形与状态机跃迁图。",
        ],
        "reference_and_license": {
            "references": [
                "Linux SocketCAN Kernel Documentation (GPLv2, https://docs.kernel.org/networking/can.html)",
                "MISRA C / 工业控制表驱动状态机设计规范",
            ],
            "reuse_vs_self_impl": (
                "仅使用 Linux 标准系统调用与 C++ 标准库 / GoogleTest；"
                "二进制协议编解码、CRC 计算、SPSC 环形缓冲区与表驱动状态机均由本人独立实现。"
            ),
        },
    },
)


def recommend_projects_for_skills(
    skill_stats: dict,
    profile: dict | None = None,
) -> dict:
    """结合真实岗位高频技能统计结果与个人画像（含探索模式），生成分层项目推荐与实践路线图。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    prof_map = extract_skill_proficiency_map(prof)
    rec_mode = str(prof.get("recommendation_mode") or "exploration").strip().lower()

    overall_skills = {
        s["skill_id"]: s
        for s in (skill_stats.get("overall", {}).get("top_skills") or [])
    }
    for dim_list in (skill_stats.get("overall", {}).get("skills_by_dimension") or {}).values():
        for s in dim_list:
            overall_skills[s["skill_id"]] = s

    by_cat = skill_stats.get("by_category") or {}

    cpp_info = prof_map.get("c++") or {"level": "basic"}
    py_info = prof_map.get("python") or {"level": "none", "not_learned_yet": True}
    py_not_learned = bool(py_info.get("not_learned_yet"))

    enriched_projects = []
    for bp in PROJECT_BLUEPRINT_CATALOG:
        bound_skills = []
        for sid in bp["skill_ids"]:
            s_stat = overall_skills.get(sid)
            if s_stat:
                bound_skills.append({
                    "skill_id": sid,
                    "canonical_name": s_stat["canonical_name"],
                    "dimension_label": s_stat["dimension_label"],
                    "unique_job_count": s_stat["unique_job_count"],
                    "unique_job_pct": s_stat["unique_job_pct"],
                    "tier_role_label": s_stat["tier_role_label"],
                    "must_ratio_pct": s_stat["must_ratio_pct"],
                })
            elif sid in _SKILL_BY_ID:
                _, cname, dim_key, _, _ = _SKILL_BY_ID[sid]
                bound_skills.append({
                    "skill_id": sid,
                    "canonical_name": cname,
                    "dimension_label": SKILL_DIMENSION_LABELS.get(dim_key, dim_key),
                    "unique_job_count": 0,
                    "unique_job_pct": 0.0,
                    "tier_role_label": "方向专项技能",
                    "must_ratio_pct": 0,
                })

        bound_categories = []
        covered_job_sum = 0
        covered_companies: list[str] = []
        for ckey in bp["target_categories"]:
            c_stat = by_cat.get(ckey)
            if not c_stat:
                continue
            u_cnt = c_stat.get("unique_job_count", 0)
            covered_job_sum += u_cnt
            for co in (c_stat.get("top_companies") or [])[:3]:
                if co not in covered_companies:
                    covered_companies.append(co)
            bound_categories.append({
                "category_key": ckey,
                "category_label": c_stat.get("category_label", ckey),
                "unique_job_count": u_cnt,
                "sample_sufficient": c_stat.get("sample_sufficient", False),
            })

        pid = bp["project_id"]
        if pid in ("proj_cpp_epoll_kv", "proj_explore_embedded_can_sim"):
            skill_base_note = f"【当前技能基础】直接复用你正在学习的 C++ ({cpp_info.get('level', 'basic')})、CMake 与操作系统/网络基础。"
            learning_cost_note = "【学习成本：低~中低】无需切换主编程语言，主要学习成本在于 Linux 系统调用 (`epoll`/`timerfd`) 与 GDB/ASan 排障工具。"
        elif pid == "proj_qt_cpp_visualizer":
            skill_base_note = "【当前技能基础】直接复用你已有的 C++ 基础与小型 OpenGL 着色器项目经验。"
            learning_cost_note = "【学习成本：中低】渲染部分已有现成经验，只需额外花 1–2 周掌握 Qt6 信号槽与多线程 UI 解耦。"
        elif pid in ("proj_sql_redis_service", "proj_sdet_quality_gate", "proj_agent_rag_eval_sandbox"):
            if py_not_learned:
                skill_base_note = "【当前技能基础】你在 profile.yaml 中声明 Python『还没开始学习』，但已具备 C++ 编程思维与 Git 基础。"
                learning_cost_note = "【学习成本：中低（需先补 Python 基础）】建议先花 1–2 周掌握 Python 基础语法与 pytest，再进入项目实现（也可选择用 Go 语言实现服务端）。"
            else:
                skill_base_note = "【当前技能基础】可结合 Python/C++ 基础与 SQL/网络概念开展。"
                learning_cost_note = "【学习成本：低~中低】框架轻量、文档丰富，重点在于工程验证与调优实验。"
        else:
            skill_base_note = "【当前技能基础】属于跨语言/跨生态探索项目（Go 或 Java），可迁移复用你的 C++ 语法习惯与计算机基础。"
            learning_cost_note = "【学习成本：中】Go 语言约 1 周可上手；Java Spring Boot 生态较庞大，约需 3–4 周前置准备。"

        interest_fit_note = (
            "【工作内容兴趣适配】项目围绕『问题拆解 → 核心机制编码实现 → 自动化测试与故障注入 → 性能/指标量化对比』展开，"
            "契合你“喜欢分析问题、把想法做成可运行系统并通过实验验证效果”的工程偏好。"
        )

        gaps_bridged = []
        for s in bound_skills:
            sid = s["skill_id"]
            if sid == "cs_sql_rdbms":
                gaps_bridged.append(f"弥补『{s['canonical_name']}』缺口（全库 {s['unique_job_pct']}% 岗位要求，当前仅停留在概念）")
            elif sid == "cs_cache_mq":
                gaps_bridged.append(f"弥补『{s['canonical_name']}』缺口（全库 {s['unique_job_pct']}% 岗位要求，后端/平台岗核心必备）")
            elif sid == "tool_test_auto":
                gaps_bridged.append(f"建立『{s['canonical_name']}』工程证据（全库 {s['unique_job_pct']}% 岗位提及，摆脱纯玩具代码印象）")
            elif sid == "tool_debug_profile":
                gaps_bridged.append(f"沉淀『{s['canonical_name']}』量化排障案例（全库 {s['unique_job_pct']}% 岗位看重）")
            elif sid == "fw_qt":
                gaps_bridged.append("补齐跨平台桌面与工业软件客户端（Qt6）工程交互能力")
            elif sid in ("lang_golang", "lang_java", "fw_spring"):
                gaps_bridged.append(f"低成本试水主流企业级服务端技术栈（{s['canonical_name']}）")

        enriched_projects.append({
            **bp,
            "bound_skills": bound_skills,
            "bound_categories": bound_categories,
            "covered_unique_jobs": covered_job_sum,
            "representative_companies": covered_companies[:6],
            "separation_evaluation": {
                "current_skill_base": skill_base_note,
                "learning_cost": learning_cost_note,
                "interest_alignment": interest_fit_note,
            },
            "gaps_bridged": gaps_bridged[:4],
        })

    practice_advice = {
        "core_philosophy": (
            "本实践路线遵循『知识必须转化为可实现、可验证、可使用的工程成果』原则。"
            "这里的“先做 (P0)”建议严格依据【跨岗位类别覆盖广度】与【当前能力缺口紧迫度】得出，旨在帮你用最小沉没成本打通最多实习机会，绝不是替你永久锁定唯一职业路线。"
        ),
        "exploration_mode_active": rec_mode == "exploration",
        "profile_context_notes": [
            f"当前画像模式：{'探索模式 (Exploration) —— 跨语言、跨方向开放推荐' if rec_mode == 'exploration' else '聚焦模式'}",
            f"能力边界诚实核验：C++ 处于『{cpp_info.get('level', 'basic')}』阶段；Python 标注为『{'尚未开始系统学习（已在项目计划中预留 1–2 周语法上手期）' if py_not_learned else py_info.get('level', 'basic')}』。",
            "反误判保护已开启：不会因为你学过 C++ 或做过小型 OpenGL 练习就只推荐底层/图形项目，同时提供后端数据库、自动化测试、Agent评测、Go/Java微服务与工业控制仿真项目供横向体验。",
        ],
        "phased_roadmap": [
            {
                "phase_key": "P0_foundation_projects",
                "phase_title": "第一优先级（建议先做 P0 · 任选其一启动）：覆盖面最广、直接弥补核心工程缺口",
                "rationale": (
                    "根据真实岗位技能统计，『Linux/OS网络 + C/C++并发调试』与『SQL关系型数据库 + Redis缓存 + 自动化测试』是跨越后端、客户端、系统、测开、数据与AI工程的最高频必学底座。"
                    "优先完成项目一或项目二，可同时解锁 60% 以上计算机研发实习岗位的简历关与技术一面。"
                ),
                "recommended_project_ids": ["proj_sql_redis_service", "proj_cpp_epoll_kv"],
                "how_to_choose": (
                    "• 若想优先留在熟悉的 C++ 语境深化系统与网络能力 → 先做【项目二：C++17 Reactor 与 WAL 并发键值存储】；\n"
                    "• 若想尽快补齐数据库(MySQL/Redis)短板、顺带用 1–2 周学会 Python/Go 打开最宽的后端与数据/测开大门 → 先做【项目一：多级缓存与索引调优检索服务】。"
                ),
            },
            {
                "phase_key": "P1_direction_deepening",
                "phase_title": "第二优先级（方向进阶 P1 · 完成 P0 后按兴趣选做 1 个）：建立细分赛道差异化代表作",
                "rationale": (
                    "在拥有 1 个通用底座项目后，可根据你对『可视化的桌面/工业软件』、『自动化质量与故障注入』或『AI Agent 检索与评测』的实际体感，选做 1 个进阶项目形成简历上的“双项目组合”。"
                ),
                "recommended_project_ids": ["proj_qt_cpp_visualizer", "proj_sdet_quality_gate", "proj_agent_rag_eval_sandbox"],
                "how_to_choose": (
                    "• 倾向高 WLB 办公/工业/医疗桌面软件（金山办公/联影/万兴/柏楚）→ 选【项目三：C++17 + Qt6 三维检视与性能诊断桌面端】（直接升级你现有的 OpenGL 项目）；\n"
                    "• 倾向高 WLB 外企/大厂测试开发与质量工程（德州仪器/博世/恩智浦）→ 选【项目四：接口契约测试、混沌故障注入与 CI 质量门禁】；\n"
                    "• 倾向 AI 应用与智能体研发（合合信息/大厂 AI 工程岗）→ 选【项目五：BM25+向量混合检索与 Agent 量化评测沙盒】。"
                ),
            },
            {
                "phase_key": "P2_cross_stack_exploration",
                "phase_title": "第三优先级（跨方向探索试水 P2 · 按需体验）：用最小闭环验证其他语言与设备控制方向",
                "rationale": (
                    "当你想评估自己是否适合主流 Java/Go 企业级服务端开发，或工业自动化/车载嵌入式控制软件（汇川技术/卡斯柯等）时，无需盲目报班或啃几百集视频，可用 2–3 周完成这两个项目的 MVP 阶段即可获得真实体感。"
                ),
                "recommended_project_ids": ["proj_explore_go_java_microservice", "proj_explore_embedded_can_sim"],
                "how_to_choose": (
                    "• 想评估 Go/Java 企业级后端与分布式调度 → 体验【项目六：分布式定时任务调度与幂等执行引擎】；\n"
                    "• 想评估工业控制/车载设备软件（无需买开发板）→ 体验【项目七：工业/车载控制器报文仿真与安全状态机】。"
                ),
            },
        ],
        "anti_pitfalls": [
            "🚫 拒绝“玩具级空泛项目”：不要做只有增删改查页面、没有并发/索引/异常测试的“图书管理系统/个人博客/简单调包聊天框”；",
            "🚫 拒绝“直接 Fork 改名”：使用第三方库（如 Qt、FastAPI、pytest）完全正常，但项目的核心机制与压测实验必须亲手写、亲手跑，面试官一问边界条件就能区分真假；",
            "🚫 拒绝“战线过长半途而废”：严格按照每个项目卡片中的『阶段 1 (MVP) → 阶段 2 → 阶段 3』推进，先在 2 周内跑通带单测的阶段 1，它就已经是一个完整可用的成果！",
        ],
    }

    return {
        "tier_a_skill_overview": skill_stats,
        "tier_b_projects": enriched_projects,
        "tier_c_practice_advice": practice_advice,
    }


def _load_skill_project_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"snapshots": [], "last_report": None}


def _save_skill_project_state(state_path: Path, state: dict) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state["snapshots"] = (state.get("snapshots") or [])[-10:]
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("保存技能栈与项目推荐缓存状态失败: %s", exc)


def update_or_get_skill_projects_report(
    items: list[dict],
    profile: dict | None = None,
    conn=None,
    state_path: str | Path | None = None,
    force_refresh: bool = False,
    allow_ai: bool = False,
    ai_caller=None,
) -> dict:
    """低成本持续更新入口。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    mod_cfg = prof.get("skill_project_module") or {}
    if mod_cfg.get("enabled") is False:
        return {
            "enabled": False,
            "status": "disabled",
            "message": "技能栈与实现项目推荐模块已在配置中关闭 (skill_project_module.enabled: false)",
        }

    min_cat_samples = int(mod_cfg.get("min_category_samples", MIN_CATEGORY_UNIQUE_SAMPLES))
    prof_vid = job_filters.profile_version_id(prof)
    spath = Path(state_path) if state_path else DEFAULT_SKILL_PROJECT_STATE_PATH
    state = _load_skill_project_state(spath)
    last_report = state.get("last_report")

    now_iso = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    first_job_id = (items[0].get("job", items[0]).get("id") if items and isinstance(items[0], dict) else None)
    last_job_id = (items[-1].get("job", items[-1]).get("id") if items and isinstance(items[-1], dict) else None)
    quick_dataset_sig = f"{len(items)}:{first_job_id}:{last_job_id}:{min_cat_samples}"

    if (
        not force_refresh
        and isinstance(last_report, dict)
        and last_report.get("version_info", {}).get("profile_version_id") == prof_vid
        and last_report.get("version_info", {}).get("quick_dataset_sig") == quick_dataset_sig
        and last_report.get("tier_b_projects")
    ):
        reused = dict(last_report)
        vinfo = dict(reused.get("version_info") or {})
        vinfo["cache_hit"] = True
        vinfo["checked_at"] = now_iso
        reused["version_info"] = vinfo
        if conn is not None:
            try:
                save_skill_projects_to_db(conn, reused)
            except Exception:
                pass
        return reused

    skill_stats = aggregate_skills_by_category(items, prof, min_category_samples=min_cat_samples)
    cur_stats_fp = skill_stats["stats_fingerprint"]
    unique_total = skill_stats["overall"]["unique_job_count"]
    sample_sufficient = skill_stats["overall"]["sample_sufficient"]

    if not sample_sufficient and isinstance(last_report, dict) and last_report.get("tier_b_projects"):
        preserved = dict(last_report)
        preserved["tier_a_skill_overview"] = skill_stats
        vinfo = dict(preserved.get("version_info") or {})
        vinfo.update({
            "checked_at": now_iso,
            "cache_hit": True,
            "sample_sufficient": False,
            "update_reason": (
                f"当前有效独立岗位样本数 ({unique_total}) 低于最小阈值 ({MIN_OVERALL_UNIQUE_SAMPLES})，"
                "已自动保留上一份有效项目推荐并标记数据不足，避免小样本导致推荐剧烈抖动。"
            ),
        })
        preserved["version_info"] = vinfo
        return preserved

    if (
        not force_refresh
        and isinstance(last_report, dict)
        and last_report.get("version_info", {}).get("stats_fingerprint") == cur_stats_fp
        and last_report.get("version_info", {}).get("profile_version_id") == prof_vid
        and last_report.get("tier_b_projects")
    ):
        reused = dict(last_report)
        vinfo = dict(reused.get("version_info") or {})
        vinfo["cache_hit"] = True
        vinfo["checked_at"] = now_iso
        reused["version_info"] = vinfo
        return reused

    report_bundle = recommend_projects_for_skills(skill_stats, prof)
    ai_note = ""
    used_ai = False
    if allow_ai or ai_caller is not None:
        try:
            caller = ai_caller
            if caller is None:
                import analyzer as _analyzer_mod
                caller = _analyzer_mod.call_deepseek_api
            top_sk_names = [s["canonical_name"] for s in skill_stats["overall"]["top_skills"][:6]]
            sys_p = "你是严谨的计算机工程教学与校招项目导师。请根据最新岗位高频技能统计，用100字以内给出一条针对本科生工程项目实践的精炼洞察。"
            usr_p = f"独立岗位样本：{unique_total}个；高频技能Top6：{', '.join(top_sk_names)}；画像版本：{prof_vid}"
            raw_resp = caller(sys_p, usr_p)
            if raw_resp and str(raw_resp).strip():
                ai_note = str(raw_resp).strip()[:240]
                used_ai = True
        except Exception as exc:
            logger.warning("技能项目模块可选 AI 洞察调用未完成，使用确定性工程蓝图: %s", exc)

    report_vid = "sp-" + hashlib.sha256(f"{cur_stats_fp}:{prof_vid}".encode("utf-8")).hexdigest()[:8]
    version_info = {
        "report_version_id": report_vid,
        "module_version": SKILL_PROJECT_VERSION,
        "generated_at": now_iso,
        "checked_at": now_iso,
        "stats_fingerprint": cur_stats_fp,
        "profile_version_id": prof_vid,
        "quick_dataset_sig": quick_dataset_sig,
        "unique_job_sample_count": unique_total,
        "raw_posting_count": skill_stats["overall"]["raw_posting_count"],
        "sample_sufficient": sample_sufficient,
        "cache_hit": False,
        "used_ai": used_ai,
        "ai_insight": ai_note,
        "update_reason": "首次生成或检测到岗位技能分布/个人画像发生实质变化，已更新统计与项目关联数据。",
    }

    full_result = {
        "enabled": True,
        "status": "ok",
        "version_info": version_info,
        **report_bundle,
    }

    state["last_report"] = full_result
    state.setdefault("snapshots", []).append({
        "report_version_id": report_vid,
        "generated_at": now_iso,
        "stats_fingerprint": cur_stats_fp,
        "profile_version_id": prof_vid,
        "unique_job_count": unique_total,
    })
    _save_skill_project_state(spath, state)

    if conn is not None:
        try:
            save_skill_projects_to_db(conn, full_result)
        except Exception as exc:
            logger.warning("写入技能统计与项目推荐至数据库失败（不影响主流程）: %s", exc)

    return full_result


def save_skill_projects_to_db(conn, report_data: dict) -> None:
    """将技能统计快照与项目推荐幂等写入 SQLite 数据库。"""
    vinfo = report_data.get("version_info") or {}
    stats = report_data.get("tier_a_skill_overview") or {}
    projects = report_data.get("tier_b_projects") or []
    advice = report_data.get("tier_c_practice_advice") or {}

    stats_fp = str(vinfo.get("stats_fingerprint") or "")
    prof_vid = str(vinfo.get("profile_version_id") or "")
    gen_at = str(vinfo.get("generated_at") or datetime.now(_CHINA_TZ).isoformat())
    if not stats_fp:
        return

    conn.execute(
        """INSERT INTO skill_stats_snapshots
           (stats_fingerprint, profile_version_id, unique_job_count, raw_posting_count,
            sample_sufficient, earliest_date, latest_date, stats_json, practice_advice_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(stats_fingerprint, profile_version_id) DO UPDATE SET
               unique_job_count=excluded.unique_job_count,
               raw_posting_count=excluded.raw_posting_count,
               sample_sufficient=excluded.sample_sufficient,
               stats_json=excluded.stats_json,
               practice_advice_json=excluded.practice_advice_json,
               created_at=excluded.created_at""",
        (
            stats_fp,
            prof_vid,
            int(vinfo.get("unique_job_sample_count") or 0),
            int(vinfo.get("raw_posting_count") or 0),
            1 if vinfo.get("sample_sufficient") else 0,
            str(stats.get("time_range", {}).get("earliest_date") or ""),
            str(stats.get("time_range", {}).get("latest_date") or ""),
            json.dumps(stats, ensure_ascii=False),
            json.dumps(advice, ensure_ascii=False),
            gen_at,
        ),
    )

    for proj in projects:
        pid = str(proj.get("project_id") or "")
        if not pid:
            continue
        conn.execute(
            """INSERT INTO skill_project_recommendations
               (project_id, stats_fingerprint, profile_version_id, title, tier_priority,
                difficulty_level, target_categories_json, target_skills_json, project_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, profile_version_id) DO UPDATE SET
                   stats_fingerprint=excluded.stats_fingerprint,
                   title=excluded.title,
                   tier_priority=excluded.tier_priority,
                   difficulty_level=excluded.difficulty_level,
                   target_categories_json=excluded.target_categories_json,
                   target_skills_json=excluded.target_skills_json,
                   project_json=excluded.project_json,
                   updated_at=excluded.updated_at""",
            (
                pid,
                stats_fp,
                prof_vid,
                str(proj.get("title") or ""),
                str(proj.get("tier_priority") or ""),
                str(proj.get("difficulty_level") or ""),
                json.dumps(proj.get("target_categories") or [], ensure_ascii=False),
                json.dumps(proj.get("skill_ids") or [], ensure_ascii=False),
                json.dumps(proj, ensure_ascii=False),
                gen_at,
            ),
        )
    conn.commit()

PROJECT_RECOMMENDATION_CATALOG = PROJECT_BLUEPRINT_CATALOG
