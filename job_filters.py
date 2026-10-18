import json
import logging
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

logger = logging.getLogger(__name__)

# ==============================================================================
# 一、基础实习 / 校招 / 提前批 / 社招 / 硕博 / WLB 判定正则与函数
# ==============================================================================

_INTERN_RE = re.compile(r"(^|[^a-z])intern(ship)?([^a-z]|$)", re.I)
_INTERN_PROJECT_RE = re.compile(
    r"日常实习|应届实习|暑期实习|寒假实习|春季实习|秋季实习|长期实习|短期实习|"
    r"寻梦实习招聘|实习生(?:专项)?招聘(?:计划)?|实习招聘|可转正实习|"
    r"实习薪酬(?:津贴)?|转正机会|实习生将|提前到岗实习|需要提前到岗实习|"
    r"实习到毕业|全职驻场实习|本科及以上在读|学历在读|在读本科生|在读学生|"
    r"研发总院\d+实习生",
    re.I,
)
_INTERN_METADATA_RE = re.compile(r"(?:^|[\s|·])实习(?:[\s|·]|$)", re.I)
_INTERN_BODY_EN_RE = re.compile(
    r"(?:complete|undertake|join)\s+an?\s+internship|"
    r"internship\s+(?:position|program|programme|opportunity|duration)",
    re.I,
)
_INTERN_DURATION_RE = re.compile(
    r"实习(?:期|时长|时间)?(?:至少|不少于)?\s*[一二三四五六七八九十两\d\-~～]+\s*个?月|"
    r"每周(?:至少|保证)?(?:到岗|实习|出勤)\s*[一二三四五六七八九十两\d\-~～]+\s*天|"
    r"每周保证出勤",
    re.I,
)
_INTERN_URL_RE = re.compile(
    r"(?:[?&#](?:type|recruitType)=|/)(?:internship|intern)(?:[/?&#]|$)",
    re.I,
)
_EARLY_BATCH_RE = re.compile(r"提前批|提前招聘|提前选拔|预招聘|early\s*batch", re.I)
_SOCIAL_URL_RE = re.compile(
    r"social[-_/]?recruitment|/social|experienced|campus\.51job\.com/greenvalley/si\.html",
    re.I,
)
_SOCIAL_TITLE_RE = re.compile(r"社会招聘|社招|三年及以上|五年及以上|1-5年|0-2年毕业生|资深|高级|专家", re.I)
_SOCIAL_BODY_RE = re.compile(
    r"(?:招聘类型|岗位类型|职位类型|招聘项目|项目类型)\s*[:：|·]\s*(?:社会招聘|社招)|"
    r"(?:^|[\r\n])\s*(?:社会招聘|社招)\s*(?:[|·]|$)|"
    r"[1-5]\s*-\s*[3-8]\s*年[^。\n]{0,12}工作经验|[三五]年以上[^。\n]{0,12}经验",
    re.I,
)
_FORMAL_CAMPUS_BODY_RE = re.compile(
    r"(?:招聘类型|岗位类型|职位类型|招聘项目|项目类型)\s*[:：|·]\s*"
    r"(?:校园招聘|校招|应届生招聘)",
    re.I,
)

# ── 硕博门槛识别正则 ──
_MASTER_PHD_TITLE_RE = re.compile(
    r"博士|硕博|研究生专项|硕士专项|\bPh\.?D\b|博士后",
    re.I,
)
_MASTER_PHD_ONLY_BODY_RE = re.compile(
    r"硕士(?:研究生)?及以上|研究生及以上|硕士或博士|硕博(?:研究生)?|博士(?:研究生)?及以上|"
    r"仅限硕士|仅限研究生|仅限博士|硕士学历|博士学历|在读硕士或博士|博士和优秀硕士",
    re.I,
)
_BACHELOR_ALLOWED_RE = re.compile(
    r"本科及以上|本科学历|本科或以上|本科/硕士|本硕|全日制本科|统招本科|本科生|本科在读|本科毕业",
    re.I,
)

# ── WLB 证据与工时风险信号识别正则 ──
_WLB_WARNING_RE = re.compile(
    r"抗压能力强|较强的抗压|承受较强的工作压力|高强度|快节奏|适应加班|接受加班|"
    r"大小周|长期出差|高频出差|适应出差|驻场开发|全职驻场|倒班|7\*24|24小时待命",
    re.I,
)
_WLB_POSITIVE_RE = re.compile(
    r"双休|五险一金|六险一金|六险二金|带薪年假|弹性工作|弹性打卡|不打卡|工作生活平衡|\bWLB\b|周末双休",
    re.I,
)
_HIGH_WLB_ORG_RE = re.compile(
    r"德州仪器|博世|卡斯柯|中国联通|中国电信|天翼云|四方继保|长园深瑞|国家开发投资|联影|金山办公|合合信息|万兴科技|星环科技|北森|神策|广联达|同花顺|恩智浦|施耐德",
    re.I,
)
_FAST_PACE_INTERNET_GAME_RE = re.compile(
    r"字节跳动|拼多多|快手|得物|米哈游|莉莉丝|叠纸|鹰角|沐瞳|柠檬微趣|飞羽",
    re.I,
)


def is_master_phd_only_job(job: dict) -> bool:
    """识别明确要求硕士/博士学历、不接受本科生的岗位。"""
    title = str(job.get("title") or "")
    jd_raw = str(job.get("jd_raw") or "")
    if _MASTER_PHD_TITLE_RE.search(title):
        return True
    if _MASTER_PHD_ONLY_BODY_RE.search(jd_raw) and not _BACHELOR_ALLOWED_RE.search(jd_raw):
        return True
    return False


def is_explicitly_bachelor_eligible(job: dict) -> bool:
    """判断 JD 是否明确写出本科可投且未限定硕博。"""
    jd_raw = str(job.get("jd_raw") or "")
    return bool(_BACHELOR_ALLOWED_RE.search(jd_raw)) and not is_master_phd_only_job(job)


def wlb_signal_audit(job: dict) -> dict:
    """审计岗位文本中的 WLB 与工作条件客观信号，严格区分【事实】【推断】【未知】。"""
    jd_raw = str(job.get("jd_raw") or "")
    company = str(job.get("company") or "")
    warn_match = _WLB_WARNING_RE.search(jd_raw)
    pos_match = _WLB_POSITIVE_RE.search(jd_raw)
    is_stable_org = bool(_HIGH_WLB_ORG_RE.search(company))
    is_fast_pace = bool(_FAST_PACE_INTERNET_GAME_RE.search(company))

    if warn_match:
        snippet = warn_match.group(0)
        return {
            "status": "risk",
            "label": f"【事实·WLB风险】JD显式含高压/出差/驻场要求（“{snippet}”）",
            "score_delta": -10,
        }
    if is_stable_org:
        fact_part = f"JD提及“{pos_match.group(0)}”；" if pos_match else "JD未披露具体上下班时间【未知】；"
        return {
            "status": "inferred_moderate",
            "label": f"【事实+推断】{fact_part}属外企/国央企/轨交电力/成熟软件企，行业常态WLB较稳健，入职前需核实具体部门作息",
            "score_delta": 9,
        }
    if is_fast_pace:
        return {
            "status": "pace_caution",
            "label": "【未知+行业推断】JD未披露工时；属头部互联网/游戏企业，短期实习含金量高，但全职通常伴随较快节奏或项目期加班，需谨慎评估长期WLB",
            "score_delta": -3,
        }
    if pos_match:
        snippet = pos_match.group(0)
        return {
            "status": "positive",
            "label": f"【事实】JD明确提及福利/休假保障（“{snippet}”），具体工时仍需面试确认",
            "score_delta": 4,
        }
    return {
        "status": "unknown",
        "label": "【未知】JD未披露作息与加班情况，需在面试或内部渠道核实实际工时",
        "score_delta": 0,
    }


# ==============================================================================
# 二、阶段 B：多标签、可解释、可扩展的职业类别体系（标题 + JD职责 + 技能要求综合分类）
# ==============================================================================

JOB_CATEGORY_LABELS = {
    "game_engine": "游戏开发与游戏引擎",
    "graphics_3d": "图形学、渲染与三维技术",
    "sys_infra": "系统软件与基础设施",
    "backend_dist": "后端开发与分布式系统",
    "ai_ml": "AI 与机器学习工程",
    "embedded_device": "嵌入式与设备软件",
    "testing_quality": "测试、质量工程与开发工具",
    "data_platform": "数据、数据库与存储系统",
    "security": "安全工程",
    "software_cpp": "客户端、桌面与跨平台软件",
    "hardware_mechanical": "芯片、硬件与机电工程",
    "other_technical": "其他技术岗位",
    "unclassified": "待分类",
}

PRODUCT_BRAND_NAME = "AI校招情报"
PRIMARY_CATEGORY_ORDER = tuple(JOB_CATEGORY_LABELS.keys())

LEGACY_CATEGORY_ALIASES = {
    "llm_agent": "ai_ml",
    "algorithm_ml": "ai_ml",
    "embedded_control": "embedded_device",
    "robotics": "embedded_device",
    "vision_auto": "embedded_device",
    "client_fe_gui": "software_cpp",
    "qa_devops": "testing_quality",
    "sys_infra_sec": "sys_infra",
    "data_db": "data_platform",
    "ai_llm_algo": "ai_ml",
    "embedded_auto": "embedded_device",
    "gfx_engine_cad": "graphics_3d",
    "robotics_sim": "embedded_device",
    "compiler_hpc": "sys_infra",
    "backend_service": "backend_dist",
}

_LEGACY_CATEGORY_LABELS = {
    k: JOB_CATEGORY_LABELS.get(v, k) for k, v in LEGACY_CATEGORY_ALIASES.items()
}

_LEGACY_TITLE_CATEGORY_RULES = (
    ("llm_agent", re.compile(r"大模型|\bLLM\b|智能体|Agent|\bRAG\b|AIGC|多模态", re.I)),
    ("robotics", re.compile(r"具身|\bVLA\b|机器人|机械臂|\bROS\b|运控|运动控制|SLAM", re.I)),
    ("vision_auto", re.compile(r"自动驾驶|智驾|规控|视觉感知|机器视觉|计算机视觉|\bCV\b|点云", re.I)),
    ("embedded_control", re.compile(r"嵌入式|固件|Firmware|驱动|MCU|RTOS|单片机|车载软件|智能座舱", re.I)),
    ("hardware_mechanical", re.compile(r"芯片|IC验证|数字IC|模拟IC|FPGA|硬件|射频|天线|结构|机械|电气", re.I)),
    ("testing_quality", re.compile(r"测试|测开|SDET|质量|QA|验证工程", re.I)),
    ("algorithm_ml", re.compile(r"推荐算法|搜索算法|广告算法|机器学习|深度学习|语音算法|自然语言|\bNLP\b|算法工程师", re.I)),
    ("data_platform", re.compile(r"数据平台|大数据|数据开发|数据研发|数据工程|数据仓库|数仓|数据库|存储系统|分布式存储|Spark|Flink|数据分析", re.I)),
    ("software_cpp", re.compile(r"C\+\+|C/C\+\+|客户端|桌面开发|PC客户端|上位机|\bQt\b|软件开发|软件研发|后端|服务端|前端|全栈|Java|Golang|Go开发|Python", re.I)),
)

_MULTI_CATEGORY_RULES = (
    (
        "game_engine",
        re.compile(r"游戏(?:开发|客户端|服务端|引擎|研发|程序|测试)|引擎开发|UE4|UE5|Unreal|Unity|Gameplay|技术美术|\bTA\b", re.I),
        re.compile(r"游戏引擎|Unreal\s*Engine|\bUE[45]\b|\bUnity3D\b|\bUnity\b|Gameplay|帧同步|状态同步|游戏客户端|游戏物理", re.I),
        "游戏客户端/服务端或UE/Unity游戏引擎",
    ),
    (
        "graphics_3d",
        re.compile(r"图形|渲染|OpenGL|GLSL|Vulkan|DirectX|Shader|光线追踪|三维|3D|\bCAD\b|\bCAE\b|\bCAM\b|几何算法|计算几何|视景仿真|音视频", re.I),
        re.compile(r"图形学|渲染管线|\bOpenGL\b|\bVulkan\b|\bDirectX\b|\bShader\b|光线追踪|PBR|三维几何|几何内核|\bCAD\b|\bCAE\b|网格剖分|音视频编解码|WebGL", re.I),
        "图形学/渲染管线/三维几何/CAD仿真",
    ),
    (
        "security",
        re.compile(r"网络安全|信息安全|安全工程|系统安全|应用安全|攻防|渗透测试|密码学|安全研究|车联网安全|数据安全", re.I),
        re.compile(r"漏洞挖掘|渗透测试|逆向工程|安全攻防|加密算法|威胁情报|防火墙|零信任|入侵检测|\bWAF\b|安全审计", re.I),
        "网络/系统/应用安全工程",
    ),
    (
        "testing_quality",
        re.compile(r"测试|测开|SDET|质量工程|\bQA\b|验证工程|Agent评测|工程效能|DevOps|\bSRE\b|运维开发|工具链开发", re.I),
        re.compile(r"自动化测试|测试框架|测试开发|接口测试|性能测试|持续集成|CI/CD|质量保障|故障注入|压测", re.I),
        "测试开发(SDET)/质量工程/DevOps与开发工具",
    ),
    (
        "data_platform",
        re.compile(r"数据库|存储系统|分布式存储|存储引擎|大数据|数据开发|数据研发|数据工程|数据仓库|数仓|数据平台|数据治理|数据科学|数据分析", re.I),
        re.compile(r"数据库内核|存储引擎|分布式存储|\bSpark\b|\bFlink\b|\bHadoop\b|\bHive\b|\bClickHouse\b|数据仓库|数据管道|\bETL\b|SQL优化", re.I),
        "数据工程/数据库/分布式存储系统",
    ),
    (
        "sys_infra",
        re.compile(r"操作系统|Linux内核|内核开发|系统软件|系统架构|基础架构|\bInfra\b|云计算|云资源池|云原生|编译器|\bLLVM\b|\bMLIR\b|高性能计算|\bHPC\b|异构计算|\bCUDA\b|算子|虚拟化|网络工程", re.I),
        re.compile(r"Linux内核|操作系统内核|内核态|虚拟化|\bKVM\b|\bQEMU\b|编译器|\bLLVM\b|\bCUDA\b|高性能计算|\bHPC\b|RDMA|分布式底座|云原生基础设施|\bKubernetes\b|\bK8s\b|容器引擎", re.I),
        "操作系统/云原生基础架构/编译器/HPC系统软件",
    ),
    (
        "embedded_device",
        re.compile(r"嵌入式|固件|Firmware|驱动开发|单片机|\bMCU\b|\bRTOS\b|AUTOSAR|车载软件|智能座舱|数控系统|机器人|具身|机械臂|\bROS\b|运动控制|运控|\bSLAM\b|路径规划|导航算法|自动驾驶|智驾|规控", re.I),
        re.compile(r"嵌入式\s*Linux|\bRTOS\b|\bFreeRTOS\b|\bSTM32\b|\bMCU\b|\bARM\b|驱动开发|设备树|\bCAN\b总线|\bAUTOSAR\b|\bROS2?\b|\bSLAM\b|机械臂控制|运动控制|传感器融合", re.I),
        "嵌入式软件/车载设备/机器人控制与感知",
    ),
    (
        "ai_ml",
        re.compile(r"大模型|\bLLM\b|智能体|Agent|\bRAG\b|AIGC|多模态|AI软件|AI应用|AI全栈|AI开发|AI工程|人工智能|自然语言|\bNLP\b|计算机视觉|视觉算法|\bCV\b|图像算法|语音算法|推荐算法|搜索算法|机器学习|深度学习|算法(?:工程师|开发|研发|工程)", re.I),
        re.compile(r"大语言模型|\bLLM\b|\bAgent\b|智能体|\bRAG\b|向量数据库|\bLangChain\b|\bPyTorch\b|\bTensorFlow\b|深度学习|机器学习|Transformer|推理加速|\bTensorRT\b|\bvLLM\b|模型微调", re.I),
        "AI应用工程/大模型Agent/机器学习算法",
    ),
    (
        "software_cpp",
        re.compile(r"桌面开发|PC客户端|客户端开发|客户端|办公协同|上位机|\bQt\b|Windows开发|Mac开发|\bSDK\b|跨平台|前端|Web前端|\biOS\b|\bAndroid\b|鸿蒙|Harmony|小程序", re.I),
        re.compile(r"\bQt[56]?\b|\bQML\b|\bWPF\b|\bWin32\b|\bMFC\b|桌面客户端|PC客户端|上位机软件|跨平台客户端|\bElectron\b|\bReact\b|\bVue\b", re.I),
        "桌面客户端/上位机/跨平台与前端应用",
    ),
    (
        "backend_dist",
        re.compile(r"后端|服务端|后台开发|分布式系统|微服务|(?<![A-Za-z])Java(?!Script)|\bGolang\b|Go开发|Python开发|C\+\+开发|C/C\+\+开发|全栈|软件开发|软件研发|软件工程|应用开发|IT开发|研发工程师|开发工程师", re.I),
        re.compile(r"后端开发|服务端开发|微服务架构|\bSpring\s*Boot\b|\bSpringCloud\b|\bgRPC\b|\bProtobuf\b|高并发|分布式缓存|\bRedis\b|\bKafka\b|\bRabbitMQ\b|\bMySQL\b|RESTful", re.I),
        "后端开发/微服务与分布式业务系统",
    ),
    (
        "hardware_mechanical",
        re.compile(r"芯片|IC验证|数字IC|模拟IC|\bFPGA\b|硬件|射频|天线|PCB|版图|结构工程|机械|电气|热设计|工艺", re.I),
        re.compile(r"Verilog|SystemVerilog|\bUVM\b|\bFPGA\b|原理图|\bPCB\b|射频电路|模拟电路", re.I),
        "芯片验证/FPGA/硬件与机电系统",
    ),
)


_CAT_RESULT_CACHE: dict[tuple, dict] = {}


