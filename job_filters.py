import re


_INTERN_RE = re.compile(r"(^|[^a-z])intern(ship)?([^a-z]|$)", re.I)
_INTERN_PROJECT_RE = re.compile(
    r"日常实习|应届实习|暑期实习|寒假实习|春季实习|秋季实习|长期实习|短期实习|"
    r"寻梦实习招聘|实习生(?:专项)?招聘(?:计划)?|实习招聘|可转正实习|"
    r"实习薪酬(?:津贴)?|转正机会",
    re.I,
)
_INTERN_METADATA_RE = re.compile(r"(?:^|[\s|·])实习(?:[\s|·]|$)", re.I)
_INTERN_BODY_EN_RE = re.compile(
    r"(?:complete|undertake|join)\s+an?\s+internship|"
    r"internship\s+(?:position|program|programme|opportunity|duration)",
    re.I,
)
_INTERN_DURATION_RE = re.compile(
    r"(?:至少|能够|能|可)?实习(?:期)?(?:至少|不少于)?\s*"
    r"[一二三四五六七八九十两\d]+\s*个?月|"
    r"每周(?:至少)?(?:到岗|实习)\s*[一二三四五六七八九十两\d]+\s*天",
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
_SOCIAL_TITLE_RE = re.compile(r"社会招聘|社招|三年及以上|五年及以上|资深|高级|专家", re.I)
_SOCIAL_BODY_RE = re.compile(
    r"(?:招聘类型|岗位类型|职位类型|招聘项目|项目类型)\s*[:：|·]\s*(?:社会招聘|社招)|"
    r"(?:^|[\r\n])\s*(?:社会招聘|社招)\s*(?:[|·]|$)",
    re.I,
)
_FORMAL_CAMPUS_BODY_RE = re.compile(
    r"(?:招聘类型|岗位类型|职位类型|招聘项目|项目类型)\s*[:：|·]\s*"
    r"(?:校园招聘|校招|应届生招聘)",
    re.I,
)


JOB_CATEGORY_LABELS = {
    "backend_service": "后端、云服务与应用研发 (Java/Go/Python/C++)",
    "ai_llm_algo": "AI算法、大模型与智能体应用 (LLM/CV/NLP)",
    "qa_devops": "测试开发、质量工程与DevOps (SDET/QA/SRE)",
    "sys_infra_sec": "系统软件、基础架构与安全 (OS/Infra/云原生/安全)",
    "embedded_auto": "嵌入式软件、固件与智能车载 (MCU/RTOS/车载)",
    "robotics_sim": "机器人、自动驾驶与控制仿真 (ROS/SLAM/规控)",
    "data_db": "数据工程、大数据与数据库系统 (数仓/Spark/DB内核)",
    "client_fe_gui": "前端、移动端与桌面客户端 (Web/iOS/Android/Qt)",
    "gfx_engine_cad": "图形渲染、游戏引擎、音视频与工业CAD",
    "compiler_hpc": "编译工具链、高性能计算与异构加速 (LLVM/HPC/CUDA)",
    "other_technical": "其他非典型或跨界技术岗",
}

_JOB_CATEGORY_RULES = (
    ("gfx_engine_cad", re.compile(
        r"图形|渲染|OpenGL|GLSL|Vulkan|DirectX|Shader|光线追踪|游戏引擎|引擎开发|游戏客户端|UE4|UE5|Unreal|Unity|技术美术|\bTA\b|音视频|编解码|流媒体|工业软件|\bCAD\b|\bCAE\b|几何算法|计算几何",
        re.I,
    )),
    ("compiler_hpc", re.compile(
        r"编译器|LLVM|MLIR|工具链|性能工程|高性能计算|\bHPC\b|并行计算|异构计算|CUDA|GPU计算|算子",
        re.I,
    )),
    ("data_db", re.compile(
        r"数据库内核|数据库研发|数据库|存储系统|分布式存储|存储引擎|大数据|数据开发|数据工程|数据仓库|数仓|Spark|Flink|数据分析",
        re.I,
    )),
    ("robotics_sim", re.compile(
        r"机器人|具身|机械臂|\bROS\b|运动控制|运控|SLAM|路径规划|导航算法|自动驾驶|智驾|规控|感知算法|定位算法|仿真",
        re.I,
    )),
    ("embedded_auto", re.compile(
        r"嵌入式|固件|Firmware|驱动开发|底层开发|单片机|MCU|RTOS|AUTOSAR|车载软件|智能座舱",
        re.I,
    )),
    ("sys_infra_sec", re.compile(
        r"操作系统|Linux内核|内核开发|系统软件|基础架构|\bInfra\b|云计算|云原生|中间件|分布式系统|网络工程|网络安全|信息安全|安全工程",
        re.I,
    )),
    ("client_fe_gui", re.compile(
        r"前端|Web前端|iOS|Android|鸿蒙|Harmony|小程序|桌面端|上位机|\bQt\b|客户端",
        re.I,
    )),
    ("qa_devops", re.compile(
        r"测试|测开|SDET|质量|QA|验证工程|运维|DevOps|SRE|工程效能",
        re.I,
    )),
    ("ai_llm_algo", re.compile(
        r"大模型|\bLLM\b|智能体|Agent|\bRAG\b|AIGC|多模态|自然语言|\bNLP\b|计算机视觉|视觉算法|\bCV\b|图像算法|语音算法|推荐算法|搜索算法|机器学习|深度学习|AI算法|算法工程师",
        re.I,
    )),
    ("backend_service", re.compile(
        r"后端|服务端|Java|Golang|Go开发|Python|C\+\+|C/C\+\+|PHP|\.NET|微服务|全栈|软件开发|软件研发|软件工程|应用开发|研发工程师|开发工程师",
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

# High-confidence role names outside this repository's technical target profile.
# Keep these title-based: scanning JD prose for words such as "sales" or
# "operations" would incorrectly reject engineering roles that collaborate
# with those teams.
_DIRECTION_OUT_TITLE_RE = re.compile(
    r"(?:"
    r"产品经理|产品总经理|产品运营|项目经理|项目管理|"
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

# Some product roles use branded titles that contain no role keyword. Only
# match explicit ownership of product planning/design in the JD; a broad search
# for "product" or "product manager" would reject engineers who merely work
# with a product team.
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
    """Recognize internships even when the title is shared with a formal role."""
    return internship_reason(job) is not None


def internship_reason(job: dict) -> str | None:
    """Return the high-confidence signal proving that a role is an internship.

    Generic mentions such as "有实习经历者优先" deliberately do not match.
    """
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
    if _INTERN_PROJECT_RE.search(jd_raw):
        return "招聘项目或正文明确标注实习"
    if "实习生" in jd_raw[:260] or _INTERN_METADATA_RE.search(jd_raw[:260]):
        return "页面头部元数据明确标注实习"
    if _INTERN_BODY_EN_RE.search(jd_raw):
        return "英文正文明确要求参加实习"
    if _INTERN_DURATION_RE.search(jd_raw):
        return "岗位要求当前候选人连续实习"
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
        or _SOCIAL_BODY_RE.search(body[:500]) is not None
    )


def is_formal_campus_job(job: dict) -> bool:
    return True


def job_track_label(job: dict) -> str:
    """Return track label: 实习 / 社招参考 / 提前批 / 校招."""
    if is_intern_job(job):
        return "实习"
    if is_social_job(job):
        return "社招参考"
    if is_early_batch_job(job):
        return "提前批"
    return "校招"



_NON_CS_ROLE_RE = re.compile(
    r"机械|电气|电子工程师|硬件|结构|工艺|设备|热设计|光学|射频|天线|PCB|版图|IC验证|芯片|模拟IC|数字IC|"
    r"材料|化学|化工|生物|医药|临床|护理|照护|医生|农艺|养殖|食品|营养|"
    r"销售|营销|市场|运营|客服|政企|客户|商务|采购|供应链|物流|仓储|关务|门店|店长|导购|"
    r"财务|会计|审计|税务|法务|合规|人力|HR|招聘|行政|文秘|公关|品牌|文案|编辑|主播|记者|"
    r"土建|土木|建筑|造价|暖通|给排水|环评|EHS|安全员|质量管理|体系工程师|SQE|PQE|DQE|IQC|"
    r"教师|教研|主讲|班主任|辅导员|管培生|管理培训生",
    re.I,
)
_EXPLICIT_CS_ROLE_RE = re.compile(
    r"嵌入式软件|软件测试|测试开发|测开|游戏测试|系统软件|软件开发|软件研发|软件工程|算法|大模型|后端|前端|客户端|数据库|编译器|渲染|游戏引擎|全栈|数据开发|大数据|网络安全|信息安全|音视频",
    re.I,
)
_GENERAL_CS_ROLE_RE = re.compile(
    r"软件|程序|开发|研发|后端|前端|全栈|客户端|服务端|移动端|iOS|Android|鸿蒙|Web|Java|Python|C\+\+|Golang|Go语言|Rust|"
    r"算法|人工智能|\bAI\b|大模型|\bLLM\b|智能体|Agent|机器学习|深度学习|视觉|自然语言|语音|推荐|搜索|多模态|"
    r"数据|数据库|操作系统|内核|基础架构|\bInfra\b|云计算|云原生|分布式|存储|编译器|LLVM|HPC|高性能计算|"
    r"图形|渲染|OpenGL|Vulkan|Shader|游戏引擎|UE4|UE5|Unreal|Unity|音视频|工业软件|\bCAD\b|\bCAE\b|几何|"
    r"机器人|\bROS\b|SLAM|运动控制|自动驾驶|智驾|规控|感知|仿真|嵌入式|固件|驱动|测试|测开|SDET|运维|DevOps|SRE|安全",
    re.I,
)


def is_cs_software_role(job: dict) -> bool:
    """Identify computer science / software / algorithm / data / QA roles, excluding pure mechanical/electrical/process roles."""
    title = str(job.get("title") or "").strip()
    if _EXPLICIT_CS_ROLE_RE.search(title):
        return True
    if _NON_CS_ROLE_RE.search(title):
        return False
    return bool(_GENERAL_CS_ROLE_RE.search(title))


_MUST_VERB_RE = re.compile(r"精通|熟练|掌握|扎实|必须|具备.*能力|熟悉", re.I)
_BONUS_CONTEXT_RE = re.compile(r"优先|加分|了解即可|更佳|者佳|bonus|preferred|plus", re.I)

# (id, group_tag, dim_label, name, pattern, val_general, val_domain, val_explore, val_practice, prereq_and_cost)
TECH_REQUIREMENT_CATALOG = (
    (
        "prog_cpp",
        "通用与多路线基础",
        "1.编程语言与特性",
        "C / C++ 语言与内存模型 (STL/RAII/指针/现代特性)",
        re.compile(r"C\+\+|C/C\+\+|(?<![A-Za-z])C语言|STL|内存管理|RAII|C\+\+1[147]|C\+\+20", re.I),
        "高（覆盖系统、嵌入式、图形、游戏、机器人、HPC、基础架构、客户端等众多方向）",
        "极高（底层系统、图形引擎、工业CAD、机器人控制、高频交易的核心工作语言）",
        "高（有助于理解计算机底层内存与编译链接机制，但不是所有软件岗位的唯一选择）",
        "高（能训练严谨的资源管理与调试能力，但语法细节多、心智负担高于Python/Go）",
        "你当前已在学习；建议稳步掌握现代 C++ 常用特性（智能指针/容器），无需在探索期死磕偏门模板元编程",
    ),
    (
        "prog_python",
        "通用与多路线基础",
        "1.编程语言与特性",
        "Python 语言与快速原型/自动化开发",
        re.compile(r"\bPython\b", re.I),
        "极高（AI/大模型、算法、测试开发、数据工程的第一语言，也是C++/系统岗最常用的辅助脚本语言）",
        "极高（AI算法、大模型应用、自动化测试、数据分析的必备核心）",
        "极高（能以极低代码量快速验证新想法、写爬虫/Agent、跑通模型或搭建原型）",
        "高（极其适合编写自动化脚本、测试工具与胶水层，大幅提升日常工程效率）",
        "学习成本极低；即使主攻 C++，掌握 Python 也能让你同时具备探索 AI 应用、测开与工具链的灵活性",
    ),
    (
        "prog_java_go",
        "候选方向技术栈",
        "1.编程语言与特性",
        "Java / Go 与现代服务端生态 (Spring/微服务/高并发后端)",
        re.compile(r"\bJava\b(?!Script)|\bGolang\b|\bGo语言\b|Spring\s*Boot|SpringCloud|微服务", re.I),
        "极高（互联网与企业级后端、云原生、金融/政企信息化招聘体量最大的主流语言栈）",
        "极高（企业级服务端、云原生基础设施、高并发业务系统的核心技术栈）",
        "中（适合用来对比“业务逻辑与分布式服务开发”和“C++系统/图形开发”的工作体验差异）",
        "高（生态成熟、工程规范清晰，能快速构建可上线的完整 Web/后端服务）",
        "注意样本局限：原库曾将197条Java岗与43条Go岗标为C档删库；实际上后端开发应届生岗位基数极大，若未来希望拓宽就业面可重点观察",
    ),
    (
        "ds_algo_core",
        "通用与多路线基础",
        "2.数据结构与算法",
        "数据结构与算法基础 (数组/树/图/哈希/排序/复杂度分析)",
        re.compile(r"数据结构|算法基础|常用算法|时间复杂度|空间复杂度|链表|二叉树|图论|动态规划", re.I),
        "极高（几乎所有校招与实习技术笔试、一面手撕代码的共同门槛）",
        "中高（图形/CAD重空间树，后端/数据库重索引与哈希，算法岗重优化与图论）",
        "中（属于通用敲门砖，但刷题本身不能直接帮你判断喜欢哪个工程领域）",
        "中高（帮助写出复杂度合理的工程代码，避免性能退化）",
        "你当前正在学习；保持常规课程理解与适度中等难度练习即可，不必按竞赛标准过度消耗精力",
    ),
    (
        "cs_os_linux_net",
        "通用与多路线基础",
        "3.计算机系统基础",
        "操作系统、Linux 基础与计算机网络 (进程线程/内存/TCP/IP/HTTP)",
        re.compile(r"操作系统|\bLinux\b|多线程|并发|进程|线程|虚拟内存|计算机网络|TCP/IP|Socket|\bHTTP\b", re.I),
        "极高（后端、系统、嵌入式、测开、安全、客户端面试共同高频考察的三大件）",
        "高（系统软件、基础架构、后端高并发服务、嵌入式开发的核心理论支撑）",
        "高（理解程序如何在操作系统和网络上运行，是打通底层与应用层认知的关键）",
        "高（排查死锁、内存异常、网络超时、Linux服务器部署均直接依赖此基础）",
        "你当前正在学计算机系统基础；这是无论未来选不选底层路线都不会作废的高保值知识",
    ),
    (
        "eng_build_debug_test",
        "通用与多路线基础",
        "6.工程实践能力",
        "基础工程工具链：构建、调试、测试与版本控制 (Git/CMake/调试/单元测试)",
        re.compile(r"\bCMake\b|Makefile|\bGit\b|CI/CD|调试|\bGDB\b|单元测试|GTest|自动化测试", re.I),
        "高（所有正规软件研发团队日常协作的基础设施）",
        "中高（C++项目重CMake/GDB，测开/DevOps重CI/CD与自动化测试框架）",
        "中（属于“把代码变成工程”的工具能力，能降低尝试任何新开源项目的门槛）",
        "极高（直接决定你能不能独立跑通开源代码、定位 Bug 和管理自己的项目代码）",
        "你当前已在学习 CMake、调试与测试；在日常作业中自然使用即可，无需脱离具体代码单独死记硬背命令",
    ),
    (
        "sql_db_usage",
        "通用与多路线基础",
        "5.开发框架与数据工具",
        "数据库使用与 SQL 基础 (MySQL/PostgreSQL/Redis/数据建模)",
        re.compile(r"\bSQL\b|MySQL|PostgreSQL|Redis|SQLite|关系型数据库", re.I),
        "高（后端研发、全栈应用、数据分析、测试开发、Agent应用均高频使用）",
        "高（应用层研发与数据工程必备；注意区分“使用数据库写业务”与“研发数据库内核”）",
        "中高（掌握基础 SQL 和缓存概念，就能自己写出带数据存储的完整应用或工具）",
        "高（就像本项目中用 SQLite 存岗位一样，是极其实用的工程落地技能）",
        "先修门槛低；哪怕不走数据库内核研发方向，花几天掌握基础 SQL 与索引概念对所有应用开发都很有用",
    ),
    (
        "gfx_game_engine",
        "候选方向技术栈",
        "8.领域专属技术",
        "图形学、渲染管线与游戏引擎/客户端 (OpenGL/GLSL/Shader/UE/Unity)",
        re.compile(r"图形学|渲染管线|\bOpenGL\b|\bGLSL\b|\bVulkan\b|\bDirectX\b|\bShader\b|光线追踪|游戏引擎|\bUE4\b|\bUE5\b|\bUnreal\b|\bUnity\b", re.I),
        "中低（岗位集中于游戏、三维设计、影视数字人、自动驾驶可视化等特定领域）",
        "极高（图形渲染、游戏引擎、技术美术TA、三维工业软件岗位的决定性核心能力）",
        "极高（你当前初步感兴趣的方向；视觉反馈直观，非常适合用几周时间做小实验来检验自己是否真的喜欢）",
        "高（能综合锻炼数学变换、C++编程与调试能力；商业引擎 UE/Unity 则能快速做出可交互作品）",
        "纠偏说明：OpenGL适合理解渲染底层，而Unity/UE/Qt适合快速做出可视化应用；两者服务于不同细分岗位，没有高低贵贱之分，可根据你想走“引擎底层”还是“交互应用/客户端”按需尝试",
    ),
    (
        "ai_llm_app_infra",
        "候选方向技术栈",
        "8.领域专属技术",
        "AI 与大模型方向：应用开发 (Agent/RAG/API) vs 推理系统 (PyTorch/CUDA/部署)",
        re.compile(r"大模型|\bLLM\b|智能体|Agent|\bRAG\b|Prompt|PyTorch|推理加速|模型部署|\bTensorRT\b|\bONNX\b|\bCUDA\b", re.I),
        "高（当前校招与实习新增岗位最多的热点领域，涵盖从低门槛应用层到高门槛系统层）",
        "极高（AI应用工程、算法工程、端侧部署、高性能计算方向的核心要求）",
        "极高（你正在搭建的 Career Agent 本身就是一次绝佳的 AI Agent 应用层实践！）",
        "高（AI应用层能快速解决实际个人效率问题；AI推理/CUDA层则与C++/GPU体系结构相通）",
        "注意区分两条子路线：① AI应用与Agent开发（偏Python/API/工程产品，上手快、本科学历友好）；② AI推理加速与算法（偏C++/CUDA/数学或要求硕士学历）",
    ),
    (
        "robotics_emb_cad",
        "候选方向技术栈",
        "8.领域专属技术",
        "机器人、嵌入式车载与工业几何软件 (ROS/SLAM/RTOS/CAD/计算几何)",
        re.compile(r"\bROS\b|\bROS2\b|\bSLAM\b|运动控制|路径规划|RTOS|AUTOSAR|计算几何|几何算法|\bCAD\b|\bCAE\b", re.I),
        "中（集中于智能制造、车企、机器人独角兽与工业软件企业，国内近年招聘需求稳定）",
        "极高（机器人软件、自动驾驶规控、嵌入式系统、国产工业软件的核心壁垒）",
        "中高（如果喜欢软硬件结合、物理世界交互或三维几何造型，是很好的探索方向）",
        "高（强调系统稳定性、实时性与工程联调能力）",
        "提示：ROS 和 Qt 在机器人与工业软件中是高频实用工具，若对机器人或桌面工业软件感兴趣，完全可以通过官方教程体验，无需将其视为“低价值禁区”",
    ),
    (
        "web_client_frontend",
        "候选方向技术栈",
        "5.开发框架与客户端",
        "Web 前端、跨端与桌面 GUI 应用开发 (JS/TS/React/Vue/Qt/移动端)",
        re.compile(r"JavaScript|TypeScript|\bVue\b|\bReact\b|HTML|CSS|\bQt\b|Android|iOS|鸿蒙|跨端", re.I),
        "中高（互联网产品、SaaS、桌面工业软件界面、工具链可视化普遍需要）",
        "极高（前端工程、移动端、桌面客户端、上位机软件的核心能力）",
        "高（能直接做出别人看得见、点得着的产品界面，正反馈快，入门门槛相对友好）",
        "高（培养用户交互思维与全栈独立交付能力，比如本项目的 HTML 看板就是前端技术的体现）",
        "注意样本局限：原库曾将216条前端岗与127条Android/iOS岗作为C档删库；如果你发现自己更喜欢做直观的产品与应用而非底层黑框，这依然是重要的就业方向",
    ),
)


def aggregate_tech_and_learning_decisions(items: list[dict], profile: dict | None = None) -> dict:
    """Calibrated cross-job analysis: separates CS roles from non-CS roles, disclosures cache bias, and evaluates 4 value dimensions."""
    from collections import Counter
    import sqlite3

    all_substantive = 0
    non_cs_substantive = 0
    cs_jobs = []
    platform_counts = Counter()
    dir_counts = Counter()

    for it in items:
        job = it.get("job") or it
        jd_raw = str(job.get("jd_raw") or "").strip()
        if len(jd_raw) < 100:
            continue
        all_substantive += 1
        if not is_cs_software_role(job):
            non_cs_substantive += 1
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
        cat_label = JOB_CATEGORY_LABELS.get(job_category(job), "其他非典型或跨界技术岗")
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
            f"{d.split(' ')[0]} ({round(c / max(1, dir_counts[d]) * 100)}%)"
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
        "cs_substantive_jd": len(cs_jobs),
        "platform_distribution": dict(platform_counts.most_common()),
        "direction_distribution": dict(dir_counts.most_common()),
        "tech_evaluations": result_items,
    }


def is_direction_out_job(job: dict) -> bool:
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
        r"产品(?:经理|总经理|运营)|\bProduct\s+Manager\b|项目(?:经理|管理)|销售|售前|"
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