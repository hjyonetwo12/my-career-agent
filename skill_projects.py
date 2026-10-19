"""独立功能模块：「技能栈与实现项目」(skill_projects.py, v2.0 完整交互闭环版)。

核心能力与设计原则：
1. 自动获取输入材料（零重复粘贴）：
   - 自动从 SQLite 数据库 (`jobs` 表) 读取有效岗位、类别、JD职责与要求、技术栈、原始链接与时效信息；
   - 自动读取 `profile.yaml` 中的学习阶段、现有技能与能力边界（如 Python 尚未系统学习、C++ 基础阶段）、
     未锁死方向的探索状态、个人兴趣爱好（游戏局势拆解、动漫与剧情、系统底层好奇、视觉创作/绘画、知识/日程/预算管理、日语/原版作品阅读）与反偏置规则。
2. 真实岗位技能统计（7 大维度）与可追踪「技能约束清单 (skill_constraints)」：
   - 按 10 大计算机岗位类别分组统计，合并同义词，剔除单岗多城重复发布偏差，区分必需 (must)、常见加分 (bonus) 与少量岗位特殊要求 (niche)，样本不足时明确标黄预警；
   - 生成项目前先构造内部可追踪的 `skill_constraints` 清单（含技能名、来源类别、频率与样本量、原始招聘证据引用、必需/加分定性、对应工程能力、需达到的训练深度）。
3. 将就业技能约束重设计为个人兴趣驱动的 6 个实质差异化候选工程项目：
   - 固化 `PROJECT_REDESIGN_PROMPT_TEMPLATE` 与确定性工程蓝图引擎：技能栈要求不变，项目主题与产品形态由个人兴趣自然驱动（拒绝给普通 CRUD 管理系统套二次元 UI 皮肤的假重设计）；
   - 每个候选项目完整包含 12 项要素 + 严格三档标准的「技能覆盖审计表 (完整覆盖 / 部分覆盖 / 未覆盖)」+ 「AI 辅助开发 vs 个人必须亲自完成与验证边界」。
4. 完整交互闭环（先生成候选 → 用户自主选择 → 生成实施计划 → 迭代反馈与对比 → 状态持久化与防误覆盖）：
   - 在用户主动选择前，绝不自动将某个项目设为最终项目，也不预生成绑定计划；
   - 用户选择项目后可一键生成包含 10 大要素的分阶段实施计划并持久保存；
   - 支持缩小 MVP、强化特定技能、调整主题侧重、双项目对比等迭代反馈，且刷新技能统计不会无提示覆盖用户已选项目与已生成计划。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import job_filters
from profile_config import extract_skill_proficiency_map, load_profile

logger = logging.getLogger(__name__)

_CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
SKILL_PROJECT_VERSION = "sp-v3.0"
MIN_CATEGORY_UNIQUE_SAMPLES = 15
MIN_OVERALL_UNIQUE_SAMPLES = 15

DEFAULT_SKILL_PROJECT_STATE_PATH = (
    Path(__file__).resolve().parent / "data" / "skill_projects_state.json"
)

# ──────────────────────────────────────────────────────────────────────────
# 固化的项目重设计 Prompt 模板（模块内置默认行为，无需用户每次重复输入）
# ──────────────────────────────────────────────────────────────────────────
PROJECT_REDESIGN_PROMPT_TEMPLATE = """你是资深软件架构师与工程教育专家。
请严格基于【真实岗位技能约束清单】与【用户个人画像（含兴趣爱好与能力边界）】，将硬核就业技能训练要求重设计为用户真正愿意持续投入、可分阶段实现并验证的工程项目。
必须遵守以下铁律：
1. 技能栈要求不变，项目主题重构；就业训练目标不变，产品形态由个人兴趣自然驱动。
2. 严禁把普通后台管理系统仅替换成动漫/游戏 UI 皮肤就宣称完成重设计；产品的使用场景、核心机制、交互方式与数据模型必须与主题自然融合。
3. 严禁因追求趣味性而删减数据库、网络、并发、数据结构、自动化测试或调试排障等硬核训练要求；若某项目确实无法自然融入某项技能，必须在技能覆盖审计表中诚实标记为「部分覆盖」或「未覆盖」，并提供独立补充训练建议。
4. 严格遵守反偏置规则：不得因为用户正在学习 C++ 就只推荐 C++ 项目；不得将玩游戏/看动漫/画画直接等同于锁定游戏开发或图形学职业。
5. 明确区分「用户必须亲自理解、设计、编码和验证的核心任务」与「可由 AI 加速的辅助任务」。"""

# ──────────────────────────────────────────────────────────────────────────
# 5.1 七大技能统计维度定义与 23 组规范化同义词词典
# ──────────────────────────────────────────────────────────────────────────
SKILL_DIMENSION_LABELS = {
    "programming_languages": "1. 编程语言",
    "frameworks_and_tools": "2. 框架与开发工具",
    "ds_and_algorithms": "3. 数据结构与算法",
    "os_network_db": "4. 操作系统、计算机网络与数据库",
    "concurrency_perf_debug_test": "5. 并发、性能分析、调试与测试",
    "build_vcs_cicd_deploy": "6. 构建、版本管理、CI/CD 与部署",
    "domain_specific": "7. 不同岗位类别专属技术能力",
}

SKILL_SYNONYM_CATALOG: tuple[tuple[str, str, str, list[str], re.Pattern, str, str], ...] = (
    (
        "lang_cpp",
        "C/C++",
        "programming_languages",
        ["C++", "C/C++", "C语言", "C++11", "C++14", "C++17", "C++20", "现代C++", "STL"],
        re.compile(r"C\s*/\s*C\+\+|C\+\+(?:11|14|17|20)?|(?<![A-Za-z])C语言|\bSTL\b|RAII|智能指针", re.I),
        "使用现代 C++ (RAII/智能指针/STL容器/移动语义) 构建内存安全、模块化的系统或客户端核心模块",
        "需亲手实现带资源生命周期管理与异常安全的模块，并通过 ASan/Valgrind 零泄漏验证，不能仅调现成库接口",
    ),
    (
        "lang_python",
        "Python",
        "programming_languages",
        ["Python", "Python3", "PyTorch脚本", "Python自动化"],
        re.compile(r"\bPython(?:3)?\b", re.I),
        "使用 Python 编写结构化后端服务、数据处理流水线、自动化测试框架或 Agent 工具调用底座",
        "需掌握类型注解、模块化分层、异常容错与 pytest 自动化验证，不能仅停留在几十行无测试脚本",
    ),
    (
        "lang_java",
        "Java",
        "programming_languages",
        ["Java", "Java8", "Java11", "Java17", "JVM", "J2EE"],
        re.compile(r"\bJava\b(?!Script)|\bJVM\b|\bJDK\b|\bJ2EE\b", re.I),
        "使用 Java 与集合/并发包构建高可靠的企业级服务端业务系统与领域模型",
        "需理解面向对象分层、JVM 内存与线程模型、事务边界控制及接口契约测试",
    ),
    (
        "lang_golang",
        "Go (Golang)",
        "programming_languages",
        ["Golang", "Go语言", "Go开发", "Goroutine"],
        re.compile(r"\bGolang\b|\bGo\s*语言\b|\bGo\s*开发\b|\bGoroutine\b", re.I),
        "使用 Go 语言及 Goroutine/Channel 构建高并发微服务、定时调度器或云原生中间件",
        "需掌握 Context 超时传递、Channel 并发同步、逃逸分析基础与 pprof 性能剖析",
    ),
    (
        "lang_ts_js",
        "TypeScript / JavaScript",
        "programming_languages",
        ["TypeScript", "JavaScript", "ES6", "Node.js", "TS/JS"],
        re.compile(r"\bTypeScript\b|\bJavaScript\b|\bNode\.?js\b|\bES6\b", re.I),
        "使用 TypeScript/JavaScript 构建类型安全的交互式前端界面、可视化画布或全栈工具",
        "需掌握异步事件流、状态管理、DOM/Canvas 渲染性能控制与接口联调",
    ),
    (
        "lang_rust",
        "Rust",
        "programming_languages",
        ["Rust", "Cargo", "Tokio"],
        re.compile(r"\bRust\b|\bTokio\b", re.I),
        "使用所有权与借用检查机制编写零数据竞争的系统组件或高性能命令行工具",
        "需理解生命周期标注、Result 错误处理与无锁/异步并发模型",
    ),
    (
        "fw_qt_gui",
        "Qt / 跨平台客户端框架 (Qt6/QML/WPF/Electron)",
        "frameworks_and_tools",
        ["Qt", "Qt5", "Qt6", "QML", "WPF", "MFC", "Win32", "Electron", "跨平台客户端"],
        re.compile(r"\bQt[56]?\b|\bQML\b|\bWPF\b|\bWin32\b|\bElectron\b|上位机|桌面客户端", re.I),
        "设计跨平台桌面应用架构，实现信号槽事件驱动、自定义绘制控件及 UI 线程与耗时任务线程解耦",
        "需保证后台高负载计算/加载时 UI 主线程帧率稳定不卡顿，并具备崩溃日志收集能力",
    ),
    (
        "fw_spring",
        "Spring Boot / Spring Cloud 微服务生态",
        "frameworks_and_tools",
        ["Spring", "SpringBoot", "Spring Boot", "SpringCloud", "Spring Cloud", "MyBatis", "Netty"],
        re.compile(r"\bSpring\s*Boot\b|\bSpring\s*Cloud\b|\bMyBatis\b|\bNetty\b", re.I),
        "基于依赖注入、AOP 切面、统一异常拦截与 ORM 映射构建标准企业级 REST/RPC 服务",
        "需独立设计事务传播策略、接口幂等拦截器与多环境配置隔离",
    ),
    (
        "fw_web_py_go",
        "轻量 Web 与 RPC 服务框架 (FastAPI/Gin/gRPC/Flask)",
        "frameworks_and_tools",
        ["FastAPI", "Flask", "Django", "Gin", "gRPC", "Protobuf", "RESTful"],
        re.compile(r"\bFastAPI\b|\bFlask\b|\bDjango\b|\bGin\b|\bgRPC\b|\bProtobuf\b|\bRESTful\b", re.I),
        "设计清晰的 OpenAPI/Protobuf 接口契约，实现参数校验、分页游标、鉴权中间件与优雅停机",
        "需通过并发压测验证接口 P95/P99 延迟，并提供完整的错误码规范与接口自动化测试",
    ),
    (
        "fw_frontend_modern",
        "现代前端与可视化框架 (React/Vue/Canvas/Three.js)",
        "frameworks_and_tools",
        ["React", "Vue", "Vue3", "Angular", "Three.js", "WebGL", "ECharts"],
        re.compile(r"\bReact\b|\bVue[23]?\b|\bThree\.?js\b|\bWebGL\b|\bECharts\b", re.I),
        "构建组件化、响应式的数据可视化看板、图形编辑画布或单页交互应用",
        "需实现组件状态解耦、大数据量列表/画布虚拟化渲染与窄屏自适应布局",
    ),
    (
        "algo_ds_core",
        "数据结构与常用算法 (树/图/哈希/排序/时空复杂度)",
        "ds_and_algorithms",
        ["数据结构", "算法", "链表", "二叉树", "红黑树", "哈希表", "图论", "动态规划", "时间复杂度"],
        re.compile(r"数据结构|常用算法|算法基础|时间复杂度|空间复杂度|二叉树|红黑树|哈希表|动态规划|最短路", re.I),
        "在真实工程场景中選用或手写合适的数据结构（如 LRU 链表、前缀树 Trie、跳表 SkipList、A* 优先队列、BVH 空间树）解决检索与调度瓶颈",
        "需在项目中提供 O(N) 朴素实现与 O(log N)/O(1) 优化实现的实测耗时对比数据，而非仅背诵刷题模板",
    ),
    (
        "os_linux_sys",
        "Linux 环境与操作系统原理 (进程线程/虚拟内存/文件系统/I/O)",
        "os_network_db",
        ["Linux", "Unix", "操作系统", "OS", "进程", "虚拟内存", "文件系统", "epoll", "系统调用"],
        re.compile(r"\bLinux\b|\bUnix\b|操作系统|虚拟内存|系统调用|\bepoll\b|\bio_uring\b|进程间通信|\bIPC\b", re.I),
        "理解用户态与内核态交互、文件描述符生命周期、I/O 多路复用 (`epoll`)、缓冲刷盘 (`fsync`/WAL) 与虚拟内存布局",
        "需能在 Linux 环境下编写守护服务或系统工具，并使用 `strace`/`lsof`/`/proc` 分析运行状态",
    ),
    (
        "net_tcp_http",
        "计算机网络与协议栈 (TCP/IP/HTTP/Socket/粘包与重传)",
        "os_network_db",
        ["计算机网络", "网络编程", "TCP/IP", "TCP", "UDP", "HTTP", "HTTPS", "WebSocket", "Socket"],
        re.compile(r"计算机网络|网络编程|TCP/IP|\bTCP\b|\bUDP\b|\bHTTP(?:S)?\b|\bWebSocket\b|\bSocket\b", re.I),
        "设计应用层通信协议（帧头长度/魔数/CRC校验），处理非阻塞 Socket 读写、半包粘包、心跳保活、超时重试与断线重连",
        "需在模拟弱网（延迟/丢包/乱序）或并发连接下验证通信零崩溃、数据零错乱",
    ),
    (
        "db_sql_rdbms",
        "关系型数据库与 SQL 优化 (MySQL/PostgreSQL/SQLite/索引与事务)",
        "os_network_db",
        ["MySQL", "PostgreSQL", "SQL", "SQLite", "Oracle", "关系型数据库", "索引优化", "数据库事务"],
        re.compile(r"\bMySQL\b|\bPostgreSQL\b|\bSQLite\b|\bSQL\b|关系型数据库|事务隔离|执行计划|B\+树索引", re.I),
        "完成规范化数据库表结构建模、B+ 树复合索引设计、ACID 事务隔离控制（乐观锁/悲观锁）与慢查询 `EXPLAIN` 调优",
        "需在万级以上真实/合成数据集上对比建索引前后的扫描行数与查询延迟（下降 >80%），拒绝只会写无索引 `SELECT *`",
    ),
    (
        "db_cache_mq",
        "缓存中间件与消息队列 (Redis/Kafka/RabbitMQ/RocketMQ)",
        "os_network_db",
        ["Redis", "Kafka", "RabbitMQ", "RocketMQ", "消息队列", "分布式缓存", "分布式锁"],
        re.compile(r"\bRedis\b|\bKafka\b|\bRabbitMQ\b|\bRocketMQ\b|消息队列|分布式缓存|缓存击穿|缓存雪崩", re.I),
        "实现 Cache-Aside 多级缓存、TTL 随机抖动防雪崩、互斥锁防击穿、分布式锁幂等控制或异步任务队列削峰",
        "需实现缓存节点故障时的自动熔断回源降级，并通过压测验证缓存命中率与吞吐量提升倍数",
    ),
    (
        "conc_multithread",
        "多线程并发编程与同步机制 (线程池/锁机制/原子操作/协程)",
        "concurrency_perf_debug_test",
        ["多线程", "并发编程", "高并发", "线程池", "互斥锁", "读写锁", "无锁", "原子操作", "协程"],
        re.compile(r"多线程|并发编程|高并发|线程池|互斥锁|无锁|死锁|线程安全|原子操作|协程", re.I),
        "使用线程池、任务队列、条件变量、读写锁或原子变量实现高吞吐并发任务处理，消除数据竞争 (Data Race) 与死锁",
        "需在并发压力测试与 ThreadSanitizer (TSan) / 竞态检测下通过确定性正确性验证",
    ),
    (
        "test_debug_profile",
        "调试排障与性能/内存分析 (GDB/ASan/Valgrind/perf/抓包)",
        "concurrency_perf_debug_test",
        ["GDB", "Valgrind", "ASan", "AddressSanitizer", "perf", "pprof", "Wireshark", "tcpdump", "性能调优", "内存泄漏"],
        re.compile(r"\bGDB\b|\bValgrind\b|\bASan\b|AddressSanitizer|\bperf\b|\bpprof\b|\bWireshark\b|\btcpdump\b|内存泄漏|性能分析|性能调优|故障排查", re.I),
        "运用调试器、内存消毒器与剖析器定位段错误、内存泄漏、CPU 热点函数及网络重传瓶颈，输出量化诊断报告",
        "需在项目文档中保留至少 1 份真实的『故障复现 → 工具定位 → 修复前后火焰图/内存曲线对比』实验记录",
    ),
    (
        "test_automation_qa",
        "单元测试、接口测试与自动化测试框架 (pytest/GTest/JUnit/Fuzzing)",
        "concurrency_perf_debug_test",
        ["单元测试", "自动化测试", "接口测试", "性能测试", "回归测试", "pytest", "GTest", "GoogleTest", "JUnit", "Selenium", "Playwright"],
        re.compile(r"单元测试|自动化测试|接口测试|性能测试|回归测试|测试框架|\bpytest\b|\bGTest\b|\bGoogleTest\b|\bJUnit\b|压力测试", re.I),
        "设计覆盖正常路径、边界条件、异常输入与故障注入的自动化测试套件，统计代码覆盖率并接入持续集成门禁",
        "核心模块需具备 ≥15 条可自动运行的单元测试与异常边界断言，单测不通过禁止合并代码",
    ),
    (
        "build_git_cmake",
        "版本控制与现代构建系统 (Git/CMake/Make/代码评审)",
        "build_vcs_cicd_deploy",
        ["Git", "GitHub", "GitLab", "CMake", "Makefile", "Ninja", "Gradle", "Maven", "Code Review"],
        re.compile(r"\bGit\b|\bGitHub\b|\bGitLab\b|\bCMake\b|\bMakefile\b|\bGradle\b|\bMaven\b|代码评审|版本控制", re.I),
        "使用规范的 Git 分支与 Commit 历史管理演进，编写跨平台 CMakeLists.txt / 依赖锁定文件实现一键可复现编译",
        "项目必须能从干净环境通过单条构建命令 (`cmake -B build && cmake --build build` 或同等级命令) 零警告编译通过",
    ),
    (
        "tool_docker_k8s",
        "容器化部署与 CI/CD 流水线 (Docker/K8s/GitHub Actions)",
        "build_vcs_cicd_deploy",
        ["Docker", "Kubernetes", "K8s", "CI/CD", "GitHub Actions", "Jenkins", "容器化", "云原生"],
        re.compile(r"\bDocker\b|\bKubernetes\b|\bK8s\b|CI/CD|持续集成|持续交付|容器化|\bJenkins\b", re.I),
        "编写 Dockerfile / docker-compose.yml 编排应用与数据库依赖，配置 GitHub Actions 自动执行构建、单测与回归检查",
        "需实现一键容器拉起与 CI 流水线自动化测试验证",
    ),
    (
        "dom_llm_rag_agent",
        "LLM 应用工程、RAG 检索增强与 Agent 工具调用评测",
        "domain_specific",
        ["大模型", "LLM", "Agent", "智能体", "RAG", "向量数据库", "Prompt", "Function Calling", "LangChain", "模型评测"],
        re.compile(r"大模型|\bLLM\b|\bAgent\b|智能体|\bRAG\b|向量数据库|Function\s*Calling|工具调用|\bLangChain\b|检索增强", re.I),
        "构建混合检索 (BM25+向量+RRF重排)、确定性 Schema 校验工具调用、超时熔断回退与基于黄金数据集的量化评测沙盒",
        "拒绝简单套壳调 API；必须包含检索召回率 (`Recall@K`) 与工具调用成功率的自动化对比实验",
    ),
    (
        "dom_graphics_3d",
        "计算机图形学、渲染管线与三维几何 (OpenGL/GLSL/Vulkan/3D/CAD)",
        "domain_specific",
        ["OpenGL", "GLSL", "Vulkan", "DirectX", "Shader", "图形学", "渲染管线", "CAD", "CAE", "Unity", "UE4/UE5"],
        re.compile(r"\bOpenGL\b|\bGLSL\b|\bVulkan\b|\bDirectX\b|\bShader\b|渲染管线|图形学|\bCAD\b|\bCAE\b|\bUnity\b|\bUE[45]?\b|\bUnreal\b", re.I),
        "实现可编程渲染管线 (Vertex/Fragment Shader)、纹理与帧缓冲后处理、粒子系统或三维网格空间加速结构 (BVH/四叉树)",
        "需掌握矩阵变换推导、GPU 缓冲管理与 RenderDoc/帧耗时分析，避免黑盒照搬引擎模板",
    ),
    (
        "dom_embedded_rtos",
        "嵌入式系统、RTOS、驱动与工业/车载总线 (MCU/ARM/RTOS/CAN/ROS)",
        "domain_specific",
        ["嵌入式Linux", "RTOS", "FreeRTOS", "MCU", "STM32", "ARM", "CAN总线", "UART/SPI/I2C", "ROS/ROS2", "SLAM"],
        re.compile(r"嵌入式|\bRTOS\b|\bFreeRTOS\b|\bMCU\b|\bSTM32\b|\bARM\b|\bCAN\b总线|\bAUTOSAR\b|\bROS2?\b|\bSLAM\b|驱动开发|固件", re.I),
        "理解硬件抽象层、中断与实时任务调度、工业/车载总线帧协议解析、看门狗与确定性有限状态机 (FSM) 容错保护",
        "可通过 Linux 虚拟总线 (SocketCAN/PTY) 完成协议编解码、CRC 校验与异常注入闭环验证",
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

    for skill_id, canon_name, dim_key, synonyms, pattern, eng_cap, depth_req in SKILL_SYNONYM_CATALOG:
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
                    evidence_clause = cl[:110]
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
            "engineering_capability": eng_cap,
            "required_training_depth": depth_req,
        }
    return found


_SKILL_STATS_MEM_CACHE: dict[tuple, dict] = {}


def aggregate_skills_by_category(
    items: list[dict],
    profile: dict | None = None,
    min_category_samples: int = MIN_CATEGORY_UNIQUE_SAMPLES,
) -> dict:
    """对真实岗位数据按岗位类别提取高频技能栈（含去重校正、必需/加分/小众区分、时间趋势与样本充足性标记）。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    first_id = (items[0].get("job", items[0]).get("id") if items and isinstance(items[0], dict) else None)
    last_id = (items[-1].get("job", items[-1]).get("id") if items and isinstance(items[-1], dict) else None)
    mem_key = (len(items), first_id, last_id, min_category_samples, job_filters.profile_version_id(prof))
    if mem_key in _SKILL_STATS_MEM_CACHE:
        return _SKILL_STATS_MEM_CACHE[mem_key]

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
            "skill_categories": defaultdict(Counter),
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
        pcat_label = job_filters.JOB_CATEGORY_LABELS.get(pcat, pcat)
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
                target_b["skill_categories"][sid][pcat_label] += 1
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
                    if skill_ids[i] != "build_git_cmake" and skill_ids[j] != "build_git_cmake":
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
            _, cname, dim_key, syns, _, eng_cap, depth_req = _SKILL_BY_ID[sid]
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
            top_cat_list = [
                f"{ck}({cnt}岗)"
                for ck, cnt in bucket["skill_categories"][sid].most_common(4)
            ]
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
                "source_categories": top_cat_list,
                "trend_summary": trend_str,
                "examples": bucket["skill_examples"][sid],
                "engineering_capability": eng_cap,
                "required_training_depth": depth_req,
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
            "top_skills": skills_out[:16],
            "all_skills": skills_out,
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


# ──────────────────────────────────────────────────────────────────────────
# 5.2 构造内部可追踪的「技能约束清单 (skill_constraints)」
# ──────────────────────────────────────────────────────────────────────────
def build_skill_constraints_checklist(skill_stats: dict) -> list[dict]:
    """在生成候选项目前，基于真实岗位技能统计构造内部可追踪的技能约束清单（Section 5.2）。"""
    overall = skill_stats.get("overall") or {}
    all_skills = overall.get("all_skills") or overall.get("top_skills") or []
    total_unique = int(overall.get("unique_job_count") or 0)

    constraints: list[dict] = []
    for idx, sk in enumerate(all_skills[:14], 1):
        sid = sk["skill_id"]
        examples = sk.get("examples") or []
        ev_refs = []
        for ex in examples[:2]:
            ev_refs.append({
                "company": ex.get("company", ""),
                "title": ex.get("title", ""),
                "city": ex.get("city", ""),
                "url": ex.get("url", ""),
                "quote": ex.get("evidence_clause", ""),
            })
        if not ev_refs:
            ev_refs.append({
                "company": "全库统计汇总",
                "title": "多岗位共性要求",
                "city": "多地",
                "url": "",
                "quote": f"在 {sk.get('unique_job_count', 0)} 个独立岗位 JD 中出现",
            })

        ev_quotes_alias = [
            {
                **r,
                "jd_url": r.get("url", ""),
                "snippet": r.get("quote", ""),
            }
            for r in ev_refs
        ]
        constraints.append({
            "constraint_id": f"SC-{idx:02d}",
            "skill_id": sid,
            "skill_name": sk["canonical_name"],
            "dimension": sk["dimension"],
            "dimension_label": sk["dimension_label"],
            "source_categories": sk.get("source_categories") or ["多类别通用"],
            "unique_job_count": sk["unique_job_count"],
            "sample_size": total_unique,
            "unique_job_pct": sk["unique_job_pct"],
            "must_ratio_pct": sk.get("must_ratio_pct", 0),
            "frequency_and_sample": {
                "unique_job_count": sk["unique_job_count"],
                "unique_job_pct": sk["unique_job_pct"],
                "raw_posting_count": sk["raw_posting_count"],
                "total_sample_jobs": total_unique,
                "summary": f"{sk['unique_job_count']} / {total_unique} 独立岗位 ({sk['unique_job_pct']}%)",
            },
            "raw_recruitment_evidence": ev_quotes_alias,
            "evidence_quotes": ev_quotes_alias,
            "requirement_type": sk["tier_role"],
            "requirement_type_label": sk["tier_role_label"],
            "engineering_capability": sk.get("engineering_capability") or "",
            "required_training_depth": sk.get("required_training_depth") or "",
        })
    return constraints