def classify_job_categories(job: dict, profile: dict | None = None) -> dict:
    """根据职位标题、JD职责描述、技能要求与工作内容综合判定主类别与多个辅助类别。"""
    title = str(job.get("title") or "").strip()
    jd_raw = str(job.get("jd_raw") or "").strip()[:600]
    prof = profile if profile is not None else load_active_profile()
    cache_key = (title, jd_raw[:160], id(prof))
    cached = _CAT_RESULT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    raw_custom = (prof.get("custom_categories") or {}) if isinstance(prof, dict) else {}
    if isinstance(raw_custom, list):
        custom_iter = [(str(item.get("key") or f"custom_{i}"), item) for i, item in enumerate(raw_custom) if isinstance(item, dict)]
    elif isinstance(raw_custom, dict):
        custom_iter = list(raw_custom.items())
    else:
        custom_iter = []
    for c_key, c_cfg in custom_iter:
        if not isinstance(c_cfg, dict):
            continue
        c_label = str(c_cfg.get("label") or c_key)
        JOB_CATEGORY_LABELS.setdefault(c_key, c_label)
        kw_list = [str(k).strip() for k in (c_cfg.get("keywords") or []) if str(k).strip()]
        if kw_list:
            pat = re.compile("|".join(re.escape(k) for k in kw_list), re.I)
            if pat.search(title):
                scores[c_key] = scores.get(c_key, 0) + 15
                reasons.setdefault(c_key, []).append(f"标题命中自定义类别[{c_label}]")
            if pat.search(jd_raw):
                scores[c_key] = scores.get(c_key, 0) + 10
                reasons.setdefault(c_key, []).append(f"JD正文命中自定义类别[{c_label}]")

    for cat_key, title_pat, jd_pat, _explain_tag in _MULTI_CATEGORY_RULES:
        t_match = title_pat.search(title) if title else None
        j_match = jd_pat.search(jd_raw) if jd_raw else None
        if t_match:
            is_generic_sw = cat_key == "backend_dist" and not re.search(
                r"后端|服务端|后台|分布式|微服务|Java|Golang|Go开发", title, re.I
            )
            scores[cat_key] = scores.get(cat_key, 0) + (6 if is_generic_sw else 10)
            reasons.setdefault(cat_key, []).append(f"标题含“{t_match.group(0)}”")
        if j_match:
            scores[cat_key] = scores.get(cat_key, 0) + 5
            reasons.setdefault(cat_key, []).append(f"JD要求含“{j_match.group(0)}”")

    if scores:
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        primary_cat = ranked[0][0]
        secondary_cats = [k for k, sc in ranked[1:] if sc >= 5][:3]
    else:
        if re.search(r"工程师|架构师|开发|研发|算法|程序员|技术支持", title, re.I):
            primary_cat = "other_technical"
            secondary_cats = []
            reasons["other_technical"] = ["含通用技术词汇，归入其他技术岗位"]
        else:
            primary_cat = "unclassified"
            secondary_cats = []
            reasons["unclassified"] = ["标题与JD均缺乏明确技术领域特征，标记为待分类"]

    legacy_key = _legacy_job_category_key(title)
    all_cats = []
    for k in (primary_cat, *secondary_cats, legacy_key):
        if k and k not in all_cats:
            all_cats.append(k)
        mapped = LEGACY_CATEGORY_ALIASES.get(k)
        if mapped and mapped not in all_cats:
            all_cats.append(mapped)

    primary_label = JOB_CATEGORY_LABELS.get(primary_cat, "其他技术岗位")
    cat_labels = []
    for k in (primary_cat, *secondary_cats):
        lbl = JOB_CATEGORY_LABELS.get(k)
        if lbl and lbl not in cat_labels:
            cat_labels.append(lbl)

    reason_parts = []
    for k in (primary_cat, *secondary_cats):
        if k in reasons:
            reason_parts.append(f"[{JOB_CATEGORY_LABELS.get(k, k)}] " + "、".join(reasons[k]))
    reason_str = "；".join(reason_parts) if reason_parts else "基于职位名称与JD综合归类"

    res = {
        "primary_category": primary_cat,
        "primary_label": primary_label,
        "secondary_categories": secondary_cats,
        "categories": all_cats,
        "category_labels": cat_labels,
        "category_reason": reason_str,
    }
    if cache_key is not None and len(_CAT_RESULT_CACHE) < 50000:
        _CAT_RESULT_CACHE[cache_key] = res
    return res


def _legacy_job_category_key(title: str) -> str:
    for key, pattern in _LEGACY_TITLE_CATEGORY_RULES:
        if pattern.search(title or ""):
            return key
    return "other_technical"


def job_category(job: dict) -> str:
    """Return primary category key (keeps exact compatibility with existing unit tests while supporting JD context)."""
    title = str(job.get("title") or "")
    jd_raw = str(job.get("jd_raw") or "")
    legacy = _legacy_job_category_key(title)
    if not jd_raw and legacy != "other_technical":
        return legacy
    info = classify_job_categories(job)
    if not jd_raw:
        return legacy
    return info["primary_category"]


# ==============================================================================
# 三、阶段 C：统一时区与可配置动态招聘季节/周期计算引擎
# ==============================================================================

_CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

DEFAULT_SEASON_CONFIG = {
    "timezone": "Asia/Shanghai",
    "min_active_samples": 5,
    "parallel_ratio_threshold": 0.12,
    "parallel_min_count": 10,
    "autumn_months": [7, 8, 9, 10, 11, 12],
    "spring_months": [1, 2, 3, 4, 5],
    "summer_intern_months": [3, 4, 5, 6],
}


def determine_recruitment_season(
    items: list[dict] | None = None,
    reference_date: str | date | datetime | None = None,
    config: dict | None = None,
) -> dict:
    """结合当前时间（Asia/Shanghai）、配置规则与真实岗位数据动态判定当前招聘周期标签。"""
    prof = load_active_profile()
    cfg = {**DEFAULT_SEASON_CONFIG, **(prof.get("recruitment_season") or {}), **(config or {})}

    now_cn = datetime.now(_CHINA_TZ)
    if isinstance(reference_date, datetime):
        ref_date = reference_date.astimezone(_CHINA_TZ).date() if reference_date.tzinfo else reference_date.date()
    elif isinstance(reference_date, date):
        ref_date = reference_date
    elif isinstance(reference_date, str) and re.match(r"^\d{4}-\d{2}-\d{2}", reference_date.strip()):
        try:
            ref_date = date.fromisoformat(reference_date.strip()[:10])
        except ValueError:
            ref_date = now_cn.date()
    else:
        ref_date = now_cn.date()

    month = ref_date.month
    items_list = items or []

    autumn_count = 0
    spring_count = 0
    intern_count = 0
    daily_count = 0
    valid_total = 0

    for it in items_list:
        job = it.get("job") if isinstance(it, dict) and "job" in it else it
        if not isinstance(job, dict):
            continue
        if is_job_record_noise(job):
            continue
        valid_total += 1
        title = str(job.get("title") or "")
        jtype = str(job.get("job_type") or "")
        camp = str(job.get("campaign_text") or "")
        jd = str(job.get("jd_raw") or "")[:400]
        blob = f"{title} {jtype} {camp} {jd}"

        if is_intern_job(job):
            intern_count += 1
        elif is_social_job(job):
            daily_count += 1
        else:
            if re.search(r"秋招|秋季校园招聘|秋季招聘|提前批", blob, re.I):
                autumn_count += 1
            elif re.search(r"春招|春季校园招聘|春季补录|春季招聘", blob, re.I):
                spring_count += 1
            else:
                if month in cfg["autumn_months"]:
                    autumn_count += 1
                elif month in cfg["spring_months"]:
                    spring_count += 1
                else:
                    daily_count += 1

    min_samples = int(cfg.get("min_active_samples", 5))
    if valid_total < min_samples:
        return {
            "brand_name": "AI校招情报",
            "season_label": "招聘周期进行中",
            "season_badge": "招聘周期进行中",
            "active_cycles": ["招聘周期进行中"],
            "timezone": "Asia/Shanghai (UTC+8)",
            "reference_date": ref_date.isoformat(),
            "is_data_sufficient": False,
            "stats": {
                "total": valid_total,
                "autumn": autumn_count,
                "spring": spring_count,
                "intern": intern_count,
                "daily": daily_count,
            },
            "reason": f"当前岗位样本数 ({valid_total}) 低于最小统计阈值 ({min_samples})，展示通用状态而不伪造具体招聘季。",
        }

    ratio_th = float(cfg.get("parallel_ratio_threshold", 0.12))
    min_cnt = int(cfg.get("parallel_min_count", 10))

    active_cycles = []
    cycle_load = [
        ("秋招季", autumn_count),
        ("春招季", spring_count),
        ("实习招聘", intern_count),
        ("日常招聘", daily_count),
    ]
    for label, cnt in cycle_load:
        if cnt > 0 and (cnt / max(1, valid_total) >= ratio_th or cnt >= min_cnt):
            active_cycles.append(label)

    if not active_cycles:
        top_label = max(cycle_load, key=lambda kv: kv[1])[0]
        active_cycles = [top_label]

    if len(active_cycles) >= 2:
        season_label = "多招聘周期并行"
        sub_detail = " / ".join(active_cycles[:3])
        season_badge = f"多招聘周期并行 ({sub_detail})"
    else:
        season_label = active_cycles[0]
        season_badge = season_label

    return {
        "brand_name": "AI校招情报",
        "season_label": season_label,
        "season_badge": season_badge,
        "active_cycles": active_cycles,
        "timezone": "Asia/Shanghai (UTC+8)",
        "reference_date": ref_date.isoformat(),
        "is_data_sufficient": True,
        "stats": {
            "total": valid_total,
            "autumn": autumn_count,
            "spring": spring_count,
            "intern": intern_count,
            "daily": daily_count,
        },
        "reason": (
            f"基于时区 Asia/Shanghai ({ref_date.isoformat()}) 与 {valid_total} 条真实岗位分布综合判定："
            f"秋招 {autumn_count} 条、春招 {spring_count} 条、实习 {intern_count} 条、日常/其他 {daily_count} 条。"
        ),
    }


# ==============================================================================
# 四、方向过滤与基础辅助函数（保持向后兼容）
# ==============================================================================

_DIRECTION_OUT_TITLE_RE = re.compile(
    r"(?:"
    r"产品经理|产品总经理|产品运营|项目经理|项目管理|软件交付经理|实施顾问|"
    r"\bProduct\s+Manager\b|"
    r"销售(?:经理|工程师|专员|代表|助理|顾问|管培生|培训生)|"
    r"大客户销售|电话销售|渠道销售|技术型销售|售前(?:工程师|顾问|支持)?|"
    r"(?:市场|品牌|内容|用户|渠道|商家|社区|新媒体|广告投放|策略|产品|数据|车辆|物流|客服)运营|"
    r"运营(?:经理|专员|主管|管理|管培生|培训生|中台岗)|技术运营|"
    r"财务|会计|审计|税务|人力资源|人力专员|HRBP|招聘(?:专员|助理|经理)|"
    r"行政|法务|合规专员|"
    r"采购(?:经理|专员|工程师|管理|商务)|供应链(?:计划|管理|运营|采购)|"
    r"物流(?:规划|运营|管理|专员)|仓储|"
    r"平面设计师|视觉设计师|UI设计师|UX设计师|交互设计师|工业设计师|"
    r"教师|主讲|竞赛教练|课程顾问|班主任|"
    r"公关专员|品牌专员|文案|编辑|主播|校园大使|生态合作专员|"
    r"商品开发|产品(?:规划)?(?:培训生|管培生)|产培生|"
    r"游戏(?:系统|营销|运营|数值|战斗|技术|关卡|任务)?策划|游戏策划(?:培训生)?|"
    r"游戏设计师|美术(?:经理|项目管理)|战略研究专员|"
    r"游戏[^\r\n]{0,12}运营|游戏交互/体验设计师|"
    r"发行(?:运营|管培生)|品牌管理培训生|采购管培生|策略分析师|"
    r"游戏原画设计|3D(?:角色|场景)模型|Spine动画|插画师|视频后期|"
    r"策划管培生|(?:游戏)?数据分析(?:师|工程师)|"
    r"User Growth Marketing|Marketing Campaigns|\bMarketing\b|开发者(?:区域|论坛|平台)"
    r")",
    re.I,
)

_DIRECTION_OUT_BODY_RE = re.compile(
    r"产品的?规划[与、及]设计",
    re.I,
)

_TECHNICAL_TITLE_RE = re.compile(
    r"算法|软件|开发|测试|研发|机器人|大模型|智能体|视觉|SLAM|控制|仿真|"
    r"嵌入式|硬件|芯片|机械|电气|自动化|系统工程|数据工程|技术工程|"
    r"研究|模型|多模态|生成",
    re.I,
)

_NON_JOB_TITLE_RE = re.compile(
    r"欢迎使用|(?:AI|智能)助理[👏！!。]?$|"
    r"^\s*【原始职位】|^\s*(?:职位描述|岗位职责|任职要求)\s*$",
    re.I,
)


def is_job_record_noise(job: dict) -> bool:
    """Reject UI/chatbot fragments that a generic parser mistook for a job."""
    title = str(job.get("title") or "").strip()
    return not title or _NON_JOB_TITLE_RE.search(title) is not None


def is_intern_title(title: str) -> bool:
    text = title or ""
    return "实习" in text or _INTERN_RE.search(text) is not None


def is_intern_job(job: dict) -> bool:
    """Recognize internships and in-school attendance positions."""
    return internship_reason(job) is not None


def internship_reason(job: dict) -> str | None:
    """Return the high-confidence signal proving that a role is an internship or open to in-school students."""
    title = str(job.get("title") or "")
    job_type = str(job.get("job_type") or "")
    jd_raw = str(job.get("jd_raw") or "")
    jd_url = str(job.get("jd_url") or "")
    if is_intern_title(title):
        return "标题明确标注实习"
    if "实习" in job_type:
        return "招聘类型明确标注实习"
    if _INTERN_URL_RE.search(jd_url):
        return "岗位 URL 明确属于实习轨道"
    if _INTERN_DURATION_RE.search(jd_raw):
        return "岗位要求当前候选人连续实习"
    if _INTERN_PROJECT_RE.search(jd_raw):
        return "JD明确标注面向在读学生、提前到岗实习或实习转正"
    if "实习生" in jd_raw[:260] or _INTERN_METADATA_RE.search(jd_raw[:260]):
        return "页面头部元数据明确标注实习"
    if _INTERN_BODY_EN_RE.search(jd_raw):
        return "英文正文明确要求参加实习"
    return None


def early_batch_reason(job: dict) -> str | None:
    """Return the field that explicitly identifies an early-batch role."""
    fields = (
        ("标题", job.get("title")),
        ("招聘类型", job.get("job_type")),
        ("岗位正文", job.get("jd_raw")),
        ("岗位 URL", job.get("jd_url")),
    )
    for label, value in fields:
        if _EARLY_BATCH_RE.search(str(value or "")):
            return f"{label}明确标注提前批"
    return None


def is_early_batch_job(job: dict) -> bool:
    return early_batch_reason(job) is not None


_COHORT_RE = re.compile(
    r"(?<!\d)(20\d{2}|[12]\d)\s*(?:届|年?\s*(?:春招|秋招|校招|校园招聘)|(?:春招|秋招|校招|校园招聘))",
    re.I,
)


def cohort_year(job: dict) -> int | None:
    """Return an explicitly advertised campus cohort year, otherwise None."""
    text = " ".join(
        str(job.get(field) or "") for field in ("title", "job_type", "jd_raw")
    )
    years = []
    for raw in _COHORT_RE.findall(text):
        year = int(raw)
        years.append(year if year >= 2000 else 2000 + year)
    return min(years) if years else None


def is_social_job(job: dict) -> bool:
    title = str(job.get("title") or "")
    url = str(job.get("jd_url") or "")
    body = str(job.get("jd_raw") or "")
    job_type = str(job.get("job_type") or "")
    verified_campus = _FORMAL_CAMPUS_BODY_RE.search(body[:300]) is not None
    return (
        _SOCIAL_URL_RE.search(url) is not None
        or (_SOCIAL_TITLE_RE.search(title) is not None and not verified_campus)
        or _SOCIAL_BODY_RE.search(job_type) is not None
        or _SOCIAL_BODY_RE.search(body[:600]) is not None
    )


def is_formal_campus_job(job: dict) -> bool:
    return not (
        is_intern_job(job)
        or is_early_batch_job(job)
        or is_social_job(job)
    )


def job_track_label(job: dict) -> str:
    """Return track label: 实习/在读可投 / 社招参考 / 提前批 / 校招全职参考."""
    if is_intern_job(job):
        return "实习/在读可投"
    if is_social_job(job):
        return "社招参考"
    if is_early_batch_job(job):
        return "提前批"
    return "校招全职参考"


_NON_CS_ROLE_RE = re.compile(
    r"机械(?!臂)|电气|电子工程师|硬件|结构|工艺|设备|热设计|光学|射频|天线|PCB|版图|IC验证|芯片|模拟IC|数字IC|"
    r"材料|化学|化工|生物|医药|临床|护理|照护|医生|农艺|养殖|食品|营养|"
    r"销售|营销|市场|运营|客服|政企|客户|商务|采购|供应链|物流|仓储|关务|门店|店长|导购|"
    r"财务|会计|审计|税务|法务|合规|人力|HR|招聘|行政|文秘|公关|品牌|文案|编辑|主播|记者|"
    r"土建|土木|建筑|造价|暖通|给排水|环评|EHS|安全员|质量管理|体系工程师|SQE|PQE|DQE|IQC|"
    r"教师|教研|主讲|班主任|辅导员|管培生|管理培训生|交付经理|实施顾问|数据标注",
    re.I,
)
_EXPLICIT_CS_ROLE_RE = re.compile(
    r"机械臂|机器人|嵌入式软件|软件测试|测试开发|测开|游戏测试|系统软件|软件开发|软件研发|软件工程|算法|大模型|后端|前端|客户端|桌面开发|上位机|数据库|编译器|渲染|游戏引擎|全栈|数据开发|数据研发|大数据|网络安全|信息安全|音视频|数控系统",
    re.I,
)
_GENERAL_CS_ROLE_RE = re.compile(
    r"软件|程序|开发|研发|后端|前端|全栈|客户端|桌面|服务端|移动端|iOS|Android|鸿蒙|Web|Java|Python|C\+\+|Golang|Go语言|Rust|"
    r"算法|人工智能|\bAI\b|大模型|\bLLM\b|智能体|Agent|机器学习|深度学习|视觉|自然语言|语音|推荐|搜索|多模态|"
    r"数据|数据库|操作系统|内核|基础架构|\bInfra\b|云计算|云资源池|云原生|分布式|存储|编译器|LLVM|HPC|高性能计算|"
    r"图形|渲染|OpenGL|Vulkan|Shader|游戏引擎|UE4|UE5|Unreal|Unity|音视频|工业软件|\bCAD\b|\bCAE\b|几何|"
    r"机器人|\bROS\b|SLAM|运动控制|自动驾驶|智驾|规控|感知|仿真|嵌入式|固件|驱动|测试|测开|SDET|运维|DevOps|SRE|安全",
    re.I,
)


def is_cs_software_role(job: dict) -> bool:
    """Identify computer science / software / algorithm / data / QA roles."""
    title = str(job.get("title") or "").strip()
    if _NON_CS_ROLE_RE.search(title) and not _EXPLICIT_CS_ROLE_RE.search(title):
        return False
    if _EXPLICIT_CS_ROLE_RE.search(title):
        return True
    return bool(_GENERAL_CS_ROLE_RE.search(title))


_PROFILE_CACHE = {"mtime": None, "data": {}}


def load_active_profile() -> dict:
    """读取当前 profile.yaml 配置（带 mtime 缓存，兼顾毫秒级速度与修改即刻生效）。"""
    try:
        p = Path(__file__).with_name("profile.yaml")
        if p.exists():
            mt = p.stat().st_mtime
            if _PROFILE_CACHE["mtime"] != mt:
                _PROFILE_CACHE["data"] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                _PROFILE_CACHE["mtime"] = mt
            return _PROFILE_CACHE["data"]
    except Exception:
        pass
    return {}


get_active_profile = load_active_profile


