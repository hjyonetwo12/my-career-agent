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
    "graphics_render": "图形学与实时渲染",
    "game_engine": "游戏引擎与客户端",
    "compiler_perf": "编译工具链与性能工程",
    "db_storage": "数据库内核与存储系统",
    "hpc_parallel": "高性能计算(HPC)",
    "cad_cae": "工业软件(CAD/CAE)与几何",
    "robotics_sim": "机器人软件与仿真",
    "infra_os": "系统软件与基础设施",
    "software_cpp": "C++软件开发与系统开发",
    "other_technical": "其他技术岗位",
}

_JOB_CATEGORY_RULES = (
    ("graphics_render", re.compile(
        r"图形|渲染|OpenGL|GLSL|Vulkan|DirectX|Shader|光线追踪|实时图形|三维渲染|技术美术|\bTA\b",
        re.I,
    )),
    ("game_engine", re.compile(
        r"游戏引擎|引擎开发|游戏客户端|UE4|UE5|Unreal|Unity|物理引擎|客户端开发",
        re.I,
    )),
    ("compiler_perf", re.compile(
        r"编译器|LLVM|MLIR|工具链|性能工程|性能优化|构建系统|调试工具|工程效能",
        re.I,
    )),
    ("db_storage", re.compile(
        r"数据库内核|数据库研发|数据库系统|存储系统|分布式存储|存储引擎|文件系统|NoSQL|NewSQL",
        re.I,
    )),
    ("hpc_parallel", re.compile(
        r"高性能计算|\bHPC\b|并行计算|异构计算|CUDA|GPU计算|算子开发|科学计算",
        re.I,
    )),
    ("cad_cae", re.compile(
        r"工业软件|\bCAD\b|\bCAE\b|\bCAM\b|几何算法|计算几何|三维内核|网格生成|数值仿真|EDA",
        re.I,
    )),
    ("robotics_sim", re.compile(
        r"机器人|具身|机械臂|\bROS\b|机器人仿真|运动控制|运控|SLAM|路径规划|仿真工程",
        re.I,
    )),
    ("infra_os", re.compile(
        r"操作系统|Linux内核|内核开发|系统软件|基础架构|\bInfra\b|中间件|分布式系统|网络编程|云原生",
        re.I,
    )),
    ("software_cpp", re.compile(
        r"C\+\+|C/C\+\+|C语言|软件开发|软件工程|系统开发|研发工程师|后端|服务端|桌面端|上位机|Qt",
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


def job_track_label(job: dict) -> str:
    """区分岗位轨道标签：实习 / 社招参考 / 提前批 / 校招"""
    if is_intern_job(job):
        return "实习"
    if is_social_job(job):
        return "社招参考"
    if is_early_batch_job(job):
        return "提前批"
    return "校招"


def is_formal_campus_job(job: dict) -> bool:
    """允许校招、实习、提前批和社招参考岗位全部保留入库与展示。"""
    return True

def is_direction_out_job(job: dict) -> bool:
    """Return True for explicit non-target roles that should never enter DB.

    The rule intentionally uses the title only and matches concrete role names,
    not broad business-domain words. For example, "广告推荐算法工程师" and
    "软件开发-运营开发方向" remain eligible for the AI coarse screen.
    """
    title = str(job.get("title") or "").strip()
    body = str(job.get("jd_raw") or "").strip()
    if is_job_record_noise(job):
        return True
    if _DIRECTION_OUT_BODY_RE.search(body) and not _TECHNICAL_TITLE_RE.search(title):
        return True
    match = _DIRECTION_OUT_TITLE_RE.search(title)
    if match is None:
        return False

    # Domain words can occur inside a genuine engineering title, such as
    # "供应链管理系统开发工程师". Preserve those for AI evaluation unless
    # the title explicitly names a non-target role.
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
    """Drop high-confidence non-target roles and parser noise before upsert."""
    kept, dropped = [], []
    for job in jobs:
        (dropped if is_direction_out_job(job) else kept).append(job)
    return kept, dropped
