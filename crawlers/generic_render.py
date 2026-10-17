"""通用渲染爬虫（增强版） —— 服务「自建 SPA 校招站」(无公开 API 或 API 带反爬)。

升级亮点：
1. XHR/JSON 自动嗅探：在 Playwright 渲染过程中自动监听后台 JSON 响应，若发现包含
   positionName/jobName/postName/title 等标准字段的职位数组，直接提取结构化岗位，
   彻底绕过复杂 DOM 选择器限制。
2. 选择器与解析器口径对齐 + 优先具名 Class：消除 `_pick_selector` 选出裸 `div`/`a` 或选出
   含“发布时间/招聘人数”的卡片后在 `_parse` 阶段被 `_CONTEXT_NOISE` 误杀导致抓到 0 个岗位的问题。
3. 快速超时降级：将 networkidle 超时缩短并自动降级为 domcontentloaded，避免死链卡死 90 秒。
"""
import logging
import re
from collections import defaultdict
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .base import BaseCrawler

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_CITY_NAMES = (
    "北京", "上海", "天津", "重庆", "香港", "澳门", "深圳", "广州", "杭州",
    "南京", "苏州", "无锡", "常州", "南通", "徐州", "扬州", "镇江", "昆山",
    "宁波", "温州", "嘉兴", "绍兴", "金华", "台州", "湖州", "武汉", "宜昌",
    "襄阳", "成都", "绵阳", "西安", "合肥", "芜湖", "长沙", "株洲", "郑州",
    "洛阳", "济南", "青岛", "烟台", "潍坊", "威海", "厦门", "福州", "泉州",
    "漳州", "珠海", "东莞", "佛山", "惠州", "中山", "大连", "沈阳", "长春",
    "哈尔滨", "石家庄", "太原", "南昌", "南宁", "海口", "贵阳", "昆明",
    "兰州", "乌鲁木齐", "呼和浩特", "全国", "海外", "国外",
)
_CITY_RE = re.compile(
    rf"({'|'.join(sorted(_CITY_NAMES, key=len, reverse=True))})(?:市)?"
)
_JOB_KW = re.compile(
    r"工程师|研发|开发|算法|架构|设计师|实习生|管培|培训生|专员|主管|"
    r"产品经理|运营|测试|分析师|顾问|储备|工艺|科学家|研究员|策划|助理|应届|博士后|"
    r"trainee|engineer|developer|intern|graduate|analyst",
    re.I,
)
_NOISE = re.compile(
    r"筛选|清除|职位类别|工作地点|大职能|仅查看|热门|须知|登录|注册|开发者论坛|开发者平台|开发者区域|"
    r"计划是|面向|推出|全球|高校|在校生|清空|展开|收起|首页|关于|联系|"
    r"招聘流程|投递方式|个人中心|加入我们|了解更多|立即申请|"
    r"校招流程|校招岗位|校招FAQ|FAQ|招聘公告|招聘简章|招聘动态|校招行程|快捷通道|"
    r"应届生招聘|实习生招聘|博士生招聘|进入校招|实习机会|"
    r"产品类|技术类|职能类|运营市场类|研发美术类|运营美术类|岗位类别|"
    r"子类别|业务开发与支持|系统开发管理|系统运维管理|网络规划与投资计划|项目计划管理|网络运营管理|"
    r"开发板|评估板|参考设计|所有开发板|开发工具|测试服务|"
    r"研发中心|开发团队|业务架构|工程中心|工厂中心|需求部门|"
    r"职位描述|岗位职责|任职要求|工作内容|招聘对象|招收对象|毕业时间|"
    r"年及以上|三年及以上|五年及以上|经验者优先|全流程测试能力|大学|"
    r"产品中心|解决方案|开发成本|协同研发|硬件基础|开箱即用|"
    r"高性能CAE|几何内核|约束求解|数字化仿真|数字化制造|数字化设计|"
    r"行业应用|应用验证|信创生态|自主可控|功能成熟|高效替代|合作咨询|人事招聘|"
    r"测试机|通用数字测试机|复杂SoC测试机|DDIC测试机|CIS测试机|半导体测试机|"
    r"摄像头模组测试|测试系统软件开发服务",
    re.I,
)
_BAD_TITLE_RE = re.compile(r"^\d+[、.，,]|[。；;]$|^20(1\d|2[0-4])-\d{2}-\d{2}|类\s*\|\s*")
# 注意：正规校招卡片常含“发布时间/招聘人数/工作地点/了解更多/薪资范围”，绝不可放入 _CONTEXT_NOISE 误杀
_CONTEXT_NOISE = re.compile(
    r"社会招聘|三年及以上|五年及以上|3年以上工作经验|5年以上工作经验|博士后工作站|"
    r"双选会|宣讲会|基层就业|学院就业|"
    r"通用数字测试机|复杂SoC测试机|DDIC测试机|CIS测试机|半导体测试机|摄像头模组测试|"
    r"单位名称|单位所在地",
    re.I,
)
_NON_JOB_HOSTS = {
    "youtube.com", "www.youtube.com", "linkedin.com", "www.linkedin.com",
    "bilibili.com", "www.bilibili.com", "weibo.com", "www.weibo.com",
}