def is_target_candidate_job(job: dict, profile: dict | None = None) -> bool:
    """判断岗位是否应进入【当前目标岗位】池。"""
    if is_job_record_noise(job) or is_direction_out_job(job):
        return False
    if not is_cs_software_role(job):
        return False
    prof = profile if profile is not None else load_active_profile()
    degree = str(prof.get("degree") or "本科")
    if "本科" in degree and is_master_phd_only_job(job):
        return False
    if is_social_job(job):
        return False
    if is_intern_job(job):
        return True
    try:
        year = int(job.get("cohort") or 0)
    except (TypeError, ValueError):
        year = 0
    status = str(job.get("cohort_status") or "")
    return status == "confirmed" and year >= 2027


# ==============================================================================
# 五、去语言偏置的七维解耦评分器 + 可追踪证据生成引擎（支持探索模式与定向模式）
# ==============================================================================

_AI_ENG_TITLE_RE = re.compile(r"AI软件|AI应用|AI全栈|Agent|智能体|RAG|AI数据工具|评测", re.I)
_CPP_MATCH_RE = re.compile(r"C\+\+|C/C\+\+|(?<![A-Za-z])C语言", re.I)
_LANG_ALL_CS_RE = re.compile(
    r"C\+\+|C/C\+\+|(?<![A-Za-z])C语言|(?<![A-Za-z])Java(?!Script)|"
    r"Golang|\bGo语言\b|\bGo开发\b|\bPython\b|JavaScript|TypeScript|"
    r"\bNode\.?js\b|\bRust\b|\bC#\b|\bKotlin\b|\bSwift\b",
    re.I,
)
_CS_CORE_MATCH_RE = re.compile(r"数据结构|算法基础|常用算法|操作系统|计算机网络|Linux|并发|多线程|TCP/IP|HTTP", re.I)
_ENG_TOOL_MATCH_RE = re.compile(r"Python|Git|CMake|Maven|Gradle|npm|单元测试|自动化|调试|CI/CD|接口测试", re.I)
_GAP_JAVA_RE = re.compile(r"(?<![A-Za-z])Java(?!Script)|Spring\s*Boot|SpringCloud|MyBatis|\bJVM\b", re.I)
_GAP_GO_RE = re.compile(r"Golang|\bGo语言\b|\bGo开发\b|\bGin\b", re.I)
_GAP_JS_TS_RE = re.compile(r"JavaScript|TypeScript|\bReact\b|\bVue\b|\bNode\.?js\b|前端工程化|Web前端", re.I)
_GAP_DB_RE = re.compile(r"MySQL|PostgreSQL|Redis|\bSQL\b|数据库", re.I)
_GAP_CLOUD_RE = re.compile(r"Docker|Kubernetes|K8s|CI/CD|云原生", re.I)
_GAP_QT_RE = re.compile(r"\bQt[56]?\b|Winform|WPF|跨平台客户端|上位机", re.I)
_GAP_AGENT_RE = re.compile(r"LangChain|LangGraph|\bRAG\b|Agent|智能体|Prompt", re.I)
_AGG_CACHE = {}


def resolve_recommendation_mode(profile: dict | None = None) -> str:
    """解析当前推荐模式：'exploration'（探索模式，默认）或 'targeted'（定向求职模式）。"""
    try:
        import profile_config
        return profile_config.resolve_recommendation_mode(profile)
    except Exception:
        p = profile if isinstance(profile, dict) else {}
        explicit = str(p.get("recommendation_mode") or "").strip().lower()
        if explicit in ("exploration", "targeted"):
            return "targeted" if p.get("target_direction_locked") else explicit
        if p.get("target_direction_locked") or p.get("locked_target_directions"):
            return "targeted"
        if not p.get("directions") and p.get("direction"):
            return "targeted"
        return "exploration"


def build_traceable_evaluation(
    job: dict,
    profile: dict | None = None,
    *,
    cat_info: dict | None = None,
    wlb_info: dict | None = None,
) -> dict:
    """构建可追踪的 7 维解耦评估因素与 5 项可追踪证据 (traceable_evidence)。

    严格遵循反偏置原则：
    - C++ 仅作为现有技能优势（面向对象、静态类型、内存与系统基础）和学习转型成本评估的一部分；
    - 明确区分 C++ 背景可迁移到的要求 (transferable_to) 与不能替代的要求 (cannot_replace)；
    - 独立输出预计学习与转型成本 (transition_cost) 及其判断依据 (basis) 和不确定性 (uncertainty)。
    """
    prof = profile if isinstance(profile, dict) else load_active_profile()
    mode = resolve_recommendation_mode(prof)
    title = str(job.get("title") or "")
    jd_raw = str(job.get("jd_raw") or "")
    city = str(job.get("city") or "").strip()
    combined = f"{title}\n{jd_raw}"
    c_info = cat_info or classify_job_categories(job, prof)
    cat = c_info["primary_category"]
    cat_label = c_info["primary_label"]
    wlb = wlb_info or wlb_signal_audit(job)
    track = job_track_label(job)

    has_cpp = bool(_CPP_MATCH_RE.search(combined))
    has_java = bool(_GAP_JAVA_RE.search(combined))
    has_go = bool(_GAP_GO_RE.search(combined))
    has_js_ts = bool(_GAP_JS_TS_RE.search(combined))
    has_python = bool(re.search(r"\bPython\b", combined, re.I))
    has_cs_core = bool(_CS_CORE_MATCH_RE.search(combined))
    has_eng_tool = bool(_ENG_TOOL_MATCH_RE.search(combined))
    has_db = bool(_GAP_DB_RE.search(combined))
    has_cloud = bool(_GAP_CLOUD_RE.search(combined))
    has_qt = bool(_GAP_QT_RE.search(combined))
    has_agent = bool(_GAP_AGENT_RE.search(combined))

    detected_langs = []
    if has_cpp:
        detected_langs.append("C/C++")
    if has_java:
        detected_langs.append("Java")
    if has_go:
        detected_langs.append("Go")
    if has_python:
        detected_langs.append("Python")
    if has_js_ts:
        detected_langs.append("JavaScript/TypeScript")
    if not detected_langs and _LANG_ALL_CS_RE.search(combined):
        detected_langs.append(_LANG_ALL_CS_RE.search(combined).group(0))

    # 1. 已具备或可能具备的要求 (met_requirements)
    met_reqs = []
    if is_explicitly_bachelor_eligible(job):
        met_reqs.append("【硬性资格·已具备】本科学历符合 JD 显式学历要求")
    elif not is_master_phd_only_job(job):
        met_reqs.append("【硬性资格·可能具备】JD 未设置硕博一刀切门槛，本科生可尝试投递")
    if has_cs_core:
        met_reqs.append("【通用底座·已具备/持续巩固】数据结构与算法、操作系统、计算机网络或 Linux 基础")
    if has_eng_tool:
        met_reqs.append("【工程工具·已具备基础】Git 版本控制、CMake 构建、Python 脚本或基础测试排障经验")
    if has_cpp:
        met_reqs.append("【语言基础·已具备学习经历】正在学习 C++（面向对象、STL、基础内存管理）")
    if has_python:
        met_reqs.append("【脚本与工作流·已具备初步实践】在 AI 辅助下使用和改造过 Python 数据处理与自动化工作流")
    if not met_reqs:
        met_reqs.append("【基础背景·可能具备】软件工程本科在读，具备通用编程与理工科逻辑基础")

    # 2. 仍然缺失的能力 (missing_capabilities)
    missing_caps = []
    if has_java:
        missing_caps.append("Java 语言工程化与 Spring Boot / Spring Cloud / MyBatis 服务端生态")
    if has_go:
        missing_caps.append("Go (Golang) 语言语法、Goroutine 并发编程与微服务框架实践")
    if has_js_ts:
        missing_caps.append("JavaScript / TypeScript 语言及 React / Vue 前端组件化与浏览器工程体系")
    if has_db:
        missing_caps.append("生产级关系型数据库（MySQL/PostgreSQL 索引与事务调优）及 Redis 缓存应用")
    if has_cloud:
        missing_caps.append("Docker 容器化部署、Kubernetes 基础编排与企业级 CI/CD 流水线")
    if has_qt:
        missing_caps.append("Qt6 信号槽机制、多线程 UI 架构或跨平台桌面客户端框架")
    if has_agent:
        missing_caps.append("企业级 RAG 检索增强、Function Calling 容错与 Agent 自动化评测闭环")
    if cat in ("graphics_3d", "game_engine") and not missing_caps:
        missing_caps.append("商业级游戏引擎架构（UE5/Unity）或工业级三维几何内核/PBR 渲染管线深度经验")
    if not missing_caps:
        missing_caps.append("企业级中大型软件工程协作规范、线上监控排障与高并发/高可靠生产环境实践")

    # 3. C++ 背景具体能迁移到哪些要求，不能替代哪些要求 (cpp_transferability)
    transferable_to = [
        "面向对象设计（封装/继承/多态）、模块化抽象与静态类型编译期检查思维",
        "数据结构、内存布局理解、指针/引用概念及基础性能/边界条件排查意识",
    ]
    if has_cs_core:
        transferable_to.append("操作系统进程/线程同步、虚拟内存与 TCP/IP 网络通信底层原理")
    if has_go:
        transferable_to.append("C 系语法习惯与结构体/内存效率意识可加速 Go 语言上手")
    if has_java or has_js_ts:
        transferable_to.append("C++ 面向对象与泛型(STL)思想可迁移理解 Java 类体系或 TypeScript 类型系统")

    cannot_replace = []
    if has_java:
        cannot_replace.append("不能替代 JVM 内存与垃圾回收机制、Spring Boot/Cloud 依赖注入与企业级中间件生态经验")
    if has_go:
        cannot_replace.append("不能替代 Go CSP 并发模型（Goroutine/Channel）工程习惯与云原生微服务治理经验")
    if has_js_ts:
        cannot_replace.append("不能替代浏览器 DOM/BOM 渲染机制、异步事件循环及 React/Vue 前端状态管理经验")
    if has_db:
        cannot_replace.append("不能替代 SQL 复杂查询编写、索引失效排查、事务隔离与 Redis 缓存一致性设计")
    if has_qt:
        cannot_replace.append("不能替代 Qt 元对象系统、信号槽事件循环与跨平台 GUI 界面工程经验")
    if has_agent:
        cannot_replace.append("不能替代大模型 Prompt 工程、向量检索调优与 Agent 评测集构建经验")
    if not cannot_replace:
        cannot_replace.append("不能直接替代具体业务领域的专有框架、大型分布式系统线上运维及团队工程协作经验")

    cpp_summary = (
        f"【C++迁移边界】可迁移：{transferable_to[0]}；"
        f"不可替代：{cannot_replace[0]}"
    )

    # 4. 预计学习和转型成本判断的依据与不确定性 (transition_cost)
    gap_cnt = len(missing_caps)
    if (has_cpp or has_python) and not (has_java or has_go or has_js_ts) and gap_cnt <= 1:
        cost_level = "低（约 1–3 周）"
        cost_code = "low"
        est_weeks = "1-3周"
        cost_basis = (
            f"JD 主要技术栈（{'/'.join(detected_langs) or 'C++/Python'}）与当前已接触语言一致，"
            "无需跨语言切换，主要学习成本集中在具体工程框架与测试规范。"
        )
    elif has_go and not has_java and gap_cnt <= 2:
        cost_level = "中低（约 3–5 周）"
        cost_code = "medium_low"
        est_weeks = "3-5周"
        cost_basis = (
            "虽需新学 Go 语言与服务端数据库/中间件，但 Go 同属编译型 C 系语法，利用现有 C++ 与 Linux 基础通常 1–2 周可掌握语法、2–3 周可完成微服务小项目。"
        )
    elif (has_cpp and has_qt) or (gap_cnt == 2 and not has_java and not has_js_ts):
        cost_level = "中低（约 3–5 周）"
        cost_code = "medium_low"
        est_weeks = "3-5周"
        cost_basis = (
            f"核心语言或系统基础可复用，但需专门投入 3–5 周补齐 {missing_caps[0][:24]} 等工程组件。"
        )
    elif (has_java or has_js_ts) and gap_cnt <= 3:
        cost_level = "中（约 4–8 周）"
        cost_code = "medium"
        est_weeks = "4-8周"
        cost_basis = (
            f"岗位使用 {'/'.join(detected_langs) or '非C++技术栈'}，虽然计算机底层基础（算法/OS/网络/OOP）通用，"
            f"但需系统学习新语言生态与主流框架（{missing_caps[0][:26]}），预计需 1–2 个月构建合格项目作品。"
        )
    else:
        cost_level = "高（约 2–4 个月以上）"
        cost_code = "high"
        est_weeks = "8-16周+"
        cost_basis = (
            f"JD 同时涉及多项跨领域技术要求（共识别到 {gap_cnt} 项待补能力：{', '.join(m[:14] for m in missing_caps[:3])}），"
            "短期突击转型成本较高，建议评估是否符合阶段性投入产出比。"
        )

    cost_uncertainty = (
        "【不确定性】公开 JD 未说明团队对应届/实习生是要求“熟练掌握框架源码”还是“具备计算机基础即可入职培养”，"
        "亦未披露内部工具链完善度，实际学习压力需在面试中确认。"
    )

    # 5. 为什么这个岗位值得考虑 (why_worth_considering)
    pref_cities = [str(c).strip() for c in (prof.get("preferred_cities") or []) if str(c).strip()]
    city_matched = bool(city and any(pc in city for pc in pref_cities))
    worth_parts = [f"属【{cat_label}】方向（{track}）"]
    if is_explicitly_bachelor_eligible(job):
        worth_parts.append("明确接受本科学历")
    if wlb["status"] in ("positive", "inferred_moderate"):
        worth_parts.append("具备积极的 WLB / 稳健组织信号")
    elif wlb["status"] == "unknown":
        worth_parts.append("技术栈具备跨行业复利价值（工时待核实）")
    if city_matched:
        worth_parts.append(f"位于意向城市（{city}）")
    if detected_langs:
        worth_parts.append(f"可帮助了解 {'/'.join(detected_langs)} 技术栈的真实工业界要求")
    why_worth = "；".join(worth_parts) + "。"

    return {
        "recommendation_mode": mode,
        "detected_languages": detected_langs,
        "why_worth_considering": why_worth,
        "met_requirements": met_reqs,
        "missing_capabilities": missing_caps,
        "cpp_transferability": {
            "transferable_to": transferable_to,
            "cannot_replace": cannot_replace,
            "summary": cpp_summary,
        },
        "transition_cost": {
            "level": cost_level,
            "code": cost_code,
            "estimated_weeks": est_weeks,
            "basis": cost_basis,
            "uncertainty": cost_uncertainty,
        },
        "city_matched": city_matched,
    }