# ──────────────────────────────────────────────────────────────────────────
# 4.3 自动化输入材料组装逻辑（岗位数据 + 技能统计 + 技能约束 + 个人画像）
# ──────────────────────────────────────────────────────────────────────────
def assemble_skill_project_inputs(
    items: list[dict],
    profile: dict | None = None,
    min_category_samples: int = MIN_CATEGORY_UNIQUE_SAMPLES,
) -> dict:
    """自动组装岗位数据、技能统计、技能约束清单与个人画像，无需用户每次重复粘贴背景或提示词（Section 4）。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    skill_stats = aggregate_skills_by_category(items, prof, min_category_samples=min_category_samples)
    constraints = build_skill_constraints_checklist(skill_stats)

    pp = prof.get("personal_profile") or {}
    edu = prof.get("education") or {}
    prefs = prof.get("preferences") or {}
    prof_map = extract_skill_proficiency_map(prof)
    rec_mode = str(prof.get("recommendation_mode") or "exploration").strip().lower()

    overall_u = int(skill_stats.get("overall", {}).get("unique_job_count") or 0)
    data_warnings = []
    if overall_u == 0:
        data_warnings.append("⚠️ 当前数据库中暂无完整 JD 的计算机岗位记录，请先运行爬虫或检查数据库文件。")
    elif overall_u < MIN_OVERALL_UNIQUE_SAMPLES:
        data_warnings.append(
            f"⚠️ 当前有效独立岗位样本量仅 {overall_u} 个（低于最小稳健统计阈值 {MIN_OVERALL_UNIQUE_SAMPLES} 个），统计分布仅供初步参考，系统绝不编造行业趋势。"
        )

    cat_insufficient = [
        f"{c['category_label']}({c['unique_job_count']}岗)"
        for c in (skill_stats.get("by_category") or {}).values()
        if not c.get("sample_sufficient")
    ]
    if cat_insufficient:
        data_warnings.append(
            f"ℹ️ 部分细分岗位类别样本较少（{'、'.join(cat_insufficient[:4])}），已自动标记样本不足，不包装为普遍要求。"
        )

    assembled_profile = {
        "profile_version_id": job_filters.profile_version_id(prof),
        "recommendation_mode": rec_mode,
        "target_direction_locked": bool(prof.get("target_direction_locked", False)),
        "skill_proficiency_map": prof_map,
        "education_stage": {
            "degree": str(prof.get("degree") or edu.get("degree") or "本科"),
            "graduation_year": int(prof.get("target_cohort") or edu.get("graduation_year") or 2029),
            "current_stage": str(edu.get("current_stage") or "本科在读"),
            "target_job_type": str(prof.get("job_type") or "实习"),
        },
        "current_skills_and_boundaries": {
            "declared_skills": list(prof.get("skills") or []),
            "project_evidence": list(prof.get("project_evidence") or []),
            "proficiency_map": prof_map,
            "python_not_learned_yet": bool((prof_map.get("python") or {}).get("not_learned_yet", True)),
            "cpp_stage": str((prof_map.get("c++") or {}).get("level", "基础学习阶段")),
        },
        "interest_themes_from_profile": {
            "thinking_and_work_style": list(pp.get("thinking_and_work_style") or []),
            "hobbies_and_general_interests": list(pp.get("hobbies_and_general_interests") or []),
            "possible_work_content_preferences": list(pp.get("possible_work_content_preferences") or []),
            "interest_caveats_anti_bias": list(pp.get("interest_caveats") or []),
        },
        "priorities": {
            "wlb_priority": str(prefs.get("wlb_priority") or "high"),
            "growth_priority": str(prefs.get("growth_priority") or "high"),
            "preferred_cities": list(prof.get("preferred_cities") or []),
        },
    }

    return {
        "assembled_at": datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
        "data_sufficient": overall_u >= MIN_OVERALL_UNIQUE_SAMPLES,
        "data_warnings": data_warnings,
        "profile_context": assembled_profile,
        "skill_stats": skill_stats,
        "skill_constraints": constraints,
        "redesign_prompt_template": PROJECT_REDESIGN_PROMPT_TEMPLATE,
    }


# ──────────────────────────────────────────────────────────────────────────
# 六、6 个个人兴趣驱动 × 硬核就业技能约束的候选工程项目蓝图（sp-v3.0 导师级重构版）
#     生成顺序严格遵循：
#     第一步：先说清楚项目到底是什么（6个白话问答 + 非计算机背景日常例子 + 第一层普通用户速览）
#     第二步：明确最小可交付版本 MVP（解决什么问题、3-5个核心操作、输入/输出、展示方式、明确不做、闭环证明）
#     第三步：克制且有理由的技术选型（单一主力技术栈 + 基础版→进阶版→更进一步 + 前置条件）
#     第四步：清楚的 4 阶段学习路径（阶段0准备与15分钟自测 → 阶段1最小闭环 → 阶段2核心技术5问 → 阶段3验证交付）
#     第五步：现实的双轨时间估算（区分“写代码跑通” vs “理解原理+测试调试+文档”，含估算依据与误差来源）
#     第六步：可执行、可复现的验收标准（禁止无依据的“QPS提升5倍”等口号，明确命令、输入、输出与基线）
#     第七步：诚实一致的技能覆盖审计与 AI 协同边界
# ──────────────────────────────────────────────────────────────────────────

PROJECT_BLUEPRINT_CATALOG: tuple[dict, ...] = (
    # ══════════════════════════════════════════════════════════════════════
    # 候选项目一：跨媒介作品设定图谱与多周目进度检索服务（主力：Python + FastAPI + SQLite→MySQL）
    # ══════════════════════════════════════════════════════════════════════
    {
        "project_id": "proj_anime_4d_collection_vault",
        "title": "候选项目一：《跨媒介作品（番剧/游戏/书籍）设定图谱与多周目进度检索服务》",
        "short_title": "跨媒介番剧/游戏设定图谱与进度检索服务",
        "interest_theme_tag": "🎬 动漫/游戏/书籍归档 + 复杂设定关系与多周目分支拆解",
        "tier_priority": "P0_prioritize_now",
        "priority_badge": "🌟 P0 首选 · 补齐数据库索引与后端接口最大公约数",
        "difficulty_level": "入门 → 进阶 (Entry to Intermediate)",
        "primary_languages": ["Python (FastAPI) [进阶可选 Go (Gin)]"],
        "skill_ids": ["db_sql_rdbms", "net_tcp_http", "algo_ds_core", "test_automation_qa", "lang_python", "db_cache_mq"],
        "target_categories": ["backend_dist", "data_platform", "testing_quality", "ai_ml"],
        "target_roles": ["后端开发工程师", "数据工程/平台研发工程师", "测试开发工程师(SDET)", "AI应用全栈工程师"],
        "plain_intro": {
            "what_is_it_called": "跨媒介作品（番剧/游戏/书籍）设定图谱与多周目进度检索服务",
            "who_uses_it": "平时喜欢看番、玩多结局 RPG/视觉小说、读设定集，希望管理自己看过/玩过的作品并理清人物关系和支线进度的普通爱好者（首先是拿你自己当第一用户）。",
            "user_problem": "普通备忘录或表格只能记个名字和打分，遇到『某部多结局游戏打到第2周目卡关，忘了前置支线条件有没有触发』或者『想查某个声优/编剧参与过自己收藏夹里哪些高分科幻作品』时，普通笔记根本查不出来，标签多了还会越翻越慢。",
            "what_user_can_do": "打开网页接口面板或命令行工具后，用户可以：① 录入或批量导入自己的番剧/游戏条目与标签；② 记录某部游戏的支线解锁先后条件（自动检查有没有死循环矛盾）；③ 输入作品名或角色名的前几个字，毫秒级弹出自动补全建议；④ 按“媒介类型 + 多个标签交集 + 评分范围”组合筛选并秒出结果。",
            "visible_outcome": "一个在浏览器打开 `http://127.0.0.1:8000/docs` 就能直接点击测试的交互式 Web 接口页面 + 命令行查询工具 + 1份实测 SQL 索引优化前后耗时对比表。",
            "why_worth_doing": "它不是网上烂大街的“图书增删改查作业”，而是把校招后端/数据/测开岗必考的『关系型数据库建表与复合索引调优、RESTful 接口契约、手写前缀树 (Trie) 与有向无环图 (DAG) 算法、pytest 自动化回归测试』全部装进你自己每天都能用的收藏库里。",
            "everyday_example": "【给非计算机专业朋友的通俗比喻】：就像给自己建了一座带“智能向导”的私人动漫游戏博物馆。以前你的手办和攻略卡片全堆在纸箱里，找“某画师参与过的所有治愈系作品”得翻半小时；现在你给每张卡片贴好标准索引签（数据库复合索引），还在门口放了本按拼音首字母秒查的目录册（Trie前缀树）和一张防走错路的剧情闯关地图（DAG拓扑检查），一秒钟就能精确调出想要的档案。",
        },
        "layer1_overview": {
            "what_it_does": "帮你管理看过的番剧、玩过的多结局游戏和书籍，支持毫秒级名字联想、多标签交集筛选，以及游戏支线解锁先后顺序检查。",
            "final_outcome": "1个可浏览器直接交互的本地 Web 服务 (`/docs`) + 1套含 15+ 条自动化测试的代码库 + 1份真实的数据库索引加速对比报告。",
            "v1_first_step": "第一版（MVP）不用装庞大的 MySQL 和 Redis！先用 Python 自带的 SQLite 单文件数据库 + FastAPI 跑通 4 个核心操作（录入作品、前缀联想、多标签筛选、支线依赖检查），确保 10 分钟内就能在本地跑起来。",
            "realistic_time": "总计约 34–46 小时（若每周课余投入 8–10 小时，约 4–5 周完成；其中阶段 1 最小可用版仅需 10–12 小时，约 1.5 周即可看到成果）。",
            "what_you_learn": "SQL 规范化建表与 B+ 树复合索引调优 (`EXPLAIN`)、HTTP RESTful 接口设计、前缀树 (Trie) 与有向无环图 (DAG) 环路检测、pytest 自动化测试。",
            "biggest_challenge": "当同时按多个标签交集 + 评分排序查询上万条记录时，如何设计复合索引消除全表扫描；以及如何从 C++ 思维平滑适应 Python Web 工程分层。",
        },
        "core_purpose": (
            "解决普通表格无法表达“多周目分支前置条件”和“跨作品人物/标签多对多关联检索”的痛点，构建一个从 SQLite 单机闭环平滑演进到 MySQL 复合索引调优的跨媒介作品检索服务端。"
        ),
        "why_matches_interest": (
            "直接源自 profile.yaml 中『持续接触动漫与剧情作品、动作/RPG/开放世界等多类型游戏』以及『喜欢拆解事物内部结构』的个人画像。"
            "数据模型原生围绕“多周目分支剧情树 (DAG)”与“跨媒介角色/制作人员关联”设计，拒绝给普通管理系统套二次元壁纸的假换皮。"
        ),
        "tech_selection_rationale": {
            "primary_stack": "Python 3.11 + FastAPI + SQLite 3（基础版默认） / MySQL 8.0（阶段2进阶） + pytest",
            "why_chosen": "FastAPI 原生支持利用 Python 类型注解自动生成 OpenAPI 交互界面 (`/docs`)，省去写前端 HTML/CSS 的负担，让你把 100% 精力花在数据结构、SQL 索引和接口测试上；同时你后续维护 Career Agent 也能直接复用这套 Python 技术栈。",
            "restraint_rules_applied": [
                "不混用多语言：本项目锁定 Python 为唯一主力语言（不强制同时写 Go 或 C++）；",
                "不盲目堆叠中间件：阶段 1 (MVP) 仅用 Python 内置的 `sqlite3`，零外部服务依赖；只有到阶段 2 验证万级数据慢查询时才对比 MySQL，只有实测出现高频重复热点聚合时才选配内存 LRU 或 Redis；",
                "不做复杂前端：直接复用 FastAPI 自带的 Swagger UI 与命令行脚本展示结果。",
            ],
            "progression_tiers": [
                {
                    "tier": "基础版（阶段 1 MVP 必做）",
                    "stack": "Python + FastAPI + 内置 SQLite + 手写内存 Trie/DAG",
                    "why_needed": "无需安装任何数据库服务器或 Docker，克隆代码即可完成『输入数据 → 校验环路 → 落盘保存 → 再次读取 → 单测验证』的完整小闭环。",
                },
                {
                    "tier": "进阶版（阶段 2 核心工程化）",
                    "stack": "升级或对比 MySQL 8.0 + 复合索引 + EXPLAIN 执行计划分析 + 进程内手写 LRU 缓存",
                    "why_needed": "当导入 5 万条合成作品与标签关联数据后，多标签交集查询会出现明显变慢（全表扫描），此时引入复合索引与 EXPLAIN 调优才有真实问题支撑。",
                },
                {
                    "tier": "更进一步（阶段 3 可选扩展）",
                    "stack": "Redis 缓存降级演练（仅在需要跨进程共享缓存或模拟缓存宕机回源时引入）",
                    "why_needed": "训练后端面试常问的 Cache-Aside 模式与缓存失效降级保護；若课业紧张，手写线程安全 LRU 字典同样能达成核心训练目的。",
                },
            ],
            "prerequisites": "你在 profile.yaml 中声明 Python『本人还没开始学习』，但已有 C++ 与数据结构基础。不可假设你已熟悉 Python Web 开发，因此必须在阶段 0 先花 6–8 小时补齐 Python 字典/类/类型注解与 pytest 基础。",
        },
        "skill_classification_breakdown": {
            "job_required_skills": ["关系型数据库与 SQL 优化 (MySQL/SQLite)", "Python 工程化开发", "数据结构与常用算法", "计算机网络与 HTTP 接口", "单元测试与自动化测试 (pytest)", "缓存中间件 (Redis)"],
            "project_essential_skills": ["Python + FastAPI 接口开发", "SQL 规范化建表与复合索引 (`db_sql_rdbms`)", "手写 Trie 前缀树与 DAG 环检测 (`algo_ds_core`)", "pytest 接口与边界测试 (`test_automation_qa`)"],
            "later_extension_skills": ["Redis 独立缓存容器与故障降级 (`db_cache_mq`)", "Docker Compose 容器编排 (`tool_docker_k8s`)"],
            "standalone_exercise_skills": ["C++ 底层指针与内存管理（由候选项目二/三覆盖）", "Linux epoll 系统调用（可通过 150 行独立小练习或候选项目三补充）"],
        },
        "natural_tech_stack_usage": (
            "• SQLite（阶段1）→ MySQL（阶段2）：存储作品主表、标签多对多关联表及多周目支线节点表，解决多标签交集与排序下的全表扫描慢查询问题；\n"
            "• 手写 Trie 前缀树与 DAG 拓扑排序（核心算法）：Trie 解决作品/角色名输入时的毫秒级前缀补全问题；DAG 拓扑排序解决录入游戏多周目支线前置条件时的死循环矛盾检测问题；\n"
            "• 手写 LRU / Redis（阶段2可选）：解决高频访问『个人年度榜单』与『热门标签统计』时的重复聚合计算开销；\n"
            "• pytest + TestClient：在不手动点网页的情况下，用一条命令自动化验证正常写入、环路拦截与异常输入处理。"
        ),
        "project_goal": (
            "构建一个从单文件 SQLite 最小闭环起步、逐步完成 5 万条数据复合索引调优、手写 Trie/DAG 核心算法与 pytest 自动化验证的个人跨媒介作品检索服务。"
        ),
        "core_features": [
            "1. 作品与多标签归档闭环：支持番剧/游戏/书籍条目与多标签关联的事务写入、按 ID 精确读取与状态修改；",
            "2. 多周目支线依赖图 (DAG) 校验：录入游戏结局或支线前置条件时，使用拓扑排序/DFS 实时检测并拦截循环依赖（如 A 依赖 B 且 B 依赖 A）；",
            "3. 内存 Trie 树前缀自动补全：启动时加载作品标题与角色别名构建前缀树，支持输入 1–3 个字符返回前 10 条匹配候选；",
            "4. 多条件组合查询与复合索引调优：支持按媒介类型、标签交集、评分段筛选，提供 `/api/debug/query-plan` 输出 SQLite/MySQL 执行计划对比。",
        ],
        "mvp_scope": {
            "problem_solved": "解决个人番剧/游戏收藏无法按多标签交集高效检索、且多周目支线解锁条件容易记混成死循环的问题。",
            "core_operations": [
                "操作 1（新增作品与标签）：调用 `POST /api/works` 传入作品标题、媒介类型(anime/game/book)、评分与标签列表，保存至 SQLite 并返回唯一 `work_id`；",
                "操作 2（配置支线前置条件并检环）：调用 `POST /api/works/{id}/branches` 传入支线节点与前置节点 ID，若构成环路则立即返回 HTTP 400 并指出冲突边；",
                "操作 3（前缀联想补全）：调用 `GET /api/suggest?prefix=命运` 从内存 Trie 树返回匹配的作品与角色列表；",
                "操作 4（多条件组合检索）：调用 `GET /api/works?media_type=game&tag=科幻&min_score=8.5` 返回按评分降序排列的作品清单与查询耗时。",
            ],
            "inputs": "JSON 格式的作品条目（标题、类型、评分 0~10、标签数组）、支线依赖边 `(from_node, to_node)`、或查询 URL 参数。",
            "outputs": "结构化 JSON 响应（包含状态码、数据列表、匹配总数、`elapsed_ms` 实测耗时及可选的 SQL `EXPLAIN` 计划文本）。",
            "display_method": "无需编写前端网页！直接通过浏览器访问 FastAPI 自动生成的 `http://127.0.0.1:8000/docs` 交互面板点击执行，或在终端用 `curl` / `pytest` 查看格式化 JSON 输出。",
            "out_of_scope_initially": [
                "第一版明确不做：用户注册登录、密码加密与多租户权限系统（个人本地使用完全不需要）；",
                "第一版明确不做：React/Vue 复杂前端页面、动漫海报图片爬虫或视频播放流；",
                "第一版明确不做：分布式分库分表、消息队列 (Kafka/RabbitMQ) 与多机集群部署。",
            ],
            "closed_loop_proof": "执行 `pytest tests/test_mvp_closed_loop.py`：自动依次完成『写入 3 部作品与支线图 → 尝试插入一条构成环路的非法支线（验证返回 400 拦截） → 按标签+评分查询并读取刚保存的数据 → 校验重启服务后 SQLite 文件中数据 100% 一致』。",
            "must_have": [
                "完成 SQLite 核心三张表（作品表、标签关联表、多周目支线边表）建表与事务读写；",
                "手写 Trie 前缀补全与 DAG 环路检测算法并绑定到上述 4 个核心 REST 接口；",
                "编写 12+ 条 pytest 自动化测试证明数据写入→读取→重启持久化闭环正确。",
            ],
            "must_have_mvp": "使用 Python + FastAPI + SQLite 完成 4 个核心操作（作品录入、DAG 支线检环、Trie 前缀补全、多标签筛选）及 12+ 条 pytest 闭环测试（无需预装 MySQL/Redis 即可独立运行）。",
            "shrunk_mvp_fallback": "若课业繁忙每周不足 6 小时：砍掉阶段 2 的 MySQL/Redis 外部容器迁移，全程基于 SQLite 完成 5 万条合成数据的 `EXPLAIN QUERY PLAN` 复合索引前后耗时对比 + 手写 80 行 Python 字典+双向链表 LRU 缓存即可保底交付。",
            "estimated_mvp_weeks": "1.5–2 周（约 10–14 小时）",
        },
        "engineering_tasks": [
            "需求拆解与数据库建模：设计 `works`、`work_tags`、`story_branches` 三张表，对比建复合索引前后的查询扫描方式；",
            "核心数据结构编码：在 `core/trie.py` 和 `core/dag.py` 中独立手写前缀树与拓扑排序环检测，不调现成图论黑盒库；",
            "接口契约与错误处理：使用 Pydantic 定义输入边界校验（如评分超界、空标题、环路依赖均返回明确错误码与原因）；",
            "基准测量与回归测试：编写合成数据生成脚本，先测无索引基线耗时，再测添加复合索引与 LRU 缓存后的耗时变化。",
        ],
        "engineering_challenges": [
            "挑战 1（多标签交集查询的全表扫描问题）：当查询『同时包含标签 A 和标签 B 且评分 ≥ 8.5』的作品时，朴素写法会触发全表扫描与临时表排序，需通过 `GROUP BY + HAVING COUNT` 配合 `(tag_name, work_id)` 复合索引优化；",
            "挑战 2（DAG 动态加边时的环路检测）：每次新增支线前置条件时，如何用 DFS 三色标记法或 Kahn 拓扑排序在 O(V+E) 时间内定位出具体的死循环路径并告知用户；",
            "挑战 3（缓存脏数据问题）：当修改某部作品的评分后，如何确保缓存中的高分榜单同步失效或更新。",
        ],
        "knowledge_mapping": [
            "数据库系统原理：关系型建表范式、B+ 树索引与最左前缀匹配原则、`EXPLAIN` / `EXPLAIN QUERY PLAN` 输出解读、ACID 事务原子性；",
            "数据结构与算法：前缀树 (Trie) 节点设计与前缀遍历、有向图邻接表表示与拓扑排序/DFS 环检测、LRU 缓存（哈希表+双向链表）；",
            "计算机网络与软件工程：HTTP 方法与状态码语义 (200/201/400/404/422)、RESTful 参数校验、pytest 夹具 (`fixture`) 与自动化断言。",
        ],
        "acceptance_criteria": [
            "✅ 阶段 1 最小闭环验收：在终端执行 `uvicorn app.main:app` 启动服务，运行 `pytest tests/test_mvp.py -v`；输入 3 条正常作品 JSON 与 1 条含循环依赖的支线（如 `A->B, B->A`），验证正常条目写入并可二次读取一致，环路输入返回 `HTTP 400` 且含 `cycle_detected` 字段，全部 12+ 条用例 100% 通过；",
            "✅ 阶段 2 索引与缓存基线对比验收：运行 `python scripts/seed_benchmark.py --count 50000` 生成 5 万条测试作品数据，执行 `python scripts/run_benchmark.py`；验证输出 `EXPLAIN` 计划从全表扫描 (`SCAN TABLE` / `type: ALL`) 变为索引查找 (`SEARCH TABLE USING INDEX` / `type: ref`)，并在本地同一环境下记录优化前基线耗时与优化后实测耗时对比表；",
            "✅ 阶段 3 异常注入与交付验收：测试传入空字符串、超长标签、评分 `-5` 或 `99`、以及缓存层不可用（关闭缓存开关或停止 Redis）4 类异常场景，验证接口均返回预期状态码且零未捕获崩溃，README 含完整一键复现步骤。",
        ],
        "difficulty_workload_risks": {
            "difficulty": "入门 → 进阶",
            "workload_range": "总计约 34–46 小时（其中：写出能运行代码约 16 小时，理解原理+编写测试+调试排障+整理文档约 22 小时；按大二每周课余投入 8–10 小时计，约 4–5 周从容完成）",
            "estimation_premise": "估算依据：假设你已学过 C++ 基础语法与栈/队列/树概念，但 Python Web 框架与 SQL 索引调优为首次实战；误差来源：若本地配置 MySQL/Docker 网络环境遇到镜像拉取慢，可能额外耗时 2–4 小时（此时可直接用 SQLite 完成核心实验，零环境阻塞）。",
            "main_risks": [
                "风险 1：把时间花在收集几百张动漫封面图或折腾前端 CSS 美化上 → 应对：明确规定第一版只存文本元数据，直接用 FastAPI `/docs` 界面交互；",
                "风险 2：一上来同时装 MySQL、Redis、Docker 导致环境报错劝退 → 应对：严格按『阶段 1 先用内置 SQLite 跑通闭环 → 阶段 2 再测索引与缓存』推进。",
            ],
        },
        "deliverables_and_demos": [
            "1. 结构清晰的 Python FastAPI 源码仓库（含 `core/trie.py`、`core/dag.py`、`db/schema.sql`、`tests/` 及一键合成数据脚本）；",
            "2. 《5 万条作品数据下 SQL 复合索引与 LRU 缓存实测基线对比报告》（附真实 `EXPLAIN` 输出与 P50/P95 延迟实测表）；",
            "3. 2 分钟操作演示（展示 `/docs` 面板录入作品、拦截支线死循环、前缀秒级联想与测试全绿输出）。",
        ],
        "prerequisites": "掌握基础编程逻辑与树/图基本概念（已有 C++ 基础即可，阶段 0 提供 6–8 小时 Python + pytest 快速过渡指导）。",
        "estimated_effort": "约 34–46 小时（写代码约 16h + 理解原理/测试/调试/文档约 22h；每周投入 8–10 小时约 4–5 周完成，阶段 1 最小闭环仅需 10–12 小时）。",
        "resume_value": (
            "帮你补齐校招研发岗最看重的『SQL 表结构与复合索引调优 + 核心数据结构工程落地 (Trie/DAG/LRU) + RESTful 接口与 pytest 自动化测试』三大基本功，彻底告别没有性能数据的课程作业式 CRUD。"
        ),
        "staged_extensions": [
            "阶段 0（准备与自测 · 6–8h）：用 C++ 对照法快速掌握 Python 容器、类型注解与 pytest 断言，完成 50 行自测练习；",
            "阶段 1（最小闭环 MVP · 10–12h）：基于 FastAPI + 内置 SQLite 实现作品增查、DAG 支线检环与 Trie 前缀补全，跑通 12 条闭环单测；",
            "阶段 2（核心工程化 · 12–16h）：注入 5 万条合成数据建立慢查询基线，完成复合索引 `EXPLAIN` 调优与 LRU/Redis 缓存一致性控制；",
            "阶段 3（验证与交付 · 6–10h）：补齐边界异常测试、整理架构图与实测对比 README，配置可选 GitHub Actions 自动跑 pytest。",
        ],
        "data_vs_inference_boundary": {
            "from_real_data": "Python、关系型数据库与 SQL 优化、数据结构与算法、自动化测试、计算机网络与缓存在当前数据库真实 JD 统计中均位列高频核心要求（见技能约束清单 SC-01~SC-10）。",
            "reasonable_inference": "以“跨媒介番剧/游戏设定图谱与多周目进度”作为业务场景，是基于你 profile.yaml 中动漫与游戏兴趣设计的工程训练载体，旨在提升动手动力，不代表绑定内容或文娱行业。",
        },
        "reference_and_license": {
            "references": [
                "FastAPI 官方中文教程 (MIT License, https://fastapi.tiangolo.com/zh/)",
                "SQLite Query Planner & Indexing 官方文档 (Public Domain, https://www.sqlite.org/queryplanner.html)",
                "MySQL 8.0 Optimization & EXPLAIN 官方手册 (https://dev.mysql.com/doc/refman/8.0/en/execution-plan-information.html)",
            ],
            "reuse_vs_self_impl": (
                "允许正常使用 FastAPI、Pydantic、sqlite3/SQLAlchemy 与 pytest 框架；"
                "必须由本人亲手编写：数据库表结构与复合索引语句、`Trie` 前缀树与 `DAG` 拓扑检环代码、缓存失效逻辑及基准测试对比脚本。"
            ),
        },
        "ai_collab_boundary": {
            "must_do_personally": [
                "理解数据流并亲自设计 `works`、`work_tags`、`story_branches` 三张表的字段与复合索引顺序；",
                "独立手写 `core/trie.py`（前缀树插入与前缀搜寻）和 `core/dag.py`（拓扑排序环路检测）核心算法，并能向面试官解释时间复杂度；",
                "独立调试至少 1 次“无索引慢查询 vs 复合索引命中”的执行计划差异，亲自运行 `EXPLAIN` 并记录真实耗时；",
                "亲自设计并编写针对『循环依赖支线』『缓存更新失效』『非法评分边界』的 pytest 测试用例。",
            ],
            "ai_can_accelerate": [
                "生成 FastAPI 项目目录脚手架与 Pydantic 请求/响应模型样板代码；",
                "生成用于性能测试的 5 万条仿真番剧/游戏/书籍 JSON 种子数据生成脚本；",
                "辅助解释 SQLite/MySQL `EXPLAIN` 输出列（如 `type`、`key`、`rows`、`Extra`）的含义及辅助排版 README 表格。",
            ],
            "understanding_verification": [
                "在不看 AI 提示的情况下，画出从『用户调用 `POST /api/works/{id}/branches`』到『DAG 检环通过并落盘 SQLite』的完整函数调用与数据流图；",
                "现场把复合索引 `(tag_name, work_id)` 的顺序颠倒或删除，运行 `EXPLAIN` 并解释为什么扫描行数发生了变化。",
            ],
        },
        "coverage_matrix": {
            "db_sql_rdbms": ("完整覆盖", "设计作品/标签/支线三张规范表与复合索引（`db/schema.sql`），完成 5 万条数据 EXPLAIN 基线对比验证", "已完整覆盖核心要求"),
            "algo_ds_core": ("完整覆盖", "在 `core/trie.py` 与 `core/dag.py` 中亲手实现前缀树联想、DAG 拓扑排序环检测与 LRU 淘汰结构", "已完整覆盖核心要求"),
            "net_tcp_http": ("完整覆盖", "在 `app/api.py` 中实现 RESTful 核心接口、状态码规范、分页参数校验与异常响应", "若需强化底层 TCP 字节流拆包，可做 1 个 100 行 Socket 练习或看候选项目三"),
            "test_automation_qa": ("完整覆盖", "在 `tests/` 下编写 15+ 条 pytest 正常/边界/环路拦截自动化测试与基准测试脚本", "已完整覆盖核心要求"),
            "lang_python": ("完整覆盖", "使用 Python 3.11 类型注解、模块化分层与 FastAPI 完成完整服务端工程（含阶段 0 预热阶梯）", "已针对 profile 中 Python 未学状态提供阶段 0 准备路径"),
            "fw_web_py_go": ("完整覆盖", "使用 FastAPI 实现 OpenAPI 契约定义、依赖注入与统一错误处理", "已完整覆盖核心要求"),
            "db_cache_mq": ("部分覆盖", "阶段 2 实现进程内手写 LRU 缓存与 Cache-Aside 失效逻辑（可选扩展 Redis）", "【独立小练习建议】花 2 小时用 `redis-py` 完成 1 个带 TTL 抖动与 `SET NX` 互斥锁的 80 行独立练习"),
            "tool_docker_k8s": ("部分覆盖", "提供轻量 GitHub Actions 自动跑 pytest 配置，阶段 1–2 不强制依赖复杂 Docker 集群", "【独立小练习建议】项目完成后可花 1.5 小时编写 1 个 `Dockerfile` 将服务打包为容器镜像"),
            "lang_cpp": ("未覆盖", "本项目锁定 Python 单一语言以控制大二学习负担，不混编 C++", "【独立补充建议】你正在主修 C++，若想主攻 C++ 客户端或系统岗，请优先选择候选项目二或项目三"),
            "os_linux_sys": ("未覆盖", "本项目聚焦 Web 接口与数据库索引，不直接调用 Linux 底层系统 API", "【独立小练习建议】可在 WSL2 Linux 下用 `lsof`/`ps`/`strace` 观察本服务进程的文件描述符变化"),
        },
    },

    # ══════════════════════════════════════════════════════════════════════
    # 候选项目二：C++17 + Qt6 + OpenGL 动态壁纸粒子合成与帧耗时诊断器（主力：C++17）
    # ══════════════════════════════════════════════════════════════════════
    {
        "project_id": "proj_live_shader_particle_desk",
        "title": "候选项目二：《基于 C++17 + Qt6 + OpenGL 的动态壁纸粒子合成与帧耗时诊断工具》",
        "short_title": "C++17+Qt6 动态壁纸粒子合成与渲染诊断工具",
        "interest_theme_tag": "🎨 动漫视觉/数字绘画探索 + 动态粒子壁纸 + 现有 OpenGL 项目深化",
        "tier_priority": "P0_prioritize_now",
        "priority_badge": "🌟 P0 首选 · 直接升级你现有的 C++ 与 OpenGL 基础",
        "difficulty_level": "进阶 (Intermediate)",
        "primary_languages": ["C++ (C++17)"],
        "skill_ids": ["lang_cpp", "fw_qt_gui", "dom_graphics_3d", "algo_ds_core", "conc_multithread", "test_debug_profile", "build_git_cmake"],
        "target_categories": ["software_cpp", "graphics_3d", "game_engine", "sys_infra"],
        "target_roles": ["C++客户端研发工程师", "跨平台桌面软件工程师", "工业可视化/图形工具工程师", "游戏客户端/引擎工具工程师"],
        "plain_intro": {
            "what_is_it_called": "基于 C++17 + Qt6 + OpenGL 的动态壁纸粒子合成与帧耗时诊断工具",
            "who_uses_it": "喜欢二次元动态桌面壁纸、想给自己画的或喜欢的动漫立绘加上樱花飘落/雨滴/星轨特效，同时想直观看到显卡和 CPU 每一帧花了多少毫秒的桌面软件用户。",
            "user_problem": "你之前写过一个小型 OpenGL 程序，但改一个参数就要重新编译代码，而且当屏幕上粒子数量增加到几万个时就会掉帧卡顿，也不知道到底是 CPU 计算慢还是显卡绘制慢。",
            "what_user_can_do": "打开这款桌面软件后，用户可以：① 导入一张背景立绘并开启粒子发射器（调节数量、重力、风向、颜色）；② 在界面上切换『普通离散内存模式』与『连续内存对象池模式』，亲眼看到帧率与每帧毫秒数的实时变化；③ 修改本地 `.frag` 着色器文件并保存，软件无需重启立即刷新画面（若写错语法则在底部面板高亮报错行号而不闪退）；④ 用鼠标在画面上划过，通过四叉树空间索引瞬间吹散或点选鼠标周围的粒子。",
            "visible_outcome": "一个带右侧参数滑块面板、中央 OpenGL 实时渲染视口、底部帧耗时与内存诊断栏的跨平台 C++ 桌面软件窗口 + GoogleTest 单元测试套件。",
            "why_worth_doing": "把你简历上原本像“课堂小作业”的 OpenGL 练习，直接升级成金山办公(WPS)、万兴科技、联影医疗、大疆等桌面与工业软件企业最看重的『C++17 RAII 内存管理 + Qt6 界面与内核解耦 + 对象池/四叉树性能优化 + ASan 内存零泄漏验证』硬核工程代表作。",
            "everyday_example": "【给非计算机专业朋友的通俗比喻】：就像一个“带实时体检仪的桌面特效摄影棚”。以前你在画布上撒一万片花瓣（粒子），每撒一片都要临时去仓库领一个盒子、掉地上再扔掉（频繁申请释放内存），人一多就堵得走不动；现在你在棚里准备了一排固定座位（连续内存对象池）和网格分区地图（四叉树），不仅五万片花瓣同时飘落依然丝滑顺畅，旁边仪表盘还能精确显示每一毫秒花在了哪里。",
        },
        "layer1_overview": {
            "what_it_does": "让你在桌面窗口里实时调节数万个动态壁纸粒子（樱花/雨雪/星光）和着色器特效，并实时测量每帧 CPU/GPU 耗时与内存状态。",
            "final_outcome": "1个可交互的 C++17 + Qt6 桌面软件程序 + `RenderCore` 独立静态库与 GTest 单元测试 + 1份对象池与四叉树加速前后的帧耗时实测表。",
            "v1_first_step": "第一版（MVP）不做复杂的节点连线编辑器，也不引入多线程！先用单线程 C++17 + Qt6 基础滑块 + 你熟悉的 OpenGL 跑通『加载立绘 → 发射粒子 → 滑块调参 → 保存/读取 JSON 配置 → 显示每帧耗时』的最小闭环。",
            "realistic_time": "总计约 36–48 小时（你有 C++ 和 OpenGL 基础，省去了图形学入门时间；写代码约 18h + 学习 Qt6 信号槽/写 GTest/用 ASan 调内存/写文档约 22h，按每周 8–10h 约 4–5 周完成）。",
            "what_you_learn": "C++17 RAII 与智能指针、连续内存对象池 (Object Pool)、四叉树 (QuadTree) 空间索引、Qt6 信号槽与 MVC 解耦、CMake 多目标构建、ASan 内存泄漏排查。",
            "biggest_challenge": "如何把纯 C++ 渲染与算法内核 (`RenderCore`) 从 Qt 界面代码中彻底剥离出来以便独立跑单元测试，以及如何保证着色器热重载失败或异常输入时不泄漏内存、不崩溃。",
        },
        "core_purpose": (
            "在你已有的小型 OpenGL 程序基础上，构建一个兼具视觉创作乐趣与性能诊断价值的 C++17 + Qt6 桌面工具，重点训练现代 C++ 内存布局优化、空间索引数据结构与桌面客户端工程化架构。"
        ),
        "why_matches_interest": (
            "直接呼应 profile.yaml 中『曾探索数字绘画、素描和动漫临摹』『接触动漫与剧情作品』及『已完成小型 OpenGL 图形程序』的背景。"
            "同时严格遵守反偏置原则：不将你锁死在高压游戏渲染岗，而是用视觉工具载体训练高 WLB 办公/工业/医疗桌面软件最通用的 C++17 + Qt6 + 性能诊断能力。"
        ),
        "tech_selection_rationale": {
            "primary_stack": "C++17 + Qt6 (`QOpenGLWidget` + `QWidget` 基础控件) + OpenGL 3.3 Core + CMake + GoogleTest",
            "why_chosen": "100% 复用你正在学习的 C++ 与已有的 OpenGL 3.3 着色器经验，零语言切换成本；Qt6 是国内 C++ 桌面客户端与工业上位机招聘占比最高的框架（见技能统计 `fw_qt_gui`）。",
            "restraint_rules_applied": [
                "单一语言纯粹性：全项目仅使用 C++17（配合极少量 GLSL 着色器脚本），绝不混入 Python/Java/Go；",
                "不强塞无关后端组件：桌面渲染工具完全不需要 MySQL、Redis 或消息队列，配置持久化直接使用轻量 JSON 文件；",
                "界面克制：阶段 1 仅使用最简单的 `QSlider`、`QCheckBox` 和 `QPlainTextEdit`，不做华而不实的自定义复杂节点图 UI。",
            ],
            "progression_tiers": [
                {
                    "tier": "基础版（阶段 1 MVP 必做）",
                    "stack": "C++17 + Qt6 基础窗口 + OpenGL 单线程粒子渲染 + JSON 配置读写 + GTest",
                    "why_needed": "先在单线程下把『UI 滑块调参 → 粒子更新绘制 → JSON 保存与重读 → GTest 数学/容器测试』完整闭环跑通。",
                },
                {
                    "tier": "进阶版（阶段 2 核心工程化）",
                    "stack": "连续内存对象池 (`ParticlePool`) + 四叉树空间索引 (`QuadTree`) + GLSL 文件监听热重载",
                    "why_needed": "当粒子数从 1,000 增加到 50,000 且开启鼠标范围交互时，朴素 `new/delete` 和 O(N) 遍历会出现明显帧耗时升高，此时引入对象池与四叉树能立刻测出真实加速收益。",
                },
                {
                    "tier": "更进一步（阶段 3 可选扩展）",
                    "stack": "后台工作线程 (`std::thread` + 线程安全队列) 异步解码高分辨率大图壁纸",
                    "why_needed": "仅当切换 4K 大图壁纸导致主界面出现肉眼可见的百毫秒卡顿时，才引入后台解码线程，训练多线程同步与 OpenGL 上下文线程边界。",
                },
            ],
            "prerequisites": "你已具备 C++ 基础与小型 OpenGL 经验，但尚未系统使用过 Qt6 与 GoogleTest。阶段 0 需花 6–8 小时掌握 CMake 多目标构建、GTest 断言以及 Qt6 `connect` 信号槽机制。",
        },
        "skill_classification_breakdown": {
            "job_required_skills": ["C/C++ (现代 C++17/STL/RAII)", "Qt / 跨平台客户端框架", "数据结构与常用算法", "调试排障与内存分析 (GDB/ASan)", "版本控制与 CMake 构建", "计算机图形学与 OpenGL", "多线程并发编程"],
            "project_essential_skills": ["C++17 RAII 与连续内存对象池 (`lang_cpp`)", "四叉树空间索引 (`algo_ds_core`)", "Qt6 信号槽与 OpenGL 视口集成 (`fw_qt_gui`, `dom_graphics_3d`)", "CMake + GTest + ASan 内存消毒 (`build_git_cmake`, `test_debug_profile`)"],
            "later_extension_skills": ["后台多线程异步纹理加载与双缓冲队列 (`conc_multithread`)"],
            "standalone_exercise_skills": ["SQL 数据库与 Redis 缓存（由候选项目一覆盖，或通过独立 SQLite 练习补充）", "Linux TCP/HTTP 网络协议（由候选项目三覆盖）"],
        },
        "natural_tech_stack_usage": (
            "• C++17 连续内存对象池 (`ParticlePool`)：解决每帧成千上万粒子生成与消亡时频繁 `new/delete` 造成的内存碎片与 CPU 缓存未命中问题；\n"
            "• 四叉树 (`QuadTree`)：解决鼠标在画面上拖拽排斥/拾取粒子时，朴素 O(N) 全量遍历耗时随粒子数线性暴涨的问题；\n"
            "• Qt6 `QFileSystemWatcher` + 状态回退：解决修改 GLSL 着色器代码后需反复重启程序的痛点，并在语法错误时自动保留上一版正常 Shader；\n"
            "• CMake 静态库拆分 (`libRenderCore` + `StudioApp` + `CoreTests`)：让核心粒子池与四叉树完全不依赖窗口界面即可在终端用 `ctest` 毫秒级跑完自动化单元测试。"
        ),
        "project_goal": (
            "完成一个将纯 C++17 算法/渲染内核与 Qt6 界面解耦的动态壁纸粒子工具，通过实测对比验证连续内存对象池与四叉树空间索引的性能收益，并通过 ASan 零内存泄漏检查。"
        ),
        "core_features": [
            "1. 内核与界面解耦架构：核心粒子模拟、四叉树索引与 JSON 序列化独立为纯 C++17 `RenderCore` 模块，可脱离 Qt 窗口直接运行 GTest 单元测试；",
            "2. 连续内存对象池 vs 朴素分配对比模式：界面提供一键切换开关，实时显示两种内存布局下每帧 CPU 更新耗时 (`update_ms`)；",
            "3. 四叉树空间范围交互与可视化调试框：支持鼠标引力/斥力场交互，勾选『显示四叉树网格』即可在屏幕上直观看到空间递归划分框；",
            "4. GLSL 着色器热重载与容错回退：修改 `.frag` 文件保存后自动重编译，若存在语法错误则在底部诊断栏显示驱动报错行号并保持原画面不崩。",
        ],
        "mvp_scope": {
            "problem_solved": "解决原有 OpenGL 小程序参数调节需重新编译、且缺乏工程化模块化测试与帧耗时诊断的问题。",
            "core_operations": [
                "操作 1（调节粒子发射器）：拖动右侧滑块修改粒子数量（1,000 ~ 50,000）、下落速度与风力，画面实时响应；",
                "操作 2（保存与重读预设闭环）：点击『导出预设』将当前发射器参数与着色器路径保存为 `preset.json`，重启程序点击『加载预设』100% 还原画面状态；",
                "操作 3（空间交互与网格检视）：按住鼠标左键在画面拖动推开周围粒子，勾选『显示四叉树分割线』查看空间加速结构；",
                "操作 4（导出帧耗时基线报告）：点击『采集 300 帧性能快照』，自动生成包含平均帧耗时、P95 帧耗时与对象池命中情况的 `perf_report.json`。",
            ],
            "inputs": "用户滑块交互事件、JSON 预设配置文件、本地 `.vert/.frag` 着色器源码文件、以及鼠标坐标 `(x, y)`。",
            "outputs": "60FPS 实时渲染画面、底部诊断栏实时文本（帧耗时 `ms` / 活跃粒子数 / 四叉树节点数 / Shader 编译日志）及导出的 `perf_report.json`。",
            "display_method": "Qt6 跨平台桌面主窗口（左侧 75% 为 OpenGL 渲染画布，右侧 25% 为参数控制栏，底部为诊断输出栏）+ 终端 `ctest` 测试报告。",
            "out_of_scope_initially": [
                "第一版明确不做：复杂的图形化节点连线编辑器（Node Graph UI 极其耗费界面坐标调试时间，用标准滑块面板代替）；",
                "第一版明确不做：3D 骨骼动画蒙皮、复杂物理碰撞引擎或音频 FFT 频谱分析；",
                "第一版明确不做：接管 Windows 系统底层桌面句柄（先作为独立标准窗口程序稳定运行）。",
            ],
            "closed_loop_proof": "运行 `ctest --output-on-failure`：自动验证『创建 10,000 个粒子 → 执行 60 步物理更新与四叉树范围查询 → 序列化保存为 JSON → 反序列化读取并校验所有参数误差 < 1e-6 → ASan 检查 0 字节内存泄漏』。",
            "must_have": [
                "完成 CMake 三目标构建（`RenderCore` 库 + `QtApp` 窗口 + `CoreTests` 单测）；",
                "亲手实现连续内存 `ParticlePool`、二维 `QuadTree` 空间查询与 JSON 配置读写闭环；",
                "实现底部帧耗时统计栏与 GLSL 热重载错误捕获。",
            ],
            "must_have_mvp": "完成 CMake + C++17 `RenderCore`（连续内存粒子池 + 四叉树 + JSON 读写）+ Qt6 基础滑块控制窗口 + 12+ 条 GTest 单元测试。",
            "shrunk_mvp_fallback": "若安装配置 Qt6 开发环境遇到困难或课业繁忙：可直接使用你已熟悉的 `GLFW + Dear ImGui`（轻量单头文件界面，CMake 几秒钟即可编译）替代 Qt6 外壳，100% 保留 C++17 对象池、四叉树、Shader 热重载与 GTest/ASan 核心训练！",
            "estimated_mvp_weeks": "2 周（约 14–16 小时）",
        },
        "engineering_tasks": [
            "模块化架构拆分：将不依赖图形上下文的数据结构 (`ParticlePool`, `QuadTree`, `PresetSerializer`) 放入 `src/core/`，编写独立 GTest；",
            "对照实验设计：编写基准测试函数，在相同粒子数（如 30,000）下分别测量『每次动态分配指针』与『预分配连续 `std::vector` 对象池』的 500 帧总 CPU 耗时；",
            "异常与资源安全：使用 C++ RAII 封装 OpenGL VAO/VBO/ShaderProgram，确保异常分支或程序退出时确定性释放资源；",
            "内存消毒验证：在 CMake Debug 模式下开启 `-fsanitize=address,undefined` 编译并运行全部单测与热重载压力测试。",
        ],
        "engineering_challenges": [
            "挑战 1（对象池中的活跃粒子紧凑遍历）：当粒子频繁生成和消亡时，如何使用 Swap-and-Pop（将死亡粒子与末尾活跃粒子交换）保持活跃粒子在数组中 100% 连续紧凑，从而消除遍历分支并跑满 CPU 缓存行；",
            "挑战 2（四叉树每帧重建 vs 动态更新的开销权衡）：粒子每帧都在移动，何时重建四叉树、最大深度设多少层才能让『建树开销 + 查询开销』小于朴素 O(N) 遍历；",
            "挑战 3（GLSL 热重载失败时的状态保护）：当用户保存了未写完的错误 Shader 代码时，如何先编译临时 Program、确认 `GL_LINK_STATUS == GL_TRUE` 后再原子替换旧 Program。",
        ],
        "knowledge_mapping": [
            "C++ 语言与内存模型：RAII 资源管理、移动语义、连续内存布局与 CPU Cache 局部性原理、Swap-and-Pop 对象池技巧；",
            "数据结构与算法：二维四叉树 (QuadTree) 递归空间划分与轴对齐包围盒 (AABB) 相交判定、环形缓冲区（滑动窗口计算帧率均值与 P95）；",
            "构建与调试工程：CMake 静态库与测试目标组织、GoogleTest 参数化测试、AddressSanitizer (ASan) 内存越界与泄漏排查。",
        ],
        "acceptance_criteria": [
            "✅ 阶段 1 最小闭环与单测验收：执行 `cmake -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build && ctest --test-dir build --output-on-failure`；验证对象池分配回收、Swap-and-Pop 边界、四叉树矩形范围查询准确性与损坏 JSON 容错等 12+ 条 GTest 用例 100% 通过；",
            "✅ 阶段 2 基线对比实测验收：运行基准测试 `./build/core_benchmark --particles 30000 --frames 500`；首先建立『朴素指针分配 + O(N) 暴力范围查询』耗时基线，再对比开启『连续内存池 + 四叉树查询』后的实测每帧毫秒数，生成 `perf_report.json` 记录真实优化比例；",
            "✅ 阶段 3 异常注入与 ASan 验收：在开启 `-fsanitize=address` 下，故意向热重载模块注入语法错误的 `.frag` 文件及不存在的纹理路径各 10 次，验证程序不崩溃、界面输出具体错误行号，且正常退出时 ASan 报告 `0 bytes leaked`。",
        ],
        "difficulty_workload_risks": {
            "difficulty": "进阶",
            "workload_range": "总计约 36–48 小时（其中：写出能运行代码约 18 小时，理解 Cache 局部性与 Qt 信号槽+写 GTest+ASan 调试+整理文档约 22 小时；按每周 8–10 小时计，约 4–5 周完成）",
            "estimation_premise": "估算依据：你已完成过小型 OpenGL 程序，因此无需从零补习顶点缓冲与着色器基础；主要不确定性在于 Windows 下首次配置 Qt6 + CMake 路径可能耗时 2–3 小时（若超时可随时按缩减方案退回 GLFW + ImGui）。",
            "main_risks": [
                "风险 1：在复杂的美术效果或自定义 Qt 样式表上耗费过多时间 → 应对：使用简单的几何光点/雪花纹理与默认控件样式，专注对象池与四叉树实测数据；",
                "风险 2：本项目属于纯桌面客户端工程，不训练 SQL 数据库与服务端网络 → 应对：已在覆盖审计表中如实标记为『未覆盖』，并提供 2 小时独立补充练习。",
            ],
        },
        "deliverables_and_demos": [
            "1. 跨平台 C++17 源码仓库（含 `src/core/` 无依赖内核、`src/ui/` 界面层、`tests/` GTest 用例与 `benchmarks/` 压测程序）；",
            "2. 《对象池连续内存布局与四叉树空间索引实测耗时对比报告》（附不同粒子规模下的实测毫秒数表格）；",
            "3. 30 秒软件运行录屏（展示滑块调参、四叉树网格可视化、故意改错 Shader 触发底部报错不闪退、修复后画面即时恢复）。",
        ],
        "prerequisites": "掌握 C++ 基础语法与类设计，了解基础 OpenGL VAO/VBO/Shader 概念（与你当前画像完全吻合）。",
        "estimated_effort": "约 36–48 小时（写代码约 18h + 原理理解/GTest单测/ASan调试/文档约 22h；每周 8–10 小时约 4–5 周完成）。",
        "resume_value": (
            "直接把你现有的 OpenGL 兴趣练习拔高为具备『模块化静态库解耦 + 连续内存池 Swap-and-Pop 优化 + 四叉树空间索引 + GTest/ASan 自动化验证』的成熟 C++ 客户端工程作品，投递金山办公、万兴科技、联影医疗、工业软件与游戏客户端岗时说服力极强。"
        ),
        "staged_extensions": [
            "阶段 0（准备与自测 · 6–8h）：配置 CMake + GoogleTest + Qt6（或 ImGui），通过 1 个 40 行对象池小测试验证环境；",
            "阶段 1（最小闭环 MVP · 12–14h）：实现 `ParticlePool`、基础粒子渲染视口、右侧参数滑块与 JSON 预设导入导出闭环；",
            "阶段 2（核心工程化 · 12–16h）：实现二维 `QuadTree` 鼠标范围交互、GLSL 文件监听热重载容错、以及朴素模式 vs 优化模式基准测量；",
            "阶段 3（验证与交付 · 6–10h）：可选接入后台异步图片解码线程，跑通 ASan 内存消毒与 15+ 条 GTest，输出性能对比 README。",
        ],
        "data_vs_inference_boundary": {
            "from_real_data": "C/C++ (全库第一高频 29.5%)、数据结构与算法 (16.7%)、单元测试 (14.4%)、调试与内存分析 (13.7%)、Git/CMake (12.8%) 及 Qt 客户端框架均来自真实招聘 JD 统计。",
            "reasonable_inference": "以“动态壁纸与粒子视觉合成”为主题是基于你已有 OpenGL 练习与数字绘画/动漫兴趣的顺延设计，重点训练通用 C++ 内存与客户端架构，不将你锁定在纯图形学研究岗。",
        },
        "reference_and_license": {
            "references": [
                "Game Programming Patterns - Object Pool & Spatial Partition 章节 (https://gameprogrammingpatterns.com/)",
                "Qt 6 OpenGL 模块官方文档 (https://doc.qt.io/qt-6/qtopengl-index.html)",
                "GoogleTest 快速入门指南 (BSD-3-Clause, https://google.github.io/googletest/)",
            ],
            "reuse_vs_self_impl": (
                "允许复用 Qt6/ImGui 界面控件、glm 数学向量库、stb_image 图片加载与 nlohmann/json；"
                "必须由本人亲手编写：`ParticlePool` 连续内存 Swap-and-Pop 管理、`QuadTree` 节点分裂与范围检索、Shader 热重载安全替换状态机与基准测试代码。"
            ),
        },
        "ai_collab_boundary": {
            "must_do_personally": [
                "理解并亲手编写 `ParticlePool` 的连续内存存储与死亡粒子 Swap-and-Pop 回收逻辑，禁止使用低效的 `std::list` 逐个 `new`；",
                "独立编写 `QuadTree::insert` 与 `QuadTree::queryRange` 递归逻辑，并亲手设计边界点（正好落在分割线上、超出画布边界）的 GTest 用例；",
                "独立使用 AddressSanitizer 或调试器排查至少 1 次数组越界或资源未释放问题；",
                "能够在不依赖 AI 的情况下解释 `RenderCore` 是如何做到不引任何 Qt 头文件即可独立编译测试的。",
            ],
            "ai_can_accelerate": [
                "生成 Qt 右侧参数面板 (`QVBoxLayout` / `QSlider` / `QLabel`) 的重复性界面布局样板代码；",
                "生成 `preset.json` 字段与 C++ 结构体之间的 `nlohmann/json` 序列化样板代码；",
                "提供基础粒子颜色渐变或简单风场扰动的 GLSL 片段着色器数学公式参考。",
            ],
            "understanding_verification": [
                "画图解释：当第 3 号粒子生命周期结束时，Swap-and-Pop 是如何在 O(1) 时间内回收它且不破坏连续内存遍历的？",
                "现场展示 `core_benchmark` 输出：说出在你的电脑上，当粒子数为 30,000 时，开启四叉树前后鼠标范围查询各耗时多少微秒？",
            ],
        },
        "coverage_matrix": {
            "lang_cpp": ("完整覆盖", "在 `src/core/` 中使用 C++17 RAII、智能指针与连续内存 `ParticlePool` 实现核心模块", "已完整覆盖核心要求"),
            "fw_qt_gui": ("完整覆盖", "使用 Qt6 实现参数控制面板、OpenGL 渲染视口、文件监听信号槽与底部诊断栏", "已完整覆盖核心要求"),
            "dom_graphics_3d": ("完整覆盖", "实现 OpenGL 可编程着色器管线、万级粒子批量绘制与 GLSL 实时热重载容错", "已完整覆盖核心要求"),
            "algo_ds_core": ("完整覆盖", "手写二维四叉树 (`QuadTree`) 空间索引、Swap-and-Pop 对象池与滑动窗口耗时统计队列", "已完整覆盖核心要求"),
            "test_debug_profile": ("完整覆盖", "内置帧耗时基准测试器 + CMake 开启 ASan 内存零泄漏验证", "已完整覆盖核心要求"),
            "build_git_cmake": ("完整覆盖", "使用 CMake 多目标构建 (`RenderCore` 静态库 + 桌面端 + `CoreTests` 单测)", "已完整覆盖核心要求"),
            "test_automation_qa": ("完整覆盖", "在 `tests/` 下使用 GoogleTest 编写 12+ 条覆盖对象池、四叉树边界与配置解析的自动化单测", "已完整覆盖核心要求"),
            "conc_multithread": ("部分覆盖", "阶段 1–2 为单线程确定性渲染；阶段 3 可选引入后台 `std::thread` 异步解码大图纹理", "【独立小练习建议】若暂未做阶段 3 异步加载，可单独写 1 个 80 行的 C++ `std::mutex + std::condition_variable` 生产者消费者队列单测"),
            "db_sql_rdbms": ("未覆盖", "桌面渲染工具使用轻量 JSON 保存配置，未训练关系型数据库 SQL 与索引调优", "【独立小练习建议】花 3 小时用 SQLite/MySQL 完成 1 个含 5 万条记录的复合索引 `EXPLAIN` 对比小实验，或搭配候选项目一"),
            "net_tcp_http": ("未覆盖", "纯本地桌面程序，不涉及 TCP/HTTP 网络通信协议", "【独立小练习建议】花 2 小时写 1 个本地 UDP/TCP 命令行控制端向本窗口发送切换壁纸指令，或参考候选项目三"),
        },
    },

    # ══════════════════════════════════════════════════════════════════════
    # 候选项目三：C++17 回合战术状态机、确定性录像回放与 Linux 网络对战沙盒（主力：C++17）
    # ══════════════════════════════════════════════════════════════════════
    {
        "project_id": "proj_game_tactical_sandbox_replay",
        "title": "候选项目三：《基于 C++17 的回合战术状态机、时光倒流分叉推演与 Linux 网络沙盒》",
        "short_title": "C++17 回合战术回放推演与 Linux 网络沙盒",
        "interest_theme_tag": "♟️ 游戏局势分析/找最优解并验证 + 探究系统底层为什么这样工作",
        "tier_priority": "P0_prioritize_now",
        "priority_badge": "🌟 P0 硬核底座 · C++ 系统/网络/存储/算法深度训练",
        "difficulty_level": "进阶 (Intermediate)",
        "primary_languages": ["C++ (C++17, Linux / WSL2)"],
        "skill_ids": ["lang_cpp", "algo_ds_core", "os_linux_sys", "net_tcp_http", "conc_multithread", "test_debug_profile", "build_git_cmake", "test_automation_qa"],
        "target_categories": ["backend_dist", "sys_infra", "software_cpp", "game_engine", "security"],
        "target_roles": ["C++后端研发工程师", "Linux系统软件工程师", "基础架构/存储研发工程师", "游戏服务端/战斗逻辑工程师"],
        "plain_intro": {
            "what_is_it_called": "基于 C++17 的回合战术状态机、时光倒流分叉推演与 Linux 网络沙盒",
            "who_uses_it": "喜欢玩战棋/回合策略游戏（如火焰纹章、陷阵之志 Into the Breach、明日方舟等）、经常思考“如果第 3 回合我不走左边而是先放控制技能，结局会不会反败为胜”的策略游戏玩家与系统编程学习者。",
            "user_problem": "普通游戏一旦走错一步覆盖了存档就很难回头对比不同打法的收益；而在学习 C++ 和 Linux 网络编程时，教材上的“聊天室/Echo回声服务器”又太枯燥，不知道二进制协议拆包、预写日志 (WAL) 和寻路算法在真实工程里到底怎么配合工作。",
            "what_user_can_do": "在终端运行程序后，用户可以：① 在 10×10 战术网格上指挥单位移动（自动算出绕开障碍物和沼泽的最短路径）并释放技能；② 随时输入 `rewind 3` 将战局一秒倒回第 3 回合，从那里开辟一条新平行时间线推演不同打法的伤害与存活结果；③ 将整局操作保存为带 CRC 校验的二进制指令日志 (WAL)，哪怕中途直接强杀进程 (`kill -9`)，重启后也能一模一样恢复到断电前那一秒的战局；④（阶段2）打开两个终端窗口通过本地 TCP 网络联机过招。",
            "visible_outcome": "1个可在终端直接显示彩色 ASCII 战术棋盘与时间线分支树的 C++17 程序 + 1套二进制协议粘包与断电恢复自动化测试 + 实测性能基准报告。",
            "why_worth_doing": "它把 C++ 后端与系统软件面试必考的 4 大硬骨头——『A* 寻路与状态机设计、WAL 预写日志崩溃恢复、TCP 二进制帧协议防粘包 (`RingBuffer`)、Linux `epoll` 非阻塞网络』——串成了一个逻辑严密、极其好玩的“时光机战术沙盒”。",
            "everyday_example": "【给非计算机专业朋友的通俗比喻】：就像一台“带平行宇宙推演功能的象棋复盘机”。每走一步棋，机器不仅算好棋子怎么绕路最近（A*寻路），还会用防篡改墨水把这步棋记在账本上（WAL预写日志）。哪怕突然停电，来电后翻开账本就能瞬间摆回原样；更酷的是，你可以随时说“退回第 5 步，我想试试另一种走法”，机器会立刻复制出一条新分支让你对比哪种走法赢面更大。",
        },
        "layer1_overview": {
            "what_it_does": "一个支持 A* 自动寻路、随时倒回任意回合分叉推演不同打法、且断电强杀后能 100% 恢复战局的 C++17 战术沙盒（阶段 2 支持双端 TCP 联机）。",
            "final_outcome": "1个命令行可交互的 C++17 战术沙盒与双端联机服务 + 15+ 条 GTest 单元测试（含万次半包/粘包与损坏日志注入测试） + 技术复盘文档。",
            "v1_first_step": "第一版（MVP）完全不碰复杂的 Linux 网络和多线程！先在单机命令行下完成『10×10 网格状态机 + A* 寻路 + 回合快照倒流分叉 + 本地 WAL 日志保存与恢复』，确保核心逻辑简单纯粹、一眼看懂。",
            "realistic_time": "总计约 38–52 小时（写代码约 18h + 理解 WAL/TCP 粘包/epoll 原理、写 GTest 异常测试、用 GDB/ASan 排障与写文档约 24h；每周 8–10 小时约 4.5–5 周完成）。",
            "what_you_learn": "A* 启发式寻路与优先队列、Command 模式与确定性状态回滚、WAL 预写日志与 CRC32 校验、TCP 字节流 `RingBuffer` 拆包粘包处理、Linux `epoll` 事件循环。",
            "biggest_challenge": "如何保证重放日志时状态 100% 确定性一致（无未初始化内存或随机漂移），以及在阶段 2 中如何正确处理 TCP 把半个指令帧拆成两次到达（半包/粘包）的边界情况。",
        },
        "core_purpose": (
            "以“可倒流分叉推演的回合战术沙盒”为载体，遵循从单机确定性状态机与 WAL 存储（阶段 1）到 TCP 二进制协议与 Linux epoll 网络（阶段 2）的渐进路径，扎实训练 C++ 系统与后端底层工程能力。"
        ),
        "why_matches_interest": (
            "精准契合 profile.yaml 中『游戏偏好：喜欢分析具体局势、找到解法并及时验证；对拆解事物、理解系统内部机制有持续好奇心』。"
            "每一个底层技术点（A*、Command 快照、WAL 日志、RingBuffer 拆包）都直接服务于“战局推演与崩溃不丢档”，绝非生搬硬套技术名词。"
        ),
        "tech_selection_rationale": {
            "primary_stack": "C++17 + Linux / Windows WSL2 + CMake + GoogleTest + AddressSanitizer",
            "why_chosen": "完全贴合你正在学习的 C++ 主力语言、数据结构与计算机基础课程；无需学习任何 GUI 界面库或第三方重型框架，纯靠 C++ 标准库与 Linux 系统调用就能构建出含金量极高的系统级作品。",
            "restraint_rules_applied": [
                "不堆砌外部数据库或中间件：不引入 MySQL、Redis、Kafka，直接亲手实现 200 行的二进制 WAL（预写日志）文件存储，面试官对亲手写过 WAL + CRC32 截断恢复的本科生评价极高；",
                "不做图形界面：使用清晰的终端彩色 ASCII 10×10 棋盘输出，把原本调 UI 的 20 小时省下来吃透网络拆包与 GDB/ASan 调试；",
                "先单机后网络、先单线程后多线程：阶段 1 严禁引入网络与多线程，先把单机状态机与 WAL 测到 100% 稳固，阶段 2 再加网络。",
            ],
            "progression_tiers": [
                {
                    "tier": "基础版（阶段 1 MVP 必做 · 纯单机闭环）",
                    "stack": "C++17 单机确定性战术状态机 + A* 寻路 + 回合倒流分叉 + 本地 WAL 二进制日志读写 + GTest",
                    "why_needed": "隔离网络与并发干扰，先证明你能用 C++17 写出零内存泄漏的寻路算法、状态快照克隆与文件持久化闭环。",
                },
                {
                    "tier": "进阶版（阶段 2 核心工程化 · 网络与协议）",
                    "stack": "自定义二进制帧协议 (`Length + Cmd + Seq + CRC32 + Payload`) + `RingBuffer` 粘包拆包器 + Linux 单线程 `epoll` 联机服务",
                    "why_needed": "当需要支持两个终端窗口联机同步战术指令时，TCP 字节流天然会出现粘包与半包，此时引入定长帧头协议与 `RingBuffer` 是解决该问题的唯一正确工程方案。",
                },
                {
                    "tier": "更进一步（阶段 3 可选扩展 · 并发推演加速）",
                    "stack": "固定大小 C++17 线程池 (`ThreadPool`) 并行推演多条候选战术分支",
                    "why_needed": "当在某回合需要同时向外推演 50 种不同走法组合并按收益排序时，引入线程池并行计算可直观测出多核加速比与线程安全边界。",
                },
            ],
            "prerequisites": "已掌握 C++ 基础类、STL `vector`/`priority_queue` 与基本文件读写；阶段 2 需在 Windows WSL2 (Ubuntu) 或 Linux 环境下运行，并在做阶段 2 前先花 3 小时了解 Socket 与 TCP 字节流概念。",
        },
        "skill_classification_breakdown": {
            "job_required_skills": ["C/C++ 语言与内存管理", "数据结构与常用算法", "Linux 环境与操作系统原理", "计算机网络与协议栈 (TCP/Socket)", "调试排障与内存分析 (GDB/ASan)", "单元测试 (GTest)", "多线程并发编程"],
            "project_essential_skills": ["C++17 状态机与 RAII 资源管理 (`lang_cpp`)", "A* 寻路、二叉堆与 RingBuffer (`algo_ds_core`)", "WAL 磁盘日志与 Linux epoll I/O (`os_linux_sys`)", "TCP 二进制帧协议与半包粘包处理 (`net_tcp_http`)", "GTest + ASan 内存消毒 (`test_automation_qa`, `test_debug_profile`)"],
            "later_extension_skills": ["C++17 线程池并行分支推演 (`conc_multithread`)"],
            "standalone_exercise_skills": ["SQL 关系型数据库索引调优（由候选项目一覆盖，或通过独立 SQLite/MySQL 练习补充）"],
        },
        "natural_tech_stack_usage": (
            "• A* 寻路与优先队列（`core/pathfinder.cpp`）：解决战棋单位在含障碍物与不同移动消耗（平地=1，沼泽=3）的网格上寻找最短路径的实际问题；\n"
            "• Command 模式 + 历史快照树（`core/timeline.cpp`）：解决“倒回第 K 回合开辟新分支对比胜率”的问题；\n"
            "• WAL 预写日志 + CRC32（`storage/wal.cpp`）：解决进程意外退出后恢复对局进度、且识别断电导致的尾部半截损坏日志问题；\n"
            "• `RingBuffer` + 二进制帧头（阶段 2 `net/codec.cpp`）：解决 TCP 传输中多个动作指令粘连或被拆成碎片到达的问题。"
        ),
        "project_goal": (
            "从单机确定性战术沙盒与 WAL 崩溃恢复起步，逐步实现 TCP 二进制帧拆包器与 Linux epoll 双端同步服务，全流程通过 GTest 故障注入与 ASan 零泄漏验证。"
        ),
        "core_features": [
            "1. 10×10 确定性战术状态机与带权 A* 寻路：支持单位移动、攻击、地形阻碍计算与 ASCII 棋盘实时打印；",
            "2. 时光倒流与平行分支推演树：支持从任意历史回合 `rewind(turn_id)` 创建新分支，对比不同战术序列造成的总伤害与战损；",
            "3. 带 CRC32 校验的 WAL 预写日志与断电截断恢复：每条指令落盘记录，启动时自动重放并安全丢弃尾部损坏字节；",
            "4. TCP 二进制帧编解码器 (`RingBuffer`) 与 Linux `epoll` 联机（阶段 2）：支持双端联机提交回合指令，抵御任意半包、粘包与畸形帧注入。",
        ],
        "mvp_scope": {
            "problem_solved": "解决策略推演中无法低成本回退对比不同分支解法、且进程中断后对局状态丢失的问题。",
            "core_operations": [
                "操作 1（寻路与执行动作）：在命令行输入 `move unit_1 4 6` 或 `attack unit_1 enemy_2`，程序用 A* 算出路径、更新棋盘并追加写入 `match.wal`；",
                "操作 2（时光倒流与分叉）：输入 `branch 2` 倒回第 2 回合创建新分支 `branch_B`，输入新动作序列并输出与主分支 `branch_A` 的战果对比表；",
                "操作 3（崩溃恢复验证）：强行终止程序后重新运行 `./tactical_sandbox --recover match.wal`，画面与所有单位血量/坐标 100% 恢复；",
                "操作 4（协议分片压力自检 · 阶段2）：运行 `./protocol_fuzz_test` 将 1,000 条指令随机切成 1~7 字节碎片喂给解码器，验证 100% 还原。",
            ],
            "inputs": "终端战术指令（或脚本批量指令文件）、地形配置矩阵、以及阶段 2 的 TCP 字节流帧。",
            "outputs": "终端彩色 ASCII 棋盘状态、A* 路径坐标序列、分支收益对比表（总伤害/剩余血量/回合数）及 WAL 恢复校验报告。",
            "display_method": "终端命令行交互界面（清晰打印 10×10 网格、单位状态与分支树）+ `ctest` 自动化测试输出。",
            "out_of_scope_initially": [
                "第一版明确不做：复杂的 2D/3D 图形界面渲染（保持纯命令行 ASCII 棋盘，聚焦底层算法与系统机制）；",
                "第一版明确不做：成百上千种复杂技能词条配表（仅保留移动、普通攻击、击退控制 3 种核心动作即可验证全部机制）；",
                "第一版明确不做：账号匹配大厅、排行榜数据库与跨公网 NAT 穿透。",
            ],
            "closed_loop_proof": "运行 `ctest -R test_mvp_sandbox -V`：自动执行『加载 10×10 地图 → A* 寻路移动 5 步并写入 WAL → 从第 2 步分叉执行另一套动作 → 从磁盘重读 WAL 新建状态机实例 → 断言重放后的棋盘哈希值与原实例 100% 相等』。",
            "must_have": [
                "完成 10×10 网格状态机、带地形权重的 A* 寻路与终端 ASCII 棋盘展示；",
                "完成基于快照/Command 的回合倒流分叉对比与 WAL 二进制日志读写（含 CRC32 尾部损坏截断）；",
                "完成阶段 2 的 `RingBuffer` TCP 帧拆包器与 15+ 条 GTest 单元测试。",
            ],
            "must_have_mvp": "完成单机 C++17 战术状态机（A* 寻路 + 回合倒流分叉推演 + WAL 二进制日志崩溃恢复）及 12+ 条 GTest 单测（阶段 1 无需网络即可独立闭环）。",
            "shrunk_mvp_fallback": "若在阶段 2 调试 Linux `epoll` 多客户端联机超时：可保留已写好的 `RingBuffer` 二进制协议编解码器，直接用内存管道/本地文件流喂入分片字节流完成粘包拆包验证，砍掉多房间并发网络服务。",
            "estimated_mvp_weeks": "2 周（阶段 1 单机闭环约 14–16 小时）",
        },
        "engineering_tasks": [
            "确定性状态机设计：消除状态更新中的未定义行为与随机种子漂移，为棋盘状态实现 `uint64_t state_hash()` 以便快速比对一致性；",
            "WAL 日志容错实现：设计 `[4B Magic][4B Length][4B CRC32][Payload]` 磁盘记录格式，编写专门测试模拟“写到一半断电（文件末尾被截断 5 个字节）”时的安全恢复；",
            "TCP 拆包粘包验证：编写 `RingBuffer` 读写游标逻辑，用单字节馈送 (`1-byte feed`) 与多帧粘连馈送 (`multi-frame coalescing`) 两种极端测试验证零错包；",
            "GDB 与 ASan/UBSan 实战：全程开启 `-fsanitize=address,undefined` 编译测试。",
        ],
        "engineering_challenges": [
            "挑战 1（WAL 尾部半截损坏记录的识别与恢复）：当进程在写第 10 条日志中途被 `kill -9`，重启读取时如何通过长度头与 CRC32 校验识别出前 9 条有效、第 10 条不完整，并使用 `ftruncate` 安全修复文件；",
            "挑战 2（TCP 粘包与半包边界处理）：当 `recv()` 一次读入 2.5 个帧、下次再读入 0.5 个帧时，`RingBuffer` 如何正确维护读写指针而不发生内存越界；",
            "挑战 3（带权网格 A* 的平局决胜与确定性）：当存在多条相同代价路径时，如何保证不同机器/不同运行次数下 A* 返回的路径 100% 唯一确定。",
        ],
        "knowledge_mapping": [
            "数据结构与算法：带权图 A* 启发式搜索（曼哈顿距离启发函数、Open/Closed 集合）、环形缓冲区 (`RingBuffer`)、状态树克隆；",
            "操作系统与存储基础：文件 I/O 缓冲与 `fsync` 落盘语义、预写式日志 (WAL) 崩溃恢复原理、Linux 非阻塞文件描述符与 `epoll` 基础；",
            "计算机网络：TCP 面向字节流无消息边界特性、定长帧头+载荷长度协议设计、CRC32 循环冗余校验。",
        ],
        "acceptance_criteria": [
            "✅ 阶段 1 单机状态机与 WAL 恢复验收：执行 `ctest -R test_stage1_wal -V`；验证：① 在含沼泽(代价3)与障碍物的地图上 A* 返回唯一最短路径；② 从第 3 回合分叉出的两条时间线状态互不污染；③ 故意向 `match.wal` 末尾追加 7 个损坏字节后执行恢复，程序打印 `Warning: truncated 1 corrupted tail record` 并 100% 恢复前序有效回合；",
            "✅ 阶段 2 协议拆包与联机验收：执行 `ctest -R test_stage2_codec -V`；将 5,000 条合法指令帧分别按『每次只喂 1 字节（极端半包）』、『每次合并喂 8 帧（极端粘包）』、『随机翻转 1 bit 载荷（损坏帧）』输入 `FrameDecoder`，验证合法帧 100% 还原、损坏帧 100% 触发 CRC 错误拦截且进程零崩溃；",
            "✅ 阶段 3 内存消毒与基准测量验收：在开启 `-fsanitize=address,undefined` 下跑通全部测试套件，报告 `0 errors`；并运行 `./sandbox_bench` 测量本地单线程每秒可推演的战术动作步数与 WAL 恢复耗时基线。",
        ],
        "difficulty_workload_risks": {
            "difficulty": "进阶",
            "workload_range": "总计约 38–52 小时（其中：写出能运行代码约 18 小时，理解 WAL/TCP 拆包/epoll 原理+编写异常注入测试+GDB/ASan 调试+整理文档约 24 小时；按每周 8–10 小时计，约 4.5–5 周完成）",
            "estimation_premise": "估算依据：你正在学习 C++ 与数据结构算法，阶段 1 完全在你的舒适区向上够一够的范围内；误差来源：阶段 2 若首次接触 Linux `epoll` 非阻塞读写（处理 `EAGAIN`），可能需要额外 4–6 小时查阅文档与单步调试。",
            "main_risks": [
                "风险 1：试图把战棋规则做得像商业游戏一样复杂（几十种职业、装备、暴击随机数） → 应对：严禁加入随机暴击（会破坏确定性回放），只保留 3 种确定性基础动作；",
                "风险 2：阶段 1 就开始写 Socket 网络代码导致两头卡壳 → 应对：阶段 1 必须先在纯本地命令行下把状态机、A* 和 WAL 跑通并通过单测。",
            ],
        },
        "deliverables_and_demos": [
            "1. C++17 源码仓库（含 `core/` 状态机与 A*、`storage/wal.cpp`、`net/codec.cpp`、`tests/` 15+ 条 GTest 用例与 CMakeLists.txt）；",
            "2. 《WAL 断电截断恢复与 TCP 半包/粘包拆包器实验复盘文档》（附帧格式图与测试输出日志）；",
            "3. 终端演示录屏（展示 ASCII 棋盘 A* 寻路、倒回第 3 回合分叉推演对比、以及模拟损坏日志自动截断恢复）。",
        ],
        "prerequisites": "掌握 C++ 基础语法与 STL 容器，可在 Windows WSL2 或 Linux 下使用 `g++/cmake` 编译程序。",
        "estimated_effort": "约 38–52 小时（写代码约 18h + 原理理解/异常测试/GDB与ASan调试/文档约 24h；阶段 1 最小闭环仅需 14–16 小时）。",
        "resume_value": (
            "直击 C++ 后端、Linux 系统软件、存储与游戏服务端一面最高频考点：面试官看到你亲手实现并测试了『WAL 断电截断恢复』与『TCP 半包/粘包 RingBuffer 解码器』，会立刻认可你的系统工程潜力。"
        ),
        "staged_extensions": [
            "阶段 0（准备与自测 · 4–6h）：熟悉 WSL2 Linux 下 CMake + GTest + ASan 编译参数，手写 1 个 50 行二进制文件读写小练习；",
            "阶段 1（最小闭环 MVP · 14–16h）：实现 10×10 确定性战术状态机、带权 A* 寻路、回合倒流分叉推演与本地 WAL 日志恢复；",
            "阶段 2（核心工程化 · 12–18h）：实现自定义二进制帧协议、`RingBuffer` 半包/粘包解码器与 Linux 单线程 `epoll` 双端同步；",
            "阶段 3（验证与交付 · 8–12h）：可选加入线程池并行推演多分支，完成万次分片故障注入测试与 README 架构复盘。",
        ],
        "data_vs_inference_boundary": {
            "from_real_data": "C/C++ (29.5%)、数据结构与算法 (16.7%)、Linux/OS (15.8%)、单元测试 (14.4%)、调试排障 (13.7%) 与计算机网络 (7.4%) 均是全库真实岗位统计中的核心底座要求。",
            "reasonable_inference": "以“可倒流分叉的回合战术沙盒”包装上述底层训练点，是基于你 profile.yaml 中『喜欢分析局势找到解法并验证』与『探究系统内部机制』设计的工程载体，训练出的能力通用于 C++ 后端、基础架构与工业控制。",
        },
        "reference_and_license": {
            "references": [
                "Red Blob Games - A* Pathfinding 交互式图解教程 (https://www.redblobgames.com/pathfinding/a-star/introduction.html)",
                "Beej's Guide to Network Programming (Public Domain, https://beej.us/guide/bgnet/)",
                "SQLite / LevelDB WAL 日志格式设计说明 (https://github.com/google/leveldb/blob/main/doc/log_format.md)",
            ],
            "reuse_vs_self_impl": (
                "允许复用 GoogleTest 测试框架与 C++ STL 标准容器；"
                "必须由本人亲手编写：带权网格 A* 寻路、状态快照分叉树、WAL 二进制编码与尾部截断恢复、`RingBuffer` 帧拆包状态机。"
            ),
        },
        "ai_collab_boundary": {
            "must_do_personally": [
                "亲手编写带地形权重的 A* 寻路核心循环与代价更新逻辑，并能解释为何曼哈顿距离在此处满足可采纳性 (Admissibility)；",
                "亲手设计 WAL 日志帧头结构并编写读取时的 CRC32 校验与尾部残缺记录截断 (`ftruncate`) 逻辑；",
                "亲手编写 `RingBuffer` 处理 TCP 半包与粘包的游标推进代码，并用 GDB/ASan 独立调试至少 1 次边界越界问题；",
                "亲自设计“1字节分片喂入”与“文件尾部截断 5 字节”的 GTest 破坏性测试用例。",
            ],
            "ai_can_accelerate": [
                "生成终端彩色 ASCII 10×10 棋盘格式化打印函数与命令行参数解析代码；",
                "生成标准 CRC32 查表计算工具函数与 CMakeLists.txt 样板配置；",
                "辅助解释 Linux `epoll_wait` 返回码 (`EAGAIN` / `EINTR`) 的处理规范。",
            ],
            "understanding_verification": [
                "在纸上或白板上画出：当一个长度为 24 字节的协议帧被拆成 10 字节和 14 字节两次到达时，`RingBuffer` 的 `read_pos` 和 `write_pos` 如何变化？",
                "解释为什么战棋状态机里若混入 `rand()` 未固定种子的随机数，就会彻底破坏 WAL 日志重放与分叉推演的一致性？",
            ],
        },
        "coverage_matrix": {
            "lang_cpp": ("完整覆盖", "在 `core/`、`storage/`、`net/` 中使用 C++17 实现状态机、RAII 资源封装与二进制序列化", "已完整覆盖核心要求"),
            "algo_ds_core": ("完整覆盖", "手写带权 A* 寻路算法、优先队列、环形缓冲区 (`RingBuffer`) 与状态快照分叉树", "已完整覆盖核心要求"),
            "os_linux_sys": ("完整覆盖", "实现 WAL 预写日志落盘与截断恢复，阶段 2 实践 Linux 非阻塞 Socket 与 `epoll` 事件驱动", "已完整覆盖核心要求"),
            "net_tcp_http": ("完整覆盖", "设计定长帧头+CRC32 二进制协议，在 `net/codec.cpp` 中彻底解决 TCP 半包与粘包问题", "已完整覆盖核心要求"),
            "test_automation_qa": ("完整覆盖", "使用 GTest 编写 15+ 条覆盖 A* 寻路、WAL 断电截断、TCP 1字节分片/粘包注入的自动化测试", "已完整覆盖核心要求"),
            "test_debug_profile": ("完整覆盖", "全程开启 ASan/UBSan 编译验证零内存泄漏与零未定义行为，建立推演吞吐基线", "已完整覆盖核心要求"),
            "build_git_cmake": ("完整覆盖", "使用 CMake 管理核心库、沙盒主程序与 GTest 测试套件", "已完整覆盖核心要求"),
            "conc_multithread": ("部分覆盖", "阶段 1–2 采用确定性单线程事件循环；阶段 3 可选引入 C++17 线程池并行推演多条战术分支", "【独立小练习建议】若只做到阶段 2，可花 2 小时单独完成 1 个 80 行 C++17 `ThreadPool` 并行计算小练习"),
            "db_sql_rdbms": ("未覆盖", "本项目采用自研二进制 WAL 日志持久化，未涉及 SQL 关系型数据库与多表索引", "【独立小练习建议】建议花 3 小时单独完成 1 个 SQLite/MySQL 复合索引 `EXPLAIN` 调优小练习，或搭配候选项目一"),
            "fw_qt_gui": ("未覆盖", "采用终端彩色 ASCII 棋盘展示，不涉及 Qt 图形界面开发", "若想做桌面 GUI，可选择候选项目二"),
        },
    },

    # ══════════════════════════════════════════════════════════════════════
    # 候选项目四：游戏存档二进制解析、变异 Fuzzing 与弱网故障注入测试平台（主力：Python + C++）
    # ══════════════════════════════════════════════════════════════════════
    {
        "project_id": "proj_gamedev_qa_chaos_harness",
        "title": "候选项目四：《游戏存档二进制解构、变异模糊测试 (Fuzzing) 与弱网故障注入诊断工具》",
        "short_title": "游戏存档解构、变异Fuzzing与故障注入测试工具",
        "interest_theme_tag": "🛠️ 游戏存档技术解构 + 破坏性实验排障 + 自动化质量工程 (SDET)",
        "tier_priority": "P1_direction_deepening",
        "priority_badge": "🎯 P1 高WLB赛道 · 瞄准外企/大厂测试开发 (SDET) 与质量工程",
        "difficulty_level": "入门 → 进阶 (Entry to Intermediate)",
        "primary_languages": ["Python (pytest 测试与变异器) + C++17 (300行被测存档解析器)"],
        "skill_ids": ["test_automation_qa", "test_debug_profile", "lang_python", "lang_cpp", "net_tcp_http", "build_git_cmake", "db_sql_rdbms"],
        "target_categories": ["testing_quality", "software_cpp", "backend_dist", "security"],
        "target_roles": ["测试开发工程师 (SDET)", "质量工程/研发效能工程师", "C++/Python 工具链开发工程师", "客户端/服务端稳定性工程师"],
        "plain_intro": {
            "what_is_it_called": "游戏存档二进制解构、变异模糊测试 (Fuzzing) 与弱网故障注入诊断工具",
            "who_uses_it": "对“游戏存档文件里到底存了什么字节、为什么改坏一个字节游戏就会闪退、为什么联机卡顿时偶尔会吞掉道具”感到好奇的技术解构爱好者，以及软件团队里的测试开发工程师 (SDET)。",
            "user_problem": "初学者写完 C++ 程序后，往往只测试“输入正常数据能不能跑”，一旦遇到损坏的存档文件、负数长度字段或网络卡顿重发，程序就会发生内存越界崩溃或重复扣钱，而靠人工手点根本测不出这些隐藏 Bug。",
            "what_user_can_do": "运行这套工具后，用户可以：① 用 C++ 靶场程序生成一份标准二进制游戏存档（含角色等级、金币、背包物品 TLV 列表和 CRC32 校验码），并用解析器将其可读化展示；② 启动 Python 自动化变异器 (Fuzzer)，对存档执行位翻转、长度截断、超长整数溢出等破坏性变异（自动重算 CRC 绕过外层检查），在 30 秒内自动抓出 C++ 解析器里预埋的 5 个内存安全漏洞；③ 最小化保存导致崩溃的样本种子，在修复 C++ 代码后一键回归验证全部漏洞清零。",
            "visible_outcome": "1个 300 行的 C++17 二进制存档解析靶场程序 + 1套 Python `pytest` 自动化变异测试与弱网重放诊断脚本 + 1份《5 类典型内存与幂等缺陷捕获与修复前后对比报告》。",
            "why_worth_doing": "测试开发 (SDET) 是本科生进入德州仪器、博世等高 WLB 外企及大厂研发效能部门性价比极高的赛道（全库 828 个独立岗位），而本项目用极具极客趣味的“存档解构与自动找茬器”帮你同时练透 C++ 内存安全 (ASan) 与 Python 自动化测试框架。",
            "everyday_example": "【给非计算机专业朋友的通俗比喻】：就像给保险箱（C++ 存档解析程序）请了一位“全自动暴力开锁检测员（Python Fuzzer）”。普通检查员只用正确的钥匙试开一次就说合格；而你的自动检测员会在一分钟内自动造出一千把奇形怪状的假钥匙（缺齿的、超长的、刻着假防伪标的），专门试探保险箱会不会卡死或散架，并把每一把能弄坏锁芯的假钥匙自动拍照留档，逼着工匠把锁芯彻底修牢。",
        },
        "layer1_overview": {
            "what_it_does": "先写一个精简的 C++ 游戏存档二进制解析器，再用 Python 编写“全自动破坏性变异测试器”，自动揪出内存越界、整数溢出和重复扣款漏洞并验证修复。",
            "final_outcome": "1套含 C++ 被测模块与 Python 变异测试器的双语工程仓库 + 5个真实漏洞的复现种子与 ASan 修复对比报告 + SQLite 测试批次记录。",
            "v1_first_step": "第一版（MVP）完全不碰网络代理！先在本地文件层面完成『C++ 读写二进制存档 → 故意预埋 3 个边界 Bug → 用 Python 脚本自动变异存档字节并调用 C++ 程序 → 自动捕获崩溃并生成复现文件』的最小闭环。",
            "realistic_time": "总计约 28–38 小时（C++ 靶场仅 300 行，直接复用你现有 C++ 基础；Python 测试脚本结构清晰，写代码约 14h + 理解 ASan 报错/修复漏洞/写回归测试与文档约 18h，每周 8–10h 约 3–4 周完成）。",
            "what_you_learn": "TLV 二进制文件格式解析、带 CRC 重算的结构化模糊测试 (Smart Fuzzing)、AddressSanitizer (ASan) 内存越界定位、pytest 参数化回归测试、弱网重放幂等验证。",
            "biggest_challenge": "如果变异器随便乱改存档字节，99% 都会被第一道 CRC 校验直接拦住；必须理解并实现『先变异内部字段、再自动重新计算合法 CRC32 签名』，才能穿透表层校验测出深层 C++ 内存漏洞。",
        },
        "core_purpose": (
            "以“游戏二进制存档解构与破坏性测试实验”为切入点，训练测试开发 (SDET) 与系统研发共同看重的『二进制协议边界防护、自动化变异测试、ASan 内存排障与可复现回归门禁』能力。"
        ),
        "why_matches_interest": (
            "结合了 profile.yaml 中『对拆解事物、弄清它为什么这样工作有好奇心』『喜欢有反馈的实践，通过测试、运行结果检验成果』与『游戏兴趣』。"
        ),
        "tech_selection_rationale": {
            "primary_stack": "C++17（被测二进制存档解析器，约 300 行） + Python 3.11 (`pytest` 自动化变异与诊断框架) + SQLite（测试结果归档）",
            "why_chosen": "在真实工业界（如大疆、博世、金山办公、游戏引擎团队），底层高性能解析核心通常用 C++ 编写，而外层自动化测试、Fuzzing 驱动与报表聚合统一使用 Python + pytest。这种『C++ 被测小内核 + Python 自动化测试框架』是唯一合理且极贴合真实 SDET 岗位的双语言组合（职责边界极其清晰，绝非无理由混用）。",
            "restraint_rules_applied": [
                "严格限制被测 C++ 靶场体积：C++ 靶场控制在 300 行以内（仅负责二进制存档解析与状态扣减），绝不演变成庞大复杂的游戏工程；",
                "第一版不引入网络与容器：阶段 1 仅通过本地二进制文件与命令行退出码交互；",
                "零重型中间件：测试历史归档直接使用 Python 内置 `sqlite3`，不引入 MySQL、Redis 或 Jenkins。",
            ],
            "progression_tiers": [
                {
                    "tier": "基础版（阶段 1 MVP 必做 · 本地文件解构与变异）",
                    "stack": "C++17 存档解析器 (开启 ASan) + Python 结构化变异器 (自动重算 CRC32) + pytest 回归套件",
                    "why_needed": "用最轻量的方式跑通『生成合法存档 → 变异特定字段并重算 CRC → 触发 C++ 预埋越界/溢出 Bug → 保存最小复现文件 → 修复代码后回归绿灯』的完整闭环。",
                },
                {
                    "tier": "进阶版（阶段 2 核心工程化 · 弱网重放与 SQLite 基线）",
                    "stack": "Python 本地 TCP 故障注入代理（模拟丢包/延迟/重复发包） + SQLite 测试批次与耗时基线库",
                    "why_needed": "训练网络环境下的接口幂等性验证（例如同一笔“购买道具”报文因网络超时被重发 3 次，验证服务端是否多扣了金币）及历史回归对比。",
                },
                {
                    "tier": "更进一步（阶段 3 可选扩展 · CI 自动化门禁）",
                    "stack": "GitHub Actions 工作流自动编译 C++ (ASan) 并运行 pytest 套件",
                    "why_needed": "证明任何重新引入内存越界或性能明显退化的代码提交都会被 CI 自动拦截。",
                },
            ],
            "prerequisites": "已掌握 C++ 基础文件/内存操作；阶段 0 需花 6 小时熟悉 Python 基础语法、`subprocess` 调用与 `pytest` 断言。",
        },
        "skill_classification_breakdown": {
            "job_required_skills": ["单元测试与自动化测试框架 (pytest/GTest)", "调试排障与内存分析 (GDB/ASan)", "Python 脚本与工具开发", "C/C++ 语言与内存安全", "计算机网络与幂等容错", "版本控制与 CMake/CI"],
            "project_essential_skills": ["Python + pytest 自动化测试与结构化 Fuzzer (`test_automation_qa`, `lang_python`)", "C++17 二进制解析与 ASan 漏洞定位修复 (`lang_cpp`, `test_debug_profile`)", "CMake 构建 (`build_git_cmake`)"],
            "later_extension_skills": ["TCP 弱网重放与乱序注入代理 (`net_tcp_http`)", "SQLite 测试历史基线存储 (`db_sql_rdbms`)", "GitHub Actions CI 门禁 (`tool_docker_k8s`)"],
            "standalone_exercise_skills": ["复杂 B+ 树多表 SQL 调优與 Redis 缓存（由候选项目一覆盖）", "Qt 桌面 GUI 开发（由候选项目二覆盖）"],
        },
        "natural_tech_stack_usage": (
            "• C++17 二进制 TLV 解析器（`target/save_parser.cpp`）：解析包含 `[Magic:4B][Version:2B][Coin:4B][ItemCount:2B][Items...][CRC32:4B]` 的存档文件，作为真实内存安全测试靶场；\n"
            "• Python 结构化变异器（`fuzzer/mutator.py`）：针对 `ItemCount` 与实际字节长度不匹配、负数金币、超长字符串等边界生成变异样本，并自动重算尾部 CRC32；\n"
            "• AddressSanitizer (`-fsanitize=address,undefined`)：将 C++ 中隐藏的堆缓冲区溢出 (`heap-buffer-overflow`) 和有符号整数溢出转化为明确的非零退出码与精确行号调用栈；\n"
            "• SQLite 测试归档（阶段 2）：记录每次回归测试的随机种子 `seed`、检出漏洞数与解析耗时。"
        ),
        "project_goal": (
            "构建一套由 C++ 二进制存档解析靶场与 Python 结构化变异测试框架组成的自动化诊断工具，亲手完成『预埋 5 类典型缺陷 → 自动变异检出 → ASan 定位修复 → pytest 回归锁死』的完整质量工程闭环。"
        ),
        "core_features": [
            "1. 二进制游戏存档生成与解构器：支持将角色与背包 JSON 打包为紧凑二进制 `.sav` 文件，或将二进制文件解构还原并校验 CRC32；",
            "2. 穿透式 CRC 重算变异引擎 (Smart Mutator)：支持位翻转、极值整数替换 (`0`, `-1`, `INT32_MAX`, `UINT16_MAX`)、声明长度与实际载荷不一致变异；",
            "3. 5 类典型 C++ 缺陷靶场与修复对照：内置可通过宏开关切换的『修复前 (vulnerable) vs 修复后 (patched)』双模式，一键演示自动抓 Bug 与修复后全绿；",
            "4. 最小复现包归档与本地弱网重放测试（阶段 2）：自动将触发崩溃的二进制样本保存至 `crashes/seed_xxxx.sav`，并支持模拟重复请求验证扣款幂等性。",
        ],
        "mvp_scope": {
            "problem_solved": "解决手工测试无法覆盖二进制文件畸形边界、导致隐藏的 C++ 内存越界与整数溢出漏洞逃逸的问题。",
            "core_operations": [
                "操作 1（生成与正常解构存档）：运行 `python cli.py pack sample.json -o hero.sav` 生成二进制存档，再运行 `./save_parser hero.sav` 输出解析后的角色金币与装备列表；",
                "操作 2（运行穿透式变异测试抓 Bug）：对未修复版靶场运行 `python cli.py fuzz --seeds 200 --recalc-crc`，终端在 10 秒内自动抓出预埋的越界/溢出崩溃并保存 `crashes/crash_01.sav`；",
                "操作 3（单样本精准复现与 ASan 诊断）：运行 `./save_parser_vuln crashes/crash_01.sav`，直接复现 ASan 报错行号；",
                "操作 4（验证修复后 100% 免疫）：对修复版靶场运行 `pytest tests/ -v`，验证合法存档正常解析、所有畸形存档优雅返回错误码且零内存越界。",
            ],
            "inputs": "JSON 初始存档配置、随机变异种子整数 `seed`、以及变异后的 `.sav` 二进制字节流。",
            "outputs": "控制台诊断汇总表（总测试样本数、拦截非法格式数、触发 ASan 崩溃数、最小复现文件路径）与 SQLite 回归记录。",
            "display_method": "终端命令行清晰表格输出 + `pytest -v` 自动化测试报告 + 生成的 Markdown 漏洞诊断报告。",
            "out_of_scope_initially": [
                "第一版明确不做：针对商业加密壳游戏的逆向破解或内存外挂注入（仅针对自己编写的开源 C++ 存档格式进行合法安全测试）；",
                "第一版明确不做：复杂的 Web 分布式任务调度后台；",
                "第一版明确不做：Linux 内核态网卡驱动限速（阶段 2 仅用 100 行 Python 本地 Socket 转发模拟重放与丢包）。",
            ],
            "closed_loop_proof": "执行 `pytest tests/test_fuzz_closed_loop.py`：自动依次验证『正常存档打包与解包一致 → 关闭 CRC 重算时变异样本被表层拦截 → 开启 CRC 重算时成功触发 `save_parser_vuln` 预埋漏洞并生成复现文件 → 切换至修复版 `save_parser_safe` 后相同复现文件被安全拦截且退出码为预期错误码（非 ASan 崩溃）』。",
            "must_have": [
                "完成 300 行 C++17 二进制 `.sav` 解析器（含预埋缺陷开关与安全修复版）；",
                "完成 Python 带 CRC32 自动重算的结构化变异器 (`mutator.py`)；",
                "完成 15+ 条 pytest 回归测试，覆盖 5 类典型边界缺陷的复现与修复验证。",
            ],
            "must_have_mvp": "完成 C++ 二进制存档解析器（含预埋漏洞与修复对照）+ Python 自动重算 CRC 变异测试器 + 15+ 条 pytest 回归测试（纯本地文件闭环，无需网络）。",
            "shrunk_mvp_fallback": "若课业繁忙：完全跳过阶段 2 的 TCP 弱网代理与 SQLite 历史表，仅保留本地二进制存档解析器 + 5 类边界变异 Fuzzing + pytest/ASan 修复前后对比报告，约 18–20 小时即可高质量交付。",
            "estimated_mvp_weeks": "1.5–2 周（约 12–15 小时）",
        },
        "engineering_tasks": [
            "二进制格式契约定义：制定 `.sav` 格式规范，编写 C++ 解析器并在其中显式设计 5 个教学级缺陷（① `ItemCount` 声明为 1000 但文件只有 20 字节导致读越界；② 物品名长度字段未检查上限导致栈/堆缓冲溢出；③ 购买数量乘单价有符号 32 位整数溢出变成负数反加金币；④ 除零错误；⑤ 异常分支提前 `return` 导致内存泄漏）；",
            "智能变异器开发：在 Python 中实现按字段变异并重新打包计算 CRC32，对比“盲目随机改字节”与“结构化变异+重算CRC”的漏洞检出效率；",
            "代码修复与回归锁死：在 C++ 中使用边界检查、`uint64_t` 安全算术与 `std::vector` / RAII 修复上述 5 个缺陷，并将抓到的崩溃样本固化为 `tests/regression_seeds/` 下的永久回归测试。",
        ],
        "engineering_challenges": [
            "挑战 1（穿透表层校验）：如何让 Fuzzer 既能破坏内部业务字段（如把物品数量改成 65535），又能重新生成合法的尾部 CRC32 从而进入核心解析逻辑；",
            "挑战 2（区分“优雅报错退出”与“内存崩溃退出”）：Python 测试驱动器如何通过子进程返回码与 stderr 中的 `AddressSanitizer` 标识，准确区分 C++ 程序是正常拦截了坏存档（返回码 `1`）还是发生了内存崩溃（返回码 `-6`/`-11` 或 ASan 退出码 `77`）；",
            "挑战 3（随机测试的 100% 可复现性）：如何通过固定伪随机数种子 (`random.seed(seed)`) 确保任何一次跑出的崩溃都能用同一个 `seed` 一秒复现。",
        ],
        "knowledge_mapping": [
            "软件测试与质量工程：边界值分析、等价类划分、灰盒/结构化模糊测试 (Smart Fuzzing)、回归测试门禁；",
            "C++ 内存安全与二进制表示：字节序 (Endianness)、TLV 编码、有符号与无符号整数溢出、堆/栈内存越界原理与 ASan 检测机制；",
            "计算机网络基础（阶段 2）：请求重放攻击与接口幂等令牌 (`Idempotency-Key`) 去重原理。",
        ],
        "acceptance_criteria": [
            "✅ 阶段 1 漏洞检出与修复对照验收：执行 `pytest tests/test_stage1_fuzzer.py -v`；固定随机种子 `seed=42` 生成 100 个结构化变异存档：① 针对 `save_parser_vuln` 运行，验证全部 5 类预埋漏洞均被触发并被 ASan 捕获；② 针对修复后的 `save_parser_safe` 运行相同 100 个变异存档，验证 ASan 崩溃数降为 `0`，所有畸形输入均在 10ms 内安全返回错误提示；",
            "✅ 阶段 2 CRC 穿透对比与幂等验收：运行 `python scripts/compare_fuzz_modes.py --samples 500`；实测记录『不重算 CRC 的盲目变异』与『自动重算 CRC 的结构化变异』进入核心解析分支的比例差异；并在重放测试中连续发送 5 次相同 `request_id` 的扣金币指令，验证仅扣减 1 次；",
            "✅ 阶段 3 交付验收：在干净目录下通过 CMake 编译 C++ 靶场并执行 `pytest`，全部 15+ 条正常与回归测试 100% 通过，输出 Markdown 诊断报告。",
        ],
        "difficulty_workload_risks": {
            "difficulty": "入门 → 进阶",
            "workload_range": "总计约 28–38 小时（其中：写出 C++ 靶场与 Python 变异器约 14 小时，理解 ASan 报告+修复 5 类漏洞+写 pytest 回归测试与文档约 18 小时；按每周 8–10 小时计，约 3–4 周完成）",
            "estimation_premise": "估算依据：这是 6 个候选项目中工作量最友好、成就感反馈最快的一个（因为 C++ 代码量仅 300 行，且每次抓到 Crash 都有极强的正反馈）；误差来源：Windows 下若使用 MSVC 编译器开启 ASan 需配置特定编译选项，建议直接在 WSL2 Ubuntu 下使用 `g++ -fsanitize=address`（零配置困难）。",
            "main_risks": [
                "风险 1：把 C++ 靶场写得太复杂 → 应对：严格限定 `.sav` 格式只有 4 个头部字段和 1 个物品数组，300 行代码封顶；",
                "风险 2：误以为测试开发项目不需要懂底层代码 → 应对：在简历与 README 中重点展示你是如何用 ASan 定位 C++ 整数溢出与堆越界并亲手修复源码的。",
            ],
        },
        "deliverables_and_demos": [
            "1. 包含 C++17 双模式靶场 (`vuln` / `safe`) 与 Python 结构化 Fuzzer 的完整仓库；",
            "2. 《5 类典型二进制解析与并发幂等漏洞：自动变异检出、ASan 调用栈分析与修复对照报告》；",
            "3. 终端录屏（展示 10 秒内自动抓出 5 个 ASan 内存越界崩溃，切换到修复版后 500 个变异样本 0 崩溃全绿通过）。",
        ],
        "prerequisites": "掌握 C++ 基础语法与结构体/文件读写，愿意在阶段 0 花 6 小时上手 Python 基础与 pytest。",
        "estimated_effort": "约 28–38 小时（写代码约 14h + 理解内存漏洞原理/ASan调试/pytest回归测试/文档约 18h；每周 8–10 小时约 3–4 周完成）。",
        "resume_value": (
            "极具辨识度的测试开发 (SDET) 与客户端/系统安全工程作品：比起只会用 Postman 点接口的候选人，你能拿出亲手写的『带 CRC 重算的结构化 Fuzzer + 5 类 C++ ASan 内存漏洞捕获与修复闭环』，在外企与大厂测开面试中优势巨大。"
        ),
        "staged_extensions": [
            "阶段 0（准备与自测 · 5–6h）：熟悉 Python `struct` 二进制打包解包、`subprocess` 调用与 WSL2 下 `g++ -fsanitize=address`；",
            "阶段 1（最小闭环 MVP · 10–12h）：编写 C++ `.sav` 解析器（含 5 个预埋缺陷）与 Python 结构化变异器，跑通自动抓 Bug 与修复后回归测试；",
            "阶段 2（核心工程化 · 8–12h）：对比盲目变异 vs 重算 CRC 变异的穿透率，增加请求重放幂等测试与 SQLite 测试批次记录；",
            "阶段 3（验证与交付 · 5–8h）：固化最小崩溃种子回归库，配置 GitHub Actions 自动执行 ASan + pytest 门禁并整理复盘报告。",
        ],
        "data_vs_inference_boundary": {
            "from_real_data": "单元测试与自动化测试在全库占比 14.4%（在测试/质量工程类别中高达 69.3%），调试排障占 13.7%，C/C++ 与 Python 分列全库前两位，均来自真实岗位 JD 统计。",
            "reasonable_inference": "以“游戏存档解构与变异测试”作为实验靶场是基于个人兴趣的设计，所训练的二进制边界测试、ASan 诊断与回归门禁能力 100% 通用于芯片软件、车载系统、工业软件与互联网质量工程。",
        },
        "reference_and_license": {
            "references": [
                "pytest 官方文档 (MIT License, https://docs.pytest.org/)",
                "AddressSanitizer 官方 Wiki 与错误类型说明 (https://github.com/google/sanitizers/wiki/AddressSanitizer)",
                "Python `struct` 与 `zlib.crc32` 标准库文档 (https://docs.python.org/zh-cn/3/library/struct.html)",
            ],
            "reuse_vs_self_impl": (
                "允许使用 Python `pytest`、`struct`、`zlib.crc32` 标准库；"
                "必须由本人亲手编写：C++ `.sav` 解析器与安全修复逻辑、Python 按字段变异生成器及崩溃退出码分类判定逻辑。"
            ),
        },
        "ai_collab_boundary": {
            "must_do_personally": [
                "亲手设计 `.sav` 二进制文件头格式，并在 C++ 解析器中亲自写出那 5 类边界缺陷与对应的安全修复代码；",
                "独立编写 Python 变异器中『修改内部字段 → 重新计算 CRC32 覆盖文件尾部』的核心逻辑；",
                "亲自阅读 ASan 打印的 `heap-buffer-overflow` / `signed integer overflow` 报错栈，定位到具体 C++ 代码行；",
                "独立设计并编写 `tests/test_regression.py`，确保每个历史崩溃种子都有对应的自动化断言。",
            ],
            "ai_can_accelerate": [
                "生成命令行参数解析 (`argparse`) 与 Markdown 测试汇总表格打印样板代码；",
                "生成合法的初始测试角色与背包物品 JSON 示例文件；",
                "辅助编写 GitHub Actions `.yml` 自动运行编译与 pytest 的配置模板。",
            ],
            "understanding_verification": [
                "指着 `save_parser.cpp` 中的整数溢出漏洞代码，向面试官解释：为什么当 `item_count = 65536, price = 65536` 时，32位有符号乘法会绕过 `total_cost <= user_coins` 的检查？",
                "现场演示：如果不重算 CRC32，500 个随机变异样本中有多少个在第一行 CRC 检查就被退回了？开启重算后又有多少个成功触发了内部漏洞？",
            ],
        },
        "coverage_matrix": {
            "test_automation_qa": ("完整覆盖", "在 `fuzzer/` 与 `tests/` 中实现结构化变异测试器与 15+ 条 pytest 自动化回归断言", "已完整覆盖核心要求"),
            "test_debug_profile": ("完整覆盖", "使用 ASan/UBSan 捕获并修复 5 类 C++ 堆越界、整数溢出与内存泄漏缺陷", "已完整覆盖核心要求"),
            "lang_python": ("完整覆盖", "使用 Python `struct`、`subprocess` 与 `pytest` 构建完整自动化诊断工具链", "已完整覆盖核心要求"),
            "lang_cpp": ("完整覆盖", "使用 C++17 编写二进制 `.sav` 存档解析器并完成边界安全加固", "已完整覆盖核心要求"),
            "build_git_cmake": ("完整覆盖", "使用 CMake 编译 `save_parser_vuln` 与 `save_parser_safe` 双目标可执行文件", "已完整覆盖核心要求"),
            "net_tcp_http": ("部分覆盖", "阶段 2 实现轻量重放与丢包模拟验证扣款幂等性，不涉及复杂高并发路由服务", "【独立小练习建议】如需投递纯网络后端岗，可结合候选项目三补充 epoll 网络训练"),
            "db_sql_rdbms": ("部分覆盖", "阶段 2 使用 SQLite 记录测试运行批次与崩溃种子统计，不涉及复杂多表联查调优", "【独立小练习建议】可花 2 小时在 SQLite 中为测试记录表做一次索引 `EXPLAIN` 练习"),
            "tool_docker_k8s": ("部分覆盖", "阶段 3 配置 GitHub Actions CI 自动执行 ASan + pytest 门禁", "已满足测开岗核心 CI 要求"),
            "algo_ds_core": ("部分覆盖", "涉及二进制 TLV 偏移计算、CRC32 校验与去重哈希表，未涉及复杂树/图搜索", "【独立小练习建议】由平时数据结构课程或候选项目一/二/三互补"),
        },
    },

    # ══════════════════════════════════════════════════════════════════════
    # 候选项目五：原版动漫/游戏双语台词混合检索 (BM25) 与间隔复习工具（主力：Python）
    # ══════════════════════════════════════════════════════════════════════
    {
        "project_id": "proj_jp_media_immersion_agent",
        "title": "候选项目五：《原版动漫/游戏双语台词上下文检索 (手写BM25) 与间隔复习引擎》",
        "short_title": "动漫/游戏双语台词检索与间隔复习引擎",
        "interest_theme_tag": "🗣️ 原版动漫/游戏台词阅读 + 检索算法拆解 + 可选 AI 语法卡片",
        "tier_priority": "P1_direction_deepening",
        "priority_badge": "🤖 P1 检索与AI工程 · 手写倒排索引与离线量化评测",
        "difficulty_level": "入门 → 进阶 (Entry to Intermediate)",
        "primary_languages": ["Python (FastAPI / CLI)"],
        "skill_ids": ["algo_ds_core", "dom_llm_rag_agent", "lang_python", "db_sql_rdbms", "test_automation_qa"],
        "target_categories": ["ai_ml", "backend_dist", "data_platform", "testing_quality"],
        "target_roles": ["AI应用工程研发工程师 (RAG/检索)", "后端开发工程师 (搜索与数据工程)", "测试与评测开发工程师"],
        "plain_intro": {
            "what_is_it_called": "原版动漫/游戏双语台词上下文检索 (手写BM25) 与间隔复习引擎",
            "who_uses_it": "喜欢看原版动漫、玩日语/英语原版游戏，想在遇到某个生词或句型时，一秒钟从自己看过的几十部作品字幕里找出“包含这个词的原剧台词+前后两句剧情对话”，并加入生词本按遗忘曲线定时复习的学习者。",
            "user_problem": "普通词典里的例句干巴巴的背不住；而如果直接把几万句双语字幕塞进文本文件用 `Ctrl+F` 查找，既不能按相关性高低排序，也没法同时拿到这句话的前后剧情上下文，更没法按记忆规律自动安排复习。",
            "what_user_can_do": "运行程序后，用户可以：① 导入双语字幕文件（`.srt`/`.ass` 或 JSON 台词包），系统自动切分时间轴并绑定每句台词的上下句语境存入 SQLite；② 输入一个词或短语（如『約束』『命运』『time leap』），通过手写的 BM25 倒排索引毫秒级返回最相关的原剧台词卡片（含作品名、出现时间点、前后 2 句对话）；③ 将台词加入复习本，根据“认识/模糊/忘记”评分，由最小堆 + SM-2 算法自动算出下次复习时间；④（阶段2可选）接入受控词典/AI 语法拆解并运行 30 条黄金测试集算出检索召回率 (`Recall@5`)。",
            "visible_outcome": "1个支持双语字幕导入、BM25 相关性检索、上下文卡片展示与每日到期复习队列的本地工具 + 1份《30 条查询黄金测试集 Recall@5 实测对比报告》。",
            "why_worth_doing": "现在很多简历写“基于大模型的知识库问答”，其实只是调了一行现成库接口，面试官一问底层怎么算相关性就答不上来。本项目让你亲手用 150 行代码实现搜索引擎最核心的『倒排索引 + BM25 打分公式 + SM-2 最小堆调度 + Recall@K 量化评测』，不花一分钱 API 费也能练出真正的搜索与 RAG 工程内功。",
            "everyday_example": "【给非计算机专业朋友的通俗比喻】：就像把你看过的几十部动漫台词做成了一本“带剧情回放的活页词典”。你想学某个词怎么用，不用死背教科书，只要输入这个词，它不仅按贴切程度把你最熟的动漫角色说过的话排好序端上来，连当时角色前后两句在聊什么都附在旁边，读完点一下“加入复习”，它还会在你快要忘掉的那天早上准时递到你面前。",
        },
        "layer1_overview": {
            "what_it_does": "导入动漫/游戏双语字幕，用自己手写的 BM25 倒排索引秒查原剧例句（附前后剧情对话），并用遗忘曲线算法安排每日生词台词复习。",
            "final_outcome": "1个双语台词检索与间隔复习程序 + 手写 BM25 倒排索引模块 + 30 条测试查询的 `Recall@5` 评测报告 + 12+ 条 pytest 单测。",
            "v1_first_step": "第一版（MVP）完全不需要联网调用大模型 API，也不需要装向量数据库！先用 Python + SQLite + 手写 BM25 倒排索引完成『导入字幕 → 检索台词与上下句 → 加入复习卡并按 SM-2 算出下次复习日期』的纯本地零成本闭环。",
            "realistic_time": "总计约 30–42 小时（写代码约 14h + 推导 BM25 与 SM-2 公式、构建 30 条评测集、写 pytest 单测与文档约 20h；每周 8–10h 约 3.5–4.5 周完成）。",
            "what_you_learn": "倒排索引 (Inverted Index) 与 BM25 概率检索算法、字符 N-gram 切分、SM-2 间隔重复算法与最小堆优先队列、检索评测指标 (`Recall@K`, `MRR`)、可选 RAG 防幻觉校验。",
            "biggest_challenge": "如何正确实现中日英混排台词的切分（结合词汇与字符二元组 Bigram），避免因为中日文没有空格分隔而搜不到变形词。",
        },
        "core_purpose": (
            "剥离市面上 AI 项目常见的“黑盒调包套壳”，从零手写『双语字幕解析 → 倒排索引与 BM25 打分 → RRF 多路融合 → 黄金测试集 Recall@5 量化评测 → SM-2 复习调度』，扎实训练搜索工程与 AI RAG 检索底座能力。"
        ),
        "why_matches_interest": (
            "结合了 profile.yaml 中对动漫/游戏作品的熟悉度与『拆解事物内部结构、通过测试和运行结果验证成果』的偏好，同时日常学外语/查台词真正能用上。"
        ),
        "tech_selection_rationale": {
            "primary_stack": "Python 3.11 + SQLite 3 + 手写 BM25 倒排索引与 SM-2 调度核心 + pytest",
            "why_chosen": "字符串文本处理、分词、评测指标计算与可选的 LLM API 调用是 Python 最擅长的主场；使用内置 SQLite 存储台词与复习记录，无需额外部署 PostgreSQL、Elasticsearch 或 Redis 即可在单机跑通全部核心算法与评测。",
            "restraint_rules_applied": [
                "不黑盒调包替代核心训练：禁止在阶段 1 直接调用 Elasticsearch 或 LangChain 封装好的检索器，必须亲手用 Python `dict` 实现倒排表与 BM25 公式（仅约 120 行代码，但让你彻底搞懂搜索引擎原理）；",
                "不强制绑定付费 API 或重型显卡：阶段 1–2 默认支持纯离线运行（字符 N-gram + 词法双路 RRF 融合），即使不填 DeepSeek API Key 也能 100% 跑通所有评测；",
                "不无故引入 Redis/MySQL：个人复习卡片规模在千级以内，用 SQLite 索引 + 内存最小堆 (`heapq`) 即可毫秒级完成到期调度。",
            ],
            "progression_tiers": [
                {
                    "tier": "基础版（阶段 1 MVP 必做 · 纯离线零 API 成本）",
                    "stack": "Python + SQLite + 手写 BM25 倒排索引 + SM-2 最小堆复习调度器 + CLI/轻量 Web",
                    "why_needed": "先在不依赖任何外部网络服务的情况下，跑通『字幕解析入库 → BM25 台词上下文检索 → SM-2 复习打分与到期排序』的完整本地闭环。",
                },
                {
                    "tier": "进阶版（阶段 2 核心工程化 · 双路混合重排与量化评测）",
                    "stack": "词级 BM25 + 字符 Bigram 模糊匹配双路倒数秩融合 (RRF) + 30 条黄金测试集 (`Recall@5` / `MRR` 自动评测脚本)",
                    "why_needed": "当查询包含日语词尾变形或拼写近似词时，单路精确词匹配会漏检；引入双路 RRF 融合并在 30 条测试集上实测 `Recall@5` 变化，能让你掌握真实的搜索调优方法。",
                },
                {
                    "tier": "更进一步（阶段 3 可选扩展 · 受控语法解构 Agent）",
                    "stack": "本地词典核验工具 + JSON Schema 强校验 LLM 语法拆解（带超时与幻觉拦截回退）",
                    "why_needed": "仅在你想额外训练 AI Agent 工具调用容错时开启：强制模型輸出的每个词汇解释都必须能在原句或本地词库中对齐，否则自动触发拦截回退。",
                },
            ],
            "prerequisites": "掌握基础哈希表与堆概念；阶段 0 预留 6–8 小时快速掌握 Python 字典、正则表达式与 pytest。",
        },
        "skill_classification_breakdown": {
            "job_required_skills": ["数据结构与常用算法", "LLM 应用工程与 RAG 检索评测", "Python 工程开发", "关系型数据库 (SQL)", "单元测试与自动化评测"],
            "project_essential_skills": ["手写倒排索引、BM25、RRF 融合与最小堆调度 (`algo_ds_core`)", "离线检索评测基准与防幻觉校验 (`dom_llm_rag_agent`)", "SQLite 台词上下文与复习记录事务管理 (`db_sql_rdbms`)", "Python + pytest 自动化测试 (`lang_python`, `test_automation_qa`)"],
            "later_extension_skills": ["在线 Embedding 向量检索对比或 Redis ZSET 到期队列 (`db_cache_mq`)"],
            "standalone_exercise_skills": ["C++ 内存管理与多线程（由候选项目二/三覆盖）"],
        },
        "natural_tech_stack_usage": (
            "• 倒排索引与 BM25（`search/bm25.py`）：解决普通 SQL `LIKE '%keyword%'` 无法按词频 (TF)、逆文档频率 (IDF) 和句子长度归一化排序的问题；\n"
            "• 倒数秩融合 RRF（`search/rrf.py`）：解决“精确词匹配”与“字符二元组 (Bigram) 变形匹配”两路结果分数量纲不同、难以直接相加合并的问题；\n"
            "• 最小堆 + SM-2 算法（`review/scheduler.py`）：解决生词卡片根据用户掌握程度（评分 0~5）动态计算下次复习间隔天数并按到期时间优先弹出的问题；\n"
            "• 黄金评测集脚本（`eval/evaluate.py`）：解决“改了分词或参数后到底变好了还是变差了”的量化验证问题。"
        ),
        "project_goal": (
            "从零手写一个支持双语字幕上下文入库、BM25+Bigram RRF 混合检索、SM-2 间隔复习调度与 30 条查询量化评测 (`Recall@5` / `MRR`) 的检索学习系统。"
        ),
        "core_features": [
            "1. 双语字幕解析与上下文窗口绑定：解析 `.srt`/`.json` 台词，自动将每句台词与同作品前后各 1~2 句台词关联存入 SQLite；",
            "2. 手写 BM25 倒排索引与双路 RRF 混合检索：输出每条命中台词的得分明细（命中了哪些词项、IDF 权重是多少、来自哪部作品第几分钟）；",
            "3. SM-2 间隔记忆计算与最小堆到期队列：支持对收藏台词打分（0~5），自动更新易度因子 (`EF`) 与下次复习时间戳；",
            "4. 自动化检索评测基准 (`eval_harness.py`)：内置 30 条带标准答案 (`ground_truth_line_id`) 的查询样本，一键输出不同检索策略的 `Recall@5` 与 `MRR` 对比表。",
        ],
        "mvp_scope": {
            "problem_solved": "解决原版动漫/游戏学习中无法按相关性检索真实语境例句、且缺乏科学复习周期管理的问题。",
            "core_operations": [
                "操作 1（导入台词包）：执行 `python app.py import data/sample_subtitles.json`，解析 1,000+ 条双语台词、建立上下文关联并写入 SQLite；",
                "操作 2（检索原剧例句与上下文）：执行 `python app.py search \"约定\" --top-k 5`，毫秒级返回前 5 条双语台词、BM25 得分及每句的前后对话；",
                "操作 3（加入复习本并完成一次打分）：执行 `python app.py review-add --line-id 42` 加入生词本，再执行 `python app.py review-grade --card-id 1 --quality 4`，验证系统按 SM-2 算出下次复习日期并写入数据库；",
                "操作 4（运行量化评测）：执行 `python app.py evaluate --dataset data/golden_30.json`，输出当前检索器的 `Recall@1`、`Recall@5`、`MRR` 与平均耗时。",
            ],
            "inputs": "双语字幕文件（含作品名、时间戳、原文、译文）、搜索查询词、复习反馈评分（整数 0~5）及黄金测试集 JSON。",
            "outputs": "带上下文与得分拆解的台词卡片列表、按到期时间排序的今日待复习清单、以及 Markdown/JSON 格式的检索评测报告。",
            "display_method": "命令行交互工具或 FastAPI `/docs` 面板（二选一即可）+ `pytest` 自动化测试输出。",
            "out_of_scope_initially": [
                "第一版明确不做：视频自动语音识别 (ASR 字幕生成) 或 OCR 屏幕取词；",
                "第一版明确不做：本地部署数十 GB 的开源大语言模型权重；",
                "第一版明确不做：复杂的社区打卡排行榜与多端云同步。",
            ],
            "closed_loop_proof": "运行 `pytest tests/test_mvp_immersion.py -v`：自动验证『导入 50 条测试双语台词 → 构建 BM25 索引检索目标词并校验返回了正确的上下句语境 → 将命中台词加入复习卡并连续模拟 3 次打分(quality=5, 4, 2) → 重新从 SQLite 读取卡片验证间隔天数分别按预期增长与重置』。",
            "must_have": [
                "完成 SQLite 台词表（含上下句关联 ID）与复习卡片表建模及读写；",
                "亲手实现 `BM25Index`（含 TF、IDF、文档长度归一化）与 `SM2Scheduler` 算法；",
                "构建 30 条黄金测试查询并完成 `Recall@5` 自动化评测脚本与 12+ 条 pytest 单测。",
            ],
            "must_have_mvp": "使用 Python + SQLite 手写完成双语台词导入、BM25 倒排检索（附前后语境）、SM-2 间隔复习计算及 12+ 条 pytest 单测（纯离线零 API 成本）。",
            "shrunk_mvp_fallback": "若时间紧张：完全省去阶段 3 的外部大模型语法拆解接口，仅用本地词频 + 字符 Bigram 双路 RRF 检索 + SM-2 复习算法 + 30 条黄金样本评测报告，即可作为非常扎实的搜索/RAG 底座项目交付。",
            "estimated_mvp_weeks": "1.5–2 周（约 12–14 小时）",
        },
        "engineering_tasks": [
            "手写 BM25 与 RRF 算法：不依赖第三方搜索引擎，独立编写倒排表构建、IDF 平滑公式与 RRF (`score = sum(1 / (k + rank))`) 融合逻辑；",
            "构建黄金评测基准：从导入的双语台词中挑选 30 个典型查询（含精确词、变形词、短句），编写脚本自动对比『SQL LIKE 匹配 vs 单路词匹配 vs BM25+Bigram RRF 融合』的 `Recall@5`；",
            "SM-2 边界与持久化测试：针对评分 `< 3`（遗忘重置间隔为 1 天）与评分 `>= 3`（间隔递增且易度因子下限不低于 `1.3`）编写参数化单测。",
        ],
        "engineering_challenges": [
            "挑战 1（中日文无空格分词与变形召回）：日语动词/形容词词尾会变形，单纯按词典切分可能漏掉变形形式；如何结合字符二元组 (Character Bigram) 作为第二路检索并通过 RRF 融合提升召回；",
            "挑战 2（句子长度不均导致的打分偏差）：字幕中有只有 2 个字的感叹句，也有 50 字的长句，如何通过 BM25 的长度归一化参数 $b$ 防止长句或短句不合理霸榜。",
        ],
        "knowledge_mapping": [
            "信息检索与数据结构：倒排索引 (Inverted Index)、TF-IDF 与 BM25 概率检索公式、倒数秩融合 (RRF)、最小堆优先队列 (`heapq`)；",
            "评测工程与软件测试：信息检索离线评价指标 (`Recall@K`、平均倒数排名 `MRR`)、边界值单元测试；",
            "数据库应用：SQLite 事务批量插入、外键自关联（上句/下句 `prev_line_id`/`next_line_id`）查询。",
        ],
        "acceptance_criteria": [
            "✅ 阶段 1 最小闭环与算法单测验收：执行 `pytest tests/test_stage1_bm25_sm2.py -v`；验证：① 手写 BM25 对高频停用词权重自动降低、对稀有核心词权重升高；② SM-2 算法在输入评分 `0, 2, 3, 5` 时输出的下次间隔天数与易度因子 (`EF >= 1.3`) 100% 符合公式预期；③ 台词导入后查询结果完整包含前后语境行；",
            "✅ 阶段 2 检索基线与 RRF 对比验收：运行 `python eval/evaluate.py --dataset data/golden_30.json`；首先建立『普通子串匹配 / 单路分词』在 30 条测试集上的 `Recall@5` 与 `MRR` 基线，再对比开启『BM25 + 字符 Bigram RRF 融合』后的实测指标，自动生成 `eval_report.md` 对比表；",
            "✅ 阶段 3 异常输入与交付验收：测试输入空查询、纯特殊符号、超长字符串、以及可选 AI 接口超时/返回非法 JSON 等异常情况，验证程序 100% 优雅回退本地结果且零崩溃。",
        ],
        "difficulty_workload_risks": {
            "difficulty": "入门 → 进阶",
            "workload_range": "总计约 30–42 小时（其中：写出能运行代码约 14 小时，理解 BM25/RRF/SM-2 数学含义+构建 30 条评测集+写 pytest 单测与文档约 20 小时；按每周 8–10 小时计，约 3.5–4.5 周完成）",
            "estimation_premise": "估算依据：全部核心代码均为纯 Python 数据结构与 SQLite 操作，无复杂环境编译问题；误差来源：若对 Python 语法零基础，阶段 0 需扎实花够 6–8 小时做完预热练习。",
            "main_risks": [
                "风险 1：一上来就想调大模型 API 生成花哨的对话，忽略了底层检索与评测 → 应对：阶段 1–2 强制纯离线完成 BM25、RRF 与 `Recall@5` 评测，阶段 3 才可选接入 API；",
                "风险 2：花太多时间手动整理几万条字幕文件 → 应对：先用仓库自带的 500~1,000 条标准双语测试台词跑通全部算法与评测。",
            ],
        },
        "deliverables_and_demos": [
            "1. Python 源码仓库（含 `search/bm25.py`、`search/rrf.py`、`review/sm2.py`、`eval/evaluate.py` 与 `tests/`）；",
            "2. 《30 条动漫/游戏双语查询黄金集：单路检索 vs BM25+Bigram RRF 混合检索 Recall@5 / MRR 实测报告》；",
            "3. 演示截图或录屏（展示台词上下文检索得分拆解、SM-2 复习卡片打分调度与评测脚本输出）。",
        ],
        "prerequisites": "理解哈希表与堆的基本概念，完成阶段 0 的 Python 与 pytest 快速预热。",
        "estimated_effort": "约 30–42 小时（写代码约 14h + 公式理解/构建评测集/单测调试/文档约 20h；每周 8–10 小时约 3.5–4.5 周完成）。",
        "resume_value": (
            "直接命中全库高频的『AI 应用工程 / RAG 检索与评测 (1,186个独立岗位)』与后端搜索工程需求：面试时你能现场手推 BM25 与 RRF 公式并展示自己构建的 30 样本量化评测矩阵，瞬间把“只会调 LangChain API 的调包侠”甩在身后。"
        ),
        "staged_extensions": [
            "阶段 0（准备与自测 · 6–8h）：掌握 Python 字典统计词频、`heapq` 优先队列与 pytest 基础；",
            "阶段 1（最小闭环 MVP · 10–12h）：完成双语字幕入库 SQLite、手写 BM25 倒排检索（带前后句上下文）与 SM-2 间隔复习计算；",
            "阶段 2（核心工程化 · 8–12h）：加入字符 Bigram 第二路检索与 RRF 融合，构建 30 条黄金测试集输出 `Recall@5` / `MRR` 对比报告；",
            "阶段 3（验证与交付 · 6–10h）：可选接入带本地词典引用校验的语法拆解接口，补齐异常回退单测与 README 文档。",
        ],
        "data_vs_inference_boundary": {
            "from_real_data": "LLM/RAG/Agent 检索工程在全库 1,186 个独立岗位中出现，Python、数据结构算法、SQL 与自动化测试均为高频核心约束。",
            "reasonable_inference": "以“动漫/游戏双语台词检索与间隔记忆”作为应用场景是基于个人兴趣的实用化设计，训练的倒排索引与离线评测方法通用于企业知识库搜索、推荐系统与 AI 应用工程。",
        },
        "reference_and_license": {
            "references": [
                "BM25 (Okapi BM25) 算法维基与数学公式说明 (https://en.wikipedia.org/wiki/Okapi_BM25)",
                "SuperMemo SM-2 间隔重复算法官方公开公式 (https://super-memory.com/english/ol/sm2.htm)",
                "Reciprocal Rank Fusion (RRF) 论文与公式简介 (Cormack et al., SIGIR)",
            ],
            "reuse_vs_self_impl": (
                "允许使用 Python 标准库 (`sqlite3`, `math`, `heapq`, `re`) 与 `pytest`；"
                "必须由本人亲手编写：`BM25Index` 倒排表构建与计分、`rrf_merge` 融合排序、`sm2_next_interval` 复习计算及 `recall_at_k` / `mrr` 评测指标函数。"
            ),
        },
        "ai_collab_boundary": {
            "must_do_personally": [
                "亲手编写 `search/bm25.py` 中的倒排表构建、IDF 计算与文档长度归一化打分函数，禁止直接调第三方 BM25 库；",
                "亲手编写 `search/rrf.py` 与 `eval/evaluate.py` 中的 `Recall@K` 和 `MRR` 计算代码；",
                "亲自挑选并标注至少 20~30 条真实查询与标准答案台词 ID，亲自分析至少 2 个检索排名不理想的 Bad Case 并调优参数；",
                "独立编写 SM-2 算法在不同评分边界下的 pytest 单元测试。",
            ],
            "ai_can_accelerate": [
                "生成 `.srt` / `.ass` 字幕时间轴正则表达式解析辅助函数；",
                "协助生成 100~200 条合成双语测试台词数据用于开发初期联调；",
                "辅助美化命令行输出卡片排版。",
            ],
            "understanding_verification": [
                "不看代码向面试官解释：BM25 公式中的 $k_1$（词频饱和度）和 $b$（文档长度归一化）分别解决什么实际问题？如果把 $b$ 设为 0 会发生什么？",
                "解释为什么两路检索结果合并时，用基于名次的 RRF (`1 / (60 + rank)`) 往往比直接把两路原始分数相加更稳健？",
            ],
        },
        "coverage_matrix": {
            "dom_llm_rag_agent": ("完整覆盖", "手写 BM25+Bigram RRF 混合检索、构建 30 条黄金测试集输出 `Recall@5`/`MRR` 评测报告（阶段3可选受控工具校验）", "已完整覆盖 RAG 检索与评测核心要求"),
            "algo_ds_core": ("完整覆盖", "手写倒排索引哈希表、BM25/RRF 排序、SM-2 算法与最小堆到期调度队列", "已完整覆盖核心要求"),
            "lang_python": ("完整覆盖", "使用 Python 完成字幕清洗、检索引擎、复习调度与自动化评测全套模块", "已包含阶段 0 Python 预热阶梯"),
            "db_sql_rdbms": ("完整覆盖", "在 SQLite 中设计台词表（含自关联上下句外键）与复习卡片状态表并实现事务更新", "如需训练多表并发锁，可搭配候选项目六或独立小练习"),
            "test_automation_qa": ("完整覆盖", "包含 12+ 条 pytest 算法边界单测 + 30 样本检索准确性回归门禁", "已完整覆盖核心要求"),
            "db_cache_mq": ("部分覆盖", "阶段 1–2 使用内存最小堆 + SQLite 维护到期队列；可选用 Redis `ZSET` 扩展", "【独立小练习建议】可花 1.5 小时用 Redis `ZADD`/`ZRANGEBYSCORE` 重写到期卡片查询做对比"),
            "net_tcp_http": ("部分覆盖", "可暴露 FastAPI REST 查询接口，但不涉及底层 TCP 字节流协议", "由候选项目一/三互补"),
            "lang_cpp": ("未覆盖", "本项目使用 Python 单一语言实现，未涉及 C++", "若主攻 C++ 方向，请优先选候选项目二或三"),
        },
    },

    # ══════════════════════════════════════════════════════════════════════
    # 候选项目六：个人时间精力预算、复式记账与决策复盘服务（主力：Go (Gin)，可选 Java）
    # ══════════════════════════════════════════════════════════════════════
    {
        "project_id": "proj_life_quest_budget_scheduler",
        "title": "候选项目六：《支持并发防超支与幂等入账的个人时间精力与复式预算账本服务》",
        "short_title": "个人时间精力与复式预算账本服务 (Go)",
        "interest_theme_tag": "🧭 个人时间精力管理 + 务实预算工具 + 阶段决策复盘 (低成本试水 Go 后端)",
        "tier_priority": "P2_cross_stack_exploration",
        "priority_badge": "🧭 P2 跨语言探索 · 用 1 周从 C++ 平滑切入 Go 企业级后端",
        "difficulty_level": "进阶 (Intermediate)",
        "primary_languages": ["Go (Gin) [进阶可选对比 Java (Spring Boot)]"],
        "skill_ids": ["lang_golang", "db_sql_rdbms", "conc_multithread", "net_tcp_http", "test_automation_qa", "db_cache_mq"],
        "target_categories": ["backend_dist", "data_platform", "sys_infra"],
        "target_roles": ["Golang后端研发工程师", "企业级平台/金融科技后端工程师", "Java/通用后端开发工程师"],
        "plain_intro": {
            "what_is_it_called": "支持并发防超支与幂等入账的个人时间精力与复式预算账本服务",
            "who_uses_it": "希望每周务实规划课余学习时间（比如每周只有 15 小时可支配工程时间）和生活费预算、讨厌不切实际的空头计划、希望月底能清楚复盘“时间/钱到底从哪个账户流向了哪个项目”的大学生。",
            "user_problem": "普通记账本或待办清单只记单笔流水，经常算不清“本周剩余可用学习小时数”或“生活费账户余额为什么和明细加起来对不上”；而在后端开发中，当手机和电脑同时点击提交同一笔扣款、或者网络卡顿连点三次“确认扣减 5 小时预算”时，普通无锁代码就会把余额扣成负数或重复入账三次。",
            "what_user_can_do": "运行服务后，用户可以：① 创建自己的『每周可支配时间账户（如 15h）』和『月度生活费预算账户』；② 提交一笔带唯一请求号 (`idempotency_key`) 的时间投入或资金支出，系统自动生成一借一贷两条平衡流水（如：从“本周自由时间池”转入“C++项目开发” 2 小时）并扣减余额；③ 故意用压测脚本同时发起 50 个并发请求抢扣仅剩 10 小时的预算池，验证系统利用数据库乐观锁做到“恰好成功 5 次（每次2h）、其余 45 次安全拒绝、余额绝不变成负数、总账 100% 平衡”；④ 一键生成本周『计划投入 vs 实际投入偏差复盘表』。",
            "visible_outcome": "1个用 Go 语言编写的高可靠账本 REST 服务 + 1套 50 并发抢扣防超支与幂等防重压测脚本 + 1份《无锁脏写 vs 乐观锁 vs 悲观锁实测对比报告》。",
            "why_worth_doing": "后端开发是校招岗位数量最多的方向（全库 1,658 个独立岗位），而『如何防止并发超扣（超卖）、如何保证接口幂等不重复入账、如何做自动对账』是所有银行科技、国央企云、互联网与外企后端一面必问的三大经典母题！用与 C++ 语法极像的 Go 语言来实现这个你自己每周都能用的时间精力账本，既能低成本打开 Go/后端大门，又能拿到最硬核的事务并发证据。",
            "everyday_example": "【给非计算机专业朋友的通俗比喻】：就像给你每周的课余时间和生活费配了一位“银行级防错会计”。普通记账是你随手在纸上划掉两小时，要是你手机和电脑同时点了一下，或者手抖连按三次提交，纸上就会多扣好几次甚至扣成负数；而这位银行会计每次记账都要求核对防伪流水号（幂等令牌）和账本最新页码（乐观锁版本号），哪怕五十个人同时抢最后十块钱预算，也绝不会多发一分钱，每天晚上还会自动核对总账平不平。",
        },
        "layer1_overview": {
            "what_it_does": "把你每周有限的课余学习时间和生活费当成“预算账户”管理，支持复式记账、连点三次绝不重复扣款（幂等）、并发抢扣绝不超支成负数（乐观锁）以及周度偏差复盘。",
            "final_outcome": "1个 Go (Gin) 编写的账本后端服务 + `go test` 并发抢扣与对账测试套件 + 《无锁 vs 乐观锁并发一致性实测报告》。",
            "v1_first_step": "第一版（MVP）不搞分布式多节点集群，也不必先学庞杂的 Java Spring！直接用语法极简、编译极快的 **Go 语言 + SQLite（或 MySQL）单库** 跑通『创建预算账户 → 复式记账转入转出 → 幂等防重复提交 → 自动对账核验』4 个核心操作。",
            "realistic_time": "总计约 34–46 小时（你有 C++ 基础，花 6–8 小时即可掌握 Go 结构体、指针与协程基础；写代码约 16h + 理解事务隔离/乐观锁原理、写并发压测与文档约 22h，每周 8–10h 约 4–5 周完成）。",
            "what_you_learn": "Go 语言工程开发 (`Gin` + `go test`)、ACID 数据库事务、乐观锁 (`version` 版本号) 与悲观锁防并发超扣、接口幂等性设计 (`Idempotency-Key` 唯一索引)、复式记账对账模型。",
            "biggest_challenge": "深刻理解为什么高并发下『先 `SELECT` 查余额够不够、再 `UPDATE` 扣余额』一定会产生超扣 Bug，并通过亲手编写并发竞争测试复现这个 Bug 再用乐观锁修好它。",
        },
        "core_purpose": (
            "针对跨语言探索需求，以“个人时间精力与财务复式预算账本”为实用载体，用最小学习成本从 C++ 切入 Go 后端开发，集中攻克后端面试最核心的『数据库事务、乐观锁防并发超扣与接口幂等性』。"
        ),
        "why_matches_interest": (
            "直接呼应 profile.yaml 中『希望计划务实，不喜欢脱离现实的学习时间表』与『主动思考人生选择与时间分配』的特点。"
        ),
        "tech_selection_rationale": {
            "primary_stack": "Go 1.21+ (`Gin` 框架 + 标准库 `database/sql` + `go test`) + SQLite 3（阶段1默认） / MySQL 8.0（阶段2进阶）",
            "why_chosen": "在跨语言探索后端时，Java Spring Boot 需要背诵大量注解、XML 与 Maven 配置；而 Go 语言保留了你熟悉的 C/C++ 结构体 (`struct`)、指针与静态类型思维，编译只需 1 秒，自带 `go test -race` 竞态检测器，是最适合 C++ 背景大二学生低成本拓展后端赛道的语言。",
            "restraint_rules_applied": [
                "不再同时塞入 Go 和 Java：本项目明确以 **Go 语言** 为默认唯一实现语言（仅在说明中列出 Java 对照概念，绝不要求你同时写两套语言）；",
                "第一版不引入分布式锁与多节点：单数据库实例配合唯一索引 (`UNIQUE KEY`) 和乐观锁 (`version`) 就能 100% 解决幂等与防超支问题，阶段 1–2 绝不为了凑名词强上 Redis 分布式集群；",
                "聚焦账本内核：只做 4 个核心 REST 接口与命令行/测试验证，不做复杂图表前端。",
            ],
            "progression_tiers": [
                {
                    "tier": "基础版（阶段 1 MVP 必做 · 单机事务与幂等闭环）",
                    "stack": "Go + Gin + SQLite (开启 WAL 事务模式) + 唯一索引幂等控制 + `go test`",
                    "why_needed": "无需安装配置外部数据库服务，即可完整跑通『创建预算账户 → 带幂等键记账 → 借贷流水自动对账 → 单元测试验证』闭环。",
                },
                {
                    "tier": "进阶版（阶段 2 核心工程化 · 50 并发抢扣与乐观锁对比）",
                    "stack": "MySQL 8.0（或 SQLite 事务）+ 乐观锁 (`UPDATE ... WHERE id=? AND version=? AND balance>=?`) + Go Goroutine 并发压测",
                    "why_needed": "使用 50 个 Goroutine 同时抢扣同一预算账户，先复现朴素『先读后写』导致的负余额超扣现象，再实测乐观锁与原子条件更新如何 100% 根治超扣。",
                },
                {
                    "tier": "更进一步（阶段 3 可选扩展 · Redis 幂等缓存或定时复盘锁）",
                    "stack": "Redis `SET NX PX` 幂等前置拦截（仅在需要体验缓存中间件时选配）",
                    "why_needed": "在流量打到数据库之前先在内存层拦截重复重试请求；若不装 Redis，数据库 `idempotency_key` 唯一约束已足够保证正确性。",
                },
            ],
            "prerequisites": "你已具备 C++ 基础（结构体、指针、函数），阶段 0 仅需花 6–8 小时通过官方 Tour of Go 掌握 Go 切片 (`slice`)、`error` 显式错误处理与 `goroutine` 基础。",
        },
        "skill_classification_breakdown": {
            "job_required_skills": ["Go (Golang) 后端开发", "关系型数据库与事务隔离 (MySQL/SQLite)", "多线程/多协程并发同步", "计算机网络与 RESTful 接口幂等", "单元测试与竞态检测 (`go test -race`)"],
            "project_essential_skills": ["Go 语言与 Gin 接口开发 (`lang_golang`, `fw_web_py_go`)", "ACID 事务、复式记账与乐观锁防超支 (`db_sql_rdbms`)", "Goroutine 并发压测与 `-race` 竞态验证 (`conc_multithread`)", "接口幂等设计与自动化对账测试 (`net_tcp_http`, `test_automation_qa`)"],
            "later_extension_skills": ["Redis 分布式锁与前置幂等缓存 (`db_cache_mq`)", "Java Spring Boot 横向对照（仅作概念映射，不混写）"],
            "standalone_exercise_skills": ["C++ 桌面渲染与 Linux epoll（由候选项目二/三覆盖）"],
        },
        "natural_tech_stack_usage": (
            "• 数据库 ACID 事务与复式流水表（`internal/ledger/`）：每笔时间或资金消耗在一个事务内同时插入借方流水、贷方流水并更新账户余额，保证任何时刻 `SUM(debit) == SUM(credit)`；\n"
            "• 乐观锁版本号 `version`：解决并发请求同时读取旧余额导致“10小时预算被扣成 -6 小时”的超卖问题；\n"
            "• `idempotency_key` 唯一索引：解决客户端因网络超时自动重试 3 次导致同一笔“学习 2 小时”被重复记账 3 次的问题；\n"
            "• Go `sync.WaitGroup` + `go test -race`：用 30 行 Go 原生代码就能启动 50 个并发协程对接口发起真实竞争测试。"
        ),
        "project_goal": (
            "使用 Go 语言实现一个具备复式记账事务一致性、乐观锁防并发超支、唯一令牌幂等防重与周度偏差复盘的轻量级后端服务，并通过 50 并发抢扣实测验证。"
        ),
        "core_features": [
            "1. 时间精力与资金预算账户管理：支持创建每周可支配时间池（如 15 小时）或月度资金池，设置预算上限与预警线；",
            "2. 带幂等令牌 (`Idempotency-Key`) 的复式记账接口：同一 `idempotency_key` 重复提交 10 次仅入账 1 次，且每次记账自动生成配平的借贷流水；",
            "3. 朴素无锁 vs 乐观锁并发扣减对照实验：提供实验模式复现“先查后改”并发超扣 Bug，并验证乐观锁 (`version`) 下的零超支；",
            "4. 自动对账巡检 (`Reconcile`) 与周度计划偏差报表：一键校验所有账户余额与历史流水总和是否 100% 相等，输出计划投入 vs 实际投入偏差率。",
        ],
        "mvp_scope": {
            "problem_solved": "解决个人时间/资金预算记账容易算错总账、以及后端接口在网络重试和并发点击下出现重复记账与负余额超支的问题。",
            "core_operations": [
                "操作 1（创建预算账户）：调用 `POST /api/accounts` 创建『本周工程学习时间池（初始额度 15.0 小时）』与『C++项目投入账户（初始 0.0 小时）』；",
                "操作 2（幂等复式记账）：调用 `POST /api/transfers` 携带请求头 `Idempotency-Key: req-001` 从时间池划拨 2.0 小时到项目账户，连续重复发送 3 次相同请求，验证只扣减 1 次（剩余 13.0 小时）；",
                "操作 3（并发防超支抢扣验证）：运行 `go test -v -run TestConcurrentOverdraft` 启动 50 个协程同时抢扣仅剩 10 小时的账户（每次扣 2 小时），验证最终成功恰好 5 次、失败 45 次、期末余额严格为 `0.0`；",
                "操作 4（总账自动对账与周复盘）：调用 `GET /api/reconcile` 返回 `is_balanced: true` 及本周各分项时间投入占比。",
            ],
            "inputs": "账户初始化 JSON、转账/记账请求 JSON（含源账户、目标账户、额度、`idempotency_key`、备注）。",
            "outputs": "含最新版本号 `version` 与流水号的记账凭据 JSON、并发抢扣统计结果（成功数/冲突拒绝数/期末余额）、以及对账平衡报告。",
            "display_method": "终端 `curl` / `go test -v` 输出 + 轻量 REST JSON 响应。",
            "out_of_scope_initially": [
                "第一版明确不做：跨行清算、真实第三方支付对接或复杂的货币汇率换算；",
                "第一版明确不做：多机器节点分布式定时任务抢占集群（单机先把事务、乐观锁和幂等吃透）；",
                "第一版明确不做：复杂的移动端 App 界面。",
            ],
            "closed_loop_proof": "执行 `go test ./internal/ledger/... -v -race`：自动完成『初始化 10 小时预算账户 → 并发发起 30 次 2 小时扣减请求 + 5 次相同幂等键重试请求 → 断言成功入账恰好 5 笔、期末余额为 0、借贷流水总和差值为 0、且 Go Race Detector 报告 0 数据竞争』。",
            "must_have": [
                "完成 `accounts`、`journal_entries`（复式流水）、`idempotency_records` 三张表与事务封装；",
                "实现基于 `version` 字段的乐观锁扣减与 `idempotency_key` 幂等去重；",
                "编写并发抢扣对照测试（复现无锁超扣 vs 验证乐观锁零超扣）与自动对账函数。",
            ],
            "must_have_mvp": "使用 Go + Gin + SQLite/MySQL 完成 4 个核心操作（建账户、幂等复式记账、50并发乐观锁防超支、自动对账）及 `go test -race` 测试套件。",
            "shrunk_mvp_fallback": "若课业繁忙：全程使用 Go + 单文件 SQLite（无需安装配置 MySQL 与 Redis），仅聚焦『复式记账事务 + 乐观锁并发防超支 + 幂等唯一键』3 个核心测试函数，约 16–20 小时即可完整交付。",
            "estimated_mvp_weeks": "2 周（约 14–16 小时）",
        },
        "engineering_tasks": [
            "复式记账事务建模：在单个数据库事务 (`tx.Begin() ... tx.Commit()`) 中绑定幂等键插入、乐观锁余额更新与借贷双流水写入，任一步骤失败全部回滚；",
            "无锁超扣复现实验：专门写一个 `TransferNaiveNoLock` 对照函数，在 50 并发下亲眼观察并记录余额被扣成 `-20` 的脏写现场，再切换到 `TransferWithOptimisticLock` 验证修复；",
            "自动对账巡检实现：编写 `VerifyLedgerInvariants()`，检查所有账户 `balance >= 0` 且 `SUM(流水变动) == 当前余额 - 初始余额`。",
        ],
        "engineering_challenges": [
            "挑战 1（为什么先 SELECT 再 UPDATE 会超扣）：两个并发事务同时读到 `balance = 2`，各自判断 `2 >= 2` 通过并执行扣减，导致总共扣了 4 却只减成 0 或负数；必须通过带前置条件与版本号的原子更新 `UPDATE accounts SET balance = balance - ?, version = version + 1 WHERE id = ? AND version = ? AND balance >= ?` 并检查 `RowsAffected == 1` 解决；",
            "挑战 2（幂等检查与业务写入的原子性）：如果先查 `idempotency_key` 不存在，还没来得及写入时另一个相同请求也查到不存在怎么办？必须利用数据库 `UNIQUE` 唯一索引约束在同一个事务里原子拦截。",
        ],
        "knowledge_mapping": [
            "数据库事务与并发控制：ACID 四大特性、脏读/不可重复读/丢失修改 (Lost Update)、乐观锁 (`version`) 与悲观锁 (`SELECT ... FOR UPDATE`) 的适用场景对比；",
            "分布式与接口可靠性基础：HTTP 接口幂等性语义、超时重试副作用防护、复式记账平衡不变量；",
            "Go 语言工程基础：Goroutine 并发模型、`sync.WaitGroup`、`database/sql` 事务生命周期、`go test -race` 竞态检测。",
        ],
        "acceptance_criteria": [
            "✅ 阶段 1 幂等与总账平衡验收：运行 `go test -v -run TestIdempotentTransfer`；使用相同 `Idempotency-Key` 并发发送 10 次扣减 3 小时的请求，验证数据库仅生成 1 对借贷流水、账户余额仅扣减 3 小时，且 `VerifyLedgerInvariants()` 返回 `balanced: true`；",
            "✅ 阶段 2 并发防超扣对照验收：运行 `go test -v -run TestOptimisticLockVsNaive`；初始账户余额设为 `10`，启动 50 个并发协程各扣减 `2`：① 在 `Naive` 无锁模式下复现超扣（成功次数 > 5 或余额不一致）；② 在乐观锁模式下验证成功次数严格等于 `5`、冲突拒绝 `45` 次、期末余额严格等于 `0`；",
            "✅ 阶段 3 竞态与异常注入验收：执行 `go test ./... -race` 全部通过（0 Data Race）；并测试事务中途故意注入 SQL 失败，验证幂等记录与账户余额 100% 原子回滚，不留半截脏数据。",
        ],
        "difficulty_workload_risks": {
            "difficulty": "进阶",
            "workload_range": "总计约 34–46 小时（其中：阶段 0 学习 Go 基础语法约 6–8 小时，写出账本服务与并发测试代码约 14 小时，理解事务隔离与乐观锁原理+调试并发测试+整理文档约 18 小时；按每周 8–10 小时计，约 4–5 周完成）",
            "estimation_premise": "估算依据：你有 C++ 语法基础，学习 Go 语言结构体、指针和基础语法非常快；误差来源：初次理解数据库事务回滚与并发锁竞争可能需要多跑几次对照实验来建立直观体感。",
            "main_risks": [
                "风险 1：同时去学 Go 和 Java Spring Boot 导致精力分散 → 应对：严格锁定 Go 语言完成全部代码，Java 仅在面试准备时了解概念映射即可；",
                "风险 2：过早引入多节点分布式定时任务导致调试困难 → 应对：先把单机数据库事务、乐观锁和幂等测试做到 100% 扎实。",
            ],
        },
        "deliverables_and_demos": [
            "1. Go (Gin) 源码仓库（含 `internal/ledger/`、`cmd/server/` 与 `ledger_test.go` 并发对照测试）；",
            "2. 《50 并发抢扣实验：无锁丢失更新复现 vs 乐观锁与唯一索引幂等实测报告》；",
            "3. 终端演示录屏（展示并发抢扣测试输出、幂等重试拦截与自动对账平衡校验）。",
        ],
        "prerequisites": "掌握 C++ 基础语法与基本 SQL 增删改查概念（阶段 0 提供 6–8 小时从 C++ 到 Go 的快速过渡指南）。",
        "estimated_effort": "约 34–46 小时（含 6–8h Go 快速上手 + 14h 编码 + 18h 事务并发实验/单测/文档；每周 8–10 小时约 4–5 周完成）。",
        "resume_value": (
            "帮你低成本解锁全库体量最大的『后端开发与分布式系统 (1,658个独立岗位)』赛道，且能针对后端面试必考的“防超卖/乐观锁/接口幂等/事务回滚”拿出自己亲手跑出的 50 并发对照测试数据。"
        ),
        "staged_extensions": [
            "阶段 0（准备与自测 · 6–8h）：利用 C++ 基础快速过一遍 Go 语法（`struct`、`slice`、`error`、`goroutine`），跑通 1 个 40 行 `go test` 小练习；",
            "阶段 1（最小闭环 MVP · 10–12h）：完成账户表、复式流水表、唯一索引幂等记账接口与总账平衡校验；",
            "阶段 2（核心工程化 · 10–14h）：编写 50 并发抢扣对照实验（无锁复现超扣 vs 乐观锁 `version` 零超扣）与周度计划偏差统计接口；",
            "阶段 3（验证与交付 · 6–10h）：测试事务中途异常回滚与 `go test -race` 竞态检查，可选引入 Redis 前置幂等缓存并完成复盘文档。",
        ],
        "data_vs_inference_boundary": {
            "from_real_data": "后端开发与分布式系统在全库占 1,658 个独立岗位，其中关系型数据库事务与索引、Go/Java 服务端语言、多线程/协程并发与自动化测试均为真实高频要求。",
            "reasonable_inference": "以“个人时间精力与生活费复式账本”作为业务场景是基于你个人画像的实用化设计，用小而精的账本内核训练金融级事务与并发一致性，避免空洞庞大商城项目。",
        },
        "reference_and_license": {
            "references": [
                "A Tour of Go 官方交互式教程 (BSD-3-Clause, https://go.dev/tour/)",
                "Go `database/sql` 事务处理官方文档 (https://go.dev/doc/database/execute-transactions)",
                "Gin Web Framework 官方文档 (MIT License, https://gin-gonic.com/)",
            ],
            "reuse_vs_self_impl": (
                "允许正常使用 Gin 路由框架與 Go 标准库 `database/sql`、`sync`；"
                "必须由本人亲手编写：复式记账事务 SQL、乐观锁版本号更新与冲突判断、幂等键唯一约束处理及 50 并发抢扣测试代码。"
            ),
        },
        "ai_collab_boundary": {
            "must_do_personally": [
                "亲手编写带 `version` 乐观锁与 `balance >= amount` 条件的事务更新代码，并亲自检查 `RowsAffected()` 返回值；",
                "亲手编写 `TestConcurrentOverdraft` 并发抢扣测试，先跑出无锁版超扣 Bug，再验证乐观锁修复结果；",
                "独立调试至少 1 次事务未正确回滚 (`defer tx.Rollback()`) 或数据库连接池并发争用问题；",
                "能够在不依赖 AI 的情况下解释：为什么即使加了乐观锁，依然需要 `idempotency_key` 唯一索引来防止网络超时重试导致的重复扣款？",
            ],
            "ai_can_accelerate": [
                "把你熟悉的 C++ 类与函数写法对照翻译成符合 Go 习惯的 `struct` 与方法定义；",
                "生成 Gin HTTP 路由绑定与 JSON 参数解析的样板代码；",
                "辅助生成用于周度复盘统计的模拟记账种子数据。",
            ],
            "understanding_verification": [
                "画出时序图解释：协程 A 和协程 B 在同一毫秒读取到 `balance=10, version=1` 并各自尝试扣减 `8` 时，数据库层面发生了什么？谁成功、谁失败？",
                "解释复式记账中为什么任何一笔时间或资金划拨都必须同时写一借一贷两条记录，这给自动化对账带来了什么好处？",
            ],
        },
        "coverage_matrix": {
            "lang_golang": ("完整覆盖", "使用 Go 语言、`database/sql`、`goroutine` 与 `go test -race` 实现完整账本服务（含阶段 0 C++ 转 Go 预热）", "已完整覆盖核心要求"),
            "db_sql_rdbms": ("完整覆盖", "实现 ACID 事务回滚、复式流水表、乐观锁 (`version`) 防超扣与唯一索引幂等控制", "已完整覆盖事务与并发控制核心要求"),
            "conc_multithread": ("完整覆盖", "编写 50 并发协程抢扣实验并通过 `go test -race` 零竞态验证", "已完整覆盖核心要求"),
            "net_tcp_http": ("完整覆盖", "实现 RESTful 记账接口、`Idempotency-Key` 幂等语义与冲突状态码 (`409 Conflict`) 规范", "已完整覆盖核心要求"),
            "test_automation_qa": ("完整覆盖", "包含无锁 vs 乐观锁对照测试、幂等重试测试、事务中断回滚测试与对账不变量断言", "已完整覆盖核心要求"),
            "fw_web_py_go": ("完整覆盖", "使用 Go Gin 框架实现路由、中间件与结构化错误处理", "已完整覆盖核心要求"),
            "db_cache_mq": ("部分覆盖", "阶段 1–2 基于数据库唯一索引与乐观锁闭环；阶段 3 可选引入 Redis `SET NX PX` 前置幂等锁", "【独立小练习建议】可花 1.5 小时用 `go-redis` 写 1 个 60 行 Redis 原子限流/幂等小练习"),
            "lang_java": ("未覆盖", "为避免多语言混杂加重负担，本项目默认使用 Go 实现，未直接编写 Java 代码", "【独立补充建议】本项目中的事务、乐观锁与幂等设计与 Java Spring Boot (`@Transactional`) 原理 100% 互通"),
            "lang_cpp": ("未覆盖", "本项目专门用于低成本跨语言体验 Go 后端，不使用 C++", "与候选项目二/三互补"),
        },
    },
)

PROJECT_RECOMMENDATION_CATALOG = PROJECT_BLUEPRINT_CATALOG
_BLUEPRINT_BY_ID = {bp["project_id"]: bp for bp in PROJECT_BLUEPRINT_CATALOG}


# ──────────────────────────────────────────────────────────────────────────
# 6.5 为每个候选项目构建标准「技能覆盖审计表 (skill_coverage_table)」并严格核验一致性
# ──────────────────────────────────────────────────────────────────────────
VALID_COVERAGE_LEVELS = {"完整覆盖", "部分覆盖", "未覆盖"}


def _build_project_skill_coverage_table(
    blueprint: dict,
    skill_constraints: list[dict],
    overall_skills_by_id: dict[str, dict],
) -> list[dict]:
    """根据真实岗位技能约束清单，生成该项目的技能覆盖审计表（严格限定 完整覆盖 / 部分覆盖 / 未覆盖 三档，并提供具体模块与验收证据）。"""
    cov_map: dict[str, tuple[str, str, str]] = blueprint.get("coverage_matrix") or {}
    rows: list[dict] = []
    seen_sids: set[str] = set()
    accept_list = blueprint.get("acceptance_criteria") or []

    def _pick_verification_evidence(sid: str, level: str, task_desc: str) -> str:
        if level != "完整覆盖":
            return "未达完整覆盖标准（仅部分训练或未涉及，不虚报通过）"
        # 根据技能 ID 匹配最具体的阶段验收步骤
        if sid in ("db_sql_rdbms", "db_cache_mq") and len(accept_list) >= 2:
            return accept_list[1]
        if sid in ("test_automation_qa", "test_debug_profile", "tool_docker_k8s") and len(accept_list) >= 3:
            return accept_list[2]
        return accept_list[0] if accept_list else f"通过自动化单元测试验证：{task_desc}"

    # 1. 先按全局 Top 技能约束清单逐项审计，确保所有核心就业要求都有明确交代（不遗漏、不因名字出现而虚报完整覆盖）
    for sc in skill_constraints:
        sid = sc["skill_id"]
        seen_sids.add(sid)
        if sid in cov_map:
            level, task_desc, supp_advice = cov_map[sid]
        else:
            level = "未覆盖"
            task_desc = "当前项目未包含该技能的实际编码任务与可验证成果"
            supp_advice = f"【独立小练习建议】无需强行塞入当前项目；若投递要求『{sc['skill_name']}』的岗位，建议通过 2–3 小时独立小练习或互补候选项目补齐"
        if level not in VALID_COVERAGE_LEVELS:
            level = "部分覆盖"
        # 防虚报守则：如果 task_desc 为空或写着“未涉及/不直接涉及”，绝不允许标记为完整覆盖
        if level == "完整覆盖" and (not task_desc or "未覆盖" in task_desc or "不直接涉及" in task_desc):
            level = "未覆盖"

        rows.append({
            "constraint_id": sc["constraint_id"],
            "skill_id": sid,
            "skill_name": sc["skill_name"],
            "requirement_type_label": sc["requirement_type_label"],
            "job_frequency_str": sc["frequency_and_sample"]["summary"],
            "source_categories": "、".join(sc["source_categories"][:3]),
            "project_task_mapping": task_desc,
            "coverage_level": level,
            "verification_criteria": _pick_verification_evidence(sid, level, task_desc),
            "supplementary_training": supp_advice,
        })

    # 2. 补充该项目自身专门覆盖但不在全局 Top 14 的方向技能（如 Qt、OpenGL、Go 等）
    for sid, (level, task_desc, supp_advice) in cov_map.items():
        if sid in seen_sids:
            continue
        seen_sids.add(sid)
        s_stat = overall_skills_by_id.get(sid)
        cname = s_stat["canonical_name"] if s_stat else (_SKILL_BY_ID[sid][1] if sid in _SKILL_BY_ID else sid)
        freq_str = (
            f"{s_stat['unique_job_count']} 独立岗 ({s_stat['unique_job_pct']}%)"
            if s_stat else "方向专项技能"
        )
        src_cats = "、".join((s_stat.get("source_categories") or ["对应方向岗位"])[:3]) if s_stat else "对应细分方向岗位"
        norm_level = level if level in VALID_COVERAGE_LEVELS else "部分覆盖"
        rows.append({
            "constraint_id": "SC-DIR",
            "skill_id": sid,
            "skill_name": cname,
            "requirement_type_label": s_stat["tier_role_label"] if s_stat else "方向核心技能",
            "job_frequency_str": freq_str,
            "source_categories": src_cats,
            "project_task_mapping": task_desc,
            "coverage_level": norm_level,
            "verification_criteria": _pick_verification_evidence(sid, norm_level, task_desc),
            "supplementary_training": supp_advice,
        })

    for r in rows:
        r.setdefault("source_categories_str", r.get("source_categories", ""))
        r.setdefault("requirement_type", r.get("requirement_type_label", ""))
        r.setdefault("supplementary_advice", r.get("supplementary_training", "建议结合独立小练习补足"))
    return rows


def compute_consistent_coverage_summary(coverage_table: list[dict] | None, total_expected: int = 0) -> dict:
    """根据技能覆盖明细表实时计算汇总统计，确保统计数字与明细表 100% 一致；若表缺失则明确标记『未知/待补充』而非 0 项。"""
    if not isinstance(coverage_table, list) or len(coverage_table) == 0:
        return {
            "audit_status": "unknown_or_pending",
            "full_count": None,
            "partial_count": None,
            "uncovered_count": None,
            "unovered_count": None,
            "total_audited": 0,
            "summary_badge": "⚠️ 技能覆盖审计：未知 / 待补充（明细表缺失，拒绝虚报 0 项完成）",
            "status_label": "⚠️ 技能覆盖审计：未知 / 待补充（明细表缺失，拒绝虚报 0 项完成）",
        }
    full_c = sum(1 for r in coverage_table if r.get("coverage_level") == "完整覆盖")
    part_c = sum(1 for r in coverage_table if r.get("coverage_level") == "部分覆盖")
    none_c = sum(1 for r in coverage_table if r.get("coverage_level") == "未覆盖")
    return {
        "audit_status": "verified",
        "full_count": full_c,
        "partial_count": part_c,
        "uncovered_count": none_c,
        "unovered_count": none_c,
        "total_audited": len(coverage_table),
        "summary_badge": f"完整覆盖 {full_c} 项 · 部分覆盖 {part_c} 项 · 未覆盖 {none_c} 项（与明细表严格一致）",
        "status_label": f"完整覆盖 {full_c} 项 · 部分覆盖 {part_c} 项 · 未覆盖 {none_c} 项（与明细表严格一致）",
    }


def recommend_projects_for_skills(
    skill_stats: dict,
    profile: dict | None = None,
    skill_constraints: list[dict] | None = None,
) -> dict:
    """结合真实岗位高频技能统计、技能约束清单与个人画像（含兴趣主题与探索模式），生成候选项目与覆盖审计表。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    prof_map = extract_skill_proficiency_map(prof)
    rec_mode = str(prof.get("recommendation_mode") or "exploration").strip().lower()
    constraints = skill_constraints if isinstance(skill_constraints, list) else build_skill_constraints_checklist(skill_stats)

    overall_skills: dict[str, dict] = {
        s["skill_id"]: s
        for s in (skill_stats.get("overall", {}).get("all_skills") or skill_stats.get("overall", {}).get("top_skills") or [])
    }
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
                _, cname, dim_key, _, _, _, _ = _SKILL_BY_ID[sid]
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
        if pid == "proj_game_tactical_sandbox_replay":
            skill_base_note = f"【当前技能基础】直接复用你正在学习的 C++ ({cpp_info.get('level', 'basic')})、数据结构算法与计算机基础。"
            learning_cost_note = "【学习成本：低~中低】阶段 1 纯单机 C++17 零新框架负担；阶段 2 仅需补充 Linux Socket/epoll 与 WAL 文件格式。"
        elif pid == "proj_live_shader_particle_desk":
            skill_base_note = "【当前技能基础】直接复用你已有的 C++ 基础与小型 OpenGL 着色器项目经验。"
            learning_cost_note = "【学习成本：中低】渲染管线已有现成经验，阶段 0 仅需花 6–8 小时掌握 Qt6 信号槽与 GoogleTest。"
        elif pid in ("proj_anime_4d_collection_vault", "proj_gamedev_qa_chaos_harness", "proj_jp_media_immersion_agent"):
            if py_not_learned:
                skill_base_note = "【当前技能基础】你在 profile.yaml 中声明 Python『本人还没开始学习』，但已具备 C++ 编程与数据结构基础。"
                learning_cost_note = "【学习成本：中低（含阶段0预热）】严禁假设你已掌握 Python 工程流，已在阶段 0 专门配置 6–8 小时 Python 语法与 pytest 预热检查。"
            else:
                skill_base_note = "【当前技能基础】可结合 Python/C++ 基础与 SQL/网络概念开展。"
                learning_cost_note = "【学习成本：低~中低】阶段 1 均从内置 SQLite 或本地文件最小闭环起步，零复杂中间件环境阻力。"
        else:
            skill_base_note = "【当前技能基础】属于跨语言探索项目（锁定 Go 语言实现），可平滑迁移你的 C++ 结构体与指针思维。"
            learning_cost_note = "【学习成本：中】阶段 0 预留 6–8 小时完成 Tour of Go 核心语法与 `go test` 自测，再进入账本事务开发。"

        interest_fit_note = (
            f"【工作内容与兴趣主题适配】主题源自『{bp['interest_theme_tag']}』，"
            "先用通俗语言明确日常痛点与 4 个最小核心操作，再按『基础版 → 进阶版』克制选型，拒绝堆砌技术名词。"
        )

        coverage_table = _build_project_skill_coverage_table(bp, constraints, overall_skills)
        cov_summary = compute_consistent_coverage_summary(coverage_table)

        skill_training_locations = [
            f"• {r['skill_name']}（{r['coverage_level']}）：{r['project_task_mapping']}"
            for r in coverage_table
            if r["coverage_level"] in ("完整覆盖", "部分覆盖")
        ]

        gaps_bridged = [
            f"补齐『{r['skill_name']}』实战证据（覆盖 {r['job_frequency_str']}）"
            for r in coverage_table
            if r["coverage_level"] == "完整覆盖"
        ][:4]

        raw_pi = copy.deepcopy(bp.get("plain_intro") or {})
        raw_pi.setdefault("q1_what_is_it", raw_pi.get("what_is_it_called", ""))
        raw_pi.setdefault("q2_who_uses_it", raw_pi.get("who_uses_it", ""))
        raw_pi.setdefault("q3_what_problem", raw_pi.get("user_problem", ""))
        raw_pi.setdefault("q4_what_can_user_do", raw_pi.get("what_user_can_do", ""))
        raw_pi.setdefault("q5_visible_outcome", raw_pi.get("visible_outcome", ""))
        raw_pi.setdefault("q6_why_worth_doing", raw_pi.get("why_worth_doing", ""))
        raw_pi.setdefault("everyday_analogy", raw_pi.get("everyday_example", ""))

        raw_l1 = copy.deepcopy(bp.get("layer1_overview") or {})
        raw_l1.setdefault("v1_mvp_first", raw_l1.get("v1_first_step", ""))
        raw_l1.setdefault("estimated_time", raw_l1.get("realistic_time", ""))

        raw_mvp = copy.deepcopy(bp.get("mvp_scope") or {})
        must_list = list(raw_mvp.get("core_operations") or raw_mvp.get("must_have") or bp.get("core_features") or [])[:4]
        raw_mvp["must_have"] = raw_mvp.get("must_have") or must_list
        out_init = raw_mvp.get("out_of_scope_initially") or raw_mvp.get("out_of_scope_for_mvp") or []
        if isinstance(out_init, str):
            out_init = [out_init]
        raw_mvp["out_of_scope_initially"] = out_init
        raw_mvp["out_of_scope_for_mvp"] = out_init
        raw_mvp.setdefault("estimated_mvp_weeks", "1.5–2周")

        raw_ai = copy.deepcopy(bp.get("ai_collab_boundary") or {})
        raw_ai.setdefault("user_must_do", raw_ai.get("must_do_personally") or [])
        raw_ai.setdefault("verification_proof", raw_ai.get("understanding_verification") or [])
        dwr = bp.get("difficulty_workload_risks") or {}
        raw_dvi = copy.deepcopy(bp.get("data_vs_inference_boundary") or {})
        raw_dvi.setdefault("data_backed_facts", raw_dvi.get("from_real_data") or "基于全库真实岗位技能统计")
        raw_dvi.setdefault("reasonable_inferences", raw_dvi.get("reasonable_inference") or "结合个人画像兴趣主题进行的工程化重设计")
        raw_deliv = bp.get("final_deliverables") or bp.get("deliverables_and_demos") or [bp.get("resume_value") or "可一键运行的源码仓库与基准对比文档"]
        if isinstance(raw_deliv, str):
            raw_deliv = [raw_deliv]

        enriched_projects.append({
            **copy.deepcopy(bp),
            "id": bp["project_id"],
            "name": bp["title"],
            "plain_intro": raw_pi,
            "layer1_overview": raw_l1,
            "primary_language_stack": " / ".join(bp.get("primary_languages") or []),
            "mvp_scope": raw_mvp,
            "ai_collab_boundary": raw_ai,
            "data_vs_inference_boundary": raw_dvi,
            "final_deliverables": raw_deliv,
            "main_risks": bp.get("main_risks") or dwr.get("main_risks") or ["注意控制第一阶段范围，优先跑通最小闭环"],
            "bound_skills": bound_skills,
            "bound_categories": bound_categories,
            "covered_unique_jobs": covered_job_sum,
            "representative_companies": covered_companies[:6],
            "skill_training_locations": skill_training_locations,
            "skill_coverage_table": coverage_table,
            "coverage_summary": cov_summary,
            "separation_evaluation": {
                "current_skill_base": skill_base_note,
                "learning_cost": learning_cost_note,
                "interest_alignment": interest_fit_note,
            },
            "gaps_bridged": gaps_bridged,
        })

    practice_advice = {
        "core_philosophy": (
            "sp-v3.0 核心准则：先用普通中文说清楚项目解决什么真实问题、最小可用版本 (MVP) 怎么做，再克制地按『基础版 → 进阶版』引入技术栈；"
            "时间估算严格区分『写出能运行版本』与『理解原理、完成测试、调试排障与整理文档』所需时间；"
            "技能覆盖统计与明细表 100% 保持一致，对未覆盖技能推荐 2–3 小时独立小练习而非把所有技术硬塞进同一个项目。"
        ),
        "exploration_mode_active": rec_mode == "exploration",
        "profile_context_notes": [
            f"当前画像模式：{'探索模式 (Exploration) —— 跨语言、跨方向开放推荐，不将动漫/游戏/绘画兴趣或当前学习的 C++ 硬绑为唯一职业方向' if rec_mode == 'exploration' else '聚焦模式'}",
            f"能力边界诚实核验：C++ 处于『{cpp_info.get('level', 'basic')}』阶段；Python 标注为『{'本人还没开始学习（涉及 Python 的项目均在阶段 0 配置了必会项检查、15分钟自测题与预热小练习）' if py_not_learned else py_info.get('level', 'basic')}』。",
            "技能覆盖诚实审计：严格区分『完整覆盖 / 部分覆盖 / 未覆盖』三档，汇总统计数字与明细表实时同步校验，绝不把空表显示成 0 项缺口。",
        ],
        "phased_roadmap": [
            {
                "phase_key": "P0_foundation_projects",
                "phase_title": "第一梯队候选（通用高保值底座 P0 · 建议大二优先从中选 1 个启动）",
                "rationale": (
                    "根据全库真实岗位统计，『SQL表结构与索引调优 + HTTP接口 + 数据结构算法 + 自动化测试』与『C++17 内存管理 + Qt6桌面客户端 / Linux网络与WAL存储 + GTest/ASan』是覆盖面最广的两大底座。"
                ),
                "recommended_project_ids": ["proj_anime_4d_collection_vault", "proj_live_shader_particle_desk", "proj_game_tactical_sandbox_replay"],
                "how_to_choose": (
                    "• 想做个每天都能用的番剧/游戏设定图谱与多周目进度库，从内置 SQLite 起步补齐 SQL 索引与后端接口 → 选【候选项目一】；\n"
                    "• 想直接升级你现有的 OpenGL 小程序，做出带对象池与四叉树加速的动态壁纸粒子工具 → 选【候选项目二】；\n"
                    "• 喜欢分析战棋局势与探究系统底层，想从单机状态机/WAL 恢复逐步吃透 TCP 拆包与 Linux epoll → 选【候选项目三】。"
                ),
            },
            {
                "phase_key": "P1_direction_deepening",
                "phase_title": "第二梯队候选（方向深化与跨界探索 P1/P2 · 按兴趣自由选择或对比）",
                "rationale": (
                    "若你对二进制存档解构与自动找茬测试（测试开发 SDET）、原版动漫台词手写 BM25 检索（AI/搜索工程）或个人时间精力复式账本（Go 后端事务并发）更感兴趣，可直接选择对应方案。"
                ),
                "recommended_project_ids": ["proj_gamedev_qa_chaos_harness", "proj_jp_media_immersion_agent", "proj_life_quest_budget_scheduler"],
                "how_to_choose": (
                    "• 喜欢拆解二进制存档、做破坏性实验自动抓 C++ 内存漏洞（瞄准高 WLB 外企/大厂 SDET）→ 选【候选项目四】；\n"
                    "• 想结合动漫/游戏台词学外语，亲手写 BM25 倒排索引与 30 样本 Recall@5 量化评测 → 选【候选项目五】；\n"
                    "• 想管理自己每周有限的课余时间与预算，用 1 周从 C++ 切入 Go 语言练透事务与乐观锁防超支 → 选【候选项目六】。"
                ),
            },
        ],
        "anti_pitfalls": [
            "🚫 拒绝“先堆技术名词再拼凑阶段”：所有项目先讲清给谁用、解决什么问题、第一版最小闭环做什么，再按需引入技术；",
            "🚫 拒绝“漂亮假数字验收”：严禁无依据承诺 QPS 提升 5 倍或延迟降低 80%，必须先在本地跑出未优化基线，再实测对比；",
            "🚫 拒绝“大杂烩塞满单个项目”：一个项目无法合理覆盖的技能（如在 C++ 渲染器里硬塞 Redis），一律通过 2–3 小时独立小练习补齐！",
        ],
    }

    return {
        "skill_constraints": constraints,
        "tier_a_skill_overview": skill_stats,
        "tier_b_projects": enriched_projects,
        "candidate_projects": enriched_projects,
        "tier_c_practice_advice": practice_advice,
    }


