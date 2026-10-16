import re
from pathlib import Path
import yaml

# ==============================================================================
# 运行时自动兼容补丁：免除用户手动修改 job_cohorts.py 与 db.py
# 只要替换本文件 (job_filters.py) 和 reporter.py，系统在导入时会自动修补
# job_cohorts.is_confirmed_current 与 db.ACTIVE_WINDOW_DAYS，彻底解决
# “job_cohorts.py 打不开”以及“当前目标岗位只剩4个”的问题。
# ==============================================================================


_INTERN_RE = re.compile(r"(^|[^a-z])intern(ship)?([^a-z]|$)", re.I)
_INTERN_PROJECT_RE = re.compile(
    r"日常实习|应届实习|暑期实习|寒假实习|春季实习|秋季实习|长期实习|短期实习|"
    r"寻梦实习招聘|实习生(?:专项)?招聘(?:计划)?|实习招聘|可转正实习|"
    r"实习薪酬(?:津贴)?|转正机会|实习生将|提前到岗实习|需要提前到岗实习|"
    r"实习到毕业|全职驻场实习|本科及以上在读|学历在读|在读本科生|在读学生",
    re.I,
)
_INTERN_METADATA_RE = re.compile(r"(?:^|[\s|·])实习(?:[\s|·]|$)", re.I)
_INTERN_BODY_EN_RE = re.compile(
    r"(?:complete|undertake|join)\s+an?\s+internship|"
    r"internship\s+(?:position|program|programme|opportunity|duration)",
    re.I,
)
_INTERN_DURATION_RE = re.compile(
    r"(?:至少|能够|能|可|持续|要求)?实习(?:期|时长|时间)?(?:至少|不少于)?\s*"
    r"[一二三四五六七八九十两\d\-~～]+\s*个?月|"
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
    r"[1-5]\s*-\s*[3-8]\s*年.*工作经验|[三五]年以上.*经验",
    re.I,
)
_FORMAL_CAMPUS_BODY_RE = re.compile(
    r"(?:招聘类型|岗位类型|职位类型|招聘项目|项目类型)\s*[:：|·]\s*"
    r"(?:校园招聘|校招|应届生招聘)",
    re.I,
)