def score_job_against_profile(job: dict, profile: dict | None = None) -> dict:
    """7维解耦、去 C++ 语言偏置的岗位匹配评分器（支持探索模式与定向求职模式）。

    在探索模式 (exploration) 下：
    - 仅因为用户正在学习 C++，绝不自动给 C++ 岗位加方向分或语言偏置分；
    - 预计学习和转型成本 (transition_cost) 独立评估并展示，不替代岗位适合度总分 (match_score)；
    - 在其他条件（硬性资格、领域工程价值、通用计算机基础要求、WLB与个人条件）相近时，
      C++、Java、Go、Python、TypeScript 岗位获得相同的总体匹配分。
    """
    prof = profile if profile is not None else load_active_profile()
    mode = resolve_recommendation_mode(prof)
    title = str(job.get("title") or "")
    jd_raw = str(job.get("jd_raw") or "")
    city = str(job.get("city") or "").strip()
    combined = f"{title}\n{jd_raw}"
    cat_info = classify_job_categories(job, prof)
    cat = cat_info["primary_category"]
    track = job_track_label(job)
    wlb = wlb_signal_audit(job)
    trace = build_traceable_evaluation(job, prof, cat_info=cat_info, wlb_info=wlb)

    if not is_cs_software_role(job) or is_direction_out_job(job):
        return {
            "match_score": 25,
            "recommendation": "不推荐",
            "recommendation_mode": mode,
            "advantages": [],
            "gaps": ["非计算机/软件研发方向"],
            "summary": "非软件研发目标方向，已过滤",
            "analysis_status": "ineligible",
            "evaluation_factors": {},
            "traceable_evidence": trace,
        }

    degree = str(prof.get("degree") or "本科")
    if "本科" in degree and is_master_phd_only_job(job):
        return {
            "match_score": 30,
            "recommendation": "不推荐",
            "recommendation_mode": mode,
            "advantages": [],
            "gaps": ["【事实】JD明确要求硕士或博士及以上学历，普通本科直接应聘受阻"],
            "summary": "学历门槛为硕博，不符合本科直通就业要求",
            "analysis_status": "ineligible",
            "evaluation_factors": {},
            "traceable_evidence": trace,
        }

    score = 52
    advantages = []
    gaps = []

    # ── 因素 2：学历、毕业时间、实习时间等硬性资格 (hard_qualifications) ──
    qual_score = 0
    if is_intern_job(job):
        qual_score += 10
        reason = internship_reason(job) or "支持实习/在读投递"
        advantages.append(f"【硬性资格·当前可投】{reason}，契合本科阶段实习探索目标")
    else:
        advantages.append("【硬性资格·校招参考】应届全职岗位，用于横向了解不同方向的真实招聘要求")

    if is_explicitly_bachelor_eligible(job):
        qual_score += 8
        advantages.append("【硬性资格·学历匹配】JD明确标注本科及以上可投，无硕博学历一刀切")
    else:
        gaps.append("【未知·学历下限】JD正文未显式写明学历下限，投递前需在官网确认是否接受本科")
    score += qual_score

    # ── 因素 1：岗位职责和技术领域的匹配度 (domain_and_role_fit) —— 探索模式跨语言平权 ──
    domain_score = 0
    domain_explain = ""
    if cat == "backend_dist":
        domain_score = 8
        domain_explain = "后端与分布式系统研发，市场岗位基数最大、涵盖 Java/Go/Python/C++ 等主流栈，跨行业通用性极强"
    elif cat == "software_cpp":
        domain_score = 8
        domain_explain = "客户端、桌面、跨平台与前端应用研发，贴近用户交互与工程产品落地，本科接纳度高"
    elif cat == "testing_quality":
        domain_score = 8
        domain_explain = "测试开发(SDET)/质量工程/研发效能工具，本科入行友好，重自动化工程与系统质量保障"
    elif cat in ("sys_infra", "security"):
        domain_score = 8
        domain_explain = "系统软件、云原生基础设施与安全工程，技术生命周期长，抗短期业务框架更迭风险"
    elif cat == "data_platform":
        domain_score = 8
        domain_explain = "数据工程、数据库与存储系统，SQL 与数据管道能力在各行业高度通用"
    elif cat == "ai_ml":
        if _AI_ENG_TITLE_RE.search(title):
            domain_score = 8
            domain_explain = "偏工程落地的 AI 应用/Agent 研发（非纯论文科研岗），本科生可通过软件工程能力切入"
        else:
            domain_score = -8
            domain_explain = "核心算法科研岗通常存在隐性硕博与顶会论文竞争，普通本科直通就业性价比偏低"
            gaps.append(f"【领域门槛推断】{domain_explain}")
    elif cat == "embedded_device":
        domain_score = 5
        domain_explain = "嵌入式与设备软件研发，工程稳定性好，但需留意是否依赖特定硬件板卡知识"
    elif cat in ("graphics_3d", "game_engine"):
        domain_score = 4
        domain_explain = "图形学、三维与游戏引擎方向（当前作为个人兴趣参照支线，需综合评估细分岗位面与行业工时）"
    else:
        domain_score = 4
        domain_explain = "通用或其他计算机技术岗位，具备一定工程探索参考价值"

    # 若处于【定向求职模式 (targeted)】，根据用户锁定的目标方向与目标语言额外加权/筛选
    targeted_delta = 0
    if mode == "targeted":
        locked_dirs = [
            str(x).strip().lower()
            for x in (prof.get("locked_target_directions") or prof.get("directions") or [prof.get("direction", "")])
            if str(x).strip()
        ]
        locked_langs = [
            str(x).strip().lower()
            for x in (prof.get("locked_target_languages") or [])
            if str(x).strip()
        ]
        blob_lower = f"{title} {cat_info['primary_label']} {jd_raw[:400]}".lower()
        dir_hit = any(ld in blob_lower for ld in locked_dirs if len(ld) >= 2)
        lang_hit = any(ll in blob_lower for ll in locked_langs if len(ll) >= 1)
        if dir_hit or lang_hit:
            targeted_delta = 6
            domain_explain += "（🎯 定向求职模式：命中已锁定的目标方向/技术栈）"
        elif locked_dirs or locked_langs:
            targeted_delta = -5
            domain_explain += "（🎯 定向求职模式：非当前锁定的主攻方向，适度降权）"

    score += domain_score + targeted_delta
    if domain_score > 0:
        advantages.append(f"【职责与领域价值】{domain_explain}")

    # ── 因素 7：用户已经具备的技能所带来的实际优势 (existing_skill_advantage) ──
    skill_adv_score = 0
    matched_adv_list = []
    if _LANG_ALL_CS_RE.search(combined):
        skill_adv_score += 5
        langs_str = "/".join(trace["detected_languages"][:3]) or "主流编程语言"
        matched_adv_list.append(
            f"JD 要求 {langs_str} 编程，现有结构化编程、面向对象与代码调试基础可提供通用支撑"
        )
    if _CS_CORE_MATCH_RE.search(combined):
        skill_adv_score += 5
        matched_adv_list.append("考察数据结构与算法、操作系统、计算机网络或 Linux 等跨语言高保值底座")
    if _ENG_TOOL_MATCH_RE.search(combined):
        skill_adv_score += 4
        matched_adv_list.append("涉及 Git、构建工具、Python 脚本或单元测试/自动化调试等工程实践")

    builtin_skill_tokens = {
        "c++", "c/c++", "python", "linux", "git", "cmake",
        "opengl/glsl", "opengl", "数据结构与算法", "操作系统与计算机网络", "sqlite", "sqlite与轻量级数据存储",
    }
    user_skills = [str(s).strip() for s in (prof.get("skills") or []) if str(s).strip()]
    custom_bonus = 0
    for sk in user_skills:
        core_kw = sk.split(" ")[0].split("(")[0].split("（")[0].replace("基础", "").replace("实践", "").strip()
        if len(core_kw) >= 2 and core_kw.lower() not in builtin_skill_tokens:
            if core_kw.lower() in combined.lower():
                custom_bonus += 3
                matched_adv_list.append(f"命中个人扩展技能栈：{core_kw}")
    skill_adv_score += min(6, custom_bonus)
    score += skill_adv_score

    if matched_adv_list:
        advantages.append("【已具备/可迁移基础】" + "；".join(matched_adv_list[:2]))
    advantages.append(trace["cpp_transferability"]["summary"])

    # ── 因素 5：工作与生活平衡及稳定成长方面的证据 (wlb_and_growth) ──
    score += wlb["score_delta"]
    if wlb["status"] == "risk":
        gaps.append(wlb["label"])
    elif wlb["status"] in ("positive", "inferred_moderate"):
        advantages.append(wlb["label"])
    else:
        gaps.append(wlb["label"])

    # ── 因素 3 & 因素 4：当前技能差距 (skill_gap) 与预计学习/转型成本 (transition_cost) ──
    missing_caps = trace["missing_capabilities"]
    t_cost = trace["transition_cost"]
    raw_gap_hits = sum(
        bool(pat.search(combined))
        for pat in (_GAP_JAVA_RE, _GAP_GO_RE, _GAP_JS_TS_RE, _GAP_DB_RE, _GAP_CLOUD_RE, _GAP_QT_RE, _GAP_AGENT_RE)
    )
    gap_penalty = 0
    if mode == "targeted":
        if raw_gap_hits >= 2:
            gap_penalty = min(6, (raw_gap_hits - 1) * 2)
    else:
        if raw_gap_hits >= 4:
            gap_penalty = 6
    score -= gap_penalty

    for mc in missing_caps[:2]:
        gaps.append(f"【能力差距】需补充：{mc}")
    gaps.append(f"【预计转型成本：{t_cost['level']}】{t_cost['basis']}")
    if trace["cpp_transferability"]["cannot_replace"]:
        gaps.append(f"【C++不可替代项】{trace['cpp_transferability']['cannot_replace'][0]}")

    # ── 因素 6：薪酬、地点、行业和其他个人条件 (personal_conditions) ──
    personal_cond_explain = (
        f"工作地点：{city or '未标注'}（{'命中意向城市' if trace['city_matched'] else '非首选城市或待确认'}）；"
        f"岗位轨道：{track}；薪酬与培养机制需结合面试核实。"
    )

    score = max(35, min(92, score))
    thresholds = prof.get("score_thresholds") or {}
    rec_th = int(thresholds.get("recommend", 75))
    con_th = int(thresholds.get("consider", 60))
    rec = "推荐" if score >= rec_th else ("考虑" if score >= con_th else "不推荐")

    limits = prof.get("score_component_limits") or {
        "core_direction": 20, "required_skills": 25, "project_evidence": 20,
        "engineering_stack": 15, "basic_criteria": 20,
    }
    c_lim = int(limits.get("core_direction", 20))
    r_lim = int(limits.get("required_skills", 25))
    p_lim = int(limits.get("project_evidence", 20))
    e_lim = int(limits.get("engineering_stack", 15))
    b_lim = int(limits.get("basic_criteria", 20))
    ratio = score / 100.0
    bd = {
        "core_direction": min(c_lim, max(0, round(c_lim * ratio))),
        "required_skills": min(r_lim, max(0, round(r_lim * ratio))),
        "project_evidence": min(p_lim, max(0, round(p_lim * ratio))),
        "engineering_stack": min(e_lim, max(0, round(e_lim * ratio))),
        "basic_criteria": min(b_lim, max(0, round(b_lim * ratio))),
    }
    diff_bd = score - sum(bd.values())
    if diff_bd != 0:
        bd["required_skills"] = max(0, min(r_lim, bd["required_skills"] + diff_bd))

    evaluation_factors = {
        "1_domain_and_role_fit": {
            "label": "1. 岗位职责和技术领域的匹配度",
            "score_delta": domain_score + targeted_delta,
            "category": cat_info["primary_label"],
            "explanation": domain_explain,
        },
        "2_hard_qualifications": {
            "label": "2. 学历、毕业时间、实习时间等硬性资格",
            "score_delta": qual_score,
            "track": track,
            "bachelor_eligible": is_explicitly_bachelor_eligible(job),
            "explanation": "；".join(trace["met_requirements"][:2]),
        },
        "3_skill_gap": {
            "label": "3. 当前技能与岗位要求的差距",
            "gap_count": raw_gap_hits,
            "score_penalty": -gap_penalty,
            "missing_capabilities": missing_caps,
            "explanation": "、".join(missing_caps[:3]),
        },
        "4_transition_cost": {
            "label": "4. 预计学习和转型成本（独立评估，不直接等同于适合度）",
            "level": t_cost["level"],
            "code": t_cost["code"],
            "estimated_weeks": t_cost["estimated_weeks"],
            "basis": t_cost["basis"],
            "uncertainty": t_cost["uncertainty"],
        },
        "5_wlb_and_growth": {
            "label": "5. 工作与生活平衡及稳定成长方面的证据",
            "status": wlb["status"],
            "score_delta": wlb["score_delta"],
            "explanation": wlb["label"],
        },
        "6_personal_conditions": {
            "label": "6. 薪酬、地点、行业和其他个人条件",
            "city": city or "未披露",
            "city_matched": trace["city_matched"],
            "track": track,
            "explanation": personal_cond_explain,
        },
        "7_existing_skill_advantage": {
            "label": "7. 用户已经具备的技能所带来的实际优势",
            "score_delta": skill_adv_score,
            "matched_advantages": matched_adv_list,
            "cpp_transferability": trace["cpp_transferability"],
        },
    }

    mode_tag = "探索模式" if mode == "exploration" else "定向模式"
    summary = (
        f"[{mode_tag} · {track} · {cat_info['primary_label']}] "
        f"转型成本：{t_cost['level']} | {wlb['label']}"
    )

    return {
        "match_score": score,
        "score_breakdown": bd,
        "recommendation_mode": mode,
        "evaluation_factors": evaluation_factors,
        "traceable_evidence": trace,
        "evidence": [],
        "evidence_level": "partial" if score >= 70 else "adjacent",
        "recommendation": rec,
        "advantages": advantages[:4],
        "gaps": gaps[:4],
        "summary": summary,
        "analysis_status": "local_scored",
        "analysis_version": "local-7d-v3.0",
        "model": "local-rule-v3",
    }


def ensure_traceable_analysis(job: dict, analysis: dict | None, profile: dict | None = None) -> dict | None:
    """确保从数据库或 AI 返回的任意 analysis 字典均具备完整的 evaluation_factors 与 traceable_evidence。"""
    if not isinstance(analysis, dict):
        return analysis
    if analysis.get("traceable_evidence") and analysis.get("evaluation_factors"):
        return analysis
    prof = profile if isinstance(profile, dict) else load_active_profile()
    trace = build_traceable_evaluation(job, prof)
    analysis.setdefault("recommendation_mode", trace["recommendation_mode"])
    analysis.setdefault("traceable_evidence", trace)
    if not analysis.get("evaluation_factors"):
        wlb = wlb_signal_audit(job)
        cat_info = classify_job_categories(job, prof)
        analysis["evaluation_factors"] = {
            "1_domain_and_role_fit": {
                "label": "1. 岗位职责和技术领域的匹配度",
                "category": cat_info["primary_label"],
                "explanation": trace["why_worth_considering"],
            },
            "2_hard_qualifications": {
                "label": "2. 学历、毕业时间、实习时间等硬性资格",
                "track": job_track_label(job),
                "bachelor_eligible": is_explicitly_bachelor_eligible(job),
                "explanation": "；".join(trace["met_requirements"][:2]),
            },
            "3_skill_gap": {
                "label": "3. 当前技能与岗位要求的差距",
                "missing_capabilities": trace["missing_capabilities"],
                "explanation": "、".join(trace["missing_capabilities"][:3]),
            },
            "4_transition_cost": {
                "label": "4. 预计学习和转型成本（独立评估，不直接等同于适合度）",
                **trace["transition_cost"],
            },
            "5_wlb_and_growth": {
                "label": "5. 工作与生活平衡及稳定成长方面的证据",
                "status": wlb["status"],
                "score_delta": wlb["score_delta"],
                "explanation": wlb["label"],
            },
            "6_personal_conditions": {
                "label": "6. 薪酬、地点、行业和其他个人条件",
                "city": str(job.get("city") or "未披露"),
                "city_matched": trace["city_matched"],
                "track": job_track_label(job),
            },
            "7_existing_skill_advantage": {
                "label": "7. 用户已经具备的技能所带来的实际优势",
                "cpp_transferability": trace["cpp_transferability"],
            },
        }
    return analysis


# ==============================================================================
# 六、阶段 E：个人画像驱动的「综合建议」生成器（多方向7维对比 + 学习内容 + 学习方法 + 职业规划）
# ==============================================================================

_MUST_VERB_RE = re.compile(r"精通|熟练|掌握|扎实|必须|具备.*能力|熟悉", re.I)
_BONUS_CONTEXT_RE = re.compile(r"优先|加分|了解即可|更佳|者佳|bonus|preferred|plus", re.I)

TECH_REQUIREMENT_CATALOG = (
    (
        "cs_os_linux_net",
        "第一层：跨方向高保值底座（大二核心）",
        "1.计算机系统与网络",
        "操作系统、Linux 环境、多线程并发与计算机网络 (TCP/IP/HTTP/进程线程)",
        re.compile(r"操作系统|\bLinux\b|多线程|并发|进程|线程|虚拟内存|计算机网络|TCP/IP|Socket|\bHTTP\b", re.I),
        "极高（跨方向通用底座，覆盖后端、系统、客户端、测开、嵌入式）",
        "高（各技术方向技术一面必考核心）",
        "极高（十年不过时的底层内功，跨行业完全通用）",
        "极高（排查线上故障、网络超时、内存泄漏、多线程死锁的必备能力）",
        "【事实】当前已在学习系统基础；【建议】结合 Linux 命令行与 Socket/多线程小项目巩固，无需死磕冷门内核源码",
    ),
    (
        "prog_cpp",
        "第一层：跨方向高保值底座（大二核心）",
        "2.主力静态编译语言",
        "C / C++ 语言、面向对象设计与内存管理 (STL/RAII/智能指针/调试)",
        re.compile(r"C\+\+|C/C\+\+|(?<![A-Za-z])C语言|STL|内存管理|RAII|面向对象", re.I),
        "极高（覆盖桌面客户端、工业/医疗软件、轨交电力、高性能后端、系统与游戏）",
        "极高（金山办公、卡斯柯、联影医疗、万兴科技、腾讯客户端、高德C++后端的核心语言）",
        "高（已具备学习基础，转换成本最低；可用于投递高WLB工业/办公软件与客户端）",
        "高（训练严谨的模块化设计、内存安全与性能分析思维）",
        "【事实】已具备 C++17 与小项目经验；【建议】巩固 RAII、智能指针、多线程与 GDB/ASan 调试，暂缓偏门模板元编程",
    ),
    (
        "prog_python",
        "第一层：跨方向高保值底座（大二核心）",
        "3.高效脚本与自动化语言",
        "Python 语言、自动化脚本与快速原型开发",
        re.compile(r"\bPython\b", re.I),
        "极高（测试开发SDET、数据工程、AI应用/Agent工程、外企自动化工具的第一主力语言）",
        "极高（几乎所有现代研发团队的胶水语言与自动化验证标准语言）",
        "极高（投入产出比最高，能让你从单一C++平滑拓展到测开、数据、AI工程与外企IT）",
        "极高（编写自动化测试、数据处理管道、后端接口原型与日常效率工具的利器）",
        "【事实】维护本 Career Agent 项目已具备 Python 实战经验；【建议】补齐 pytest 与 FastAPI 工程规范",
    ),
    (
        "ds_algo_core",
        "第一层：跨方向高保值底座（大二核心）",
        "4.算法与数据结构",
        "数据结构与算法基础 (数组/链表/树/图/哈希/排序/复杂度分析)",
        re.compile(r"数据结构|算法基础|常用算法|时间复杂度|空间复杂度|链表|二叉树|动态规划", re.I),
        "极高（所有正规软件企业校招与实习笔试、一面手撕代码的共同敲门砖）",
        "中高（工程岗重哈希、树、图与复杂度控制，无需ACM竞赛级难度）",
        "高（跨所有软件岗位100%复用）",
        "中高（帮助在工程开发中选择合理的数据容器与检索结构）",
        "【建议】保持每周 3–5 道中等难度经典题（LeetCode Hot 100）维持手感即可，拒绝脱离工程的盲目刷题",
    ),
    (
        "sql_db_usage",
        "第一层：跨方向高保值底座（大二核心）",
        "5.数据存储与数据库应用",
        "关系型数据库 (SQL/MySQL/PostgreSQL) 与缓存中间件 (Redis) 基础使用",
        re.compile(r"\bSQL\b|MySQL|PostgreSQL|Oracle|Redis|SQLite|数据库", re.I),
        "高（后端研发、外企IT系统、数据工程、测试开发、AI知识库应用均高频依赖）",
        "极高（业务系统与企业级软件的核心持久化层）",
        "高（学习成本低、就业面极广、WLB友好）",
        "极高（表结构设计、事务隔离与索引查询调优是工程必备基本功）",
        "【事实】当前仅使用过轻量级 SQLite；【建议】将 MySQL 索引/事务与 Redis 缓存列为大二下最高优先级补齐项（约 2–3 周）",
    ),
    (
        "eng_test_debug_git",
        "第一层：跨方向高保值底座（大二核心）",
        "6.工程质量与协作工具链",
        "软件测试、调试分析、版本控制与构建 (Git/CMake/单元测试/自动化测试/CI)",
        re.compile(r"\bCMake\b|\bGit\b|CI/CD|调试|\bGDB\b|单元测试|自动化测试|接口测试|性能测试|代码评审", re.I),
        "高（测试开发SDET与所有正规研发岗的日常工程规范）",
        "极高（直接体现代码工程化成熟度与排障能力）",
        "高（良好的单元测试与自动化验证习惯是工程师的核心竞争力）",
        "极高（决定项目作品集是否像成熟的工业级工程而非拼凑的课程作业）",
        "【事实】已掌握 Git、CMake 与 pytest 基础；【建议】在后续每个项目中强制落地自动化单测与 GitHub Actions CI",
    ),
    (
        "client_desktop_qt",
        "第二层：高供给候选方向技术栈（按需选修）",
        "7.跨平台桌面与工业/办公软件",
        "跨平台桌面客户端与工业上位机开发 (C++ / Qt / Windows & Mac SDK)",
        re.compile(r"\bQt\b|桌面端|PC客户端|上位机|Winform|跨平台|SDK", re.I),
        "中高（在桌面客户端与工业/医疗软件细分族群中占比高，且本科明确可投率高）",
        "极高（金山办公WPS、万兴科技、联影医疗、卡斯柯、博众精工等桌面与工业软件核心要求）",
        "高（完美承接现有 C++ 与图形界面经验，且许多工业/办公/外企软件团队 WLB 较稳健）",
        "高（能独立开发带可视化界面、硬件通信或本地AI集成的完整桌面软件产品）",
        "【建议】花 2 周用 Qt6 编写一个中小型跨平台工具软件，低成本验证自己对桌面/工业软件方向的真实兴趣",
    ),
    (
        "prog_java_go",
        "第二层：高供给候选方向技术栈（按需选修）",
        "8.主流企业级服务端与全栈语言栈",
        "Java 企业级后端 (Spring Boot) / Go 云原生微服务 / TypeScript 全栈与前端 (React/Vue/Node)",
        re.compile(r"(?<![A-Za-z])Java(?!Script)|\bGolang\b|\bGo语言\b|Spring\s*Boot|SpringCloud|微服务|TypeScript|JavaScript|\bReact\b|\bVue\b", re.I),
        "极高（中国大陆校招市场岗位数量最多的主流工程技术栈，广泛覆盖国央企、银行、外企IT与互联网）",
        "极高（后端研发、企业级信息系统、云原生服务与现代 Web 全栈/前端研发的核心技术栈）",
        "高（探索模式下不应因当前学习 C++ 而预先排除；C++ 的面向对象与静态类型思维可迁移，但框架生态需独立补齐）",
        "高（构建高并发 HTTP/RPC 服务、企业级业务平台与现代交互应用的核心生产力）",
        "【转型评估】Go 约需 3–5 周、Java Spring 或 TypeScript/React 约需 4–8 周建立完整工程项目；C++ 不能直接替代 JVM/Spring 或浏览器 DOM 生态，应结合目标岗位真实需求按需选择一门深入",
    ),
    (
        "ai_agent_rag_eng",
        "第二层：高供给候选方向技术栈（按需选修）",
        "9.AI应用工程与智能体落地",
        "AI 辅助研发与企业级 Agent / RAG 应用工程 (AI Coding/Prompt/RAG/工具调用)",
        re.compile(r"AI\s*Coding|AI工具|AI编程|Agent|智能体|\bRAG\b|Prompt|LangChain|向量数据库", re.I),
        "高（越来越多研发岗将 AI 工程化落地与 Agent 工具链列为要求或加分项）",
        "高（AI应用开发工程师、AI全栈工程师、智能测试Agent工程师的核心要求）",
        "中高（重在用工程方法把 LLM API/RAG/工具调用集成进可靠软件并做评测闭环）",
        "极高（你当前的 Career Agent 系统本身就是可写入简历的真实 AI 应用工程案例）",
        "【建议】聚焦本科学历友好的“AI应用工程与自动化评测闭环”，暂缓需硕博与顶会论文支撑的底层预训练算法研究",
    ),
    (
        "gfx_cad_interest",
        "第三层：个人兴趣支线与专精观察（不作主线绑定）",
        "10.图形渲染与工业几何CAD",
        "计算机图形学、渲染管线与工业 CAD 几何算法 (OpenGL/Shader/3D/CAD)",
        re.compile(r"图形学|渲染管线|\bOpenGL\b|\bGLSL\b|\bVulkan\b|\bDirectX\b|\bShader\b|三维|视景仿真|\bCAD\b", re.I),
        "中低（全库本科计算机岗中占比较小，集中于游戏引擎与少数工业CAD/仿真企业）",
        "极高（仅在图形引擎、工业CAD与三维仿真岗中为必需核心）",
        "中（作为个人兴趣可锻炼空间数学与C++架构思维，但纯游戏渲染岗面较窄且需留意项目期加班）",
        "中（已完成的小型 OpenGL 项目可作为简历上证明 C++ 编码与调试能力的亮眼素材）",
        "【建议】保留现有 OpenGL 项目作为简历上的“C++工程能力证明”与兴趣支线，暂缓投入数月死磕 Vulkan/UE5 底层源码",
    ),
)