# ──────────────────────────────────────────────────────────────────────────
# 七、用户选择项目 → 生成分阶段实施计划 (sp-v3.0 导师级 4 阶段 + 双轨时间估算 + 核心技术5问)
# ──────────────────────────────────────────────────────────────────────────
def _build_stage0_readiness_spec(pid: str, uses_python: bool, uses_go: bool, py_not_learned: bool) -> dict:
    """根据用户真实技能差距定制阶段 0（准备与基础检查），不套用千篇一律的模板。"""
    if uses_python and py_not_learned:
        return {
            "needed": True,
            "reason_included": "你在 profile.yaml 中声明 Python『本人还没开始学习』，而本项目使用 Python 作为主力语言，因此必须配置阶段 0 过渡阶梯。",
            "must_know_before": [
                "已掌握 C++ 基础语法（变量、循环、函数、类）与基本数据结构（数组、哈希表/map、树/图概念）；",
                "会使用命令行切换目录与基础 Git 提交 (`git add/commit`)。",
            ],
            "can_learn_along_the_way": [
                "FastAPI 路由装饰器细节与 OpenAPI 文档配置（可在阶段 1 边写接口边查文档）；",
                "SQL `EXPLAIN` 执行计划各列的具体含义（可在阶段 2 跑出慢查询后再对照学习）。",
            ],
            "blocking_gaps_if_missing": [
                "若不了解 Python 字典 (`dict`)、列表 (`list`)、类 (`class`)、类型注解 (`typing`) 与虚拟环境 (`venv`)，进入阶段 1 会频繁被基础语法报错卡住；",
                "若不会运行 `pytest` 编写最基本的 `assert` 断言，将无法验证阶段 1 的最小闭环。",
            ],
            "self_check_mini_test": (
                "【15 分钟准备度小测试】：不依赖 AI 直接生成完整代码，尝试在本地新建 `warmup.py`，写一个带类型注解的函数 "
                "`def count_tag_freq(items: list[dict]) -> dict[str, int]:` 统计作品标签出现次数，并新建 `test_warmup.py` "
                "用 `pytest` 测试正常列表与空列表边界。若能在 15 分钟内独立跑通 `pytest -v` 绿灯，说明已可直接进入阶段 1！"
            ),
            "prep_mini_exercise": (
                "【若自测未通过，先完成此 4 小时预热小练习】：用 Python 重写 1 个你已经用 C++ 写过的数据结构（如包含 `get/put` 的 60 行定长字典缓存或栈括号匹配），"
                "并写出 5 条 `pytest` 测试用例（含 1 条 `with pytest.raises(ValueError):` 异常测试）。"
            ),
            "coding_hours": 3,
            "understanding_testing_debug_doc_hours": 4,
            "total_hours": "6–8 小时（约 0.5–1 周课余时间）",
        }
    if uses_go:
        return {
            "needed": True,
            "reason_included": "本项目属于跨语言探索 Go 后端，你已有 C++ 基础但尚未写过 Go 工程，需先完成语法映射与测试工具检查。",
            "must_know_before": [
                "已掌握 C++ 结构体 (`struct`)、指针与函数调用；",
                "理解关系型数据库最基本的表、行与 `SELECT/INSERT/UPDATE` 概念。",
            ],
            "can_learn_along_the_way": [
                "Gin Web 框架的路由分组与中间件写法；",
                "数据库隔离级别细节与连接池参数配置。",
            ],
            "blocking_gaps_if_missing": [
                "若不了解 Go Module (`go mod init`)、显式错误处理 (`if err != nil`) 与切片 (`slice`)，将无法编译阶段 1 代码；",
                "若不了解 `goroutine` 与 `sync.WaitGroup` 基本用法，阶段 2 无法编写并发抢扣测试。",
            ],
            "self_check_mini_test": (
                "【15 分钟准备度小测试】：新建 `account_test.go`，定义一个 `Account` 结构体并实现 `Deduct(amount int) error`（当余额不足时返回 `errors.New(\"insufficient\")`），"
                "在终端运行 `go test -v` 通过。若 15 分钟内顺利跑通，即可直接开始阶段 1！"
            ),
            "prep_mini_exercise": (
                "【若自测未通过，先完成此 5 小时预热小练习】：跟做官方《A Tour of Go》前 3 章（Basics, Methods, Concurrency），"
                "写一个启动 10 个协程用 `sync.Mutex` 安全累加计数器的 50 行程序并通过 `go test -race`。"
            ),
            "coding_hours": 3,
            "understanding_testing_debug_doc_hours": 4,
            "total_hours": "6–8 小时（约 0.5–1 周课余时间）",
        }
    if pid == "proj_live_shader_particle_desk":
        return {
            "needed": True,
            "reason_included": "你虽有 C++ 和小型 OpenGL 基础，但尚未将核心算法拆分为独立 CMake 静态库接入 GoogleTest，且初次接触 Qt6 信号槽。",
            "must_know_before": [
                "掌握 C++ 类、构造函数/析构函数 (RAII)、`std::vector` 与智能指针基础；",
                "理解 OpenGL 顶点数组 (VAO/VBO) 与着色器编译的基本流程。",
            ],
            "can_learn_along_the_way": [
                "Qt6 `QOpenGLWidget` 的生命周期钩子 (`initializeGL`, `paintGL`, `resizeGL`)；",
                "四叉树递归深度调优与 GLSL 特效数学公式。",
            ],
            "blocking_gaps_if_missing": [
                "若不会写包含 `add_library` 与 `add_executable` 的多目标 `CMakeLists.txt`，会导致核心代码与界面代码耦合、无法跑单元测试；",
                "若不了解 C++ 移动语义或悬空指针风险，粒子高频增删时极易触发段错误 (`SIGSEGV`)。",
            ],
            "self_check_mini_test": (
                "【15 分钟准备度小测试】：不写任何窗口界面，写一个 40 行的 C++ `ParticlePool` 类（预分配 `std::vector<Particle>` 并实现 `spawn()` 与 Swap-and-Pop `kill(index)`），"
                "用 `cmake` + `ctest` 编译并断言活跃粒子数正确。若能独立写完并编译通过，直接进入阶段 1！"
            ),
            "prep_mini_exercise": (
                "【若自测未通过，先完成此 4 小时预热小练习】：配置好本地 CMake + GoogleTest（以及 Qt6 或备选 ImGui），"
                "跑通一个含静态库 `libcore` 和测试程序 `core_test` 的最小 CMake 工程，并练习开启 `-fsanitize=address` 故意触发一次数组越界看懂报错栈。"
            ),
            "coding_hours": 2,
            "understanding_testing_debug_doc_hours": 4,
            "total_hours": "5–7 小时（已有 C++ 与 OpenGL 基础，预热更快）",
        }
    # 默认：proj_game_tactical_sandbox_replay (C++17 Linux)
    return {
        "needed": True,
        "reason_included": "你已具备 C++ 基础与数据结构知识，阶段 1 单机状态机可直接上手；阶段 0 仅需花 4–5 小时确认 WSL2/Linux 下 CMake、GTest 与二进制文件读写就绪。",
        "must_know_before": [
            "掌握 C++ 类设计、`std::vector`、`std::priority_queue` 与定长整数类型 (`uint32_t`, `int32_t`)；",
            "理解图的邻接表示与广度优先搜索 (BFS) / Dijkstra 最短路基本思想。",
        ],
        "can_learn_along_the_way": [
            "CRC32 校验码计算原理与 WAL 预写日志尾部截断处理（可在阶段 1 边写边学）；",
            "Linux 非阻塞 Socket 与 `epoll` 事件循环（留到阶段 2 开始前再学，阶段 1 完全不用管）。",
        ],
        "blocking_gaps_if_missing": [
            "若分不清文本文件读写与二进制按字节读写 (`std::ofstream::binary` / `memcpy`)，在写 WAL 日志与协议帧时会出现字节错位；",
            "若不会在 Linux/WSL2 下使用 `gdb` 或 `-fsanitize=address` 定位崩溃行号，调试指针/游标问题会非常痛苦。",
        ],
        "self_check_mini_test": (
            "【15 分钟准备度小测试】：在 Linux/WSL2 下用 C++17 将一个包含 `{uint32_t turn; int32_t x, y;}` 的结构体按二进制追加写入文件，"
            "再重新打开文件按字节读出并断言字段完全相等，同时开启 `-fsanitize=address` 编译无报错。通过即可直接进入阶段 1！"
        ),
        "prep_mini_exercise": (
            "【若自测未通过，先完成此 4 小时预热小练习】：在 WSL2 Ubuntu 下完成一个 60 行的二维网格 BFS 最短步数查找器 + 二进制存档读写小练习，并用 GTest 验证。"
        ),
        "coding_hours": 2,
        "understanding_testing_debug_doc_hours": 3,
        "total_hours": "4–6 小时（直接衔接现有 C++ 与数据结构基础）",
    }