_JSON_TITLE_KEYS = ("title", "name", "jobName", "positionName", "postName", "recruitPostName", "zwmc")
_JSON_CITY_KEYS = ("city", "cityName", "workPlace", "workCity", "location", "address", "gzdd")
_JSON_DESC_KEYS = ("description", "jd", "duty", "requirement", "workContent", "postDuty", "serviceCondition")


class GenericRenderCrawler(BaseCrawler):
    MAX_PAGES = 20
    NAVIGATION_ATTEMPTS = 2
    JD_RAW_LIMIT = 200
    MIN_LEN, MAX_LEN = 4, 38
    MIN_HITS = 2  # 降低至2，避免小规模招聘页被误判为空

    def _list_url(self) -> str:
        return self.careers_url

    def _clean_title(self, text: str) -> str:
        raw = (text or "").strip()
        for part in re.split(r"[\n\r|｜丨/]+", raw):
            cand = re.sub(r"\s+", " ", part).strip()
            cand = re.sub(r"\s*(立即投递|申请职位|投递简历|查看详情|了解更多|NEW|HOT)\s*$", "", cand, flags=re.I).strip()
            if "个岗位" in cand or cand.endswith("类"):
                continue
            if re.fullmatch(r"developers?", cand, re.I):
                continue
            if self.MIN_LEN <= len(cand) <= self.MAX_LEN and not _BAD_TITLE_RE.search(cand) and not _NOISE.search(cand) and _JOB_KW.search(cand):
                return cand
        return ""

    @staticmethod
    def _extract_city(text: str) -> str:
        matches = [match.group(1) for match in _CITY_RE.finditer(text or "")]
        return "、".join(dict.fromkeys(matches))[:40]

    @classmethod
    def _extract_job_city(cls, title: str, context: str) -> str:
        title_city = cls._extract_city(title)
        if title_city:
            return title_city
        location = re.search(r"工作地点.{0,40}", context or "")
        if location:
            cities = cls._extract_city(location.group(0))
            if cities:
                return cities.split("、", 1)[0]
        cities = cls._extract_city((context or "")[:120])
        return cities.split("、", 1)[0] if cities else ""

    @staticmethod
    def _card_context(el) -> str:
        context = el.get_text(" ", strip=True)
        parent = el.parent
        for _ in range(4):
            if not parent:
                break
            candidate = parent.get_text(" ", strip=True)
            if len(candidate) > 600:
                break
            context = candidate
            parent = parent.parent
        return context

    def _extract_jobs_from_json_payload(self, data, out_jobs: list, seen: set) -> None:
        """递归扫描 SPA 页面后台返回的 JSON 数据包，提取职位列表。"""
        if isinstance(data, dict):
            title_val = ""
            for k in _JSON_TITLE_KEYS:
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    cleaned = self._clean_title(v)
                    if cleaned:
                        title_val = cleaned
                        break
            if title_val and any(k in data for k in (*_JSON_CITY_KEYS, *_JSON_DESC_KEYS, "id", "postId", "jobId", "positionId")):
                key = f"json:{title_val}"
                if key not in seen:
                    seen.add(key)
                    city_val = ""
                    for ck in _JSON_CITY_KEYS:
                        cv = data.get(ck)
                        if isinstance(cv, str) and cv.strip():
                            city_val = cv.strip()[:40]
                            break
                    desc_parts = [str(data.get(dk) or "").strip() for dk in _JSON_DESC_KEYS if data.get(dk)]
                    jd_raw = "\n".join(p for p in desc_parts if p)
                    out_jobs.append(self._make_job(
                        title=title_val,
                        city=city_val,
                        jd_url=self._list_url(),
                        jd_raw=jd_raw,
                        link_kind="list",
                    ))
            for v in data.values():
                if isinstance(v, (dict, list)):
                    self._extract_jobs_from_json_payload(v, out_jobs, seen)
        elif isinstance(data, list) and len(data) <= 500:
            for item in data:
                if isinstance(item, (dict, list)):
                    self._extract_jobs_from_json_payload(item, out_jobs, seen)

    def fetch(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            logger.error("[%s] 未安装 playwright", self.company_name)
            return []

        jobs, seen = [], set()
        xhr_jobs, xhr_seen = [], set()
        selector_sig = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                          "--disable-dev-shm-usage"],
                )
                ctx = browser.new_context(user_agent=_UA, viewport={"width": 1366, "height": 768},
                                          locale="zh-CN", ignore_https_errors=True)
                page = ctx.new_page()

                def _on_response(resp):
                    try:
                        ctype = (resp.headers.get("content-type") or "").lower()
                        if "json" in ctype and resp.status == 200:
                            payload = resp.json()
                            self._extract_jobs_from_json_payload(payload, xhr_jobs, xhr_seen)
                    except Exception:
                        pass

                page.on("response", _on_response)
                reachable = self._goto_with_retry(page, PWTimeout)
                if not reachable and not xhr_jobs:
                    ctx.close(); browser.close()
                    return []
                page.wait_for_timeout(2000)

                self._click_campus_entry(page)
                selector_sig = self._pick_selector(page.content())
                if not selector_sig:
                    for kw in ("校园招聘", "校招", "应届", "查看职位", "全部职位",
                               "职位列表", "研发", "技术", "职位查询", "立即查看", "热招职位"):
                        try:
                            loc = page.get_by_text(re.compile(kw)).first
                            if loc.count() == 0:
                                continue
                            loc.click(timeout=2500)
                            page.wait_for_timeout(1500)
                            selector_sig = self._pick_selector(page.content())
                            if selector_sig:
                                break
                        except Exception:
                            continue

                if not selector_sig:
                    if xhr_jobs:
                        logger.info("[%s] 通过后台 JSON 接口自动嗅探抓到 %d 个岗位", self.company_name, len(xhr_jobs))
                        ctx.close(); browser.close()
                        return xhr_jobs
                    line_jobs = self._parse_line_jobs(page.content())
                    if line_jobs:
                        ctx.close(); browser.close()
                        return line_jobs
                    logger.info("[%s] 页面当前无公开职位列表（非招聘季空窗/仅宣讲海报），自动跳过", self.company_name)
                    ctx.close(); browser.close()
                    return []

                for _ in range(self.MAX_PAGES):
                    self._parse(page.content(), selector_sig, jobs, seen)
                    nxt = page.locator(
                        ".btn-next, .el-pagination .btn-next, .ant-pagination-next, "
                        "[class*='pagination'] [class*='next'], li[title='下一页'], a[aria-label='Next']"
                    ).first
                    try:
                        if nxt.count() == 0:
                            break
                        cls = (nxt.get_attribute("class") or "") + str(nxt.get_attribute("aria-disabled"))
                        if "disabled" in cls.lower():
                            break
                        nxt.click(timeout=3000)
                        page.wait_for_timeout(1000)
                    except Exception:
                        break

                if not jobs and xhr_jobs:
                    jobs.extend(xhr_jobs)
                if not jobs:
                    jobs.extend(self._parse_line_jobs(page.content()))

                ctx.close()
                browser.close()
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] 渲染爬取异常: %s", self.company_name, e)

        if not jobs:
            logger.info("[%s] 页面未提取到有效校招岗位（选择器=%s），自动跳过",
                        self.company_name, selector_sig or "无")
            return []

        logger.info("[%s] 通用渲染 抓到 %d 个岗位（选择器=%s）",
                    self.company_name, len(jobs), selector_sig or "XHR-JSON")
        return jobs

    def _goto_with_retry(self, page, timeout_error) -> bool:
        """先尝试 networkidle (20s)，若超时降级为 domcontentloaded (15s)，防止死链拖慢全局。"""
        url = self._list_url()
        if not url or " " in url or any(ord(c) > 127 for c in urlsplit(url).netloc):
            logger.warning("[%s] 忽略非合法网址 (%s)", self.company_name, url)
            return False
        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
            return True
        except timeout_error:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                return True
            except Exception as exc:
                logger.warning("[%s] 网址无法打开或超时 (%s): %s", self.company_name, url, exc)
                return False
        except Exception as exc:
            logger.warning("[%s] 网址连接失败 (%s): %s", self.company_name, url, exc)
            return False

    def _parse_line_jobs(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        jobs, seen = [], set()
        for match in re.finditer(r"(?:^|\n)\s*(?:职位|岗位(?:名称)?)\s*[：:]\s*([^\n\r]{2,40})", text):
            title = self._clean_title(match.group(1))
            if not title or title in seen:
                continue
            seen.add(title)
            jobs.append(self._make_job(
                title=title, city="", jd_url=self._list_url(), link_kind="list",
            ))
        return jobs

    def _sig(self, el) -> str:
        cls = ".".join(el.get("class") or [])
        return f"{el.name}.{cls}" if cls else el.name

    def _direct_text(self, el) -> str:
        direct = "".join(t for t in el.find_all(string=True, recursive=False)).strip()
        if direct:
            return direct
        for child in el.find_all(["h2", "h3", "h4", "h5", "span", "p", "div", "a"], recursive=True):
            ct = "".join(t for t in child.find_all(string=True, recursive=False)).strip()
            if self.MIN_LEN <= len(ct) <= self.MAX_LEN and _JOB_KW.search(ct):
                return ct
        return el.get_text("\n", strip=True)

    def _job_link(self, el) -> tuple[str, str]:
        nodes = [el]
        parent = el.parent
        for _ in range(3):
            if not parent:
                break
            nodes.append(parent)
            parent = parent.parent
        for node in nodes:
            anchors = [node] if getattr(node, "name", "") == "a" else node.find_all("a", href=True)
            for anchor in anchors:
                href = (anchor.get("href") or "").strip()
                if not href or href == "#" or href.lower().startswith("javascript:"):
                    continue
                resolved = urljoin(self._list_url(), href)
                if resolved == self._list_url() or re.search(r"#\d+$", resolved):
                    continue
                if urlsplit(resolved).netloc.casefold() in _NON_JOB_HOSTS:
                    continue
                return resolved, "detail"
        return self._list_url(), "list"

    def _click_campus_entry(self, page) -> None:
        for kw in ("校园招聘", "校招岗位", "校招职位", "应届生招聘", "校园职位", "实习生招聘"):
            try:
                loc = page.get_by_text(re.compile(kw)).first
                if loc.count() == 0:
                    continue
                loc.click(timeout=2000)
                page.wait_for_timeout(1200)
                return
            except Exception:
                continue

    def _pick_selector(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        score = defaultdict(int)
        seen_by_sig = defaultdict(set)
        for el in soup.find_all(["a", "h2", "h3", "h4", "h5", "span", "div", "p", "li"]):
            title = self._clean_title(self._direct_text(el))
            if not title:
                continue
            ctext = self._card_context(el)
            if _CONTEXT_NOISE.search(ctext):
                continue
            sig = self._sig(el)
            if title not in seen_by_sig[sig]:
                seen_by_sig[sig].add(title)
                score[sig] += 1
        if not score:
            return ""
        # 优先选择带有具体 CSS class 的选择器，避免无 class 的裸 div/a 抢票
        classed = {k: v for k, v in score.items() if "." in k and v >= self.MIN_HITS}
        if classed:
            best, _ = max(classed.items(), key=lambda kv: kv[1])
            return best
        best, n = max(score.items(), key=lambda kv: kv[1])
        return best if n >= self.MIN_HITS else ""

    def _parse(self, html: str, sig: str, jobs: list, seen: set) -> int:
        soup = BeautifulSoup(html, "html.parser")
        new = 0
        for el in soup.find_all(["a", "h2", "h3", "h4", "h5", "span", "div", "p", "li"]):
            if self._sig(el) != sig:
                continue
            title = self._clean_title(self._direct_text(el))
            if not title:
                continue
            key = str(abs(hash(title)) % (10 ** 8))
            if key in seen:
                continue
            ctext = self._card_context(el)
            if _CONTEXT_NOISE.search(ctext):
                continue
            seen.add(key)
            city = self._extract_job_city(title, ctext)
            jd_url, link_kind = self._job_link(el)
            jobs.append(self._make_job(
                title=title, city=city, jd_url=jd_url, link_kind=link_kind,
            ))
            new += 1
        return new