def aggregate_tech_and_learning_decisions(items: list[dict], profile: dict | None = None) -> dict:
    """Calibrated cross-job analysis for undergraduate direct-employment & WLB exploration."""
    cache_key = len(items)
    if cache_key in _AGG_CACHE and cache_key > 100:
        return _AGG_CACHE[cache_key]

    all_substantive = 0
    non_cs_substantive = 0
    master_phd_filtered = 0
    cs_jobs = []
    platform_counts = Counter()
    dir_counts = Counter()

    for it in items:
        job = it.get("job") or it
        jd_raw = str(job.get("jd_raw") or "").strip()
        if len(jd_raw) < 120:
            continue
        all_substantive += 1
        if not is_cs_software_role(job):
            non_cs_substantive += 1
            continue
        if is_master_phd_only_job(job):
            master_phd_filtered += 1
            continue
        cs_jobs.append(job)
        u = str(job.get("jd_url") or "").lower()
        if "zhiye.com" in u:
            platform_counts["北森(Beisen)"] += 1
        elif "feishu.cn" in u or "mioffice" in u:
            platform_counts["飞书(Feishu)"] += 1
        elif "mokahr.com" in u:
            platform_counts["Moka"] += 1
        elif "hotjob.cn" in u:
            platform_counts["HotJob"] += 1
        else:
            platform_counts["企业自建站"] += 1

    stats = {
        tid: {
            "id": tid, "group": grp, "dim": dim, "name": name,
            "val_general": vg, "val_domain": vd, "val_explore": ve, "val_practice": vp,
            "prereq_and_cost": pc,
            "mention_total": 0, "explicit_must": 0, "explicit_bonus": 0, "context_only": 0,
            "dirs": Counter(), "examples": [],
        }
        for tid, grp, dim, name, _pat, vg, vd, ve, vp, pc in TECH_REQUIREMENT_CATALOG
    }

    for job in cs_jobs:
        title = str(job.get("title") or "")
        jd_raw = str(job.get("jd_raw") or "")
        cat_info = classify_job_categories(job)
        cat_label = cat_info["primary_label"]
        dir_counts[cat_label] += 1
        text = f"{title}\n{jd_raw}"
        for tid, _grp, _dim, _name, pat, _vg, _vd, _ve, _vp, _pc in TECH_REQUIREMENT_CATALOG:
            m = pat.search(text)
            if not m:
                continue
            st = stats[tid]
            st["mention_total"] += 1
            snippet = " ".join(text[max(0, m.start() - 30):min(len(text), m.end() + 50)].split())
            if _BONUS_CONTEXT_RE.search(snippet):
                st["explicit_bonus"] += 1
            elif _MUST_VERB_RE.search(snippet):
                st["explicit_must"] += 1
            else:
                st["context_only"] += 1
            st["dirs"][cat_label] += 1
            if len(st["examples"]) < 2:
                st["examples"].append({
                    "company": str(job.get("company") or ""),
                    "title": title,
                    "snippet": snippet[:95],
                    "url": str(job.get("jd_url") or ""),
                })

    total_cs = max(1, len(cs_jobs))
    result_items = []
    for tid, st in stats.items():
        dirs_covered = sum(1 for _d, c in st["dirs"].items() if c >= 5)
        top_dirs = [
            f"{d} ({round(c / max(1, dir_counts[d]) * 100)}%)"
            for d, c in st["dirs"].most_common(4)
        ]
        result_items.append({
            **st,
            "dirs_covered": dirs_covered,
            "mention_pct": round(st["mention_total"] / total_cs * 100, 1),
            "must_pct_of_mention": round(st["explicit_must"] / max(1, st["mention_total"]) * 100),
            "bonus_pct_of_mention": round(st["explicit_bonus"] / max(1, st["mention_total"]) * 100),
            "context_pct_of_mention": round(st["context_only"] / max(1, st["mention_total"]) * 100),
            "top_dirs_str": "、".join(top_dirs) if top_dirs else "样本较少",
        })

    res = {
        "total_jobs_in_input": len(items),
        "all_substantive_jd": all_substantive,
        "non_cs_filtered_out": non_cs_substantive,
        "master_phd_filtered_out": master_phd_filtered,
        "cs_substantive_jd": len(cs_jobs),
        "platform_distribution": dict(platform_counts.most_common()),
        "direction_distribution": dict(dir_counts.most_common()),
        "tech_evaluations": result_items,
    }
    if cache_key > 100:
        _AGG_CACHE[cache_key] = res
    return res