def _build_stage2_engineering_upgrades(pid: str, custom_txt: str = "") -> list[dict]:
    """为阶段 2（核心技术与工程化）每个新增技术点回答用户要求的 5 个必答问题。"""
    theme_prefix = f"在「{custom_txt}」场景下，" if custom_txt else ""
    if pid == "proj_anime_4d_collection_vault":
        return [
            {
                "feature_name": "升级点 1：多条件组合查询的 B+ 树复合索引与 EXPLAIN 调优",
                "what_problem_it_solves": f"{theme_prefix}当作品与支线记录增长到 5 万条以上时，按『媒介类型 + 标签交集 + 评分排序』查询会触发全表扫描 (`SCAN TABLE` / `ALL`) 和文件排序，单次查询耗时从几毫秒恶化到百毫秒以上。",
                "why_needed_now": "阶段 1 只有几十条手工测试数据，全表扫描也很快；进入阶段 2 注入 5 万条合成数据后，慢查询真实暴露，此时建立复合索引才能直观看到扫描行数下降 90%+ 的真实收益。",
                "knowledge_required": "B+ 树索引最左前缀匹配原则、覆盖索引概念、`EXPLAIN QUERY PLAN` (SQLite) 或 `EXPLAIN` (MySQL) 输出分析。",
                "how_to_test_correctness": "运行 `python scripts/run_benchmark.py`：对比建索引前后同一条 SQL 返回的作品 ID 列表 100% 一致，且 `EXPLAIN` 计划明确显示命中复合索引 `idx_media_score` / `idx_tag_work`。",
                "what_is_lost_if_skipped": "若不做，项目就退化成了普通的增删改查作业，面试时被问到『你的数据库表是怎么建索引的？怎么证明索引生效了？』将无据可答。",
            },
            {
                "feature_name": "升级点 2：高频榜单与统计聚合的 Cache-Aside 缓存与更新失效（手写 LRU 或 Redis）",
                "what_problem_it_solves": f"{theme_prefix}首页或高频接口反复请求『全库 Top 50 高分作品与热门标签分布』，每次都去数据库做多表 `JOIN + GROUP BY` 造成大量重复计算。",
                "why_needed_now": "在阶段 1 跑通基础读写后，阶段 2 需要解决“读多写少”场景下的响应加速与“修改作品评分后缓存脏数据”这一经典工程矛盾。",
                "knowledge_required": "哈希表 + 双向链表实现 O(1) LRU 缓存淘汰、Cache-Aside（旁路缓存：先更新数据库再删除缓存）模式、缓存穿透/击穿防范。",
                "how_to_test_correctness": "编写 `test_cache_invalidation.py`：① 连续查询 2 次榜单断言第 2 次命中缓存 (`cache_hit: true`)；② 调用修改接口将某作品评分从 9.5 改为 6.0；③ 立即第 3 次查询榜单，断言返回最新评分而非旧缓存。",
                "what_is_lost_if_skipped": "会缺失后端/数据岗高频考察的『缓存淘汰算法与双写一致性』实战经验；若时间极紧，可只写 80 行内存 LRU 而暂不装外部 Redis 服务。",
            },
        ]
    if pid == "proj_live_shader_particle_desk":
        return [
            {
                "feature_name": "升级点 1：连续内存对象池 (`ParticlePool`) 与 Swap-and-Pop 回收机制",
                "what_problem_it_solves": f"{theme_prefix}当同屏粒子数达到 3万~5万个且每帧有上千个粒子生成和消亡时，朴素写法逐个 `new/delete` 或遍历含大量空洞的数组会导致严重的内存分配开销与 CPU 缓存未命中 (Cache Miss)。",
                "why_needed_now": "阶段 1 用 1,000 个粒子跑通了画面闭环；阶段 2 将规模推到 30,000+ 粒子建立帧耗时基线后，必须用连续内存池解决 CPU 更新瓶颈。",
                "knowledge_required": "C++ 连续内存布局 (`std::vector` 预分配)、CPU Cache Line 局部性原理、Swap-and-Pop O(1) 尾部交换删除法。",
                "how_to_test_correctness": "运行 `ctest -R ParticlePoolTest` 验证连续分配、满池复用、尾部元素删除边界零越界；运行 `./core_benchmark` 对比优化前后 500 帧平均更新耗时。",
                "what_is_lost_if_skipped": "若不做，粒子数过万就会明显掉帧，且失去了向 C++ 面试官展示你懂『内存布局与缓存友好设计』的最佳亮点。",
            },
            {
                "feature_name": "升级点 2：二维四叉树 (`QuadTree`) 空间索引加速鼠标范围交互",
                "what_problem_it_solves": f"{theme_prefix}当用户移动鼠标对半径 50 像素内的粒子施加斥力或高亮拾取时，朴素做法每帧要对全部 5 万个粒子计算欧氏距离，包含大量无意义的远端粒子计算。",
                "why_needed_now": "有了万级粒子池后，空间范围查询成为新的交互热点，正好用来将课堂上的树形数据结构落地为可视化的空间索引。",
                "knowledge_required": "二维空间递归四分法、轴对齐包围盒 (AABB) 相交测试、树节点容量阈值 (`capacity`) 与最大深度控制。",
                "how_to_test_correctness": "编写 GTest 随机生成 10,000 个坐标点，断言 `QuadTree::queryRange(rect)` 返回的粒子集合与朴素 O(N) 暴力遍历返回的集合 100% 相等（排序后逐一比对 ID）。",
                "what_is_lost_if_skipped": "鼠标交互退化为暴力遍历，缺少核心树形空间数据结构的亲手实现与对比证据。",
            },
        ]
    if pid == "proj_game_tactical_sandbox_replay":
        return [
            {
                "feature_name": "升级点 1：定长帧头+CRC32 二进制协议与 `RingBuffer` TCP 半包/粘包拆包器",
                "what_problem_it_solves": f"{theme_prefix}当从单机沙盒升级为双终端联机对战时，TCP 是没有消息边界的字节流：发送方连续发的 3 条战术指令可能粘成一团到达（粘包），或者 1 条指令被拆成两半先后到达（半包），直接按固定结构体强转会彻底读错崩溃。",
                "why_needed_now": "阶段 1 单机状态机与 WAL 已稳定，阶段 2 引入跨进程通信时，拆包器是保证网络数据不错乱的第一道必修关。",
                "knowledge_required": "TCP 字节流特性、应用层帧协议设计 (`[Magic:2B][Len:2B][Seq:4B][CRC32:4B][Body]`)、环形缓冲区 (`RingBuffer`) 读写游标回绕管理。",
                "how_to_test_correctness": "在 `test_codec.cpp` 中将 1,000 条序列化后的帧拼成大字节数组，分别按每次喂 1 字节、每次喂 3 字节、每次喂 200 字节调用 `decoder.feed()`，断言解码出的指令序列与原序列 100% 一致。",
                "what_is_lost_if_skipped": "若不做网络协议层而只停留在单机命令行，将缺少『计算机网络与 Linux I/O』这一高频校招技能的实战闭环。",
            },
            {
                "feature_name": "升级点 2：Linux 非阻塞 Socket 与 `epoll` 事件循环联机同步",
                "what_problem_it_solves": "传统阻塞式 `recv()` 会让进程卡死在等待单个玩家输入上，无法同时监听多个玩家连接与回合超时定时器。",
                "why_needed_now": "配合写好的 `RingBuffer` 解码器，用 150 行代码实现单线程 `epoll` Reactor 循环，即可让两个终端窗口流畅完成回合指令同步与观战。",
                "knowledge_required": "Linux `fcntl(O_NONBLOCK)`、`epoll_create1` / `epoll_ctl` / `epoll_wait`、非阻塞读写返回 `EAGAIN` 的正确处理。",
                "how_to_test_correctness": "启动服务端后，运行自动化集成测试脚本同时建立 20 个客户端连接并发发送移动/攻击指令帧，验证所有连接收到的广播状态哈希完全一致且无连接假死。",
                "what_is_lost_if_skipped": "会少掉 Linux 系统编程的核心亮点；若在 Windows 本机开发不便使用 WSL2，可退化为使用跨平台 `select`/`poll` 保留协议拆包训练。",
            },
        ]
    if pid == "proj_gamedev_qa_chaos_harness":
        return [
            {
                "feature_name": "升级点 1：穿透式 CRC32 自动重算结构化变异引擎 (Smart Fuzzing)",
                "what_problem_it_solves": f"{theme_prefix}被测存档解析器在入口处有 CRC32 完整性校验。如果普通 Fuzzer 随机翻转文件字节，99.9% 的变异样本都会因为 CRC 不匹配在第一行被拒绝，根本测不到后面的物品数量越界与金币计算逻辑。",
                "why_needed_now": "阶段 1 基础解析跑通后，要真正抓出深层 C++ 内存与整数溢出 Bug，必须让变异器具备“合法伪装”能力。",
                "knowledge_required": "TLV 二进制偏移结构解析、整数边界值（`0, -1, 0x7FFFFFFF, 0xFFFFFFFF`）、`zlib.crc32` 尾部签名重算。",
                "how_to_test_correctness": "运行 `compare_fuzz_modes.py` 统计 500 次变异中进入核心物品解析函数的样本比例：验证开启 CRC 重算后穿透率从 <1% 提升至 100%，并在 10 秒内集齐 5 类预埋漏洞。",
                "what_is_lost_if_skipped": "测试将停留在表层校验，无法证明你理解灰盒/白盒结构化测试的核心思想。",
            },
            {
                "feature_name": "升级点 2：请求重放与弱网丢包故障注入验证接口幂等性",
                "what_problem_it_solves": "当客户端点击“消耗 100 金币强化装备”时遇到网络超时重发了 3 次相同报文，若服务端未做请求号去重，会导致玩家被连扣 300 金币。",
                "why_needed_now": "将本地文件级测试进一步扩展到联机通信可靠性验证，补齐网络容错测试能力。",
                "knowledge_required": "请求唯一标识 (`request_id` / `seq_no`) 去重窗口、超时重传与状态幂等断言。",
                "how_to_test_correctness": "编写 `test_replay_idempotency.py`：向处理模块连续注入 5 个携带相同 `seq_no=1001` 的扣款指令帧，断言金币仅减少 100 且返回相同的成功凭据。",
                "what_is_lost_if_skipped": "缺少网络层面的混沌测试案例；若时间不足可优先保住本地二进制存档 Fuzzing 闭环。",
            },
        ]
    if pid == "proj_jp_media_immersion_agent":
        return [
            {
                "feature_name": "升级点 1：词级 BM25 + 字符二元组 (Bigram) 双路倒数秩融合 (RRF)",
                "what_problem_it_solves": f"{theme_prefix}中日文台词没有天然空格分隔，且日语动词/形容词词尾会活用变形。单靠精确词汇匹配会漏掉变形词，而单靠字符子串匹配又会让不相关的短句排在前面。",
                "why_needed_now": "阶段 1 实现了基础单路 BM25 后，阶段 2 通过构建 30 条黄金测试查询，会真实看到部分变形词查询未排进前 5 名，此时引入双路 RRF 融合能立即通过评测脚本验证召回率提升。",
                "knowledge_required": "字符 N-gram (Bigram) 切分、倒数秩融合公式 $\\text{RRF}(d) = \\sum \\frac{1}{k + \\text{rank}_i(d)}$、离线评测指标 `Recall@5` 与 `MRR`。",
                "how_to_test_correctness": "运行 `python eval/evaluate.py`：对比单路检索与双路 RRF 融合在 30 条黄金测试集上的 `Recall@5` 与 `MRR` 分数，并输出具体改善的查询案例列表。",
                "what_is_lost_if_skipped": "检索质量受限于单一分词策略，且缺少搜索/AI 工程面试最看重的『离线量化评测对比闭环』。",
            },
            {
                "feature_name": "升级点 2：本地词典引用核验与防幻觉回退状态机（可选接入 LLM API）",
                "what_problem_it_solves": "当调用大模型拆解台词语法时，模型有时会编造原句中根本不存在的单词解释，或者因网络波动超时返回残缺 JSON。",
                "why_needed_now": "在检索底座稳固后，用确定性工程手段约束不可靠的模型输出。",
                "knowledge_required": "Pydantic JSON Schema 严格校验、原文子串对齐核验 (Citation Grounding Check)、超时重试与本地词典兜底状态机。",
                "how_to_test_correctness": "在 `test_agent_guard.py` 中使用 Mock 注入『超时异常』『缺失字段的非法 JSON』『包含原句不存在词汇的幻觉响应』3 类输入，验证拦截器 100% 捕获并平滑回退到本地词典解析。",
                "what_is_lost_if_skipped": "若不打算投递 AI Agent 岗位，可跳过此项专注做好纯离线 BM25+RRF 检索与 SM-2 复习调度。",
            },
        ]
    # 默认：proj_life_quest_budget_scheduler (Go)
    return [
        {
            "feature_name": "升级点 1：基于版本号 (`version`) 的数据库乐观锁并发防超扣机制",
            "what_problem_it_solves": f"{theme_prefix}当多个请求在同一毫秒读取到预算账户还剩 2 小时，各自在内存里判断 `2 >= 2` 并执行扣减时，会发生经典的“丢失更新 (Lost Update)”，导致 10 小时预算被 50 个并发请求扣成负数。",
            "why_needed_now": "阶段 1 单线程顺序记账看不出并发漏洞；阶段 2 用 Go 协程启动 50 并发抢扣压测后，无锁代码会立即爆出超扣脏数据，此时引入乐观锁水到渠成。",
            "knowledge_required": "数据库事务隔离级别、乐观锁原子条件更新 `UPDATE accounts SET balance = balance - ?, version = version + 1 WHERE id = ? AND version = ? AND balance >= ?`、`RowsAffected` 冲突检测。",
            "how_to_test_correctness": "运行 `go test -v -run TestOptimisticLockVsNaive`：50 个 Goroutine 同时抢扣初始余额为 10、单次扣 2 的账户，断言成功数严格等于 5、冲突拒绝 45、期末余额严格为 0.0。",
            "what_is_lost_if_skipped": "没有并发抢扣对照实验，账本就只是普通增删改查，失去后端面试最核心的事务与并发控制说服力。",
        },
        {
            "feature_name": "升级点 2：事务内绑定的幂等令牌 (`Idempotency-Key`) 与复式记账自动对账",
            "what_problem_it_solves": "当移动端网络卡顿对同一笔记账请求自动重试 3 次时，乐观锁只能保证按顺序扣 3 次而不超支，但不能识别出“这 3 次其实是同一次操作”，只有幂等唯一键能防止重复扣款。",
            "why_needed_now": "将『防并发超扣（乐观锁）』与『防网络重试多扣（幂等键）』结合，才构成完整的企业级账本可靠性闭环。",
            "knowledge_required": "数据库 `UNIQUE` 唯一约束与业务事务原子绑定、复式记账借贷平衡不变量 (`SUM(debit) == SUM(credit)`)。",
            "how_to_test_correctness": "并发发送 10 个携带相同 `Idempotency-Key: req-100` 的转账请求，断言仅 1 个执行实际扣款，其余 9 个返回首次执行的幂等结果，且 `Reconcile()` 对账差值为 0。",
            "what_is_lost_if_skipped": "无法回答面试官必追问的『乐观锁和幂等性有什么区别？网络超时重试怎么防重？』。",
        },
    ]