# ── 硕博门槛识别正则：当 profile.yaml 为“本科”时，自动拦截仅招硕博岗位 ──
_MASTER_PHD_TITLE_RE = re.compile(
    r"博士|硕博|研究生专项|硕士专项|\bPh\.?D\b|博士后",
    re.I,
)
_MASTER_PHD_ONLY_BODY_RE = re.compile(
    r"(?:学历要求|任职要求|任职资格|岗位要求|招聘对象|教育背景|基本要求|资格要求|条件)?[^。；;\n]{0,28}?"
    r"(?:硕士(?:研究生)?及以上|研究生及以上|硕士或博士|硕博(?:研究生)?|博士(?:研究生)?及以上|"
    r"仅限硕士|仅限研究生|仅限博士|硕士学历|博士学历|在读硕士或博士|博士和优秀硕士)",
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
# 行业与企业类型客观分类（用于提供组织属性线索，不直接等同于绝对WLB承诺）
_HIGH_WLB_ORG_RE = re.compile(
    r"德州仪器|博世|卡斯柯|中国联通|中国电信|天翼云|四方继保|长园深瑞|国家开发投资|联影|金山办公|合合信息|万兴科技|星环科技|北森|神策|广联达|同花顺",
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


_FAST_PACE_INTERNET_GAME_RE = re.compile(
    r"字节跳动|拼多多|快手|得物|米哈游|莉莉丝|叠纸|鹰角|沐瞳|柠檬微趣|飞羽",
    re.I,
)


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


JOB_CATEGORY_LABELS = {
    "backend_service": "1. 后端服务、企业级平台与通用研发 (C++/Go/Python/Java)",
    "client_fe_gui": "2. 跨平台桌面客户端、办公与工业软件 (C++/Qt/SDK/Web)",
    "qa_devops": "3. 测试开发(SDET)、质量工程与DevOps (Python/C++/自动化)",
    "sys_infra_sec": "4. 云原生基础设施、Linux系统软件与安全 (Linux/Infra/安全)",
    "data_db": "5. 数据工程、数据库应用与数据平台 (SQL/Python/数仓/DB)",
    "ai_llm_algo": "6. AI应用工程、企业级Agent与算法落地 (Agent/RAG/AI工程)",
    "embedded_auto": "7. 嵌入式Linux、工业控制与智能车载软件 (C/C++/RTOS/车载)",
    "gfx_engine_cad": "8. [兴趣参照] 图形渲染、三维几何与工业CAD (OpenGL/3D/CAD)",
    "robotics_sim": "9. [高门槛对照] 机器人算法與自动驾驶规控 (ROS/SLAM/仿真)",
    "compiler_hpc": "10. [高门槛对照] 编译工具链、高性能计算与GPU加速 (LLVM/CUDA)",
    "other_technical": "11. 其他跨界或综合技术岗",
}

_JOB_CATEGORY_RULES = (
    ("gfx_engine_cad", re.compile(
        r"图形|渲染|OpenGL|GLSL|Vulkan|DirectX|Shader|光线追踪|游戏引擎|引擎开发|游戏客户端|UE4|UE5|Unreal|Unity|技术美术|\bTA\b|音视频|编解码|流媒体|\bCAD\b|\bCAE\b|几何算法|计算几何|视景仿真",
        re.I,
    )),
    ("compiler_hpc", re.compile(
        r"编译器|LLVM|MLIR|工具链|性能工程|高性能计算|\bHPC\b|并行计算|异构计算|CUDA|GPU计算|GPU\s*开发|算子",
        re.I,
    )),
    ("data_db", re.compile(
        r"数据库内核|数据库研发|数据库|存储系统|分布式存储|存储引擎|大数据|数据开发|数据研发|数据工程|数据仓库|数仓|数据治理|数据科学|Spark|Flink|数据分析",
        re.I,
    )),
    ("robotics_sim", re.compile(
        r"机器人|具身|机械臂|\bROS\b|运动控制|运控|SLAM|路径规划|导航算法|自动驾驶|智驾|规控|感知算法|定位算法",
        re.I,
    )),
    ("embedded_auto", re.compile(
        r"嵌入式|固件|Firmware|驱动开发|单片机|MCU|RTOS|AUTOSAR|车载软件|智能座舱|数控系统",
        re.I,
    )),
    ("sys_infra_sec", re.compile(
        r"操作系统|Linux内核|内核开发|系统软件|系统架构|基础架构|\bInfra\b|云计算|云资源池|云原生|中间件|分布式系统|网络工程|网络安全|信息安全|安全工程",
        re.I,
    )),
    ("client_fe_gui", re.compile(
        r"桌面开发|PC客户端|客户端|办公协同|上位机|\bQt\b|Windows|Mac|SDK|前端|Web前端|iOS|Android|鸿蒙|Harmony|小程序",
        re.I,
    )),
    ("qa_devops", re.compile(
        r"测试|测开|SDET|质量|QA|验证工程|Agent评测|运维|DevOps|SRE|工程效能",
        re.I,
    )),
    ("ai_llm_algo", re.compile(
        r"AI软件开发|AI应用|AI全栈|AI开发|大模型|\bLLM\b|智能体|Agent|\bRAG\b|AIGC|多模态|自然语言|\bNLP\b|计算机视觉|视觉算法|\bCV\b|图像算法|语音算法|推荐算法|搜索算法|机器学习|深度学习|AI算法|算法工程师",
        re.I,
    )),
    ("backend_service", re.compile(
        r"后端|服务端|Java|Golang|Go开发|Python|C\+\+|C/C\+\+|PHP|\.NET|微服务|全栈|软件开发|软件研发|软件工程|应用开发|IT开发|研发工程师|开发工程师",
        re.I,
    )),
)


def job_category(job: dict) -> str:
    """Return one stable primary category for a displayed technical job."""
    title = str(job.get("title") or "")
    for key, pattern in _JOB_CATEGORY_RULES:
        if pattern.search(title):
            return key
    return "other_technical"


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
    r"(?:本岗位|该岗位|职位)?\s*(?:主要)?负责.{0,35}"
    r"(?:产品的规划与设计|产品规划与设计|产品规划、设计|产品规划及设计)",
    re.I | re.S,
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
        return "JD明确要求连续实习时长或每周实习出勤"
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
    return True


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
    r"机械|电气|电子工程师|硬件|结构|工艺|设备|热设计|光学|射频|天线|PCB|版图|IC验证|芯片|模拟IC|数字IC|"
    r"材料|化学|化工|生物|医药|临床|护理|照护|医生|农艺|养殖|食品|营养|"
    r"销售|营销|市场|运营|客服|政企|客户|商务|采购|供应链|物流|仓储|关务|门店|店长|导购|"
    r"财务|会计|审计|税务|法务|合规|人力|HR|招聘|行政|文秘|公关|品牌|文案|编辑|主播|记者|"
    r"土建|土木|建筑|造价|暖通|给排水|环评|EHS|安全员|质量管理|体系工程师|SQE|PQE|DQE|IQC|"
    r"教师|教研|主讲|班主任|辅导员|管培生|管理培训生|交付经理|实施顾问",
    re.I,
)
_EXPLICIT_CS_ROLE_RE = re.compile(
    r"嵌入式软件|软件测试|测试开发|测开|游戏测试|系统软件|软件开发|软件研发|软件工程|算法|大模型|后端|前端|客户端|桌面开发|上位机|数据库|编译器|渲染|游戏引擎|全栈|数据开发|数据研发|大数据|网络安全|信息安全|音视频|数控系统",
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
    if _EXPLICIT_CS_ROLE_RE.search(title):
        return True
    if _NON_CS_ROLE_RE.search(title):
        return False
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
    """判断岗位是否应进入【当前目标岗位】池（无需改动 job_cohorts.py）。"""
    if is_job_record_noise(job) or is_direction_out_job(job):
        return False
    if not is_cs_software_role(job):
        return False
    prof = profile if profile is not None else load_active_profile()
    degree = str(prof.get("degree") or "本科")
    if "本科" in degree and is_master_phd_only_job(job):
        return False
    # 社招仅做参考，不作为当前本科大二主推目标岗
    if is_social_job(job):
        return False
    # 实习/在读可投岗位直接纳入
    if is_intern_job(job):
        return True
    # 校招全职作为重要参考：确认届别 >= 2026 均可纳入参考对比
    try:
        year = int(job.get("cohort") or 0)
    except (TypeError, ValueError):
        year = 0
    status = str(job.get("cohort_status") or "")
    return status == "confirmed" and year >= 2026


def _patch_job_cohorts_runtime() -> None:
    """动态修补 job_cohorts.is_confirmed_current，使用户无需打开或修改 job_cohorts.py。"""
    try:
        import job_cohorts
        job_cohorts.is_confirmed_current = lambda job: is_target_candidate_job(job)
    except Exception:
        pass


_patch_job_cohorts_runtime()


# ── 本地无偏、去图形学绑定的五维评分器 ──
def score_job_against_profile(job: dict, profile: dict | None = None) -> dict:
    """根据用户的真实优先级（本科可达性 + WLB证据 + 跨行业技术迁移度 + 现有C++/系统基础转换成本）生成透明评分。"""
    prof = profile if profile is not None else load_active_profile()
    title = str(job.get("title") or "")
    jd_raw = str(job.get("jd_raw") or "")
    company = str(job.get("company") or "")
    combined = f"{title}\n{jd_raw}"
    cat = job_category(job)
    track = job_track_label(job)

    if not is_cs_software_role(job) or is_direction_out_job(job):
        return {
            "match_score": 25,
            "recommendation": "不推荐",
            "advantages": [],
            "gaps": ["非计算机/软件研发方向"],
            "summary": "非软件研发目标方向，已过滤",
        }

    degree = str(prof.get("degree") or "本科")
    if "本科" in degree and is_master_phd_only_job(job):
        return {
            "match_score": 30,
            "recommendation": "不推荐",
            "advantages": [],
            "gaps": ["【事实】JD明确要求硕士或博士及以上学历，普通本科直接应聘受阻"],
            "summary": "学历门槛为硕博，不符合本科直通就业要求",
        }

    score = 52
    advantages = []
    gaps = []

    # 维度 1：招募轨道与本科直通可行性 (最高 +18 分)
    if is_intern_job(job):
        score += 10
        reason = internship_reason(job) or "支持实习/在读投递"
        advantages.append(f"【事实】当前可投（{reason}），契合大二正式筛选实习的目标")
    else:
        advantages.append("【校招参考】应届全职岗位，用于校准大三实习与大四秋招能力要求")

    if is_explicitly_bachelor_eligible(job):
        score += 8
        advantages.append("【事实】JD明确标注本科及以上可投，无隐性硕博学历一刀切")
    else:
        gaps.append("【未知】JD正文未显式写明学历下限，投递前需在官网确认是否接受本科")

    # 维度 2：职业族群可迁移性与本科入行友好度（彻底解除图形学加权偏置）
    if cat == "client_fe_gui":
        score += 9
        advantages.append("【迁移价值】桌面/跨平台客户端或工业办公软件，本科接纳度高(65.8%)且直接复用C++/面向对象基础")
    elif cat == "backend_service":
        score += 9
        advantages.append("【迁移价值】后端与企业级服务端研发，市场岗位基数最大、跨行业通用性极强")
    elif cat == "qa_devops":
        score += 8
        advantages.append("【迁移价值】测试开发/质量工程/DevOps，本科入行门槛友好(45.6%)，重工程自动化与系统排障")
    elif cat == "sys_infra_sec":
        score += 7
        advantages.append("【迁移价值】云原生/Linux系统与基础设施，技术生命周期长，不受短期前端/业务框架更迭影响")
    elif cat == "data_db":
        score += 7
        advantages.append("【迁移价值】数据工程与数据库应用，SQL与数据管道能力在各行业高度通用")
    elif cat == "ai_llm_algo":
        if re.search(r"AI软件|AI应用|AI全栈|Agent|智能体|RAG|AI数据工具|评测", title, re.I):
            score += 7
            advantages.append("【迁移价值】偏工程落地的AI应用/Agent研发（非纯论文科研岗），本科生可通过工程能力切入")
        else:
            score -= 6
            gaps.append("【推断】核心算法岗通常存在隐性硕博与顶会论文竞争，普通本科入行性价比偏低")
    elif cat == "gfx_engine_cad":
        # 图形学仅作个人兴趣参照，不再给高额主线加分
        score += 2
        advantages.append("【兴趣参照】含图形/三维/CAD元素，可与现有OpenGL兴趣呼应，但需注意细分岗位面较窄")
    elif cat in ("robotics_sim", "compiler_hpc"):
        score -= 4
        gaps.append("【推断】机器人算法/编译/HPC方向硕博占比高、准备周期长，普通本科直通难度较大")

    # 维度 3：现有技能与通用计算机基础匹配度（评估转换成本）
    if re.search(r"C\+\+|C/C\+\+|(?<![A-Za-z])C语言", combined, re.I):
        score += 5
        advantages.append("【事实】要求 C/C++ 编程，与你当前主修语言一致，转换成本低")
    if re.search(r"数据结构|算法基础|操作系统|计算机网络|Linux", combined, re.I):
        score += 5
        advantages.append("【事实】考察数据结构、操作系统、网络或Linux等高保值计算机基础")
    if re.search(r"Python|Git|CMake|单元测试|自动化|调试", combined, re.I):
        score += 4
        advantages.append("【事实】涉及 Python/Git/CMake/调试测试等实用工程工具链")

    # 维度 4：动态读取用户在 profile.yaml 中新增的自定义技能
    user_skills = [str(s).strip() for s in (prof.get("skills") or []) if str(s).strip()]
    for sk in user_skills:
        core_kw = sk.split(" ")[0].replace("基础", "").replace("实践", "").strip()
        if len(core_kw) >= 2 and core_kw.lower() not in ("c++", "linux", "git", "cmake", "opengl/glsl"):
            if core_kw.lower() in combined.lower():
                score += 3
                advantages.append(f"【自定义匹配】命中个人技能栈：{core_kw}")

    # 维度 5：WLB 证据与工时风险审计
    wlb = wlb_signal_audit(job)
    score += wlb["score_delta"]
    if wlb["status"] == "risk":
        gaps.append(wlb["label"])
    elif wlb["status"] in ("positive", "inferred_moderate"):
        advantages.append(wlb["label"])
    else:
        gaps.append(wlb["label"])

    # 维度 6：个人技能缺口识别（区分必需与加分）
    if re.search(r"\bJava\b(?!Script)|Spring\s*Boot|SpringCloud|MyBatis", combined, re.I):
        gaps.append("【技能缺口】需补充 Java 服务端生态（Spring Boot / MyBatis）")
    if re.search(r"Golang|\bGo语言\b", combined, re.I):
        gaps.append("【技能缺口】需补充 Go 语言与并发微服务实践")
    if re.search(r"MySQL|PostgreSQL|Redis|SQL|数据库", combined, re.I):
        gaps.append("【技能缺口】需系统建立关系型数据库 (SQL/MySQL) 与 Redis 缓存工程实践")
    if re.search(r"Docker|Kubernetes|K8s|CI/CD|云原生", combined, re.I):
        gaps.append("【技能缺口】需补充 Docker 容器化与 CI/CD 基础部署能力")
    if re.search(r"Qt|Winform|WPF|跨平台", combined, re.I):
        gaps.append("【技能缺口】若投递桌面客户端，需花 2–3 周掌握 Qt 或现代跨平台 GUI 框架")
    if re.search(r"LangChain|LangGraph|RAG|Agent|Prompt", combined, re.I):
        gaps.append("【技能缺口】需将现有 Python 脚本经验规范化为 RAG/Agent 工具调用与评测闭环")

    score = max(35, min(95, score))
    thresholds = prof.get("score_thresholds") or {}
    rec_th = int(thresholds.get("recommend", 75))
    con_th = int(thresholds.get("consider", 60))
    rec = "推荐" if score >= rec_th else ("考虑" if score >= con_th else "不推荐")

    cat_short = JOB_CATEGORY_LABELS.get(cat, "技术岗").split(". ")[-1]
    summary = f"[{track} · {cat_short}] {wlb['label']}"

    return {
        "match_score": score,
        "recommendation": rec,
        "advantages": advantages[:4],
        "gaps": gaps[:4],
        "summary": summary,
    }


_MUST_VERB_RE = re.compile(r"精通|熟练|掌握|扎实|必须|具备.*能力|熟悉", re.I)
_BONUS_CONTEXT_RE = re.compile(r"优先|加分|了解即可|更佳|者佳|bonus|preferred|plus", re.I)

TECH_REQUIREMENT_CATALOG = (
    (
        "cs_os_linux_net",
        "第一层：跨方向高保值底座（大二核心）",
        "1.计算机系统与网络",
        "操作系统、Linux 环境、多线程并发与计算机网络 (TCP/IP/HTTP/进程线程)",
        re.compile(r"操作系统|\bLinux\b|多线程|并发|进程|线程|虚拟内存|计算机网络|TCP/IP|Socket|\bHTTP\b", re.I),
        "极高（3,981个本科计算机岗中 36.5% 明确提及；28岗精析样本中达 64.3%）",
        "高（后端、客户端、系统软件、测试开发、云原生、外企研发共同的面试核心）",
        "极高（十年不过时的底层内功，无论未来去外企、国企、ToB软件还是互联网都完全通用）",
        "极高（排查线上故障、网络超时、内存泄漏、多线程死锁的必备能力）",
        "【事实】当前已在学习系统基础；建议结合 Linux 命令行与 Socket/多线程小项目巩固，无需死磕冷门内核源码",
    ),
    (
        "prog_cpp",
        "第一层：跨方向高保值底座（大二核心）",
        "2.主力静态编译语言",
        "C / C++ 语言、面向对象设计与内存管理 (STL/RAII/智能指针/调试)",
        re.compile(r"C\+\+|C/C\+\+|(?<![A-Za-z])C语言|STL|内存管理|RAII|面向对象", re.I),
        "极高（本科计算机岗中 44.4% 提及；覆盖桌面客户端、工业/医疗软件、轨交电力、高性能后端、车载等）",
        "极高（金山办公、卡斯柯、联影医疗、万兴科技、腾讯PC客户端、高德C++后端的核心语言）",
        "高（已具备学习基础，转换成本最低；可用于投递高WLB工业/办公软件与客户端，不必局限于游戏引擎或底层内卷）",
        "高（训练严谨的模块化设计、内存安全与性能分析思维）",
        "【推断】保持现代 C++ (C++11/17) 常用容器、RAII 与 GDB/ASan 调试熟练度即可，切勿把大量时间耗在偏门模板元编程",
    ),
    (
        "prog_python",
        "第一层：跨方向高保值底座（大二核心）",
        "3.高效脚本与自动化语言",
        "Python 语言、自动化脚本与快速原型开发",
        re.compile(r"\bPython\b", re.I),
        "极高（本科计算机岗中 43.4% 提及；28岗样本中占 57.1%）",
        "极高（测试开发SDET、数据工程、AI应用/Agent工程、外企自动化工具的第一主力语言）",
        "极高（投入产出比最高，几天即可上手，能让你从单一C++拓展到测开、数据、AI应用与外企IT）",
        "极高（编写自动化测试、数据处理管道、后端接口原型与日常效率工具的利器）",
        "【事实】你在维护本 Career Agent 项目时已接触 Python；建议系统掌握 pytest 与基础 Web API (FastAPI)",
    ),
    (
        "ds_algo_core",
        "第一层：跨方向高保值底座（大二核心）",
        "4.算法与数据结构",
        "数据结构与算法基础 (数组/链表/树/图/哈希/排序/复杂度分析)",
        re.compile(r"数据结构|算法基础|常用算法|时间复杂度|空间复杂度|链表|二叉树|动态规划", re.I),
        "极高（本科计算机岗中 27.7% 显式写入JD，且为几乎所有技术笔试与一面手撕代码的隐性必考项）",
        "中高（工程岗重哈希、树、图与复杂度控制，无需ACM竞赛级难度）",
        "高（所有正规软件企业校招与实习笔试的共同敲门砖）",
        "中高（帮助在工程开发中选择合理的数据容器与检索结构）",
        "【推断】保持每周3–5道中等难度经典题型（LeetCode Hot 100）维持手感即可，拒绝脱离工程实践的盲目刷题爆卷",
    ),
    (
        "sql_db_usage",
        "第一层：跨方向高保值底座（大二核心）",
        "5.数据存储与数据库应用",
        "关系型数据库 (SQL/MySQL/PostgreSQL) 与缓存中间件 (Redis) 基础使用",
        re.compile(r"\bSQL\b|MySQL|PostgreSQL|Oracle|Redis|SQLite|数据库", re.I),
        "高（本科计算机岗中 21.1% 提及，28岗跨族群样本中高达 53.6%）",
        "极高（后端研发、外企IT系统、数据工程、测试开发、AI知识库应用均高频依赖）",
        "高（区分“用数据库做应用研发”与“造数据库内核”：前者学习成本低、就业面极广、WLB友好）",
        "极高（任何真实业务系统都离不开数据持久化、表结构设计与索引查询调优）",
        "【个人缺口】你当前技术栈中尚未系统覆盖 MySQL/Redis；这是大二下学期最值得补齐的短板，投入2–3周即可显著拓宽可投岗位面",
    ),
    (
        "eng_test_debug_git",
        "第一层：跨方向高保值底座（大二核心）",
        "6.工程质量与协作工具链",
        "软件测试、调试分析、版本控制与构建 (Git/CMake/单元测试/自动化测试/CI)",
        re.compile(r"\bCMake\b|\bGit\b|CI/CD|调试|\bGDB\b|单元测试|自动化测试|接口测试|性能测试|代码评审", re.I),
        "高（本科计算机岗中 测试/调试占32.9%、Git/CMake/CI占29.8%）",
        "极高（测试开发SDET与研发效能岗的核心职责，也是所有C++/后端研发岗的日常规范）",
        "高（良好的单元测试与自动化验证习惯，是防范AI生成垃圾代码、体现工程师不可替代性的关键）",
        "极高（直接决定项目作品集是否像成熟的工业级工程而非拼凑的课程作业）",
        "【事实】已具备 Git 与 CMake 基础；下一步应在项目中加入自动化单元测试与接口测试脚本",
    ),
    (
        "prog_java_go",
        "第二层：高供给候选方向技术栈（按需选修）",
        "7.主流企业级服务端语言栈",
        "Golang 云原生后端 / Java 企业级服务端生态 (Go/Spring Boot/微服务)",
        re.compile(r"\bJava\b(?!Script)|\bGolang\b|\bGo语言\b|Spring\s*Boot|SpringCloud|微服务", re.I),
        "高（本科计算机岗中 25.9% 提及；注：原库曾删掉197条Java与43条Go岗，真实市场占比更高）",
        "极高（国央企数字化、银行金融科技、电信云、外企IT及互联网后端的最大供给技术栈）",
        "中高（Go语言语法简洁、与C/C++思维亲近，仅需1–2周即可从C++平滑切入云原生与后端服务）",
        "高（快速构建高并发HTTP/RPC服务与微服务系统）",
        "【策略建议】不必去卷Java庞杂的八股文，可利用C++基础顺带掌握结构相近的 Go 语言（或用 Python FastAPI），低成本打开后端与云服务大门",
    ),
    (
        "client_desktop_qt",
        "第二层：高供给候选方向技术栈（按需选修）",
        "8.跨平台桌面与工业/办公软件",
        "跨平台桌面客户端与工业上位机开发 (C++ / Qt / Windows & Mac SDK)",
        re.compile(r"\bQt\b|桌面端|PC客户端|上位机|Winform|跨平台|SDK", re.I),
        "中高（在桌面客户端与工业/医疗软件细分族群中占比超 70%，且本科明确可投率高达 65.8%）",
        "极高（金山办公WPS、万兴科技、联影医疗、卡斯柯、博众精工等桌面与工业软件的核心要求）",
        "高（完美承接你现有的 C++ 与图形界面经验，且避开了游戏行业的极端加班与纯互联网的35岁焦虑）",
        "高（能独立开发带可视化界面、硬件通信或本地AI集成的完整桌面软件产品）",
        "【策略建议】这是将你现有 C++ 能力转化为“高WLB、本科友好、低爆卷”就业的极佳通道，花2周体验 Qt6 即可验证兴趣",
    ),
    (
        "ai_agent_rag_eng",
        "第二层：高供给候选方向技术栈（按需选修）",
        "9.AI应用工程与智能体落地",
        "AI 辅助研发与企业级 Agent / RAG 应用工程 (AI Coding/Prompt/RAG/工具调用)",
        re.compile(r"AI\s*Coding|AI工具|AI编程|Agent|智能体|\bRAG\b|Prompt|LangChain|向量数据库", re.I),
        "高（本科计算机岗中 39.3% 提及；腾讯、阿里、字节、合合信息、顺丰、万兴均将其列为工程岗要求或加分项）",
        "高（AI应用开发工程师、AI全栈工程师、智能测试Agent工程师的核心要求）",
        "中高（重点掌握“用工程方法把LLM API/RAG/工具调用集成进可靠软件并做评测”，而非追逐每周新出的模型论文）",
        "极高（你当前的 Career Agent 系统本身就是可写入简历的真实 AI 应用工程案例！）",
        "【避坑指南】不读研应坚决避开“大模型预训练/强化学习后训练算法研究”等硕博修罗场，聚焦本科学历友好的“AI应用工程与评测闭环”",
    ),
    (
        "gfx_cad_interest",
        "第三层：个人兴趣支线与专精观察（不作主线绑定）",
        "10.图形渲染与工业几何CAD",
        "计算机图形学、渲染管线与工业 CAD 几何算法 (OpenGL/Shader/3D/CAD)",
        re.compile(r"图形学|渲染管线|\bOpenGL\b|\bGLSL\b|\bVulkan\b|\bDirectX\b|\bShader\b|三维|视景仿真|\bCAD\b", re.I),
        "低（全库本科计算机岗中仅占 6.3%，岗位高度集中于游戏公司与少数工业CAD/仿真企业）",
        "极高（仅在图形引擎、工业CAD与三维仿真岗中为必需核心）",
        "中（作为个人兴趣可锻炼空间数学与C++架构思维，但游戏行业渲染岗普遍存在项目期加班压力且转岗面窄）",
        "中（已完成的小型 OpenGL 项目可作为简历上证明 C++ 编码与调试能力的亮眼素材）",
        "【定位结论】保留现有 OpenGL 项目作为简历上的“C++工程能力证明”，将其作为个人兴趣与工业CAD备选支线，不再追加高成本的 Vulkan/UE5 底层死磕",
    ),
)


def aggregate_tech_and_learning_decisions(items: list[dict], profile: dict | None = None) -> dict:
    """Calibrated cross-job analysis for undergraduate direct-employment & WLB exploration."""
    from collections import Counter

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
        cat_label = JOB_CATEGORY_LABELS.get(job_category(job), "11. 其他跨界或综合技术岗")
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
            f"{d.split(' ')[1] if '. ' in d else d.split(' ')[0]} ({round(c / max(1, dir_counts[d]) * 100)}%)"
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

    return {
        "total_jobs_in_input": len(items),
        "all_substantive_jd": all_substantive,
        "non_cs_filtered_out": non_cs_substantive,
        "master_phd_filtered_out": master_phd_filtered,
        "cs_substantive_jd": len(cs_jobs),
        "platform_distribution": dict(platform_counts.most_common()),
        "direction_distribution": dict(dir_counts.most_common()),
        "tech_evaluations": result_items,
    }


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