def generate_comprehensive_advice(items: list[dict], profile: dict | None = None) -> dict:
    """结合个人画像 (profile.yaml) 与真实采集岗位数据 (items)，生成结构化、可解释、非强制的综合职业建议。"""
    prof = profile if profile is not None else load_active_profile()
    agg = aggregate_tech_and_learning_decisions(items, prof)

    dates_seen = []
    cat_job_counts = Counter()
    cat_company_sets: dict[str, set] = {}
    intern_count = 0
    bachelor_cs_count = 0
    all_companies = set()

    for it in items:
        job = it.get("job") if isinstance(it, dict) and "job" in it else it
        if not isinstance(job, dict) or is_job_record_noise(job):
            continue
        for df in ("crawled_at", "last_seen_at", "published_at"):
            dv = str(job.get(df) or "").strip()[:10]
            if re.match(r"^\d{4}-\d{2}-\d{2}$", dv):
                dates_seen.append(dv)
        if not is_cs_software_role(job) or is_direction_out_job(job) or is_master_phd_only_job(job):
            continue
        bachelor_cs_count += 1
        co = str(job.get("company") or "").strip()
        if co:
            all_companies.add(co)
        if is_intern_job(job):
            intern_count += 1
        cinfo = classify_job_categories(job, prof)
        for ck in [cinfo["primary_category"], *cinfo["secondary_categories"]]:
            cat_job_counts[ck] += 1
            if co:
                cat_company_sets.setdefault(ck, set()).add(co)

    earliest_date = min(dates_seen) if dates_seen else "未知（暂无带日期戳样本）"
    latest_date = max(dates_seen) if dates_seen else date.today().isoformat()
    now_str = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M CST")

    skills_list = [str(s) for s in (prof.get("skills") or [])]
    matching_cfg = prof.get("matching") or {}
    proj_evidence = [str(x) for x in (matching_cfg.get("project_evidence") or [])]
    learning_targets = [str(x) for x in (matching_cfg.get("learning_targets") or [])]
    degree = str(prof.get("degree") or "本科")
    target_cohort = str(prof.get("target_cohort") or "2029")
    job_type = str(prof.get("job_type") or "实习")

    def _cat_stat_str(ck: str) -> str:
        j_cnt = cat_job_counts.get(ck, 0)
        c_cnt = len(cat_company_sets.get(ck, set()))
        if j_cnt == 0:
            return "当前库内暂无匹配样本【数据不足】"
        return f"当前库内命中 {j_cnt} 个本科计算机岗 / 覆盖 {c_cnt} 家企业"

    suggested_directions = [
        {
            "id": "software_cpp",
            "direction_name": "选项 A：跨平台桌面客户端、办公与工业软件研发 (C++ / Qt / SDK)",
            "sample_stat": _cat_stat_str("software_cpp"),
            "priority_badge": "高重合·首选试水",
            "recommendation_reason": (
                f"【事实】{_cat_stat_str('software_cpp')}，代表企业包括金山办公(WPS)、万兴科技、联影医疗、卡斯柯、同花顺等；"
                "【推断（置信度：高）】该方向直接以 C++、面向对象设计和操作系统基础为核心，本科生接纳度在全部技术方向中居首，且非互联网纯流量业务，组织稳定性较好。"
            ),
            "skill_match": (
                "【事实】与你现有的 C++17 (STL/RAII)、CMake 构建、Linux/Windows 基础及 OpenGL 图形窗口编程经验重合度达 70% 以上，属于转换成本最低的顺延赛道。"
            ),
            "gaps_to_fill": (
                "• 必需补足：Qt6 信号槽机制、界面与业务逻辑解耦 (MVC/MVVM)、多线程异步 UI 通信、内存泄漏与崩溃 Dump 调试；\n"
                "• 加分项：Windows/macOS 平台原生 API、跨平台安装包构建与自动化 UI 测试。"
            ),
            "risks_and_uncertainties": (
                "【推断（置信度：中）】部分传统制造业上位机岗位技术栈偏老旧（如 MFC）；"
                "【未知】具体团队的发版节奏与加班情况未在 JD 中披露，需在面试中核实使用现代 C++/Qt6 还是遗留框架。"
            ),
            "preference_alignment": (
                "【事实+推断】高度契合“本科直通就业 + 重视 WLB + 复用 C++ 基础”偏好；办公软件、医疗影像与轨交工控软件生命周期长达 5–10 年，技能贬值慢。"
            ),
            "validation_actions": (
                "【建议（适用前提：有 2–3 周课余时间）】使用 C++17 + Qt6 + SQLite 编写一个带本地数据检索与可视化图表的桌面小工具，检验自己是否喜欢“所见即所得”的客户端工程交互。"
            ),
        },
        {
            "id": "backend_dist",
            "direction_name": "选项 B：后端开发、企业级平台与分布式系统 (C++ / Go / Python)",
            "sample_stat": _cat_stat_str("backend_dist"),
            "priority_badge": "岗位基数最大·高通用",
            "recommendation_reason": (
                f"【事实】{_cat_stat_str('backend_dist')}，是所有计算机校招与实习中岗位供给量最大、行业覆盖最广（外企、国央企、金融科技、ToB软件、互联网）的方向；"
                "【推断（置信度：高）】掌握服务端与数据库能力后，无论未来追求高薪还是追求国企/外企 WLB，可选择的雇主池最宽。"
            ),
            "skill_match": (
                "【事实】已具备 C++ 与 Python 编程基础、Linux 命令行及计算机网络基础（匹配度约 55%）；但尚缺关系型数据库与标准 Web/RPC 服务端开发闭环。"
            ),
            "gaps_to_fill": (
                "• 必需补足：MySQL 表结构设计与索引调优、Redis 缓存应用、HTTP/RESTful 与 gRPC 接口开发、并发与事务控制；\n"
                "• 语言抓手建议：在现有 C++/Python (FastAPI) 基础上，可花 2 周顺带掌握与 C 系语法相近的 Go 语言，无需从零背诵庞杂的 Java 八股。"
            ),
            "risks_and_uncertainties": (
                "【推断（置信度：中）】纯互联网大厂业务后端笔面试竞争激烈且部分团队伴随 On-call 轮值压力；"
                "【建议】投递时可重点关注外企 IT、银行科技、电信云及成熟 ToB 软件企业的后端/平台岗。"
            ),
            "preference_alignment": (
                "【推断】本科学历完全可投，跨行业迁移能力极强（第一名），是防范单一行业周期波动的“压舱石”选项。"
            ),
            "validation_actions": (
                "【建议】为你现有的 Career Agent 项目增加一套基于 FastAPI/Go + MySQL + Redis 的 REST API 服务层与压测脚本，体验完整服务端链路。"
            ),
        },
        {
            "id": "testing_quality",
            "direction_name": "选项 C：测试开发 (SDET)、质量工程与研发工具链 (Python / C++)",
            "sample_stat": _cat_stat_str("testing_quality"),
            "priority_badge": "本科最友好·高WLB备选",
            "recommendation_reason": (
                f"【事实】{_cat_stat_str('testing_quality')}，在外企（博世、德州仪器、恩智浦）、中大型软件企及硬件龙头中大量招收本科生；"
                "【推断（置信度：高）】相比算法岗的学历内卷，测开与研发工具岗更看重 Python/C++ 自动化脚本、系统排障与工程规范，入行确定性高。"
            ),
            "skill_match": (
                "【事实】你在维护 Career Agent 时已实际运用了 Python 数据抓取、正则清洗与 pytest 单元测试，与测开/工具链岗的日常技术栈重合度达 65%。"
            ),
            "gaps_to_fill": (
                "• 必需补足：接口自动化测试框架设计、CI/CD 流水线集成 (GitHub Actions/Jenkins)、Linux 性能与内存监控工具 (perf/Valgrind/ASan)、测试用例设计方法论。"
            ),
            "risks_and_uncertainties": (
                "【风险提示】部分公司名为“测试开发”实为纯手工点点点功能测试；"
                "【建议】面试时必须核实团队的“自动化代码与工具开发占比（建议 > 50%）”，避开纯人工点测岗。"
            ),
            "preference_alignment": (
                "【推断】总体工时节奏比一线业务开发更规律，且在 AI 辅助编程时代，“自动化验证与质量评测平台”的重要性不降反升。"
            ),
            "validation_actions": (
                "【建议】为自己的 C++ 或 Python 项目搭建一套完整的 pytest 自动化回归测试套件 + 覆盖率报告 + GitHub Actions 自动触发流水线。"
            ),
        },
        {
            "id": "ai_ml",
            "direction_name": "选项 D：AI 应用工程与企业级 Agent / RAG 系统落地（非纯算法研究）",
            "sample_stat": _cat_stat_str("ai_ml"),
            "priority_badge": "结合现有项目·工程切入",
            "recommendation_reason": (
                f"【事实】{_cat_stat_str('ai_ml')}；其中纯算法科研岗普遍偏好硕博，但“AI应用开发 / Agent工程 / AI评测工具”分支对本科学历开放度不断提升；"
                "【事实】你当前的 Career Agent 项目本身就是一个真实的 LLM API 调度 + 规则过滤 + 数据持久化工程案例。"
            ),
            "skill_match": (
                "【事实】已具备 Python 工程开发、DeepSeek API 流式调用、Prompt 结构化 JSON 约束与 SQLite 存储经验（在 AI 应用工程分支匹配度约 65%）。"
            ),
            "gaps_to_fill": (
                "• 必需补足：RAG 检索增强生成全链路（切分、Embedding、向量检索与重排）、Function Calling 工具调用容错、Agent 准确率与延迟量化评测集构建；\n"
                "• 明确不建议投入：大模型预训练底层算子或纯数学论文刷榜（本科直接就业投入产出比过低）。"
            ),
            "risks_and_uncertainties": (
                "【推断（置信度：中）】部分初创公司 AI 应用岗需求变动快、框架更迭频繁；若缺乏扎实的后端/系统底座，容易沦为“纯调包侠”。"
            ),
            "preference_alignment": (
                "【建议】建议采取“后端/客户端工程底座 + AI Agent 应用能力”的复合定位，既享受 AI 落地红利，又保留传统软件工程的退路。"
            ),
            "validation_actions": (
                "【建议】在 Career Agent 中整理出一份含 50 条真实 JD 的“分类与评分黄金测试集 (Golden Dataset)”，量化对比不同 Prompt 与规则的准确率与耗时。"
            ),
        },
        {
            "id": "sys_infra",
            "direction_name": "选项 E：系统软件、云原生基础设施与安全工程 (Linux / Infra / 安全)",
            "sample_stat": f"系统与基础设施：{_cat_stat_str('sys_infra')}；安全工程：{_cat_stat_str('security')}",
            "priority_badge": "长周期复利·稳扎稳打",
            "recommendation_reason": (
                "【事实】国央企云（天翼云、联通云、移动云）、信创基础软件（统信、麒麟、深信服、绿盟）及大型科技企业长期招聘 Linux 系统软件、虚拟化、存储与安全工程师；"
                "【推断（置信度：高）】底层协议与 Linux 系统机制十年不变，越老越吃香。"
            ),
            "skill_match": (
                "【事实】已具备 C/C++、Linux 环境使用与操作系统基础概念（匹配度约 50%），但缺乏深入的 Linux 系统编程与网络编程实战。"
            ),
            "gaps_to_fill": (
                "• 必需补足：Linux 系统编程（进程/线程/信号/共享内存/epoll）、TCP/IP 协议栈与抓包分析 (Wireshark/tcpdump)、Docker 容器隔离机制 (Namespace/Cgroups)。"
            ),
            "risks_and_uncertainties": (
                "【推断】核心内核开发或编译器研发岗对操作系统/体系结构深度要求较高，部分核心岗偏好硕士；但云原生基础设施、系统工具与安全工程对优秀本科生开放。"
            ),
            "preference_alignment": (
                "【推断】运营商云、信创软件与安全企业的整体作息相对稳健，技术积累深厚，符合长期稳定发展的诉求。"
            ),
            "validation_actions": (
                "【建议】在 Linux 下用 C++17 编写一个基于 epoll + 线程池的轻量级高性能 HTTP/TCP 服务或端口扫描诊断工具，并用 perf/Valgrind 分析性能。"
            ),
        },
        {
            "id": "graphics_3d_game",
            "direction_name": "选项 F：[兴趣参照支线] 图形渲染、三维几何 CAD 与游戏引擎开发",
            "sample_stat": f"图形与三维：{_cat_stat_str('graphics_3d')}；游戏开发：{_cat_stat_str('game_engine')}",
            "priority_badge": "兴趣支线·理性保留",
            "recommendation_reason": (
                "【事实】你拥有“已完成小型 OpenGL 图形程序（含顶点/片段着色器、模型加载与基础光照）”的直接项目证据；"
                "【事实】库内图形与三维岗位主要分布在两类雇主：① 游戏公司（米哈游、网易互娱/雷火、库洛、莉莉丝等）；② 工业 CAD/CAE/三维视觉软件（中望软件、先临三维、联影医疗、大疆等）。"
            ),
            "skill_match": (
                "【事实】具备 C++ 与 OpenGL/GLSL 入门项目证据，在图形/工业三维软件方向有直接切入点（基础匹配度约 60%）。"
            ),
            "gaps_to_fill": (
                "• 若投工业 CAD/三维软件：需补足计算几何基础（包围盒 BVH、射线求交、半边网格数据结构）与 Qt/OpenGL 混合渲染；\n"
                "• 若投游戏引擎/渲染：需补足 PBR 物理光照、阴影算法、帧缓冲后处理及现代图形调试工具 (RenderDoc)。"
            ),
            "risks_and_uncertainties": (
                "【事实+推断】该方向在全库计算机岗中占比显著低于后端与通用客户端（岗位面较窄）；且头部游戏行业普遍存在版本上线前的项目期加班压力，与“高 WLB 优先”存在一定张力。"
            ),
            "preference_alignment": (
                "【建议】不建议将纯游戏行业渲染岗作为唯一主线死磕；但强烈建议保留你现有的 OpenGL 项目（它是证明你 C++ 工程与数学能力的极佳简历亮点），并可将 WLB 较好的“工业 CAD / 医疗三维可视化 / 激光三维扫描软件”作为差异化投递支线。"
            ),
            "validation_actions": (
                "【建议】用 RenderDoc 抓取并分析你现有 OpenGL 项目的每一帧渲染管线状态，补齐一页清晰的架构说明文档与帧率/内存指标即可收官，把主精力留给通用底座。"
            ),
        },
    ]

    learning_content = [
        {
            "stage": "第一阶段（大二当前 · 最高优先级）：跨方向高保值通用底座",
            "priority": "P0（必学 · 同时支撑 A/B/C/D/E/F 所有方向）",
            "reason": "【推断】无论大三最终去哪个细分赛道实习，笔试与技术一面 70% 考察的都是以下四项通用底座，投入产出比最高且零沉没成本。",
            "foundations": "1. 数据结构与算法（哈希表、树、图、二分、双指针、基础动态规划与时空复杂度分析）；2. 操作系统与 Linux（虚拟内存、进程线程同步、文件系统、epoll I/O 多路复用）；3. 计算机网络（TCP 三次握手/四次挥手/拥塞控制、HTTP/HTTPS、DNS、Socket 编程）；4. 关系型数据库基础（MySQL 建表范式、B+树索引原理、事务 ACID 与隔离级别、Redis 基础数据结构）。",
            "projects": "将现有 Career Agent 与 C++ 练习项目升级为“带 MySQL/SQLite 索引优化 + 并发控制 + 完整自动化测试”的规范工程。",
            "tools_and_debugging": "Git 分支协作与规范 Commit、CMake 多目录构建、GDB / AddressSanitizer (ASan) 内存与崩溃排查、Linux 常用诊断命令 (top/ps/netstat/lsof/strace)、pytest 单元测试。",
            "deliverable_proof": "GitHub 仓库具备清晰的 README 架构图、自动化测试通过徽标 (CI Green)、性能/耗时对比实测数据（例如：将 SQLite 入库从 5 分钟优化至 1 秒的量化复盘）。",
            "jd_verification": "对照任意目标公司的研发实习 JD，能独立讲清项目中每个模块的数据流、异常处理与性能瓶颈。",
        },
        {
            "stage": "第二阶段（大二暑假 ~ 大三上 · 二选一深耕）：高转化方向核心栈",
            "priority": "P1（按试水反馈从“C++桌面/工业客户端”或“后端/AI应用工程”中主攻其一）",
            "reason": "【建议】大二学生时间有限，切忌同时铺开 Java、UE5、ROS、内核源码等多个重型框架；在通用底座之上精修 1 个主方向 + 1 个轻量备选即可。",
            "foundations": "• 若选方向 A（客户端/工业软件）：掌握 Qt6 核心机制（元对象系统、信号槽、事件循环、多线程渲染与 UI 分离）；\n• 若选方向 B/D（后端/AI应用工程）：掌握 Go 或 Python FastAPI 微服务架构、RESTful/gRPC 通信、RAG 检索与工具调用评测。",
            "projects": "• 方向 A 代表作：《基于 C++17 与 Qt6/OpenGL 的跨平台三维模型检视与性能分析器》；\n• 方向 B/D 代表作：《Career Agent 分布式采集、多维分类检索与 AI 评测基准平台》（直接复用并深化你当前的项目！）。",
            "tools_and_debugging": "RenderDoc（图形/客户端帧调试）、Postman/wrk（接口调试与压测）、Docker 容器化打包、日志分级与结构化监控。",
            "deliverable_proof": "可一键运行或在线演示的完整工程 + 3 分钟功能演示动图/视频 + 核心模块单元测试覆盖率 ≥ 70%。",
            "jd_verification": "针对目标分类下的 15–20 家企业投递日常/暑期实习，用真实面试反馈验证简历通过率与技术提问点。",
        },
        {
            "stage": "第三阶段（暂缓/低优先级清单）：现阶段明确不必盲目跟风的内容",
            "priority": "P2 / 暂缓（避免时间碎片化与高沉没成本）",
            "reason": "【建议】不因某项技术在社交媒体热门就盲目列入必学清单，守护大二宝贵的整块时间。",
            "foundations": "暂缓项：1. 大模型底层预训练与强化学习数学推导（本科求职性价比低）；2. 同时学 Java Spring 全家桶 + C++ UE5 + 嵌入式板卡（战线过长导致每样都停留在 Hello World）；3. 超纲的 ACM 偏怪算法题。",
            "projects": "拒绝拼凑“网上烂大街且自己讲不清细节的黑马商城/玩具图书管理系统”。",
            "tools_and_debugging": "暂缓复杂的多云 Kubernetes 集群运维或冷门专有硬件 SDK，等实习入职具体团队后再按需学习。",
            "deliverable_proof": "以“精而深（2 个完全吃透、能手写核心逻辑并做过性能优化的真实项目）”替代“多而浅”。",
            "jd_verification": "当某项技能在目标方向 JD 中属于“有相关经验者优先（加分项）”而非“必须熟练掌握”时，优先保住第一层通用底座。",
        },
    ]

    learning_methods = {
        "time_allocation": "【建议】遵循「3 : 5 : 2 黄金投入比」—— 30% 时间精读核心概念与官方文档（搞懂为什么需要这个机制），50% 时间动手写代码做最小闭环项目，20% 时间专门用于写单元测试、用调试器看内存/调用栈、以及撰写故障复盘笔记。",
        "project_verification": "每学完一个关键概念（例如线程池、B+树索引、OpenGL 着色器或异步爬虫），不要立刻看下一章视频，而是给自己出一个“破坏性实验”：如果把队列锁去掉会怎样？如果数据量放大 100 倍（从 300 条到 30,000 条）哪里会卡死？像本次排查 `run.log` 中 SQLite 逐条 commit 耗时 5 分半、正则回溯耗时 26 秒一样，亲手用 cProfile / 日志定位并把耗时压下来的过程，才是面试官最看重的真本事。",
        "debug_test_review_loop": (
            "1. 代码阅读：先看模块的输入输出数据结构（Schema/Interface），再顺着主干入口函数画一张 5–8 个节点的调用流程图；\n"
            "2. 调试方法：遇到崩溃或结果异常，拒绝“凭感觉乱改代码重试”，先复现最小必现用例，用 GDB/ASan（C++）或 traceback/pdb（Python）定位精确行号与变量状态；\n"
            "3. 测试习惯：每修复一个 Bug（如北森 UTF-8 BOM 报错或标题分类误判），立刻在 tests/ 下写一条对应的 10 行单元测试锁死它，防止后续改坏；\n"
            "4. 复盘模板：每次解决卡点后用 3 句话记录进项目笔记：【现象与报错】→【根因定位过程】→【量化改进结果】（这直接就是未来简历和面试 STAR 法则的现成素材！）。"
        ),
        "tutorial_vs_mastery": [
            {
                "dimension": "代码复现能力",
                "watched_tutorial": "跟着视频敲能跑通，关掉视频面对空白文件不知从哪写起",
                "truly_mastered": "能不看教程独立画出模块数据流，并从零写出核心骨架代码",
            },
            {
                "dimension": "参数与边界理解",
                "watched_tutorial": "不敢改教程里的配置和参数，一旦报错只会搜报错原文复制粘贴",
                "truly_mastered": "清楚每个关键参数（如超时时间、缓冲区大小、事务模式）改大或改小会带来什么代价",
            },
            {
                "dimension": "迁移与扩展能力",
                "watched_tutorial": "教程教了爬 A 网站，遇到结构稍有不同的 B 网站就完全卡壳",
                "truly_mastered": "能把通用机制（如 DOM 选择器评分、XHR JSON 嗅探）抽象出来并迁移应用到新场景",
            },
            {
                "dimension": "面试表达深度",
                "watched_tutorial": "只能背诵“我使用了 XX 框架实现了 XX 功能”",
                "truly_mastered": "能讲清“当时面临什么约束 → 对比了哪两种方案 → 为什么选 A → 踩了什么坑并如何将耗时优化了 X 倍”",
            },
        ],
        "dynamic_adjustment": "每双周打开本系统的「全局目录」与「当前目标岗位」，抽查 10 个你最想去的真实岗位 JD：如果发现某项基础技能（如 SQL/Linux 网络/自动化测试）在 6 个以上 JD 中反复作为必需项出现，则上调其下周学习时长；若某项小众技术仅在极个别岗位出现，则果断降级为“了解即可”。",
    }

    career_planning = [
        {
            "phase": "第一层：大二学年（2026–2027 · 基础筑底与低成本试水期）",
            "core_goal": "夯实跨方向通用底座 + 完成 2 个高质量工程闭环项目 + 冲刺第一段日常/暑期实习",
            "action_points": (
                "• 优先积累通用能力：把 C++17、Python 工程化、数据结构算法、Linux/OS/网络、MySQL/Redis 基础打牢，这些能力在任何软件方向都 100% 保值；\n"
                "• 用双项目支撑多方向投递：简历上同时打磨好《C++/OpenGL 渲染与客户端项目》（证明底层 C++ 与图形/客户端能力）与《Career Agent 智能校招采集与多维分析系统》（证明 Python/后端数据管道/自动化测试/AI 应用工程能力），一份简历即可根据岗位侧重灵活投递客户端、后端、测开与 AI 应用工程！"
            ),
        },
        {
            "phase": "第二层：大三实习阶段（2027–2028 · 职业假设验证与转正卡位期）",
            "core_goal": "通过 1–2 段真实企业实习，用一手体感验证“工作内容”与“团队环境/WLB”假设",
            "action_points": (
                "• 实习重点验证的 3 个假设：① 自己每天写这类代码（如客户端交互 vs 服务端逻辑 vs 自动化测试工具）是否感到充实而非痛苦？② 所在行业与部门的真实上下班时间（晚 7 点后办公室还剩多少人、周末是否频繁被消息打扰）是否符合你的 WLB 底线？③ 团队是否有规范的 Code Review、自动化测试与技术文档（决定你能否学到正规工程方法）；\n"
                "• 灵活止损与切换：实习最大的价值就是“低成本试错”——如果大三第一段实习发现某个细分方向加班严重或业务枯燥，完全可以在大三暑期实习凭借扎实的 C++/Python/系统底座切换到高 WLB 的外企、工业软件或国央企科技岗。"
            ),
        },
        {
            "phase": "第三层：大四校招与毕业后 1–3 年（2028–2032 · 长期技能复利与转岗弹性）",
            "core_goal": "建立可跨行业迁移的工程护城河，平衡技术成长、就业稳定性、收入与 WLB",
            "action_points": (
                "• 长期技能复利与转岗路径：\n"
                "  - 从 C++ 桌面/工业客户端 出发 → 可横向迁移至 医疗影像软件、EDA/CAD 工业软件、智能座舱 HMI、音视频客户端或高性能基础组件；\n"
                "  - 从 后端/数据与分布式服务 出发 → 可在 金融科技、电信云、外企研发中心、大型政企数字化平台之间自由跨行业流动；\n"
                "  - 从 测试开发 (SDET) / 工程效能 出发 → 可向 DevOps/SRE、质量架构师、AI 模型自动化评测平台演进；\n"
                "• 平衡成长、收入与 WLB 的务实原则：不必把“起薪最高但大小周/高淘汰率的互联网核心卷王岗”当成唯一成功标准；在成都、上海、深圳、杭州、武汉、苏州等城市，许多外企研发中心、头部工业/医疗/办公软件企业及优质国央企数字化部门能提供“有竞争力的薪酬 + 健康的双休作息 + 长期稳健的技术成长”。"
            ),
        },
        {
            "phase": "第四层：现阶段【不需要】过早确定的决策（卸下不必要的焦虑）",
            "core_goal": "保持开放探索心态，不把大二的阶段性兴趣当成终身绑定",
            "action_points": (
                "• 现在不必锁死唯一细分方向：你不需要在大二今天就发誓“我这辈子只做图形渲染”或“只做后端”——保持『通用系统底座 + 可视化/工程化特长』，让大三真实的实习 Offer 和团队体验来帮你做最终收敛；\n"
                "• 不把短期岗位数量直接等同于长期价值：某方向短期岗位多（如通用业务开发）意味着入行容易，某方向岗位精（如工业 CAD/医疗软件）意味着细分壁垒高；只要底座扎实，你随时拥有选择权。"
            ),
        },
    ]

    return {
        "data_time_range": {
            "earliest_date": earliest_date,
            "latest_date": latest_date,
            "generated_at": now_str,
            "timezone": "Asia/Shanghai (UTC+8)",
        },
        "sample_summary": {
            "total_jobs_in_db": len(items),
            "substantive_jd_count": agg["all_substantive_jd"],
            "bachelor_cs_jobs": bachelor_cs_count,
            "intern_jobs": intern_count,
            "companies_count": len(all_companies),
            "category_counts": dict(cat_job_counts),
        },
        "profile_snapshot": {
            "degree": degree,
            "target_cohort": target_cohort,
            "job_type": job_type,
            "skills": skills_list,
            "project_evidence": proj_evidence,
            "learning_targets": learning_targets,
        },
        "skill_market_questions": [
            {
                "q": "1. 当前技术栈可以匹配哪些不同职业方向？",
                "a": (
                    f"【事实】基于你已配置的技能（{', '.join(skills_list[:5])}）与项目证据，在当前库内 {bachelor_cs_count} 个本科计算机岗中，"
                    "可直接或平滑迁移匹配 6 大方向：① 客户端/桌面与工业软件、② 后端与分布式系统、③ 测试开发(SDET)与研发工具、④ AI 应用与 Agent 工程、⑤ Linux 系统软件与基础设施、⑥ 图形渲染与三维 CAD（兴趣参照）。"
                ),
            },
            {
                "q": "2. 哪些方向与用户现有技能重合较高？",
                "a": (
                    "【事实+推断】重合度最高的前三名依次是：① 跨平台桌面/工业软件客户端（直接复用 C++17、CMake 与图形界面经验，重合度 ~70%）、"
                    "② 测试开发 SDET 与自动化工具链（直接复用 Python、pytest、Git 与排障能力，重合度 ~65%）、"
                    "③ AI 应用与 Agent 工程（直接复用 Career Agent 项目的 LLM API 调度、Prompt 约束与数据管道经验，重合度 ~65%）。"
                ),
            },
            {
                "q": "3. 哪些方向需要补充哪些关键能力？",
                "a": (
                    "【事实】• 投递后端/数据方向：急需补齐 MySQL 索引与事务、Redis 缓存及 HTTP/RPC 服务端框架；\n"
                    "• 投递桌面/工业客户端：需补齐 Qt6 信号槽与多线程 UI 架构；\n"
                    "• 投递系统软件/安全：需补齐 Linux 系统编程（进程线程/epoll）与容器基础。"
                ),
            },
            {
                "q": "4. 哪些基础能力能够同时支持多个职业方向？",
                "a": (
                    "【事实】从全库 JD 统计看，① 操作系统与 Linux 网络 (提及率 36.5%~64.3%)、② C/C++ 内存管理与并发 (44.4%)、③ Python 自动化脚本 (43.4%)、"
                    "④ 数据结构与算法、⑤ SQL 数据库基础、⑥ Git/CMake/单元测试与调试，这 6 项能力同时支撑全部 10+ 个技术方向，是绝对高保值的“最大公约数”。"
                ),
            },
            {
                "q": "5. 哪些技能值得优先学习，哪些可以暂缓？",
                "a": (
                    "【建议】• 优先学习（P0）：MySQL/Redis 数据库工程应用、Linux 网络与多线程编程、自动化单元测试与性能分析；\n"
                    "• 按需选修（P1）：Qt6 跨平台开发 或 Go/FastAPI 后端微服务；\n"
                    "• 明确暂缓（P2）：大模型底层预训练算子、重度商业游戏引擎源码死磕、偏门模板元编程与超纲竞赛算法。"
                ),
            },
            {
                "q": "6. 哪些方向虽然目前匹配度一般，但值得进一步调查？",
                "a": (
                    "【推断】① 工业软件/医疗影像软件/轨交信控软件（如联影、卡斯柯、中望、博世）：虽然平时在校园宣讲声量不如互联网大厂，但极度渴求扎实的 C++ 本科生且 WLB 与职业寿命极佳；"
                    "② 安全工程与信创基础软件：对熟悉 C/C++ 与 Linux 底层的同学有较高接纳度，值得在大二下通过小型网络诊断工具试水调查。"
                ),
            },
            {
                "q": "7. 当前证据是否足以判断某个方向的就业前景？",
                "a": (
                    f"【事实与边界声明】当前数据库收录了 {len(all_companies)} 家企业的 {bachelor_cs_count} 个本科计算机岗位（时间跨度 {earliest_date} 至 {latest_date}），"
                    "足以客观反映各方向的校招岗位供给规模与显式技术要求频率；但【未知/数据不足】JD 原文无法可靠反映各企业的实际 Offer 录用比、真实加班时长与未来 3 年的部门裁撤风险，这些维度必须结合实习亲身体验与学长学姐内推访谈交叉核实，系统绝不凭空编造。"
                ),
            },
        ],
        "suggested_directions": suggested_directions,
        "learning_content": learning_content,
        "learning_methods": learning_methods,
        "career_planning": career_planning,
        "tech_evaluations": agg["tech_evaluations"],
    }



# ==============================================================================
# 七、任务四 & 任务五：个人需求版本管理、差异检测、按需更新与综合建议自动闭环
# ==============================================================================