def generate_project_implementation_plan(
    project: dict,
    skill_constraints: list[dict] | dict | None = None,
    profile: dict | list[dict] | None = None,
    custom_focus: str = "",
) -> dict:
    # Support both (project, constraints, profile) and (project, profile, constraints)
    if isinstance(skill_constraints, dict) and (isinstance(profile, list) or profile is None):
        prof_arg = skill_constraints
        sc_arg = profile if isinstance(profile, list) else []
        skill_constraints = sc_arg
        profile = prof_arg
    """当用户选定某个候选项目后，生成与其绑定并持久保存的完整分阶段实施计划（sp-v3.0 导师级标准）。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    prof_map = extract_skill_proficiency_map(prof)
    py_not_learned = bool((prof_map.get("python") or {}).get("not_learned_yet", True))

    pid = str(project.get("project_id") or "")
    title = str(project.get("title") or "")
    short_title = str(project.get("short_title") or title)
    mvp_obj = project.get("mvp_scope") or {}
    cov_table = project.get("skill_coverage_table") or []
    ai_bound = project.get("ai_collab_boundary") or {}
    custom_info = project.get("custom_prompt_applied") or {}
    custom_txt = str(custom_info.get("prompt_text") or custom_focus or "").strip()

    langs_str = " ".join(project.get("primary_languages") or [])
    uses_python = "Python" in langs_str
    uses_go = "Go" in langs_str and "Python" not in langs_str

    stage0_spec = _build_stage0_readiness_spec(pid, uses_python, uses_go, py_not_learned)
    stage2_upgrades = _build_stage2_engineering_upgrades(pid, custom_txt)
    accept_list = project.get("acceptance_criteria") or [
        "✅ 阶段 1 验收：执行自动化测试命令，验证输入→处理→保存→重读最小闭环 100% 通过；",
        "✅ 阶段 2 验收：先测量未优化基线耗时，再记录开启核心优化后的实测数据对比；",
        "✅ 阶段 3 验收：注入非法参数、空值与边界异常，验证程序零崩溃并完成复盘文档。",
    ]

    core_ops = mvp_obj.get("core_operations") or mvp_obj.get("must_have") or project.get("core_features") or []

    stages = [
        {
            "stage_no": f"阶段 0（准备与基础检查 · 预计投入 {stage0_spec['total_hours']}）",
            "stage_name": "前置必会知识核对、15 分钟准备度自测与针对性预热练习",
            "goal": f"{stage0_spec['reason_included']} 拒绝盲目啃整本教材，先用 15 分钟小测试判断自己是否可以直接进入阶段 1。",
            "readiness_check": stage0_spec,
            "time_breakdown": {
                "coding_hours": f"写预热代码约 {stage0_spec['coding_hours']} 小时",
                "understanding_testing_debug_doc_hours": f"熟悉工具链、跑通首个单测与调试约 {stage0_spec['understanding_testing_debug_doc_hours']} 小时",
                "total_hours": stage0_spec["total_hours"],
                "prerequisites": "；".join(stage0_spec["must_know_before"]),
                "uncertainties": "若本地编译器/虚拟环境已配好，最快 15 分钟直接跳过；若需首次配置环境或补基础语法，约需 4–8 小时。",
                "early_milestone": "本地终端一行命令成功跑通 1 个含 3 条断言的最小测试文件 (`pytest` / `ctest` / `go test`)。",
                "timeout_reduction": "若在配置重型 IDE 或外部数据库上卡壳超过 2 小时，立即改用轻量编辑器 + 单文件 SQLite/本地文件，绝不在环境配置上内耗。",
            },
            "core_tasks": [
                f"【必会检查】确认已掌握：{ '；'.join(stage0_spec['must_know_before']) }",
                f"【阻塞项排查】重点补齐：{ '；'.join(stage0_spec['blocking_gaps_if_missing']) }",
                stage0_spec["self_check_mini_test"],
                stage0_spec["prep_mini_exercise"],
            ],
            "deliverable": "通过 15 分钟自测题（或 50 行预热小练习）且自动化测试 100% 绿灯的本地开发环境。",
            "acceptance_gate": stage0_spec["self_check_mini_test"],
        },
        {
            "stage_no": "阶段 1（最小可交付闭环 MVP · 预计投入 10–14 小时，约 1.5 周）",
            "stage_name": "先完成『输入数据 → 程序处理 → 保存结果 → 再次读取 → 验证正确性』最小可用闭环",
            "goal": (
                f"第一版解决的核心问题：{mvp_obj.get('problem_solved', '完成最小完整可用闭环')}。"
                + (f"（当前已锚定定制主题：「{custom_txt}」）" if custom_txt else "")
                + " 严禁在此阶段引入非必需的分布式组件或复杂界面。"
            ),
            "mvp_definition": {
                "problem_solved": mvp_obj.get("problem_solved", ""),
                "core_operations": core_ops[:4],
                "inputs": mvp_obj.get("inputs", "结构化 JSON 或命令行参数"),
                "outputs": mvp_obj.get("outputs", "结构化状态响应与持久化文件/表记录"),
                "display_method": mvp_obj.get("display_method", "终端命令行或内置交互面板"),
                "explicitly_out_of_scope": mvp_obj.get("out_of_scope_initially", []),
                "closed_loop_proof": mvp_obj.get("closed_loop_proof", ""),
            },
            "time_breakdown": {
                "coding_hours": "写出能运行的最小闭环代码约 5–6 小时",
                "understanding_testing_debug_doc_hours": "设计数据结构/表字段、编写 8–10 条闭环单测、调试边界报错约 5–8 小时",
                "total_hours": "10–14 小时（按每周投入 8–10 小时计，约 1.5 周内完成）",
                "prerequisites": "通过阶段 0 的 15 分钟准备度自测。",
                "uncertainties": "首次将数据结构与持久化文件/数据库串联时，序列化格式或字段边界可能需要 1–2 次微调。",
                "early_milestone": "【可提前交付的中间成果（第 5 小时）】：哪怕还没写完所有操作，先跑通第 1 个核心操作（写入 1 条数据并成功读出打印）即可作为首个可见里程碑。",
                "timeout_reduction": "若超过 14 小时仍未完成全部 4 个操作，先冻结前 2 个最核心的操作（写入+核心算法查询），把其余操作移入阶段 2。",
            },
            "core_tasks": [
                f"完成核心操作闭环：{' '.join(str(x) for x in core_ops[:3])}",
                f"明确输入与输出契约：输入为『{mvp_obj.get('inputs', '')}』，输出为『{mvp_obj.get('outputs', '')}』，通过『{mvp_obj.get('display_method', '')}』直接查看结果；",
                f"严守不做边界：{'；'.join(mvp_obj.get('out_of_scope_initially') or ['不引入无关中间件'])}",
                f"闭环证明：{mvp_obj.get('closed_loop_proof', '编写自动化测试验证写入后重读一致')}",
            ],
            "deliverable": "无需复杂外部依赖即可独立运行的最小可用版本 (MVP) + 证明『写入→处理→保存→重读』闭环正确的自动化测试。",
            "acceptance_gate": accept_list[0],
        },
        {
            "stage_no": "阶段 2（核心技术与工程化 · 预计投入 12–16 小时，约 1.5–2 周）",
            "stage_name": "在可用闭环之上，针对真实瓶颈逐步引入索引/对象池/拆包/并发机制并做基线对比",
            "goal": "拒绝为了凑简历名词而盲目加组件！每项新增工程能力都必须先复现朴素写法下的真实痛点（慢查询、帧耗时高、粘包、超扣），再实施优化并用实测数据验证。",
            "engineering_upgrades_5q": stage2_upgrades,
            "time_breakdown": {
                "coding_hours": "编写核心优化模块（复合索引/四叉树/RingBuffer/乐观锁/BM25+RRF）代码约 5–6 小时",
                "understanding_testing_debug_doc_hours": "理解底层原理、生成万级测试数据建立基线、调试并发/边界 Bug、记录对比数据约 7–10 小时",
                "total_hours": "12–16 小时（约 1.5–2 周课余时间）",
                "prerequisites": "阶段 1 的最小闭环测试 100% 通过。",
                "uncertainties": "并发竞争复现或性能基准测量可能受本地电脑后台进程干扰，需通过多次采样取中位数/P95 消除误差。",
                "early_milestone": "【可提前交付的中间成果】：完成『升级点 1』并跑出第一张“优化前基线 vs 优化后实测”的命令行对比截图。",
                "timeout_reduction": f"若课业繁忙超时：{mvp_obj.get('shrunk_mvp_fallback', '只完成升级点 1 的核心实验，暂缓升级点 2 的可选扩展组件')}。",
            },
            "core_tasks": [
                f"【{u['feature_name']}】解决痛点：{u['what_problem_it_solves']} → 验证方法：{u['how_to_test_correctness']}"
                for u in stage2_upgrades
            ],
            "deliverable": "完成核心算法/索引/并发升级的工程代码 + 《优化前基线 vs 优化后实测对比数据表》。",
            "acceptance_gate": accept_list[1] if len(accept_list) >= 2 else accept_list[0],
        },
        {
            "stage_no": "阶段 3（验证、排障与交付 · 预计投入 6–10 小时，约 1 周）",
            "stage_name": "异常边界测试、内存/竞态诊断、README 复盘文档与可演示成果整理",
            "goal": "将项目打磨为经得起面试官追问与现场复现的交付物：补齐异常输入防御、清理代码、写出讲人话的 README，并能脱离 AI 独立解释关键设计。",
            "time_breakdown": {
                "coding_hours": "补充异常边界处理与一键运行脚本约 2 小时",
                "understanding_testing_debug_doc_hours": "运行异常/内存检查 (ASan/pytest/-race)、整理架构图与踩坑复盘 README、口述自检演练约 4–8 小时",
                "total_hours": "6–10 小时（约 1 周课余时间）",
                "prerequisites": "阶段 2 核心升级点与基准对比已完成。",
                "uncertainties": "开启 ASan 或跑极端边界输入时，可能会意外抓出 1–2 个之前没注意到的边界 Bug（这正是本阶段最有价值的收获！）。",
                "early_milestone": "【可提前交付的中间成果】：自动化测试套件（含正常路径+异常边界）全绿通过，且完成 README 中的『快速启动与复现步骤』小节。",
                "timeout_reduction": "不需要为了显得工业化而强加 Docker 集群、复杂 CI 或分布式压测；只要本地 3 条命令能一键编译/运行测试并附带真实实测表即视为圆满交付！",
            },
            "core_tasks": [
                "异常情况专项测试：测试空输入、非法格式、超界数值、损坏文件/断连重放等异常，确保程序返回明确错误提示而非直接崩溃；",
                "调试与质量检查：C++ 项目执行 `-fsanitize=address` 内存检查，Python 项目执行 `pytest -v`，Go 项目执行 `go test -race`；",
                "整理可复现 README 与复盘：写明『项目是干什么的、如何 3 步运行、架构数据流图、基线对比实测表、1 个亲自调试解决的真实 Bug 复盘』；",
                "完成『AI 协作边界自检』：闭卷回答下方理解度验证问题，确保简历上的每一行字自己都能讲透。",
            ],
            "deliverable": "干净可复现的 Git 仓库 + 含真实基线对比与排障复盘的 README + 1~2 分钟运行演示录屏/截图。",
            "acceptance_gate": accept_list[2] if len(accept_list) >= 3 else accept_list[-1],
        },
    ]

    time_estimation_summary = {
        "total_hours_range": (project.get("difficulty_workload_risks") or {}).get("workload_range") or project.get("estimated_effort") or "约 34–46 小时",
        "coding_vs_understanding_split": "写出能运行版本约占 40% 时间（约 14–18 小时）；理解底层原理、编写自动化测试、调试排障、测量对比基线与整理文档约占 60% 时间（约 20–26 小时）。",
        "weekly_pace_recommendation": "按大二本科生课业节奏（每周可投入 8–10 小时有效时间）估算，建议按 4–5 周规划；若某周考试周繁忙，可随时冻结在阶段 1 或阶段 2 的中间成果，绝不强制规定所有人必须在同一周数内完成。",
        "estimation_basis_and_error_sources": (
            (project.get("difficulty_workload_risks") or {}).get("estimation_premise")
            or "估算依据：基于已学 C++ 与数据结构基础的本科大二学生实操反馈；主要误差来源：① 初次配置跨语言/跨平台编译环境可能产生 2–4 小时波动；② 排查并发竞争或内存越界 Bug 的耗时具有不确定性（建议单点卡壳超 3 小时即启用缩减方案或利用 AI 提出调试假设）。"
        ),
    }

    problem_driven_learning = [
        {
            "encountered_problem": (project.get("engineering_challenges") or ["多条件查询或大规模数据更新缓慢"])[0],
            "reverse_learning_target": (project.get("knowledge_mapping") or ["数据结构与索引原理"])[0],
            "recommended_material": ((project.get("reference_and_license") or {}).get("references") or ["官方文档与基准测试指南"])[0],
            "practice_verification": "先在阶段 1 用最直观的朴素写法跑通闭环，阶段 2 放大数据规模复现瓶颈并记录基线耗时，再查阅文档实施优化并对比实测结果。",
        },
        {
            "encountered_problem": (project.get("engineering_challenges") or ["并发读写冲突、网络半包或跨线程同步卡死"])[-1],
            "reverse_learning_target": (project.get("knowledge_mapping") or ["操作系统并发与网络通信协议"])[-1],
            "recommended_material": ((project.get("reference_and_license") or {}).get("references") or ["系统编程与调试手册"])[-1],
            "practice_verification": "编写专门的破坏性测试（如 1 字节分片喂入、50 协程抢扣、CRC 重算变异），验证修复前后从报错/脏写变为 100% 绿灯。",
        },
    ]

    cov_summary = compute_consistent_coverage_summary(cov_table)
    full_cov = [r for r in cov_table if r.get("coverage_level") == "完整覆盖"]
    partial_cov = [r for r in cov_table if r.get("coverage_level") == "部分覆盖"]
    none_cov = [r for r in cov_table if r.get("coverage_level") == "未覆盖"]
    partial_or_none = partial_cov + none_cov

    if custom_txt and not custom_info:
        custom_info = evaluate_and_build_prompt_redesign(project, custom_txt, skill_constraints)

    stage0_spec["self_check_test"] = stage0_spec.get("self_check_mini_test", "")
    for upg in stage2_upgrades:
        upg.setdefault("problem_solved", upg.get("what_problem_it_solves", ""))
        upg.setdefault("why_now", upg.get("why_needed_now", ""))
        upg.setdefault("required_knowledge", upg.get("knowledge_required", ""))
        upg.setdefault("how_to_test", upg.get("how_to_test_correctness", ""))
        upg.setdefault("cost_if_skipped", upg.get("what_is_lost_if_skipped", ""))
    for st in stages:
        tb = st.get("time_breakdown") or {}
        st.setdefault("coding_hours", tb.get("coding_hours", ""))
        st.setdefault("understanding_testing_debug_doc_hours", tb.get("understanding_testing_debug_doc_hours", ""))
        st.setdefault("early_deliverable_milestone", tb.get("early_milestone", ""))
        st.setdefault("timeout_scope_reduction", tb.get("timeout_reduction", ""))

    time_estimation_methodology = {
        **time_estimation_summary,
        "estimation_basis": time_estimation_summary.get("estimation_basis_and_error_sources", ""),
        "error_sources": [
            "初次配置跨语言/跨平台编译环境可能产生 2–4 小时波动",
            "排查并发竞争、TCP分片或内存越界 Bug 的耗时具有不确定性",
            "不同同学对 SQL 执行计划或 CMake 静态库拆分的熟悉度差异",
        ],
    }

    return {
        "plan_id": "plan-" + hashlib.sha256(f"{pid}:{datetime.now(_CHINA_TZ).isoformat()}".encode("utf-8")).hexdigest()[:8],
        "stage0_readiness": stage0_spec,
        "stage2_engineering_upgrades": stage2_upgrades,
        "time_estimation_methodology": time_estimation_methodology,
        "plan_version": SKILL_PROJECT_VERSION,
        "project_id": pid,
        "project_title": title,
        "plain_intro": project.get("plain_intro") or {},
        "layer1_overview": project.get("layer1_overview") or {},
        "tech_selection_rationale": project.get("tech_selection_rationale") or {},
        "skill_classification_breakdown": project.get("skill_classification_breakdown") or {},
        "time_estimation_summary": time_estimation_summary,
        "custom_prompt_applied": custom_info,
        "interest_theme_tag": project.get("interest_theme_tag", ""),
        "generated_at": datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
        "custom_focus": custom_txt or "标准 4 阶段工程闭环与可复现验证",
        "custom_focus_applied": custom_txt or "标准 4 阶段工程闭环与可复现验证",
        "stages": stages,
        "deliverables": [s["deliverable"] for s in stages],
        "knowledge_and_materials": (project.get("knowledge_mapping") or []) + ((project.get("reference_and_license") or {}).get("references") or []),
        "problem_driven_learning_path": [
            f"遇到问题：{p['encountered_problem']} → 逆推学习：{p['reverse_learning_target']}（参考：{p['recommended_material']}；验证：{p['practice_verification']}）"
            for p in problem_driven_learning
        ],
        "user_core_tasks": ai_bound.get("must_do_personally") or ai_bound.get("user_must_do") or [],
        "ai_assisted_tasks": ai_bound.get("ai_can_accelerate") or [],
        "verification_tasks": ai_bound.get("understanding_verification") or ai_bound.get("verification_proof") or [],
        "engineering_and_release_standards": [
            "测试与异常处理：每个阶段均须包含正常路径与异常边界单元测试（pytest / GTest / go test），禁止带着未处理的崩溃进入下一阶段；",
            "性能测量原则：禁止无依据承诺漂亮数字；只有在固定本地测试环境、明确数据规模并先跑出未优化对照基线后，才记录实测加速比；",
            "版本管理与交付：按阶段使用 Git 提交，只保留对当前项目真正有用的构建与 CI 脚本，无需为了显得工业化而强塞复杂集群部署。",
        ],
        "stage_acceptance_summary": accept_list,
        "fallback_scope_reduction": {
            "when_to_trigger": "若某阶段卡壳超过 4–5 天、或考试周课业繁忙每周可用时间不足 6 小时，立即启动以下缩减方案（保住完整小闭环，绝不半途而废）：",
            "shrunk_scope": mvp_obj.get("shrunk_mvp_fallback") or "冻结阶段 2 的外围扩展组件，仅保留阶段 1 最小可用闭环 + 阶段 2 的第 1 个核心算法/索引对比实验 + 10 条单元测试。",
            "reduced_mvp_plan": [
                mvp_obj.get("shrunk_mvp_fallback") or "1. 冻结阶段 2 的外围扩展组件，仅保留阶段 1 最小可用闭环 + 1 组核心性能基线对比；",
                "2. 明确砍掉非核心项：" + "；".join((mvp_obj.get("out_of_scope_initially") or ["复杂前端与分布式部署"])[:2]),
                "3. 交付底线：宁可交付一个只有 800~1,200 行代码但具备完整输入输出闭环、12 条可复现单测、真实基线对比表与清晰 README 的精悍作品，也绝不交半途而废的空壳。",
            ],
        },
        "skill_coverage_and_gaps_audit": {
            "audit_status": cov_summary["audit_status"],
            "summary_badge": cov_summary["summary_badge"],
            "covered_count": cov_summary["full_count"],
            "partial_count": cov_summary["partial_count"],
            "uncovered_count": cov_summary["uncovered_count"],
            "covered_items": [
                f"{r['skill_name']}（{r['job_frequency_str']}）→ 证据：{r['project_task_mapping']} ｜ 验收：{r.get('verification_criteria', '自动化单测验证')}"
                for r in full_cov
            ],
            "partial_items": [
                f"{r['skill_name']} [部分覆盖] → 项目内训练：{r['project_task_mapping']} ｜ 补充建议：{r['supplementary_training']}"
                for r in partial_cov
            ],
            "uncovered_items": [
                f"{r['skill_name']} [未覆盖] → 原因：{r['project_task_mapping']} ｜ 独立小练习建议：{r['supplementary_training']}"
                for r in none_cov
            ],
            "remaining_gaps_count": len(partial_or_none) if cov_summary["audit_status"] == "verified" else None,
            "remaining_gaps_advice": [
                f"{r['skill_name']} [{r['coverage_level']}] → {r['supplementary_training']}"
                for r in partial_or_none
            ],
        },
        "1_phased_development_plan": stages,
        "2_stage_deliverables": [s["deliverable"] for s in stages],
        "3_required_knowledge_and_materials": {
            "core_knowledge_points": project.get("knowledge_mapping") or [],
            "authoritative_references": (project.get("reference_and_license") or {}).get("references") or [],
            "self_impl_boundary": (project.get("reference_and_license") or {}).get("reuse_vs_self_impl") or "",
        },
        "4_problem_driven_learning_path": problem_driven_learning,
        "5_user_core_tasks": ai_bound.get("must_do_personally") or ai_bound.get("user_must_do") or [],
        "6_ai_assisted_tasks": ai_bound.get("ai_can_accelerate") or [],
        "7_engineering_standards": {
            "testing_and_debugging": "每个阶段结束前运行自动化测试套件（pytest / GTest / go test）并检查异常输入与内存安全。",
            "version_control_git": "按阶段与功能模块使用清晰的 Git Commit 记录演进。",
            "documentation_and_release": "README 必须包含：项目用普通话说清楚解决什么问题、3步运行说明、架构数据流图、优化前基线 vs 优化后实测表、1个真实排障复盘。",
        },
        "8_stage_acceptance_criteria": accept_list,
        "9_shrunk_mvp_fallback_plan": {
            "when_to_trigger": "若某阶段卡壳超过 4–5 天或每周可用时间不足 6 小时：",
            "shrunk_scope": mvp_obj.get("shrunk_mvp_fallback") or "保留阶段 1 最小闭环 + 1 组核心基准对比实验 + 10 条单元测试。",
        },
        "10_skill_constraints_coverage_and_remaining_gaps": {
            "fully_trained_skills": [
                f"{r['skill_name']}（{r['job_frequency_str']}）：{r['project_task_mapping']}"
                for r in full_cov
            ],
            "remaining_gaps_and_remediation": [
                {
                    "skill_name": r["skill_name"],
                    "coverage_level": r["coverage_level"],
                    "job_frequency": r["job_frequency_str"],
                    "remediation_action": r["supplementary_training"],
                }
                for r in partial_or_none
            ],
        },
        "understanding_self_check_questions": ai_bound.get("understanding_verification") or [],
    }


# ──────────────────────────────────────────────────────────────────────────
# 7.4 项目方案迭代与提示词深度重构引擎（含可行性冲突检测 + 全部 9 项实质更新 + 技能覆盖重算）
# ──────────────────────────────────────────────────────────────────────────
def compare_two_candidate_projects(proj_a: dict, proj_b: dict) -> dict:
    """对比两个候选项目的兴趣切入点、技能覆盖差异、学习成本与投递岗位覆盖面（Section 7.4）。"""
    cov_a = {r["skill_name"]: r["coverage_level"] for r in (proj_a.get("skill_coverage_table") or [])}
    cov_b = {r["skill_name"]: r["coverage_level"] for r in (proj_b.get("skill_coverage_table") or [])}
    all_sk = list(dict.fromkeys(list(cov_a.keys()) + list(cov_b.keys())))

    skill_diff_rows = []
    for sk in all_sk[:10]:
        la = cov_a.get(sk, "未覆盖")
        lb = cov_b.get(sk, "未覆盖")
        skill_diff_rows.append({
            "skill_name": sk,
            "project_a_level": la,
            "project_b_level": lb,
            "advantage": "两者均完整覆盖" if la == lb == "完整覆盖" else (proj_a["short_title"] if la == "完整覆盖" else (proj_b["short_title"] if lb == "完整覆盖" else "各有侧重")),
        })

    return {
        "compared_at": datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
        "project_a": {
            "project_id": proj_a["project_id"],
            "title": proj_a["short_title"],
            "interest_theme": proj_a.get("interest_theme_tag", ""),
            "languages": " / ".join(proj_a.get("primary_languages") or []),
            "covered_jobs": proj_a.get("covered_unique_jobs", 0),
            "workload": (proj_a.get("difficulty_workload_risks") or {}).get("workload_range", proj_a.get("estimated_effort", "")),
            "learning_cost": (proj_a.get("separation_evaluation") or {}).get("learning_cost", ""),
            "coverage_summary": (proj_a.get("coverage_summary") or {}).get("summary_badge", ""),
        },
        "project_b": {
            "project_id": proj_b["project_id"],
            "title": proj_b["short_title"],
            "interest_theme": proj_b.get("interest_theme_tag", ""),
            "languages": " / ".join(proj_b.get("primary_languages") or []),
            "covered_jobs": proj_b.get("covered_unique_jobs", 0),
            "workload": (proj_b.get("difficulty_workload_risks") or {}).get("workload_range", proj_b.get("estimated_effort", "")),
            "learning_cost": (proj_b.get("separation_evaluation") or {}).get("learning_cost", ""),
            "coverage_summary": (proj_b.get("coverage_summary") or {}).get("summary_badge", ""),
        },
        "skill_comparison_rows": skill_diff_rows,
        "decision_recommendation": (
            f"• 若你当前更看重『{proj_a.get('interest_theme_tag', '')}』且希望覆盖 {', '.join(proj_a.get('target_roles', [])[:2])}（关联 {proj_a.get('covered_unique_jobs', 0)} 个独立岗位）→ 优先选【{proj_a['short_title']}】；\\n"
            f"• 若你当前更看重『{proj_b.get('interest_theme_tag', '')}』且希望覆盖 {', '.join(proj_b.get('target_roles', [])[:2])}（关联 {proj_b.get('covered_unique_jobs', 0)} 个独立岗位）→ 优先选【{proj_b['short_title']}】；\\n"
            "• 两者并非互斥绑定：你可以先花 10–12 小时完成其中一个项目的阶段 1 (最小闭环)，亲身体验自己更喜欢写哪类代码。"
        ),
    }


def evaluate_and_build_prompt_redesign(
    target_proj: dict,
    feedback_text: str,
    skill_constraints: list[dict] | None = None,
) -> dict:
    """对用户输入的提示词执行实质性项目重构与工程可行性/技能冲突检查（Section 十）。

    不仅修改标题，而是联动计算：
    1. 工程可行性与冲突检查 (conflict_detected, conflict_warning, alternative_proposal)
    2. 重构后的普通话项目定义 (plain_intro) 与第一层概览 (layer1_overview)
    3. 重构后的 MVP 4 个核心操作、输入/输出、不做边界与闭环证明 (mvp_scope)
    4. 重构后的技术栈与基础版→进阶版演进理由 (tech_selection_rationale)
    5. 重构后的验收标准 (acceptance_criteria)、工作量与前置知识、AI分工、缩减方案
    6. 对技能覆盖明细表 (skill_coverage_table) 的实质影响增量
    """
    txt = (feedback_text or "").strip()[:240]
    txt_lower = txt.lower()
    now_iso = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    pid = str(target_proj.get("project_id") or "")
    base_short = str(target_proj.get("base_short_title") or target_proj.get("short_title") or target_proj.get("title") or "")
    langs_str = " / ".join(target_proj.get("primary_languages") or ["C++/Python/Go"])

    # ── 1. 工程可行性与技能约束冲突检测 ──
    conflict_detected = False
    conflict_warning = ""
    alternative_proposal = ""

    # 检测 A：是否在提示词中要求同时塞入过多语言或重型分布式组件（如 k8s/kafka/微服务集群+3D引擎）
    lang_hits = sum(1 for kw in ("c++", "python", "java", "golang", "rust") if kw in txt_lower)
    heavy_hits = sum(1 for kw in ("kubernetes", "k8s", "kafka", "微服务集群", "分布式事务", "商业引擎", "ue5", "重写原神", "mmo") if kw in txt_lower)
    if lang_hits >= 3 or heavy_hits >= 2 or ("1周" in txt and ("分布式" in txt or "引擎" in txt)):
        conflict_detected = True
        conflict_warning = (
            f"⚠️ 【工程可行性冲突预警】您的要求（「{txt}」）同时涉及多项重型组件或多语言混编，预计工作量将超过 120+ 小时，"
            "超出大二课余合理负担，极易沦为只有空壳配置、没有核心实现的半途而废项目。"
        )
        alternative_proposal = (
            f"💡 【导师替代方案】已为您自动收敛技术栈：保留当前项目的单一主力栈（{langs_str}），"
            "第一版（阶段 1）仅提取您主题中最核心的 1 个算法/存储闭环，将分布式/重型组件剥离为阶段 3 可选扩展或独立小练习。"
        )
    # 检测 B：用户要求简化技术栈（如“只用 SQLite / 去掉 Redis / 去掉网络 / 简化为命令行”）
    elif any(k in txt_lower for k in ("sqlite", "简化", "去掉redis", "不用mysql", "单机", "2周")):
        conflict_detected = True
        conflict_warning = (
            f"ℹ️ 【技术栈简化与技能覆盖权衡提示】按您的要求（「{txt}」）将存储/架构简化为轻量单机方案（如 SQLite + 内存 LRU）后，"
            "阶段 1–2 开发阻力大幅降低，但会导致岗位约束中的『分布式缓存中间件 (Redis) / 跨节点高并发调优』从完整覆盖降为部分覆盖。"
        )
        alternative_proposal = (
            "💡 【平衡替代方案】阶段 1–2 100% 采纳您的简化要求（使用内置 SQLite 完成 `EXPLAIN QUERY PLAN` 复合索引调优 + 手写 80 行内存 LRU 缓存），"
            "确保 2 周内稳妥交付；同时在技能覆盖表中将 Redis 诚实更新为『部分覆盖』，并附赠 1 个 1.5 小时的独立 Redis 练习脚本供面试前补齐。"
        )
    # 检测 C：用户在纯后端/数据库项目上要求做重度 OpenGL 粒子/渲染，或在纯 C++ 渲染项目上要求做多表业务管理系统
    elif ("粒子" in txt or "动态壁纸" in txt or "shader" in txt_lower or "opengl" in txt_lower) and "C++" not in langs_str:
        conflict_detected = True
        conflict_warning = (
            f"⚠️ 【项目底座与主题冲突提示】当前选中的【{base_short}】主力栈为 {langs_str}（偏服务端/数据检索），"
            f"若强行在其中塞入实时图形粒子渲染（「{txt}」），会导致前后端职责混乱且无法发挥 C++ OpenGL 硬件加速优势。"
        )
        alternative_proposal = (
            "💡 【两种合理落地路线】① 路线 A（当前已为您生成）：将本项目改造为『动态壁纸/粒子特效预设工坊的元数据检索、Shader参数版本库与性能上报服务端』；"
            "② 路线 B（更推荐）：直接点击切换到【候选项目二（C++17 + Qt6 + OpenGL 动态壁纸粒子合成器）】！"
        )

    # ── 2. 识别五大高频定制意图并生成实质性重构方案 ──
    is_touhou = any(k in txt_lower for k in ("东方", "弹幕", "擦弹", "graze", "符卡", "replay", "stg"))
    is_arknights = any(k in txt_lower for k in ("明日方舟", "塔防", "干员", "技能轴", "寻路", "战棋", "dps"))
    is_anime_mgr = any(k in txt_lower for k in ("动漫", "番剧", "galgame", "轻小说", "台词", "声优", "生词", "作品管理"))
    is_wallpaper = any(k in txt_lower for k in ("壁纸", "粒子", "shader", "着色器", "特效", "渲染"))
    is_sqlite_simple = any(k in txt_lower for k in ("sqlite", "简化", "lru", "2周", "缩小"))
    is_boost_tech = any(k in txt_lower for k in ("并发", "多线程", "数据库", "mysql", "redis", "测试", "asan", "ci"))

    if is_touhou:
        new_title = f"《东方Project 弹幕谱面解析、擦弹(Graze)四叉树判定与 Replay 帧回放系统》({langs_str})"
        user_scene = "喜欢东方Project等弹幕射击游戏 (STG)，想拆解“满屏 5000 发弹幕为什么不会卡死、自机擦弹判定是怎么算的、为什么一个几十 KB 的 `.rpy` 录像文件能一秒不差还原整局操作”的玩家与开发者。"
        user_problem = "满屏几千颗子弹如果每帧都和玩家角色两两计算距离（O(N) 暴力碰撞），帧率会骤降；而且一旦录像回放里混入未受控的浮点数或随机种子，回放到第 2 分钟就会“脱节 (Desync)”撞弹身亡。"
        core_ops = [
            "操作 1（加载弹幕谱面与发射器）：读取 JSON/二进制符卡谱面（发射角度、弹速、加速度、子弹半径），初始化连续内存弹幕池；",
            "操作 2（四叉树擦弹与命中判定）：输入自机坐标 `(px, py)`、判定点半径 `r_hit=2px` 与擦弹半径 `r_graze=16px`，用四叉树 (`QuadTree`) 毫秒级输出本帧是否中弹及新增擦弹数；",
            "操作 3（录制与确定性 Replay 回放）：将每帧按键位掩码 (`uint16_t key_mask`) 与随机数种子追加写入 `.rpy` WAL 文件，重读该文件从第 1 帧重放，验证第 3600 帧自机残机、得分与擦弹数 100% 一致；",
            "操作 4（任意帧倒流练习模式）：输入 `rewind --frame 1800` 瞬间回到符卡开始前第 30 秒反复练习走位并输出每帧判定耗时表。",
        ]
        inputs_desc = "符卡弹幕配置 JSON、每帧按键输入流 (`Up/Down/Left/Right/Slow/Shoot/Bomb`)、或历史 `.rpy` 录像文件。"
        outputs_desc = "每帧存活/中弹状态、累计擦弹 (`Graze`) 计数、四叉树 vs 暴力遍历耗时对比表 (`perf_graze.json`)、以及可 100% 复现的 `.rpy` 录像文件。"
        closed_loop_proof = "运行自动化测试：加载『测试符卡：千发交叉弹幕』+ 固定 600 帧输入序列 → 执行仿真并保存 `test.rpy` → 重新加载 `test.rpy` 回放 600 帧 → 断言最终擦弹数 (`graze_count == 48`)、存活状态与每帧状态哈希 100% 吻合。"
        cov_adjustments = {
            "algo_ds_core": ("完整覆盖", "手写二维四叉树 (`QuadTree`) 弹幕空间碰撞/擦弹检索 + 连续内存子弹对象池", "运行 5000 发弹幕下四叉树 vs O(N) 距离判定耗时对比测试"),
            "test_automation_qa": ("完整覆盖", "编写 600 帧确定性 Replay 回放一致性断言与边界碰撞半径单测", "执行 `pytest` / `ctest` 验证回放零脱节 (0 Desync)"),
        }
    elif is_arknights:
        new_title = f"《战术塔防/战棋干员技能轴排期、带权 A* 寻路与帧级伤害解构沙盒》({langs_str})"
        user_scene = "喜欢玩明日方舟或战棋策略游戏，经常需要计算“敌方 Boss 绕路走到蓝门需要多少秒、在哪个帧节点开启干员决战技才能打满伤害并卡住路线”的攻略分析者。"
        user_problem = "靠脑子记或用 Excel 算技能冷却 (CD)、攻击间隔帧数与怪物移动路径非常容易算错；特に当场上放置障碍箱子动态改变怪物路线时，很难快速算出最优开技能时间轴。"
        core_ops = [
            "操作 1（导入关卡地图与敌人波次）：读取含地面、高台、禁行区与障碍物的网格地图，使用带权 A* 算出每波敌人的精确移动路径与到达各格子的帧时间戳；",
            "操作 2（编排干员部署与技能轴）：输入干员位置與技能开启帧号（如 `frame=450 开启真银斩`），若技力 (SP) 尚未攒够则立即报错提示最早可开启帧；",
            "操作 3（帧级战斗模拟与倒流分叉）：按每秒 30 帧推进确定性状态机，支持倒回第 300 帧测试『早开 2 秒 vs 晚开 2 秒』对总漏怪数与总伤害的差异；",
            "操作 4（导出技能轴复盘表）：输出整局每帧伤害明细、技力恢复曲线与最优通关操作序列 JSON。",
        ]
        inputs_desc = "关卡网格地图 JSON、敌人属性与波次时间表、干员部署与技能释放指令序列。"
        outputs_desc = "A* 寻路坐标时间轴、每帧伤害结算日志、不同技能轴分支的总伤害/漏怪对比表。"
        closed_loop_proof = "运行单测：加载 10×6 地图与 1 名高甲 Boss → 动态放置 1 个障碍箱改变路线 → 断言 A* 重新规划出唯一合法路径 → 对比两个不同技能开启帧的通关结果并保存/重读操作录像验证一致。"
        cov_adjustments = {
            "algo_ds_core": ("完整覆盖", "手写支持动态障碍重规划的 A* 寻路算法 + 最小堆帧事件调度队列", "运行动态放箱子封路检测与最短路径长度断言测试"),
            "test_automation_qa": ("完整覆盖", "编写帧级伤害结算精度测试、技力不足异常拦截测试与录像重放回归测试", "自动化测试套件 100% 通过"),
        }
    elif is_wallpaper:
        new_title = f"《节点式动态壁纸、樱花/雨雪粒子合成与 GLSL 着色器热重载诊断工具》({langs_str})"
        user_scene = "希望把静态动漫插画变成带景深视差、飘落粒子和光影特效的桌面动态壁纸，并能随时观察显卡/CPU 帧耗时性能的创作者。"
        user_problem = "商业壁纸软件黑盒封装看不见内部机制，而自己写的简单渲染程序一加多粒子就掉帧、改着色器还要反复重编译。"
        core_ops = [
            "操作 1（图层导入与粒子发射）：加载背景插画，开启连续内存粒子池调节粒子数量（1,000~50,000）、风场与下落速度；",
            "操作 2（GLSL 着色器实时热重载）：修改 `.frag` 文件保存后画面即刻刷新，若语法写错则在底部诊断面板报出具体行号并保持原画面不崩；",
            "操作 3（四叉树鼠标排斥交互）：移动鼠标推开周围粒子，对比开启四叉树前后的范围查询微秒数；",
            "操作 4（预设保存与 300 帧性能报告导出）：将壁纸配置保存为 `wallpaper_preset.json` 并导出帧耗时基线对比报告。",
        ]
        inputs_desc = "图层图片路径、粒子发射器参数 JSON、`.vert/.frag` 着色器源码与鼠标交互坐标。"
        outputs_desc = "60FPS 实时合成画面、Shader 编译诊断日志与 `perf_report.json` 帧耗时对比表。"
        closed_loop_proof = "运行测试：初始化 20,000 粒子运行 120 帧 → 导出配置 JSON → 重新读取 JSON 校验参数一致 → 注入损坏 Shader 验证安全回退且 ASan 报告 0 内存泄漏。"
        cov_adjustments = {
            "algo_ds_core": ("完整覆盖", "手写 Swap-and-Pop 连续内存粒子池 + 二维四叉树鼠标碰撞索引", "3万粒子下对象池与四叉树基准测试验证"),
            "test_debug_profile": ("完整覆盖", "内置帧耗时 Profiler + ASan 内存消毒零泄漏验证", "连续热重载 50 次退出 0 bytes leaked"),
        }
    elif is_sqlite_simple:
        new_title = f"《【轻量SQLite+手写LRU精简版】{base_short}》"
        user_scene = "希望在课业繁忙期（每周仅 6–8 小时）零配置外部数据库服务，直接用单文件 SQLite 和手写核心算法在 2–3 周内完成高含金量闭环的学生。"
        user_problem = "同时配置 MySQL、Redis、Docker 容器容易在环境报错上浪费大量时间，导致核心数据结构、SQL 索引调优和单元测试反而没时间写透。"
        core_ops = [
            "操作 1（单文件 SQLite 事务写入）：无需启动外部数据库进程，直接向本地 `vault.db` 事务写入核心业务条目与关联关系；",
            "操作 2（手写核心算法处理）：调用手写的数据结构模块（Trie 前缀树 / DAG 检环 / BM25 索引）完成毫秒级校验与检索；",
            "操作 3（手写双链表+哈希表 LRU 缓存加速）：热点查询优先命中自己手写的 80 行线程安全 `LRUCache`，数据修改时自动触发失效；",
            "操作 4（5万条数据 SQLite EXPLAIN QUERY PLAN 对比）：一键生成 5 万条本地数据，输出建复合索引前后的扫描计划与实测毫秒数对比。",
        ]
        inputs_desc = "业务条目 JSON、查询过滤参数与基准测试数据量参数。"
        outputs_desc = "SQLite 持久化文件、含 `cache_hit` 与 `elapsed_ms` 的响应 JSON、以及 `EXPLAIN QUERY PLAN` 对比表。"
        closed_loop_proof = "克隆代码后无需安装任何外部服务，直接执行 `pytest -v` 即可在 5 秒内跑通『SQLite 建表写入 → 手写 LRU 命中与失效 → 复合索引计划验证 → 异常边界拦截』全部 15 条测试。"
        cov_adjustments = {
            "db_sql_rdbms": ("完整覆盖", "使用内置 SQLite 完成三表规范建模、事务控制与 5 万条数据 `EXPLAIN QUERY PLAN` 复合索引调优", "运行基准脚本验证 `SEARCH TABLE USING INDEX`"),
            "db_cache_mq": ("部分覆盖", "在项目内亲手实现 80 行哈希表+双向链表 `LRUCache` 与失效逻辑（暂不部署外部 Redis 容器）", "【进阶补充】若面试前需体验 Redis，只需花 1.5 小时用 `redis-py` 替换 `LRUCache` 接口实现类"),
            "tool_docker_k8s": ("未覆盖", "按精简要求去掉了 Docker Compose 多容器编排，实现零外部环境依赖", "【独立小练习】后续有空可花 1 小时单独写一个 Dockerfile 练习"),
        }
    else:
        # 通用提示词/动漫管理/增强并发数据库测试定制
        new_title = f"《围绕「{txt[:22]}」深度重构：{base_short}》"
        user_scene = f"针对你提出的「{txt}」场景与要求，将原有工程骨架转化为解决该具体领域痛点的可运行工具（主力技术栈保持克制：{langs_str}）。"
        user_problem = f"在「{txt}」场景下，普通脚本或手工表格难以兼顾数据关联查询效率、状态一致性与异常边界防护，需要一套有明确输入输出闭环和自动化测试的工程方案。"
        core_ops = [
            f"操作 1（领域实体录入与边界校验）：录入「{txt[:18]}」核心条目与关联属性，自动拦截非法格式与冲突依赖并持久化保存；",
            f"操作 2（核心数据结构/算法检索）：调用手写索引/状态机模块，毫秒级完成「{txt[:18]}」的条件筛选、前缀补全或路径/状态推演；",
            f"操作 3（持久化重读与状态更新）：修改条目状态后再次读取，验证存储层与内存索引/缓存 100% 同步一致；",
            f"操作 4（基线测量与异常注入验证）：一键注入万级合成测试数据或并发/畸形请求，输出优化前后实测耗时对比表。",
        ]
        inputs_desc = f"「{txt[:18]}」领域的结构化 JSON/二进制输入、查询过滤条件与测试种子。"
        outputs_desc = "结构化处理结果、持久化数据库/日志文件、以及基线耗时与自动化测试报告。"
        closed_loop_proof = f"运行自动化测试命令：自动完成『输入 5 条「{txt[:15]}」测试数据 → 执行核心算法处理并落盘 → 重启/重读校验字段 100% 一致 → 注入 3 类非法输入验证安全拦截』。"
        cov_adjustments = {}
        if is_boost_tech:
            if any(k in txt_lower for k in ("数据库", "mysql", "redis", "sql")):
                cov_adjustments["db_sql_rdbms"] = ("完整覆盖", f"在「{txt[:15]}」中设计规范化表与复合索引，完成万级数据 EXPLAIN 对比", "执行基准脚本验证命中复合索引且结果一致")
                cov_adjustments["db_cache_mq"] = ("完整覆盖", f"在「{txt[:15]}」中引入 Cache-Aside 缓存与更新失效验证", "单测验证缓存命中、主动失效与降级回源")
            if any(k in txt_lower for k in ("并发", "多线程", "网络", "协议")):
                cov_adjustments["conc_multithread"] = ("完整覆盖", f"在「{txt[:15]}」中实现多线程/多协程并发处理并通过竞态检测", "50 并发压测与 TSan/`-race` 零数据竞争验证")
                cov_adjustments["net_tcp_http"] = ("完整覆盖", f"在「{txt[:15]}」中实现通信协议边界校验、超时重试与幂等控制", "分片/重放自动化测试 100% 通过")
            if any(k in txt_lower for k in ("测试", "单测", "asan", "ci")):
                cov_adjustments["test_automation_qa"] = ("完整覆盖", f"针对「{txt[:15]}」编写 15+ 条正常、边界与异常注入自动化单元测试", "执行 `pytest`/`ctest`/`go test` 100% 绿灯")
                cov_adjustments["test_debug_profile"] = ("完整覆盖", f"开启 ASan 或性能剖析器定位并修复「{txt[:15]}」中的边界与耗时问题", "输出修复前后实测对比表且 0 内存泄漏")

    if is_sqlite_simple:
        if "SQLite" not in new_title:
            new_title = f"《【SQLite轻量闭环版】{new_title.lstrip('《').rstrip('》')}》"
        cov_adjustments["db_sql_rdbms"] = ("完整覆盖", "使用内置 SQLite 完成三表规范建模、事务控制与 5 万条数据 `EXPLAIN QUERY PLAN` 复合索引调优", "运行基准脚本验证 `SEARCH TABLE USING INDEX`")
        cov_adjustments["db_cache_mq"] = ("部分覆盖", "在项目内亲手实现 80 行哈希表+双向链表 `LRUCache` 与失效逻辑（暂不部署外部 Redis 容器）", "【进阶补充】若面试前需体验 Redis，只需花 1.5 小时用 `redis-py` 替换 `LRUCache` 接口实现类")
        cov_adjustments["tool_docker_k8s"] = ("未覆盖", "按精简要求去掉了 Docker Compose 多容器编排，实现零外部环境依赖", "【独立小练习】后续有空可花 1 小时单独写一个 Dockerfile 练习")

    conflict_warnings_list = [conflict_warning, "已自动拦截超纲技术堆叠，避免多语言/多中间件分散核心精力"] if conflict_detected else []
    alternative_proposals_list = [alternative_proposal, "优先在单一主力技术栈内完成阶段 1 最小可用闭环，其余技能通过 2 小时独立小练习补齐"] if conflict_detected else []

    return {
        "prompt_text": txt,
        "applied_at": now_iso,
        "is_sqlite_simplified": is_sqlite_simple,
        "conflict_detected": conflict_detected,
        "conflict_warning": conflict_warning,
        "conflict_warnings": conflict_warnings_list,
        "alternative_proposal": alternative_proposal,
        "alternative_proposals": alternative_proposals_list,
        "redesigned_title": new_title,
        "redesigned_user_scene": user_scene,
        "redesigned_user_problem": user_problem,
        "redesigned_core_operations": core_ops,
        "redesigned_inputs": inputs_desc,
        "redesigned_outputs": outputs_desc,
        "redesigned_closed_loop_proof": closed_loop_proof,
        "coverage_adjustments": cov_adjustments,
        "domain_redesign_summary": (
            f"已围绕「{txt}」实质性更新了项目用途、MVP 4个核心操作、技术选型演进、阶段 0–3 任务、可复现验收标准、技能覆盖表与缩减方案。"
            + (f" 同时触发了工程可行性守则：{conflict_warning}" if conflict_detected else "")
        ),
        "skill_mapping_breakdown": [
            f"1. 项目实际用途与 MVP 闭环 → 解决：{user_problem}（通过 4 个核心操作完成『输入 → 处理 → 保存 → 重读验证』闭环）；",
            f"2. 核心数据结构与算法落点 → 在「{txt[:18]}」中亲手实现核心索引/状态机/空间树，拒绝只改标题或换皮；",
            f"3. 存储/并发与基线测量落点 → 先在本地建立未优化耗时基线，再验证索引/对象池/乐观锁在「{txt[:18]}」数据规模下的真实收益；",
            f"4. 自动化验收与技能覆盖同步 → 已同步更新阶段 1–3 可执行验收命令与技能覆盖明细表统计数。"
        ],
    }


def apply_project_iteration_feedback(
    report_data: dict,
    project_id: str,
    action_type: str,
    feedback_text: str = "",
    compare_with_id: str = "",
    profile: dict | None = None,
) -> dict:
    """对候选项目或已选项目应用用户的迭代修改要求（sp-v3.0：实质性重写全部 9 大模块并同步重算技能覆盖统计）。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    projects = report_data.get("tier_b_projects") or []
    constraints = report_data.get("skill_constraints") or []
    workspace = report_data.setdefault("user_workspace", _default_user_workspace())

    proj_map = {p["project_id"]: p for p in projects}
    target_proj = proj_map.get(project_id) or (projects[0] if projects else None)
    if not target_proj:
        return report_data

    now_iso = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    action = str(action_type or "custom_feedback").strip().lower()
    note_summary = ""

    if action == "compare_two":
        other_proj = proj_map.get(compare_with_id)
        if not other_proj:
            other_candidates = [p for p in projects if p["project_id"] != target_proj["project_id"]]
            other_proj = other_candidates[0] if other_candidates else target_proj
        comp_res = compare_two_candidate_projects(target_proj, other_proj)
        workspace["last_comparison"] = comp_res
        workspace.setdefault("comparison_history", []).append(comp_res)
        note_summary = f"已完成双项目对比：【{target_proj['short_title']}】 vs 【{other_proj['short_title']}】"

    elif action == "shrink_mvp":
        redesign = evaluate_and_build_prompt_redesign(
            target_proj, "简化技术栈，先使用 SQLite / 本地文件与核心数据结构，确保 2 周内完成最小闭环", constraints
        )
        _apply_redesign_to_project_dict(target_proj, redesign)
        target_proj["estimated_effort"] = "约 16–22 小时（已按『缩小 MVP』指令剥离外围扩展组件，聚焦阶段 1 最小闭环 + 1 组核心基准测试，2 周内可稳妥交付）"
        if "difficulty_workload_risks" in target_proj:
            target_proj["difficulty_workload_risks"]["workload_range"] = target_proj["estimated_effort"]
        if "layer1_overview" in target_proj:
            target_proj["layer1_overview"]["realistic_time"] = target_proj["estimated_effort"]
        note_summary = f"已将【{target_proj['short_title']}】实质性切换为 2 周精简闭环模式，并同步更新了技术栈階梯与技能覆盖审计表。"

    elif action in ("boost_sql_redis", "boost_network_concurrency", "boost_testing_ci", "add_skill_practice"):
        skill_prompt_map = {
            "boost_sql_redis": "希望增加数据库复合索引 EXPLAIN 调优与缓存失效一致性实践",
            "boost_network_concurrency": "希望增加网络通信协议边界拆包与多线程/多协程并发同步压测实践",
            "boost_testing_ci": "希望增加单元测试边界断言、ASan 内存消毒与自动化回归测试实践",
        }
        prompt_str = skill_prompt_map.get(action) or feedback_text or "希望增加核心工程测试与数据库并发实践"
        redesign = evaluate_and_build_prompt_redesign(target_proj, prompt_str, constraints)
        _apply_redesign_to_project_dict(target_proj, redesign)
        note_summary = f"已为【{target_proj['short_title']}】实质性强化『{prompt_str}』，并同步更新了 MVP 操作、阶段任务、验收标准与技能覆盖审计表。"

    else:
        custom_txt = (feedback_text or "调整项目主题细节与功能范围").strip()[:240]
        redesign = evaluate_and_build_prompt_redesign(target_proj, custom_txt, constraints)
        _apply_redesign_to_project_dict(target_proj, redesign)
        note_summary = f"已按提示词「{custom_txt}」深度重构项目用途、MVP 操作、技术选型、分阶段计划、验收标准与技能覆盖表"

    # 同步重新生成与更新实施计划
    plans_map = workspace.setdefault("implementation_plans_by_project", {})
    if project_id in plans_map or workspace.get("selected_project_id") == project_id:
        updated_plan = generate_project_implementation_plan(
            target_proj, constraints, profile=prof, custom_focus=feedback_text or note_summary
        )
        plans_map[project_id] = updated_plan
        if workspace.get("selected_project_id") == project_id:
            workspace["active_implementation_plan"] = updated_plan

    iter_entry = {
        "iteration_id": f"iter-{len(workspace.get('iteration_history') or []) + 1}",
        "timestamp": now_iso,
        "iterated_at": now_iso,
        "project_id": target_proj["project_id"],
        "project_title": target_proj["title"],
        "action_type": action,
        "user_feedback": feedback_text or action,
        "effect_summary": note_summary,
        "changes_applied": [note_summary],
    }
    workspace.setdefault("iteration_history", []).append(iter_entry)
    workspace["last_updated_at"] = now_iso
    report_data["candidate_projects"] = projects
    return report_data