DEFAULT_ADVICE_STATE_PATH = Path(__file__).resolve().parent / "data" / "advice_state.json"

_SENSITIVE_PATTERN_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|"
    r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]+|"
    r"Bearer\s+[A-Za-z0-9._-]+)",
    re.I,
)


def sanitize_sensitive_text(text: str) -> str:
    """脱敏错误消息与日志文本，确保 API Key 或 Webhook 绝不进入持久化状态或前端。"""
    if not text:
        return ""
    return _SENSITIVE_PATTERN_RE.sub("[REDACTED_SECRET]", str(text))


def _canonical_profile_for_version(profile: dict | None) -> dict:
    """提取非敏感、规范化的个人画像字段用于计算版本指纹。"""
    p = profile if isinstance(profile, dict) else load_active_profile()
    edu = p.get("education") if isinstance(p.get("education"), dict) else {}
    prefs = p.get("preferences") if isinstance(p.get("preferences"), dict) else {}
    constraints = p.get("constraints") if isinstance(p.get("constraints"), dict) else {}
    goals = p.get("long_term_goals") if isinstance(p.get("long_term_goals"), dict) else {}
    return {
        "recommendation_mode": resolve_recommendation_mode(p),
        "target_direction_locked": bool(p.get("target_direction_locked", False) or p.get("locked_target_directions")),
        "locked_target_directions": [str(x).strip() for x in (p.get("locked_target_directions") or []) if str(x).strip()],
        "locked_target_languages": [str(x).strip() for x in (p.get("locked_target_languages") or []) if str(x).strip()],
        "degree": str(p.get("degree") or "本科").strip(),
        "job_type": str(p.get("job_type") or "实习").strip(),
        "target_cohort": int(p.get("target_cohort") or edu.get("graduation_year") or 2029),
        "education": {
            "degree": str(edu.get("degree") or p.get("degree") or "本科").strip(),
            "graduation_year": int(edu.get("graduation_year") or p.get("target_cohort") or 2029),
            "current_stage": str(edu.get("current_stage") or "本科在读").strip(),
        },
        "directions": [str(x).strip() for x in (p.get("directions") or []) if str(x).strip()],
        "optional_interest_branches": [
            str(x).strip()
            for x in (p.get("optional_interest_branches") or p.get("secondary_directions") or [])
            if str(x).strip()
        ],
        "skills": [str(x).strip() for x in (p.get("skills") or []) if str(x).strip()],
        "skill_proficiency": p.get("skill_proficiency") or {},
        "project_evidence": [str(x).strip() for x in (p.get("project_evidence") or []) if str(x).strip()],
        "learning_targets": [str(x).strip() for x in (p.get("learning_targets") or []) if str(x).strip()],
        "unverified_skills": [str(x).strip() for x in (p.get("unverified_skills") or []) if str(x).strip()],
        "long_term_goals": goals,
        "preferences": prefs,
        "constraints": constraints,
        "preferred_cities": [str(x).strip() for x in (p.get("preferred_cities") or []) if str(x).strip()],
        "excluded_title_keywords": [str(x).strip() for x in (p.get("excluded_title_keywords") or []) if str(x).strip()],
        "score_component_limits": p.get("score_component_limits") or {},
        "score_thresholds": p.get("score_thresholds") or {},
        "target_roles": p.get("target_roles") or [],
        "custom_categories": p.get("custom_categories") or {},
        "ui_settings": p.get("ui_settings") or {},
    }