def _apply_redesign_to_project_dict(target_proj: dict, redesign: dict) -> None:
    """将 `evaluate_and_build_prompt_redesign` 的计算结果实质性写入候选项目字典，并重算技能覆盖统计。"""
    base_short = str(target_proj.get("base_short_title") or target_proj.get("short_title") or target_proj.get("title") or "")
    target_proj["base_short_title"] = base_short
    txt = redesign["prompt_text"]

    target_proj["interest_theme_tag"] = f"🎯 提示词定制：{txt[:26]}"
    target_proj["title"] = redesign["redesigned_title"]
    target_proj["name"] = target_proj["title"]
    target_proj["core_purpose"] = f"【已围绕「{txt}」重构】{redesign['redesigned_user_problem']}"

    # 1. 更新 plain_intro 与 layer1_overview
    p_intro = target_proj.setdefault("plain_intro", {})
    p_intro["what_is_it_called"] = redesign["redesigned_title"]
    p_intro["who_uses_it"] = redesign["redesigned_user_scene"]
    p_intro["user_problem"] = redesign["redesigned_user_problem"]
    p_intro["what_user_can_do"] = " ".join(redesign["redesigned_core_operations"])
    p_intro["visible_outcome"] = redesign["redesigned_outputs"]
    p_intro["q1_what_is_it"] = p_intro["what_is_it_called"]
    p_intro["q2_who_uses_it"] = p_intro["who_uses_it"]
    p_intro["q3_what_problem"] = p_intro["user_problem"]
    p_intro["q4_what_can_user_do"] = p_intro["what_user_can_do"]
    p_intro["q5_visible_outcome"] = p_intro["visible_outcome"]
    if redesign.get("is_sqlite_simplified"):
        cur_stack = str(target_proj.get("primary_language_stack") or "")
        if "SQLite" not in cur_stack:
            target_proj["primary_language_stack"] = f"{cur_stack} + SQLite + 手写LRU" 

    l1 = target_proj.setdefault("layer1_overview", {})
    l1["what_it_does"] = f"【定制主题：{txt[:24]}】{redesign['redesigned_user_problem']}"
    l1["v1_first_step"] = f"第一版先完成：{redesign['redesigned_core_operations'][0]}；并跑通『{redesign['redesigned_closed_loop_proof'][:75]}…』"

    # 2. 更新 MVP 范围与核心特征
    mvp_obj = target_proj.setdefault("mvp_scope", {})
    mvp_obj["problem_solved"] = redesign["redesigned_user_problem"]
    mvp_obj["core_operations"] = redesign["redesigned_core_operations"]
    mvp_obj["inputs"] = redesign["redesigned_inputs"]
    mvp_obj["outputs"] = redesign["redesigned_outputs"]
    mvp_obj["closed_loop_proof"] = redesign["redesigned_closed_loop_proof"]
    mvp_obj["must_have"] = list(redesign["redesigned_core_operations"])
    mvp_obj["must_have_mvp"] = f"【「{txt[:18]}」定制 MVP】" + "；".join(redesign["redesigned_core_operations"][:2])
    target_proj["core_features"] = list(redesign["redesigned_core_operations"])

    # 3. 更新验收标准（确保可执行、可复现，无虚构数字）
    base_acc = [a for a in (target_proj.get("acceptance_criteria") or []) if not str(a).startswith("✅ 【定制闭环验收】")]
    custom_acc = f"✅ 【定制闭环验收（{txt[:18]}）】{redesign['redesigned_closed_loop_proof']}"
    target_proj["acceptance_criteria"] = [custom_acc] + base_acc[:2]

    # 4. 更新 AI 与个人分工边界
    ai_b = target_proj.setdefault("ai_collab_boundary", {})
    u_tasks = [u for u in (ai_b.get("user_must_do") or ai_b.get("must_do_personally") or []) if not str(u).startswith("🎯")]
    u_custom = f"🎯 【针对「{txt[:20]}」必须亲自完成】亲自设计该场景的核心数据结构/表结构与状态流转逻辑，亲手编写并调试『{redesign['redesigned_core_operations'][1][:36]}…』及边界单元测试。"
    ai_b["user_must_do"] = [u_custom] + u_tasks
    ai_b["must_do_personally"] = ai_b["user_must_do"]

    a_tasks = [a for a in (ai_b.get("ai_can_accelerate") or []) if not str(a).startswith("🤖")]
    a_custom = f"🤖 【针对「{txt[:20]}」可由 AI 加速】让 AI 批量生成「{txt[:20]}」主题的仿真测试样本数据（JSON/SQL/二进制种子）与命令行参数解析样板，生成后必须通过你的单测核验。"
    ai_b["ai_can_accelerate"] = [a_custom] + a_tasks

    # 5. 更新技能覆盖明细表并重新计算一致的汇总统计数字
    cov_adjust = redesign.get("coverage_adjustments") or {}
    cov_table = target_proj.get("skill_coverage_table") or []
    for row in cov_table:
        sid = row.get("skill_id")
        if sid in cov_adjust:
            new_lvl, new_task, new_ver = cov_adjust[sid]
            row["coverage_level"] = new_lvl
            row["project_task_mapping"] = f"【已随「{txt[:14]}」同步更新】{new_task}"
            row["verification_criteria"] = new_ver
            if new_lvl == "完整覆盖":
                row["supplementary_training"] = "已纳入当前定制方案核心实现与自动化验收闭环"
                row["supplementary_advice"] = row["supplementary_training"]
            else:
                row["supplementary_training"] = new_ver
                row["supplementary_advice"] = new_ver

    target_proj["coverage_summary"] = compute_consistent_coverage_summary(cov_table)
    target_proj["custom_prompt_applied"] = redesign