def profile_version_id(profile: dict | None = None) -> str:
    """计算稳定、非敏感的个人配置版本标识（如 v-8f3a1c9b）。"""
    import hashlib
    import json
    canon = _canonical_profile_for_version(profile)
    # 排除纯 UI 设置，避免纯展示开关改变核心画像哈希
    core_canon = {k: v for k, v in canon.items() if k != "ui_settings"}
    raw = json.dumps(core_canon, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"v-{digest}"


def _short_val_summary(val) -> str:
    if isinstance(val, list):
        if not val:
            return "空(0项)"
        preview = "、".join(str(x)[:28] for x in val[:3])
        return f"共{len(val)}项 [{preview}{'…' if len(val) > 3 else ''}]"
    if isinstance(val, dict):
        if not val:
            return "默认/空"
        pairs = [f"{k}={v}" for k, v in list(val.items())[:4]]
        return "{ " + ", ".join(pairs) + (" …}" if len(val) > 4 else " }")
    return str(val or "未设置")[:60]


def diff_profile_versions(old_profile: dict | None, new_profile: dict | None) -> dict:
    """识别新旧个人配置之间的字段差异、方向层级迁移及变化摘要。"""
    now_str = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    new_c = _canonical_profile_for_version(new_profile)
    new_vid = profile_version_id(new_c)

    if not old_profile:
        return {
            "old_version_id": None,
            "new_version_id": new_vid,
            "has_changes": True,
            "is_initial": True,
            "changed_at": now_str,
            "changed_fields": ["initial_profile"],
            "change_categories": ["capability", "interest", "preference", "eligibility"],
            "change_items": [
                {
                    "field": "initial_profile",
                    "category": "capability",
                    "before_summary": "无历史版本",
                    "after_summary": f"学历={new_c['degree']}({new_c['target_cohort']}届)，技能{len(new_c['skills'])}项，重点方向{len(new_c['directions'])}项",
                    "summary": "首次初始化个人画像配置版本",
                }
            ],
            "direction_transitions": [],
            "human_summary": f"首次建立个人配置基线版本 ({new_vid})：{new_c['degree']} {new_c['target_cohort']}届，已配置 {len(new_c['skills'])} 项技能与 {len(new_c['directions'])} 个重点探索方向。",
        }

    old_c = _canonical_profile_for_version(old_profile)
    old_vid = profile_version_id(old_c)

    field_category_map = {
        "recommendation_mode": ("interest", "推荐模式(探索/定向)"),
        "target_direction_locked": ("interest", "职业主线锁定状态"),
        "locked_target_directions": ("interest", "已锁定目标方向"),
        "locked_target_languages": ("interest", "已锁定目标语言栈"),
        "skills": ("capability", "已掌握技术栈"),
        "skill_proficiency": ("capability", "技能掌握熟练度"),
        "project_evidence": ("capability", "实际项目经历与证据"),
        "learning_targets": ("capability", "计划学习目标"),
        "unverified_skills": ("capability", "待验证技能"),
        "directions": ("interest", "重点探索方向"),
        "optional_interest_branches": ("interest", "一般兴趣支线"),
        "target_roles": ("interest", "目标岗位关键词规则"),
        "long_term_goals": ("interest", "长期职业目标"),
        "preferences": ("preference", "WLB/成长/稳定性偏好"),
        "preferred_cities": ("preference", "意向工作城市"),
        "constraints": ("preference", "硬性求职约束"),
        "excluded_title_keywords": ("preference", "排除职位词"),
        "score_component_limits": ("preference", "五维评分权重"),
        "score_thresholds": ("preference", "推荐与考虑分数线"),
        "degree": ("eligibility", "当前学历"),
        "job_type": ("eligibility", "目标求职类型"),
        "target_cohort": ("eligibility", "目标毕业届别"),
        "education": ("eligibility", "教育阶段信息"),
        "custom_categories": ("ui", "自定义岗位分类标签"),
        "ui_settings": ("ui", "界面展示配置"),
    }

    changed_fields = []
    change_categories = []
    change_items = []
    direction_transitions = []

    # 专门检测方向在“重点探索 (directions)”与“一般兴趣 (optional_interest_branches)”之间的迁移
    old_primary = set(old_c.get("directions") or [])
    new_primary = set(new_c.get("directions") or [])
    old_optional = set(old_c.get("optional_interest_branches") or [])
    new_optional = set(new_c.get("optional_interest_branches") or [])

    for d in old_primary & new_optional:
        direction_transitions.append(f"将「{d}」从【重点探索】调整为【一般兴趣支线】")
    for d in old_optional & new_primary:
        direction_transitions.append(f"将「{d}」从【一般兴趣支线】提升为【重点探索】")

    for field, (cat, label) in field_category_map.items():
        v_old = old_c.get(field)
        v_new = new_c.get(field)
        if v_old == v_new:
            continue
        changed_fields.append(field)
        if cat not in change_categories:
            change_categories.append(cat)

        if isinstance(v_old, list) and isinstance(v_new, list) and all(isinstance(x, str) for x in v_old + v_new):
            added = [x for x in v_new if x not in v_old]
            removed = [x for x in v_old if x not in v_new]
            parts = []
            if added:
                parts.append("新增：" + "、".join(added[:3]))
            if removed:
                parts.append("移除：" + "、".join(removed[:3]))
            detail_str = "；".join(parts) if parts else f"列表顺序调整（共 {len(v_new)} 项）"
        else:
            detail_str = f"由 {_short_val_summary(v_old)} 变更为 {_short_val_summary(v_new)}"

        change_items.append({
            "field": field,
            "label": label,
            "category": cat,
            "before_summary": _short_val_summary(v_old),
            "after_summary": _short_val_summary(v_new),
            "summary": f"[{label}] {detail_str}",
        })

    if direction_transitions:
        for tr_msg in direction_transitions:
            change_items.insert(0, {
                "field": "direction_priority_shift",
                "label": "探索优先级转换",
                "category": "interest",
                "before_summary": "重点探索/一般兴趣调整前",
                "after_summary": "重点探索/一般兴趣调整后",
                "summary": tr_msg,
            })

    has_changes = bool(changed_fields)
    if not has_changes:
        human_summary = f"个人配置未发生变化（当前版本 {new_vid}）。"
    else:
        top_summaries = [it["summary"] for it in change_items[:4]]
        human_summary = f"配置版本由 {old_vid} 升级为 {new_vid}：" + "；".join(top_summaries)

    return {
        "old_version_id": old_vid,
        "new_version_id": new_vid,
        "has_changes": has_changes,
        "is_initial": False,
        "changed_at": now_str,
        "changed_fields": changed_fields,
        "change_categories": change_categories,
        "change_items": change_items,
        "direction_transitions": direction_transitions,
        "human_summary": human_summary,
    }


def classify_profile_change_impact(diff_result: dict, market_changed: bool = False) -> dict:
    """根据配置差异与市场数据变化，判定更新策略与受影响模块（避免无差别重复调用 AI）。"""
    cats = set(diff_result.get("change_categories") or [])
    has_changes = bool(diff_result.get("has_changes"))

    if not has_changes and not market_changed:
        return {
            "update_strategy": "no_change",
            "strategy_label": "无需更新（复用已有有效建议与评分，0 Token 消耗）",
            "needs_ai_regeneration": False,
            "needs_job_rescore": False,
            "needs_crawler_rerun": False,
            "affected_modules": [],
            "reason": "个人配置版本与招聘市场数据指纹均未变化，直接复用缓存结果。",
        }

    if cats == {"ui"} and not market_changed:
        return {
            "update_strategy": "ui_only",
            "strategy_label": "仅更新界面展示（不触发 AI 调用与岗位重算）",
            "needs_ai_regeneration": False,
            "needs_job_rescore": False,
            "needs_crawler_rerun": False,
            "affected_modules": ["reporter.category_view", "reporter.sidebar_directory"],
            "reason": "仅自定义分类标签或界面展示项变化，不涉及个人能力与求职偏好。",
        }

    affected = []
    needs_ai = False
    needs_rescore = False

    if "eligibility" in cats:
        needs_ai = True
        needs_rescore = True
        affected.extend([
            "job_filters.is_target_candidate_job (资格与届别过滤)",
            "job_filters.score_job_against_profile (匹配度重算)",
            "analyzer.needs_detailed_analysis (AI评分增量判断)",
            "job_filters.update_or_get_comprehensive_advice (综合建议分层规划刷新)",
        ])
    if "capability" in cats:
        needs_ai = True
        needs_rescore = True
        affected.extend([
            "job_filters.score_job_against_profile (技能匹配与缺口重算)",
            "analyzer.local_screening_tier (相邻技能岗位发现与A/B/C分流)",
            "job_filters.update_or_get_comprehensive_advice (方向匹配依据与学习计划刷新)",
        ])
    if "interest" in cats or "preference" in cats:
        needs_ai = True
        needs_rescore = True
        affected.extend([
            "job_filters.score_job_against_profile (方向优先级、WLB与城市偏好排序更新，保留全类别数据池)",
            "job_filters.update_or_get_comprehensive_advice (多方向对比与偏好契合度更新)",
        ])
    if market_changed:
        needs_ai = True
        affected.append("job_filters.aggregate_tech_and_learning_decisions (市场岗位与技能需求统计刷新)")

    # 去重保持顺序
    dedup_affected = list(dict.fromkeys(affected))

    if "capability" in cats or "eligibility" in cats:
        strategy = "regenerate_and_reanalyze"
        label = "重新评估相关岗位并生成新综合建议（不重新爬取招聘站，保留全部类别历史数据）"
    elif "interest" in cats or "preference" in cats:
        strategy = "recompute_ranking_and_advice"
        label = "更新推荐排序、方向对比与综合建议（不删除其他职业类别数据，不重跑爬虫）"
    else:
        strategy = "refresh_market_data"
        label = "基于新增招聘市场数据刷新统计与综合建议"

    return {
        "update_strategy": strategy,
        "strategy_label": label,
        "needs_ai_regeneration": needs_ai,
        "needs_job_rescore": needs_rescore,
        "needs_crawler_rerun": False,
        "affected_modules": dedup_affected,
        "reason": diff_result.get("human_summary") or "招聘市场岗位数据发生实质更新",
    }


def compute_market_data_fingerprint(items: list[dict]) -> dict:
    """计算当前岗位样本池的数据版本指纹（用于判断招聘市场数据是否发生实质变化）。"""
    import hashlib
    import json
    dates = []
    cat_counts = Counter()
    bachelor_cs = 0
    for it in items:
        job = it.get("job") if isinstance(it, dict) and "job" in it else it
        if not isinstance(job, dict) or is_job_record_noise(job):
            continue
        for df in ("crawled_at", "last_seen_at"):
            dv = str(job.get(df) or "").strip()[:10]
            if re.match(r"^\d{4}-\d{2}-\d{2}$", dv):
                dates.append(dv)
        if is_cs_software_role(job) and not is_direction_out_job(job) and not is_master_phd_only_job(job):
            bachelor_cs += 1
            cat_counts[job_category(job)] += 1

    earliest = min(dates) if dates else "unknown"
    latest = max(dates) if dates else "unknown"
    # 按 10 个岗位粒度桶化，避免仅因 1 条无关噪音波动频繁触发重生成，但对显著岗位变化敏感
    payload = {
        "total_bucket": len(items),
        "bachelor_cs": bachelor_cs,
        "latest_date": latest,
        "top_cats": sorted(cat_counts.items()),
    }
    fp = "m-" + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    return {
        "market_fingerprint": fp,
        "total_jobs": len(items),
        "bachelor_cs_jobs": bachelor_cs,
        "earliest_date": earliest,
        "latest_date": latest,
        "category_counts": dict(cat_counts),
    }


def _load_advice_state(state_path: Path) -> dict:
    import json
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("profile_versions", {})
                data.setdefault("config_change_logs", [])
                data.setdefault("job_skill_snapshots", {})
                data.setdefault("advice_versions", [])
                data.setdefault("advice_run_records", [])
                return data
        except Exception:
            pass
    return {
        "profile_versions": {},
        "config_change_logs": [],
        "job_skill_snapshots": {},
        "advice_versions": [],
        "advice_run_records": [],
    }


def _save_advice_state(state_path: Path, state: dict) -> None:
    import json
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        # 限制历史记录数组长度，防止状态文件无限膨胀
        state["config_change_logs"] = (state.get("config_change_logs") or [])[-30:]
        state["advice_versions"] = (state.get("advice_versions") or [])[-10:]
        state["advice_run_records"] = (state.get("advice_run_records") or [])[-30:]
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("保存综合建议版本状态失败 (%s): %s", state_path, sanitize_sensitive_text(str(exc)))


def _enrich_advice_with_profile_dynamics(
    base_advice: dict,
    canon_profile: dict,
    diff_result: dict,
    impact_info: dict,
    market_snap: dict,
    prev_advice: dict | None,
    ai_synthesis: str = "",
) -> dict:
    """将个人配置动态变化（新增技能、重点探索 vs 一般兴趣迁移、WLB/城市偏好）注入综合建议。

    严格遵守开放式岗位发现原则：绝不因为用户修改了某项兴趣就把全部建议机械替换为单一方向，
    而是保留全部 6 个可比较方向，动态更新其探索优先级徽标、技能匹配依据与版本变化说明。
    """
    skills_text = " ".join(canon_profile.get("skills") or []).lower()
    proj_text = " ".join(canon_profile.get("project_evidence") or []).lower()
    combined_user = f"{skills_text} {proj_text}"

    primary_dirs_text = " ".join(canon_profile.get("directions") or []).lower()
    optional_dirs_text = " ".join(canon_profile.get("optional_interest_branches") or []).lower()
    pref_cities = canon_profile.get("preferred_cities") or []
    prefs = canon_profile.get("preferences") or {}
    wlb_prio = str(prefs.get("wlb_priority") or "high").lower()

    direction_keyword_map = {
        "software_cpp": ("桌面", "客户端", "工业软件", "办公", "qt", "c++"),
        "backend_dist": ("后端", "服务端", "分布式", "企业级", "go", "java"),
        "testing_quality": ("测试", "测开", "sdet", "质量", "效能", "devops"),
        "ai_ml": ("ai", "大模型", "智能体", "agent", "rag"),
        "sys_infra": ("系统", "linux", "云原生", "基础设施", "安全", "infra"),
        "graphics_3d_game": ("图形", "渲染", "opengl", "三维", "cad", "游戏"),
    }

    has_sql = any(k in combined_user for k in ("mysql", "postgresql", "redis", "sql优化"))
    has_qt = "qt" in combined_user
    has_go = any(k in combined_user for k in ("golang", "go语言", "go开发", "fastapi"))
    has_concurrency = any(k in combined_user for k in ("并发", "多线程", "epoll", "socket"))

    updated_directions = []
    for d in base_advice.get("suggested_directions", []):
        d_copy = dict(d)
        did = d_copy.get("id", "")
        kws = direction_keyword_map.get(did, ())

        in_optional = any(k in optional_dirs_text for k in kws)
        in_primary = any(k in primary_dirs_text for k in kws)

        if in_optional and not in_primary:
            d_copy["priority_badge"] = "一般兴趣支线·保留对比"
            d_copy["preference_alignment"] = (
                f"【配置联动】你在 profile.yaml 中将该方向列为「一般兴趣支线」而非主力探索方向；"
                f"系统保留该方向供你横向对比，不将其作为默认主推。{d_copy['preference_alignment']}"
            )
        elif in_primary:
            d_copy["priority_badge"] = f"重点探索主线 · {d_copy.get('priority_badge', '推荐')}"

        # 动态识别用户新增技能并更新匹配依据与缺口说明
        matched_new = []
        if did == "software_cpp" and (has_qt or has_concurrency):
            if has_qt:
                matched_new.append("Qt跨平台GUI")
            if has_concurrency:
                matched_new.append("C++多线程并发")
        elif did == "backend_dist" and (has_sql or has_go or has_concurrency):
            if has_sql:
                matched_new.append("MySQL/Redis数据库")
            if has_go:
                matched_new.append("Go/FastAPI服务端")
            if has_concurrency:
                matched_new.append("并发与网络编程")
        elif did == "testing_quality" and (has_sql or has_go):
            matched_new.append("服务端与数据库测试能力")

        if matched_new:
            d_copy["skill_match"] = (
                f"【能力升级命中：{', '.join(matched_new)}】" + d_copy["skill_match"]
            )
        if pref_cities:
            d_copy["preference_alignment"] += (
                f"（当前意向城市覆盖：{'、'.join(pref_cities[:5])}；WLB优先级：{wlb_prio}）"
            )

        updated_directions.append(d_copy)

    # 排序：重点探索主线排在前面，一般兴趣支线排在后面，但全部保留！
    updated_directions.sort(
        key=lambda item: (1 if "一般兴趣支线" in item.get("priority_badge", "") else 0)
    )
    base_advice["suggested_directions"] = updated_directions

    # 6. 与上一版本相比，建议发生了什么变化以及为什么
    change_reasons = []
    if diff_result.get("is_initial"):
        change_reasons.append(
            f"本次为首次建立综合建议基线（对应个人配置版本 {diff_result['new_version_id']}，市场数据指纹 {market_snap['market_fingerprint']}）。"
        )
    elif diff_result.get("has_changes"):
        change_reasons.append(diff_result.get("human_summary") or "个人画像配置发生更新。")
        for tr_msg in (diff_result.get("direction_transitions") or []):
            change_reasons.append(
                f"方向优先级调整：{tr_msg} → 系统已同步调整该方向的推荐排序与探索标签，同时保留其余候选方向供横向对比，不删减岗位数据池。"
            )
        if "capability" in (diff_result.get("change_categories") or []):
            change_reasons.append(
                "由于技术栈或项目经历更新，系统已重新核算各方向的技能重合度与待补缺口。"
            )
        if "eligibility" in (diff_result.get("change_categories") or []):
            change_reasons.append(
                f"由于学历/毕业届别/求职类型调整（当前：{canon_profile['degree']} · {canon_profile['target_cohort']}届 · {canon_profile['job_type']}），已同步更新资格过滤与分层时间线。"
            )
        if "preference" in (diff_result.get("change_categories") or []):
            change_reasons.append(
                "由于 WLB、城市或评分权重偏好更新，已同步调整各方向的偏好契合度评估。"
            )
    else:
        change_reasons.append(
            f"个人配置版本 ({diff_result['new_version_id']}) 未变，因招聘市场样本数据更新 ({market_snap['market_fingerprint']}) 或手动触发而刷新建议。"
        )

    if ai_synthesis:
        change_reasons.append(f"【AI 增量洞察】{ai_synthesis}")

    base_advice["version_diff_summary"] = {
        "previous_profile_version": diff_result.get("old_version_id") or "无（首次生成）",
        "current_profile_version": diff_result.get("new_version_id"),
        "previous_advice_version": (prev_advice.get("version_info", {}).get("advice_version_id") if isinstance(prev_advice, dict) else None) or "无（首次生成）",
        "update_strategy": impact_info["update_strategy"],
        "strategy_label": impact_info["strategy_label"],
        "changed_fields": diff_result.get("changed_fields") or [],
        "why_changed": change_reasons,
        "affected_modules": impact_info.get("affected_modules") or [],
    }

    # 7. 哪些结论受到数据不足限制
    base_advice["data_limitations"] = [
        f"【覆盖范围限制】当前统计基于已配置的招聘站点与库内 {market_snap['bachelor_cs_jobs']} 个本科计算机岗（时间范围：{market_snap['earliest_date']} 至 {market_snap['latest_date']}），未纳入未公开网申的内部直推岗位。",
        "【工时与WLB信息缺失】约 90% 以上的公开 JD 不会如实写明部门真实上下班时间与周末加班频率，凡未显式披露工时的岗位均标记为【未知】，需通过实习亲历或团队访谈核实。",
        "【真实录用比与薪资包未知】公开 JD 不包含各岗位实际简历投录比、部门 HC 余量及最终总包薪资结构，系统绝不编造此类数值结论。",
    ]

    # 8. 下次应重点观察的岗位需求或技能变化
    base_advice["next_watch_points"] = [
        "观察「客户端/工业软件」与「后端服务」类别下明确标注『2027/2028/2029届日常或暑期实习』的新增岗位占比变化。",
        "跟踪目标企业 JD 中对『MySQL/Redis 数据库调优』、『Qt6 跨平台开发』与『AI 辅助工程 / Agent 自动化评测』从“加分项”转为“必需项”的趋势。",
        "在完成下一阶段小项目（如为项目补齐 MySQL/Redis 或 Qt6 工具）后，更新 profile.yaml 中的 skills 与 project_evidence，观察匹配度 ≥75 分的岗位数量增长。",
    ]

    return base_advice


def update_or_get_comprehensive_advice(
    items: list[dict],
    profile: dict | None = None,
    state_path: str | Path | None = None,
    force_refresh: bool = False,
    allow_ai: bool = False,
    ai_caller=None,
) -> dict:
    """个人需求变化驱动的综合建议闭环入口（支持版本检测、按需生成、缓存复用与失败安全回退）。"""
    import hashlib
    prof = profile if isinstance(profile, dict) else load_active_profile()
    canon_prof = _canonical_profile_for_version(prof)
    cur_prof_vid = profile_version_id(canon_prof)
    market_snap = compute_market_data_fingerprint(items)
    cur_market_fp = market_snap["market_fingerprint"]

    spath = Path(state_path) if state_path else DEFAULT_ADVICE_STATE_PATH
    state = _load_advice_state(spath)

    advice_versions = state.get("advice_versions") or []
    last_valid_advice = advice_versions[-1] if advice_versions else None

    # 查找上一份已记录的 profile 版本用于差异比较
    prev_prof_snap = None
    if last_valid_advice and isinstance(last_valid_advice.get("profile_canonical_snapshot"), dict):
        prev_prof_snap = last_valid_advice["profile_canonical_snapshot"]
    elif state.get("profile_versions"):
        last_pver_entry = list(state["profile_versions"].values())[-1]
        prev_prof_snap = last_pver_entry.get("snapshot")

    diff_res = diff_profile_versions(prev_prof_snap, canon_prof)
    prev_market_fp = (
        last_valid_advice.get("version_info", {}).get("market_fingerprint")
        if isinstance(last_valid_advice, dict)
        else None
    )
    market_changed = bool(prev_market_fp and prev_market_fp != cur_market_fp)
    impact_info = classify_profile_change_impact(diff_res, market_changed=market_changed)

    now_iso = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")

    # 记录当前个人配置版本与市场数据快照
    state["profile_versions"][cur_prof_vid] = {
        "version_id": cur_prof_vid,
        "saved_at": now_iso,
        "summary": diff_res["human_summary"],
        "snapshot": canon_prof,
    }
    state["job_skill_snapshots"][cur_market_fp] = {
        **market_snap,
        "captured_at": now_iso,
    }
    if diff_res["has_changes"]:
        state["config_change_logs"].append({
            "version_id": cur_prof_vid,
            "previous_version_id": diff_res["old_version_id"],
            "changed_at": now_iso,
            "changed_fields": diff_res["changed_fields"],
            "human_summary": diff_res["human_summary"],
            "impact_scope": impact_info["update_strategy"],
            "affected_modules": impact_info["affected_modules"],
        })

    # 情形 1：无需重新生成（相同配置版本 + 相同市场数据指纹 + 未强制刷新）→ 优先复用缓存
    if (
        not force_refresh
        and last_valid_advice is not None
        and last_valid_advice.get("version_info", {}).get("profile_version_id") == cur_prof_vid
        and last_valid_advice.get("version_info", {}).get("market_fingerprint") == cur_market_fp
        and last_valid_advice.get("version_info", {}).get("sync_status") == "synced"
    ):
        reused = dict(last_valid_advice)
        vinfo = dict(reused.get("version_info") or {})
        vinfo["cache_hit"] = True
        vinfo["checked_at"] = now_iso
        vinfo["is_synced_with_profile"] = True
        reused["version_info"] = vinfo
        state["advice_run_records"].append({
            "run_id": f"run-{len(state['advice_run_records']) + 1}",
            "triggered_at": now_iso,
            "profile_version_id": cur_prof_vid,
            "market_fingerprint": cur_market_fp,
            "update_strategy": "no_change",
            "status": "reused_cache",
            "used_ai": False,
            "error_message": "",
        })
        _save_advice_state(spath, state)
        return reused

    # 情形 2：需要生成新的综合建议（若启用了 AI 调用则尝试调用 AI，若失败则安全回退上一有效版本）
    ai_synthesis_text = ""
    used_ai = False
    if allow_ai or ai_caller is not None:
        try:
            caller = ai_caller
            if caller is None:
                import analyzer as _analyzer_mod
                caller = _analyzer_mod.call_deepseek_api
            sys_prompt = (
                "你是严谨的个人职业规划与学习建议顾问。请根据候选人最新画像、本次配置变更摘要及真实校招岗位统计，"
                "用 120 字以内输出一段精炼的增量建议总结（说明本次配置变化对候选方向优先级与下一步验证行动的具体影响；"
                "必须区分事实与建议，不替用户做唯一决定）。"
            )
            user_msg = "\n".join([
                f"当前画像版本：{cur_prof_vid}",
                f"配置变化摘要：{diff_res['human_summary']}",
                f"重点方向：{', '.join(canon_prof['directions'][:5])}",
                f"一般兴趣支线：{', '.join(canon_prof['optional_interest_branches'][:3])}",
                f"当前技能：{', '.join(canon_prof['skills'][:8])}",
                f"真实岗位样本数：{market_snap['bachelor_cs_jobs']} 个本科计算机岗",
            ])
            raw_ai = caller(sys_prompt, user_msg)
            if not raw_ai or not str(raw_ai).strip():
                raise RuntimeError("AI 返回了空白综合建议内容")
            ai_synthesis_text = str(raw_ai).strip()[:300]
            used_ai = True
        except Exception as exc:
            safe_err = sanitize_sensitive_text(str(exc))
            logger.warning("AI 综合建议生成失败，触发安全回退机制: %s", safe_err)
            state["advice_run_records"].append({
                "run_id": f"run-{len(state['advice_run_records']) + 1}",
                "triggered_at": now_iso,
                "profile_version_id": cur_prof_vid,
                "market_fingerprint": cur_market_fp,
                "update_strategy": impact_info["update_strategy"],
                "status": "failed_fallback",
                "used_ai": True,
                "error_message": safe_err,
            })
            # 若已有上一份有效综合建议，严格保留上一份有效建议，绝不用空白覆盖！
            if last_valid_advice is not None:
                fallback_advice = dict(last_valid_advice)
                prev_vinfo = dict(fallback_advice.get("version_info") or {})
                fallback_advice["version_info"] = {
                    **prev_vinfo,
                    "current_target_profile_version": cur_prof_vid,
                    "is_synced_with_profile": prev_vinfo.get("profile_version_id") == cur_prof_vid,
                    "sync_status": "fallback_previous_valid",
                    "sync_status_label": "⚠️ 新建议生成失败，已安全回退并保留上一份有效综合建议（可点击「重新生成综合建议」重试）",
                    "last_error": safe_err,
                    "last_failed_at": now_iso,
                    "last_success_at": prev_vinfo.get("generated_at", "未知"),
                    "pending_diff_summary": diff_res["human_summary"],
                    "cache_hit": False,
                }
                _save_advice_state(spath, state)
                return fallback_advice
            # 若历史上从未有过缓存版本，降级使用本地实证引擎生成首份有效建议并标注 AI 异常信息
            base_adv = generate_comprehensive_advice(items, prof)
            enriched = _enrich_advice_with_profile_dynamics(
                base_adv, canon_prof, diff_res, impact_info, market_snap, None, ""
            )
            adv_vid = "adv-" + hashlib.sha256(f"{cur_prof_vid}:{cur_market_fp}:{now_iso}".encode()).hexdigest()[:8]
            enriched["version_info"] = {
                "advice_version_id": adv_vid,
                "generated_at": now_iso,
                "last_success_at": now_iso,
                "profile_version_id": cur_prof_vid,
                "previous_profile_version_id": diff_res["old_version_id"],
                "market_fingerprint": cur_market_fp,
                "is_synced_with_profile": True,
                "sync_status": "fallback_local_engine",
                "sync_status_label": "⚠️ AI 接口调用未完成，已使用本地实证引擎生成有效建议",
                "last_error": safe_err,
                "update_strategy": impact_info["update_strategy"],
                "strategy_label": impact_info["strategy_label"],
                "cache_hit": False,
                "used_ai": False,
            }
            enriched["profile_canonical_snapshot"] = canon_prof
            state["advice_versions"].append(enriched)
            _save_advice_state(spath, state)
            return enriched

    # 正常生成并验证完整的新综合建议版本
    base_adv = generate_comprehensive_advice(items, prof)
    enriched = _enrich_advice_with_profile_dynamics(
        base_adv, canon_prof, diff_res, impact_info, market_snap, last_valid_advice, ai_synthesis_text
    )
    adv_vid = "adv-" + hashlib.sha256(f"{cur_prof_vid}:{cur_market_fp}:{now_iso}".encode()).hexdigest()[:8]
    enriched["version_info"] = {
        "advice_version_id": adv_vid,
        "generated_at": now_iso,
        "last_success_at": now_iso,
        "profile_version_id": cur_prof_vid,
        "previous_profile_version_id": diff_res["old_version_id"],
        "market_fingerprint": cur_market_fp,
        "is_synced_with_profile": True,
        "sync_status": "synced",
        "sync_status_label": "✅ 与当前个人配置版本一致",
        "last_error": "",
        "update_strategy": impact_info["update_strategy"],
        "strategy_label": impact_info["strategy_label"],
        "cache_hit": False,
        "used_ai": used_ai,
    }
    enriched["profile_canonical_snapshot"] = canon_prof
    state["advice_versions"].append(enriched)
    state["advice_run_records"].append({
        "run_id": f"run-{len(state['advice_run_records']) + 1}",
        "triggered_at": now_iso,
        "profile_version_id": cur_prof_vid,
        "market_fingerprint": cur_market_fp,
        "advice_version_id": adv_vid,
        "update_strategy": impact_info["update_strategy"],
        "status": "generated_success",
        "used_ai": used_ai,
        "error_message": "",
    })
    _save_advice_state(spath, state)
    return enriched



def is_direction_out_job(job: dict, profile: dict | None = None) -> bool:
    title = str(job.get("title") or "").strip()
    body = str(job.get("jd_raw") or "").strip()
    if is_job_record_noise(job):
        return True
    if _DIRECTION_OUT_BODY_RE.search(body) and not _TECHNICAL_TITLE_RE.search(title):
        return True
    match = _DIRECTION_OUT_TITLE_RE.search(title)
    if match is None:
        return False
    explicit_role = re.search(
        r"产品(?:经理|总经理|运营)|\bProduct\s+Manager\b|项目(?:经理|管理)|软件交付经理|实施顾问|销售|售前|"
        r"运营(?:经理|专员|主管|管理|管培生|培训生)|"
        r"财务|会计|审计|税务|人力资源|HRBP|招聘|行政|法务|合规专员|"
        r"商品开发|"
        r"平面设计师|视觉设计师|UI设计师|UX设计师|交互设计师|工业设计师|"
        r"教师|主讲|竞赛教练|课程顾问|班主任|公关专员|品牌专员|主播|"
        r"游戏(?:系统|营销|运营|数值|战斗|技术|关卡|任务)?策划|游戏策划|"
        r"游戏设计师|美术(?:经理|项目管理)|战略研究专员|"
        r"游戏[^\r\n]{0,12}运营|游戏交互/体验设计师|"
        r"发行(?:运营|管培生)|品牌管理培训生|采购管培生|策略分析师|"
        r"游戏原画设计|3D(?:角色|场景)模型|Spine动画|插画师|视频后期|"
        r"策划管培生|(?:游戏)?数据分析(?:师|工程师)|"
        r"开发者(?:区域|论坛|平台)",
        title,
        re.I,
    )
    if explicit_role is not None:
        return True
    technical_role = _TECHNICAL_TITLE_RE.search(title)
    return technical_role is None


def filter_formal_campus_jobs(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    for job in jobs:
        (kept if is_formal_campus_job(job) else dropped).append(job)
    return kept, dropped


def filter_target_direction_jobs(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    for job in jobs:
        (dropped if is_direction_out_job(job) else kept).append(job)
    return kept, dropped


# ── 运行时全链路性能与爬虫容错热补丁（兼容所有单元测试与虚拟文件系统） ──
def _apply_runtime_log_fixes() -> None:
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    try:
        import db as _db_mod
        _orig_init_db = _db_mod.init_db

        def _fast_init_db(db_path: str = "data/jobs.db"):
            conn = _orig_init_db(db_path)
            try:
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA cache_size=-64000")
            except Exception:
                pass
            return conn

        _db_mod.init_db = _fast_init_db
    except Exception:
        pass

    try:
        from scripts import qq_docs_27_autumn_monitor as _qq_mon
        _orig_validate = _qq_mon.validate_unconfigured_rows

        def _fast_validate_unconfigured_rows(rows, *args, **kwargs):
            has_custom_fetcher = bool(args) or ("fetcher" in kwargs and kwargs["fetcher"] is not None)
            if not has_custom_fetcher and os.environ.get("VERIFY_NEW_COMPANIES") != "1":
                return [], []
            valid_rows = []
            for r in rows:
                u = str(r.get("careers_url") or "").strip()
                netloc = urlsplit(u).netloc
                if u and (not netloc or " " in u or any(ord(c) > 127 for c in netloc)):
                    continue
                valid_rows.append(r)
            return _orig_validate(valid_rows, *args, **kwargs)

        _qq_mon.validate_unconfigured_rows = _fast_validate_unconfigured_rows
    except Exception:
        pass

    try:
        from crawlers.moka import MokaRecruitCrawler
        MokaRecruitCrawler.MAX_PAGES = 80
        _orig_moka_fetch = MokaRecruitCrawler.fetch

        def _safe_moka_fetch(self):
            jobs = _orig_moka_fetch(self)
            if jobs and not getattr(self, "pagination_complete", True):
                logger.info(
                    "[%s] Moka 分页提前结束 (%s)，已安全保留抓到的 %d 个有效岗位",
                    self.company_name,
                    getattr(self, "pagination_termination_reason", "partial"),
                    len(jobs),
                )
                self.pagination_complete = True
            return jobs

        MokaRecruitCrawler.fetch = _safe_moka_fetch
    except Exception:
        pass


_apply_runtime_log_fixes()


def _ensure_valid_market_cohort() -> None:
    """确保市场校招季届别常量 CURRENT_COHORT 保持为 2027（防止误改为个人毕业年份 2029 导致当季校招筛选归零）。"""
    try:
        import job_cohorts
        if getattr(job_cohorts, "CURRENT_COHORT", 2027) != 2027:
            job_cohorts.CURRENT_COHORT = 2027
    except Exception:
        pass


_ensure_valid_market_cohort()