def _default_user_workspace() -> dict:
    return {
        "selected_project_id": None,
        "selected_project_title": "",
        "selected_at": "",
        "selection_note": "尚未选择最终项目（请先浏览并对比下方 6 个候选方案，点击『选择此项目』后再生成实施计划；单次项目选择仅代表当前阶段工程训练计划，不会永久锁死你的职业方向）",
        "active_implementation_plan": None,
        "implementation_plans_by_project": {},
        "iteration_history": [],
        "comparison_history": [],
        "last_comparison": None,
        "pending_update_notice": "",
        "archived_workspaces": [],
        "last_updated_at": "",
    }


def _load_skill_project_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"snapshots": [], "last_report": None, "user_workspace": _default_user_workspace()}


def _save_skill_project_state(state_path: Path, state: dict) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state["snapshots"] = (state.get("snapshots") or [])[-10:]
        if isinstance(state.get("last_report"), dict) and isinstance(state.get("user_workspace"), dict):
            state["last_report"]["user_workspace"] = state["user_workspace"]
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("保存技能栈与项目推荐缓存状态失败: %s", exc)


# ──────────────────────────────────────────────────────────────────────────
# 八、持久化与增量更新操作接口（区分“仅更新技能统计” vs “重新生成候选项目” vs “选择与生成计划”）
# ──────────────────────────────────────────────────────────────────────────
def select_candidate_project(
    project_id: str,
    state_path: str | Path | None = None,
    conn=None,
    generate_plan_immediately: bool = False,
    profile: dict | None = None,
) -> dict:
    """用户选择某个感兴趣的候选项目（Section 7.2），可选是否立即生成分阶段实施计划。"""
    spath = Path(state_path) if state_path else DEFAULT_SKILL_PROJECT_STATE_PATH
    state = _load_skill_project_state(spath)
    last_report = state.get("last_report") or {}
    workspace = state.setdefault("user_workspace", _default_user_workspace())

    projects = last_report.get("tier_b_projects") or list(PROJECT_BLUEPRINT_CATALOG)
    target = next((p for p in projects if p.get("project_id") == project_id), None)
    if not target:
        raise ValueError(f"未找到候选项目 ID: {project_id}")

    now_iso = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    workspace["selected_project_id"] = project_id
    workspace["selected_project_title"] = str(target.get("title") or target.get("short_title") or project_id)
    workspace["selected_at"] = now_iso
    workspace["selection_note"] = (
        f"已选定当前训练项目：《{target.get('short_title') or target.get('title')}》（选定时间：{now_iso}；"
        "注：该项目选择仅用于指导当前阶段工程能力训练，不会将你的个人画像永久锁定在单一职业方向）。"
    )
    workspace["last_updated_at"] = now_iso

    plans_map = workspace.setdefault("implementation_plans_by_project", {})
    if generate_plan_immediately or project_id in plans_map:
        if generate_plan_immediately or project_id not in plans_map:
            constraints = last_report.get("skill_constraints") or []
            plan = generate_project_implementation_plan(target, constraints, profile=profile)
            plans_map[project_id] = plan
        workspace["active_implementation_plan"] = plans_map[project_id]
    else:
        workspace["active_implementation_plan"] = None

    if isinstance(last_report, dict):
        last_report["user_workspace"] = workspace
        state["last_report"] = last_report
    _save_skill_project_state(spath, state)
    if conn is not None and isinstance(last_report, dict):
        save_skill_projects_to_db(conn, last_report)
    return {"ok": True, "selected_project_id": project_id, "selected_project_title": workspace["selected_project_title"], "user_workspace": workspace, **workspace}


def build_and_save_implementation_plan(
    project_id: str | None = None,
    custom_focus: str = "",
    state_path: str | Path | None = None,
    conn=None,
    profile: dict | None = None,
) -> dict:
    """为当前已选项目生成并持久化分阶段实施计划（Section 7.3）。"""
    spath = Path(state_path) if state_path else DEFAULT_SKILL_PROJECT_STATE_PATH
    state = _load_skill_project_state(spath)
    last_report = state.get("last_report") or {}
    workspace = state.setdefault("user_workspace", _default_user_workspace())

    pid = project_id or workspace.get("selected_project_id")
    if not pid:
        raise ValueError("请先在候选项目列表中选择一个你感兴趣的项目，然后再生成实施计划。")

    projects = last_report.get("tier_b_projects") or list(PROJECT_BLUEPRINT_CATALOG)
    target = next((p for p in projects if p.get("project_id") == pid), None)
    if not target:
        raise ValueError(f"未找到候选项目 ID: {pid}")

    now_iso = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    if workspace.get("selected_project_id") != pid:
        workspace["selected_project_id"] = pid
        workspace["selected_project_title"] = str(target.get("title") or target.get("short_title") or pid)
        workspace["selected_at"] = now_iso

    constraints = last_report.get("skill_constraints") or []
    plan = generate_project_implementation_plan(target, constraints, profile=profile, custom_focus=custom_focus)
    workspace.setdefault("implementation_plans_by_project", {})[pid] = plan
    workspace["active_implementation_plan"] = plan
    workspace["last_updated_at"] = now_iso

    if isinstance(last_report, dict):
        last_report["user_workspace"] = workspace
        state["last_report"] = last_report
    _save_skill_project_state(spath, state)
    if conn is not None and isinstance(last_report, dict):
        save_skill_projects_to_db(conn, last_report)
    return {"ok": True, "implementation_plan": plan, "user_workspace": workspace, **plan}


def submit_project_iteration(
    project_id: str,
    action_type: str,
    feedback_text: str = "",
    compare_with_id: str = "",
    state_path: str | Path | None = None,
    conn=None,
    profile: dict | None = None,
) -> dict:
    """对当前项目方案提交迭代反馈或双项目对比，并持久化保存（Section 7.4）。"""
    spath = Path(state_path) if state_path else DEFAULT_SKILL_PROJECT_STATE_PATH
    state = _load_skill_project_state(spath)
    last_report = state.get("last_report")
    if not isinstance(last_report, dict) or not last_report.get("tier_b_projects"):
        raise ValueError("当前尚无已生成的候选项目方案，请先加载或生成候选项目。")

    last_report["user_workspace"] = state.setdefault("user_workspace", _default_user_workspace())
    updated_report = apply_project_iteration_feedback(
        last_report,
        project_id=project_id,
        action_type=action_type,
        feedback_text=feedback_text,
        compare_with_id=compare_with_id,
        profile=profile,
    )
    state["user_workspace"] = updated_report["user_workspace"]
    state["last_report"] = updated_report
    _save_skill_project_state(spath, state)
    if conn is not None:
        save_skill_projects_to_db(conn, updated_report)
    return {"ok": True, "comparison": updated_report["user_workspace"].get("last_comparison"), "user_workspace": updated_report["user_workspace"], **updated_report}


def refresh_skill_stats_only(
    items: list[dict],
    profile: dict | None = None,
    conn=None,
    state_path: str | Path | None = None,
) -> dict:
    """操作一（仅更新技能统计）：重新扫描岗位数据更新高频技能与约束清单，绝不无提示覆盖已生成的候选项目、用户已选项目及实施计划（Section 3.2 & 8.5）。"""
    prof = profile if isinstance(profile, dict) else job_filters.load_active_profile()
    mod_cfg = prof.get("skill_project_module") or {}
    min_cat_samples = int(mod_cfg.get("min_category_samples", MIN_CATEGORY_UNIQUE_SAMPLES))
    spath = Path(state_path) if state_path else DEFAULT_SKILL_PROJECT_STATE_PATH
    state = _load_skill_project_state(spath)
    last_report = state.get("last_report")
    workspace = state.setdefault("user_workspace", _default_user_workspace())

    if not isinstance(last_report, dict) or not last_report.get("tier_b_projects"):
        return update_or_get_skill_projects_report(
            items, profile=prof, conn=conn, state_path=spath, force_refresh=True
        )

    _SKILL_STATS_MEM_CACHE.clear()
    new_stats = aggregate_skills_by_category(items, prof, min_category_samples=min_cat_samples)
    new_constraints = build_skill_constraints_checklist(new_stats)
    now_iso = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")

    old_fp = str((last_report.get("version_info") or {}).get("stats_fingerprint") or "")
    new_fp = str(new_stats.get("stats_fingerprint") or "")

    last_report["tier_a_skill_overview"] = new_stats
    last_report["skill_constraints"] = new_constraints
    vinfo = dict(last_report.get("version_info") or {})
    vinfo.update({
        "stats_refreshed_at": now_iso,
        "checked_at": now_iso,
        "latest_stats_fingerprint": new_fp,
        "unique_job_sample_count": new_stats["overall"]["unique_job_count"],
        "raw_posting_count": new_stats["overall"]["raw_posting_count"],
        "sample_sufficient": new_stats["overall"]["sample_sufficient"],
        "stats_drift_detected": old_fp != new_fp,
    })
    if old_fp != new_fp:
        notice = (
            f"📊 岗位技能统计已刷新（新指纹 {new_fp}，原指纹 {old_fp}）。"
            "为保护您的工作进度，您当前已选定的项目、实施计划与迭代修改已完整保留，未被自动覆盖；"
            "若希望按新统计重新生成候选项目，请点击『✨ 重新生成候选项目方案』。"
        )
        workspace["pending_update_notice"] = notice
        vinfo["update_reason"] = notice
    else:
        vinfo["update_reason"] = f"已于 {now_iso} 重新扫描数据库岗位统计，技能频率分布未发生实质漂移，保留当前项目方案。"

    last_report["version_info"] = vinfo
    last_report["user_workspace"] = workspace
    state["last_report"] = last_report
    _save_skill_project_state(spath, state)
    if conn is not None:
        save_skill_projects_to_db(conn, last_report)
    return last_report


def update_or_get_skill_projects_report(
    items: list[dict],
    profile: dict | None = None,
    conn=None,
    state_path: str | Path | None = None,
    force_refresh: bool = False,
    confirm_overwrite: bool = False,
    allow_ai: bool = False,
    ai_caller=None,
) -> dict:
    """模块主入口：普通进入优先读取缓存；数据/画像更新或主动点击重新生成时，保护已选项目与实施计划（Section 7.1, 8, 9）。"""
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
    workspace = state.setdefault("user_workspace", _default_user_workspace())
    if isinstance(last_report, dict) and isinstance(last_report.get("user_workspace"), dict):
        # 合并已有用户选择状态
        if last_report["user_workspace"].get("selected_project_id") and not workspace.get("selected_project_id"):
            workspace = last_report["user_workspace"]
            state["user_workspace"] = workspace

    now_iso = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    first_job_id = (items[0].get("job", items[0]).get("id") if items and isinstance(items[0], dict) else None)
    last_job_id = (items[-1].get("job", items[-1]).get("id") if items and isinstance(items[-1], dict) else None)
    quick_dataset_sig = f"{len(items)}:{first_job_id}:{last_job_id}:{min_cat_samples}"

    # 校验缓存中的候选项目是否全部包含完整的 v2.2 必填结构（防止旧版 v1.0 或残缺缓存导致前端模块空白）
    valid_v2_ids = {bp["project_id"] for bp in PROJECT_BLUEPRINT_CATALOG}
    if workspace.get("selected_project_id") and workspace.get("selected_project_id") not in valid_v2_ids:
        workspace["selected_project_id"] = None
        workspace["selected_project_title"] = ""
        workspace["active_implementation_plan"] = None

    def _is_complete_v2_report(rep_obj: dict | None) -> bool:
        if not isinstance(rep_obj, dict):
            return False
        projs = rep_obj.get("tier_b_projects") or []
        if len(projs) < 6:
            return False
        for p in projs:
            if p.get("project_id") not in valid_v2_ids:
                return False
            if not (p.get("plain_intro") and p.get("layer1_overview") and p.get("tech_selection_rationale")):
                return False
            if not (p.get("skill_coverage_table") and len(p.get("skill_coverage_table")) >= 6):
                return False
            ai_b = p.get("ai_collab_boundary") or {}
            if not (ai_b.get("user_must_do") and ai_b.get("ai_can_accelerate")):
                return False
            mvp_b = p.get("mvp_scope") or {}
            if not (mvp_b.get("must_have") and mvp_b.get("out_of_scope_initially")):
                return False
        return True

    # 1. 快速缓存命中检查（普通进入页面零重复计算、零模型调用）
    if (
        not force_refresh
        and _is_complete_v2_report(last_report)
        and last_report.get("version_info", {}).get("module_version") == SKILL_PROJECT_VERSION
        and last_report.get("version_info", {}).get("profile_version_id") == prof_vid
        and last_report.get("version_info", {}).get("quick_dataset_sig") == quick_dataset_sig
        and last_report.get("skill_constraints")
    ):
        reused = dict(last_report)
        vinfo = dict(reused.get("version_info") or {})
        vinfo["cache_hit"] = True
        vinfo["checked_at"] = now_iso
        reused["version_info"] = vinfo
        reused["user_workspace"] = workspace
        if conn is not None:
            try:
                save_skill_projects_to_db(conn, reused)
            except Exception:
                pass
        return reused

    # 2. 组装输入材料（岗位技能统计 + 技能约束清单 + 个人画像）
    assembled = assemble_skill_project_inputs(items, prof, min_category_samples=min_cat_samples)
    skill_stats = assembled["skill_stats"]
    constraints = assembled["skill_constraints"]
    cur_stats_fp = skill_stats["stats_fingerprint"]
    unique_total = skill_stats["overall"]["unique_job_count"]
    sample_sufficient = skill_stats["overall"]["sample_sufficient"]

    # 3. 样本不足时的保护回退（不编造统计值，保留已有有效项目方案并明确标记数据不足）
    if not sample_sufficient and isinstance(last_report, dict) and last_report.get("tier_b_projects"):
        preserved = dict(last_report)
        preserved["tier_a_skill_overview"] = skill_stats
        preserved["skill_constraints"] = constraints
        preserved["assembled_inputs_summary"] = {
            "assembled_at": assembled["assembled_at"],
            "data_sufficient": False,
            "data_warnings": assembled["data_warnings"],
            "profile_context": assembled["profile_context"],
        }
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
        preserved["user_workspace"] = workspace
        return preserved

    # 4. 指纹完全一致且非强制刷新时，直接复用缓存
    if (
        not force_refresh
        and _is_complete_v2_report(last_report)
        and last_report.get("version_info", {}).get("module_version") == SKILL_PROJECT_VERSION
        and last_report.get("version_info", {}).get("stats_fingerprint") == cur_stats_fp
        and last_report.get("version_info", {}).get("profile_version_id") == prof_vid
        and last_report.get("tier_b_projects")
        and last_report.get("skill_constraints")
    ):
        reused = dict(last_report)
        vinfo = dict(reused.get("version_info") or {})
        vinfo["cache_hit"] = True
        vinfo["checked_at"] = now_iso
        vinfo["quick_dataset_sig"] = quick_dataset_sig
        reused["version_info"] = vinfo
        reused["user_workspace"] = workspace
        state["last_report"] = reused
        _save_skill_project_state(spath, state)
        return reused

    # 5. 防无提示覆盖保护（Section 8.5 & 8.6）：
    # 如果用户已经选定了项目或生成了实施计划，且未显式传入 confirm_overwrite=True：
    # - 自动刷新时保留用户已选项目、已生成计划与自定义迭代修改，并将旧工作区快照归档到 archived_workspaces！
    has_active_selection = bool(workspace.get("selected_project_id") or workspace.get("active_implementation_plan"))
    if has_active_selection and isinstance(last_report, dict):
        archived_list = workspace.setdefault("archived_workspaces", [])
        archived_list.append({
            "archived_at": now_iso,
            "previous_report_version": (last_report.get("version_info") or {}).get("report_version_id", ""),
            "selected_project_id": workspace.get("selected_project_id"),
            "selected_project_title": workspace.get("selected_project_title"),
            "had_implementation_plan": workspace.get("active_implementation_plan") is not None,
            "iteration_count": len(workspace.get("iteration_history") or []),
        })
        workspace["archived_workspaces"] = archived_list[-5:]
        if not confirm_overwrite:
            workspace["pending_update_notice"] = (
                f"🛡️ 检测到画像或技能统计更新（{now_iso}）：系统已刷新候选项目的数据依据，"
                f"同时自动为您保留了已选项目『{workspace.get('selected_project_title')}』及其实施计划（历史快照已归档）。"
            )
        else:
            # 用户显式确认重置选择
            workspace["selected_project_id"] = None
            workspace["selected_project_title"] = ""
            workspace["selected_at"] = ""
            workspace["active_implementation_plan"] = None
            workspace["pending_update_notice"] = f"已于 {now_iso} 按您的确认重置项目选择并生成全新候选方案（原方案已归档至历史记录）。"

    # 6. 生成 6 个兴趣驱动的候选项目与技能覆盖审计表（支持可选 AI 增强 + 失败安全回退）
    report_bundle = recommend_projects_for_skills(skill_stats, prof, skill_constraints=constraints)
    ai_note = ""
    ai_error = ""
    used_ai = False
    if allow_ai or ai_caller is not None or bool(mod_cfg.get("use_ai_for_project_insights", False)):
        try:
            caller = ai_caller
            if caller is None:
                import analyzer as _analyzer_mod
                caller = _analyzer_mod.call_deepseek_api
            top_sk_names = [s["canonical_name"] for s in skill_stats["overall"]["top_skills"][:6]]
            hobbies = (prof.get("personal_profile") or {}).get("hobbies_and_general_interests") or []
            sys_p = PROJECT_REDESIGN_PROMPT_TEMPLATE
            usr_p = (
                f"独立岗位样本：{unique_total}个；高频技能Top6：{', '.join(top_sk_names)}；"
                f"个人兴趣切入点：{'; '.join(str(h)[:40] for h in hobbies[:4])}；"
                "请用120字以内给出一条将上述硬核技能融入个人兴趣项目的定制化架构建议。"
            )
            raw_resp = caller(sys_p, usr_p)
            if not raw_resp or not str(raw_resp).strip():
                raise RuntimeError("AI 模型返回了空白内容")
            ai_note = str(raw_resp).strip()[:280]
            used_ai = True
        except Exception as exc:
            ai_error = job_filters.sanitize_sensitive_text(str(exc))[:200]
            logger.warning("技能项目模块 AI 创意增强调用失败，保留确定性工程重设计方案: %s", ai_error)
            # 若调用失败且已有旧版有效结果，在 version_info 中记录错误状态并保留有效工程方案（Section 9）

    # 重放用户之前已保存的针对特定项目的迭代修改（如缩小 MVP 或强化某技能），确保刷新数据不冲掉用户的修改！
    if not confirm_overwrite and workspace.get("iteration_history"):
        saved_iters = list(workspace.get("iteration_history") or [])
        workspace["iteration_history"] = []
        temp_bundle = {
            **report_bundle,
            "user_workspace": workspace,
        }
        for it_rec in saved_iters:
            if it_rec.get("action_type") != "compare_two":
                apply_project_iteration_feedback(
                    temp_bundle,
                    project_id=str(it_rec.get("project_id") or ""),
                    action_type=str(it_rec.get("action_type") or ""),
                    feedback_text=str(it_rec.get("user_feedback") or ""),
                    profile=prof,
                )
        report_bundle["tier_b_projects"] = temp_bundle["tier_b_projects"]

    report_vid = "sp-" + hashlib.sha256(f"{cur_stats_fp}:{prof_vid}:{SKILL_PROJECT_VERSION}".encode("utf-8")).hexdigest()[:8]
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
        "last_error": ai_error,
        "generation_status": "fallback_kept_valid" if ai_error else ("ok" if sample_sufficient else "insufficient_data_warning"),
        "update_reason": (
            f"⚠️ 模型调用异常 ({ai_error})，已安全保留并展示确定性工程重设计方案。"
            if ai_error
            else "已基于真实招聘岗位技能约束清单与个人兴趣画像生成 6 个候选工程方案。"
        ),
    }

    full_result = {
        "enabled": True,
        "status": "ok",
        "version_info": version_info,
        "assembled_inputs_summary": {
            "assembled_at": assembled["assembled_at"],
            "data_sufficient": assembled["data_sufficient"],
            "data_warnings": assembled["data_warnings"],
            "profile_context": assembled["profile_context"],
        },
        "user_workspace": workspace,
        **report_bundle,
    }

    state["last_report"] = full_result
    state["user_workspace"] = workspace
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
    """将技能统计快照、候选项目及其技能覆盖表、用户选择与实施计划幂等写入 SQLite 数据库（Section 8）。"""
    vinfo = report_data.get("version_info") or {}
    stats = report_data.get("tier_a_skill_overview") or {}
    projects = report_data.get("tier_b_projects") or []
    advice = report_data.get("tier_c_practice_advice") or {}
    workspace = report_data.get("user_workspace") or _default_user_workspace()

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

    valid_pids = [str(p.get("project_id") or "") for p in projects if p.get("project_id")]
    if valid_pids:
        placeholders = ",".join("?" for _ in valid_pids)
        conn.execute(f"DELETE FROM skill_project_recommendations WHERE project_id NOT IN ({placeholders})", tuple(valid_pids))
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

    # 持久化用户项目选择、实施计划与迭代反馈历史
    conn.execute(
        """CREATE TABLE IF NOT EXISTS skill_project_user_workspace (
            workspace_key            TEXT PRIMARY KEY,
            selected_project_id      TEXT DEFAULT '',
            selected_at              TEXT DEFAULT '',
            implementation_plan_json TEXT DEFAULT '{}',
            plan_generated_at        TEXT DEFAULT '',
            iteration_history_json   TEXT DEFAULT '[]',
            comparison_history_json  TEXT DEFAULT '[]',
            profile_version_id       TEXT DEFAULT '',
            stats_fingerprint        TEXT DEFAULT '',
            pending_update_notice    TEXT DEFAULT '',
            updated_at               TEXT DEFAULT ''
        )"""
    )
    active_plan = workspace.get("active_implementation_plan") or {}
    conn.execute(
        """INSERT INTO skill_project_user_workspace
           (workspace_key, selected_project_id, selected_at, implementation_plan_json,
            plan_generated_at, iteration_history_json, comparison_history_json,
            profile_version_id, stats_fingerprint, pending_update_notice, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(workspace_key) DO UPDATE SET
               selected_project_id=excluded.selected_project_id,
               selected_at=excluded.selected_at,
               implementation_plan_json=excluded.implementation_plan_json,
               plan_generated_at=excluded.plan_generated_at,
               iteration_history_json=excluded.iteration_history_json,
               comparison_history_json=excluded.comparison_history_json,
               profile_version_id=excluded.profile_version_id,
               stats_fingerprint=excluded.stats_fingerprint,
               pending_update_notice=excluded.pending_update_notice,
               updated_at=excluded.updated_at""",
        (
            "default",
            str(workspace.get("selected_project_id") or ""),
            str(workspace.get("selected_at") or ""),
            json.dumps(active_plan, ensure_ascii=False),
            str(active_plan.get("generated_at") or ""),
            json.dumps(workspace.get("iteration_history") or [], ensure_ascii=False),
            json.dumps(workspace.get("comparison_history") or [], ensure_ascii=False),
            prof_vid,
            stats_fp,
            str(workspace.get("pending_update_notice") or ""),
            str(workspace.get("last_updated_at") or gen_at),
        ),
    )
    conn.commit()
