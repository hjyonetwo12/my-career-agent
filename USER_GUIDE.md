# Career Agent（AI校招情报）本科生全流程操作与维护手册

本手册面向日常使用本项目的本科同学编写，无需具备专业的运维背景，按照步骤即可完成**本地运行、GitHub 自动运行、飞书推送接入、结果查看、数据同步与个人求职需求（`profile.yaml`）日常调整**。

## 一、项目核心文件与三个 `.bat` 脚本速览

在项目根目录下，你会看到以下核心文件与脚本，它们各司其职：

| 文件 / 脚本名 | 实际执行的命令 | 核心用途与适用场景 | 是否联网抓取招聘站 | 是否消耗 DeepSeek API 额度 | 
| ----- | ----- | ----- | ----- | ----- | 
| **`启动.bat`** | `python main.py` | **完整流水线**：抓取各企业最新岗位 → 清洗去重入库 → 届别与资格筛选 → AI 评分 → 检测个人配置变化并更新综合建议 → 生成 `reports/index.html` 与 `reports/YYYY-MM-DD.html` → 推送飞书摘要 → 自动弹出报告网页。 | 是 | 是（仅新增或变更的 A 档岗位及配置发生实质变化时消耗） | 
| **`查看.bat`** | `python view_all.py` | **快速查看与本地重算模式**：不联网爬招聘站、不调用岗位评分 API，直接读取本地 `data/jobs.db` 重新生成 `reports/index.html`（若 `profile.yaml` 有修改会自动刷新本地匹配分与综合建议）并打开浏览器。 | 否 | 否（默认零 Token，极速生成） | 
| **`管理.bat`** | `python webapp.py` | **本地投递看板与交互服务**：在本地启动 `http://localhost:5000` 网页服务，支持点击「✅ 我投了」、修改笔面试阶段、添加日程、点击「🔄 重新生成综合建议」，并自动将 `data/applications.json` 提交推送到 GitHub。 | 否 | 否（仅在手动点击「强制 AI 重新生成建议」且开启 AI 时消耗） | 
| **`profile.yaml`** | 配置文件（无需运行） | **唯一的日常个人需求配置入口**：存放你的学历、目标毕业届别、求职阶段、已掌握技术栈、项目证据、重点探索方向 vs 一般兴趣支线、WLB 偏好与目标城市。 | — | — | 
| **`config.yaml`** | 配置文件（无需运行） | **系统级企业名单与模型参数**：配置数百个企业校招官网入口、爬虫类型及单次/单日 AI 评分预算上限。日常无需频繁修改。 | — | — | 

## 二、本地运行操作指南

### 1. 启动前需准备的环境

首次在自己的 Windows 电脑上运行前，请确认已安装 **Python 3.10 或更高版本（推荐 Python 3.11）** 以及 **Git**。

在项目根目录按住 `Shift` 右键，选择 **“在终端中打开”**（或打开 PowerShell `cd` 到项目根目录），依次复制运行以下检查与安装命令：

```
# 1. 检查 Python 版本（输出应为 Python 3.10.x / 3.11.x / 3.12.x）
python --version

# 2. 安装项目运行所需的 Python 依赖包
python -m pip install -r requirements.txt

# 3. 安装测试与开发辅助依赖（用于运行 pytest 验证）
python -m pip install -r requirements-dev.txt

# 4. 安装 Playwright Chromium 浏览器内核（仅运行 启动.bat 抓取动态网页时需要）
python -m playwright install chromium

```

### 2. 设置 API 密钥与环境变量（`.env` 文件）

项目绝不将密钥硬编码在代码中，本地运行统一从项目根目录的 `.env` 文件读取凭证：

1. 在项目根目录复制 `.env.example` 并重命名为 `.env`：

   ```
   Copy-Item .env.example .env
   
   ```

2. 用记事本或 VS Code 打开 `.env`，按需填写：

   ```
   # 必填（若需 AI 深度评分与 AI 综合建议生成）：填入你的 DeepSeek API Key
   DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   
   # 选填：飞书群自定义机器人 Webhook URL（不填则跳过飞书推送，不影响生成本地网页报告）
   FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   
   # 选填：公开部署的报告网址（如 Cloudflare Pages 或 GitHub Pages 地址，用于在飞书消息末尾附带完整报告链接）
   REPORT_BASE_URL=https://你的用户名.github.io/career-agent
   
   ```

> **安全提醒**：`.env` 已被写入 `.gitignore`，Git 不会将其提交到远程仓库。切勿把 `sk-` 开头的密钥或飞书 Webhook 粘贴到 `profile.yaml` 或 `config.yaml` 中。

### 3. 在哪个目录运行、正常输出与成功标志

请始终在**项目根目录**（即包含 `main.py`、`profile.yaml`、`启动.bat` 的文件夹）双击脚本或运行命令：

#### 方式 A：日常秒看已有岗位与综合建议（最常用，耗时 15\~30 秒）

* **操作**：双击 `查看.bat`，或在终端执行 `python view_all.py`。

* **正常输出**：

  ```
  [配置闭环] profile.yaml 版本 v-xxxxxxxx (状态: unchanged / updated)
  累计报告已生成：D:\...\career-agent\reports\index.html
    共 17108 个活跃岗位
    其中 916 个已完成深度分析
  
  ```

* **如何判断真正成功**：不仅弹出了命令行窗口，且终端打印出 `累计报告已生成：... eports\index.html`，同时浏览器自动打开了左上角标有 **「AI校招情报」** 的网页报告。

#### 方式 B：执行完整抓取 + AI 评分 + 飞书推送（耗时约 5\~25 分钟）

* **操作**：双击 `启动.bat`，或在终端执行 `python main.py`。

* **正常输出**：

  1. `[INFO] DeepSeek 混合模式：岗位粗筛=deepseek-v4-flash，JD 细分析=deepseek-v4-pro`

  2. `[INFO] 数据库已就绪: ...\data\jobs.db`

  3. `[INFO] 开始抓取 N 家企业...` 及各家企业的抓取进度

  4. `[INFO] 本轮届别分流：确认27届 X、确认往届 Y、届别待确认 Z`

  5. `[INFO] 细分析完成：成功 M / 失败 0`

  6. `[INFO] 综合建议状态：版本 v-...`

  7. `今日变化：新增 X 个 / 下线 Y 个` 与 `活跃岗位总数：... → ... eports\YYYY-MM-DD.html`

* **结果保存位置**：

  * **最新累计总报告（含全部页面、全局目录、综合建议）**：`reports/index.html`（以及备份在 `outputs/index.html`）

  * **当日快照报告**：`reports/YYYY-MM-DD.html`

  * **单次运行统计摘要 JSON**：`outputs/latest_run_summary.json`

  * **本地运行日志文件**：`outputs/run.log`

  * **综合建议版本历史库**：`data/advice_state.json`

#### 方式 C：管理个人投递进度与手动触发刷新

* **操作**：双击 `管理.bat`，或在终端执行 `python webapp.py`。

* **成功标志**：终端显示 `投递记录管理器已启动：http://localhost:5000/`，浏览器打开后每个岗位右侧出现绿色「✅ 我投了」按钮，且「综合建议」页面顶部可点击「🔄 重新生成综合建议」。

### 4. 本地常见报错含义与处理方式

| 现象 / 报错信息 | 真实原因 | 解决步骤 | 
| ----- | ----- | ----- | 
| `profile.yaml 缺少字段` 或 `score_component_limits 必须包含五个评分项且合计 100` | 修改 `profile.yaml` 时删掉了必填字段、缩进用了 Tab 键，或 5 项评分权重加起来不等于 100。 | 对照本文第四节检查 `profile.yaml` 冒号后的空格与 5 项权重之和（如 `20+25+20+15+20=100`）。 | 
| `未找到 DEEPSEEK_API_KEY` | 未创建 `.env` 或未填写密钥。 | 若只需看本地评分与实证建议，可直接用 `查看.bat`；若需调用 DeepSeek Pro 精析，请在 `.env` 中填入 `DEEPSEEK_API_KEY`。 | 
| `[某公司] 网址无法打开或超时` / `页面当前无公开职位列表，自动跳过` | 该企业当前处于非招聘季空窗期、官网维护或有反爬防火墙。 | **属于正常隔离现象**：单家企业超时不会中断全局任务，也不会把该公司历史岗位误判为下线，无需人工干预。 | 
| `Executable doesn't exist at ... ms-playwright` | 尚未安装 Playwright 浏览器内核。 | 在终端运行 `python -m playwright install chromium`。 | 
| `sqlite3.OperationalError: database is locked` | 同时打开了多个 `main.py` 或 DB Browser 锁住了 `data/jobs.db`。 | 关闭其他正在运行 `main.py` 的窗口后再重试。 | 

## 三、GitHub Actions 自动运行、手动触发与结果查看

本项目在 `.github/workflows/` 下配置了 3 个真实工作流：

1. **`Daily Recruitment Report`**（文件：`.github/workflows/daily_report.yml`）：每日自动抓取、AI 评分、更新综合建议、回写数据库、发布网页报告并推送飞书。

2. **`Sync report (applications)`**（文件：`.github/workflows/sync_report.yml`）：当你推送了 `data/applications.json` 或修改了 `profile.yaml` 时，免爬虫、零 Token 快速重新生成报告并发布。

3. **`Tests`**（文件：`.github/workflows/tests.yml`）：每次提交代码时自动运行离线单元测试。

### 1. GitHub Actions 网页端完整操作步骤

1. **打开仓库**：在浏览器登录 GitHub，进入你的 `career-agent` 仓库主页。

2. **进入 Actions 页**：点击顶部导航栏的 **Actions** 标签页。

3. **选择工作流**：在左侧工作流列表中点击 **`Daily Recruitment Report`**（日常完整抓取与评分）或 **`Sync report (applications)`**（仅快速刷新报告与综合建议）。

4. **手动触发运行（Run workflow）**：

   * 点击右侧蓝色的 **Run workflow** 下拉按钮。

   * 分支保持 `main`，你会看到以下可选运行参数（已配置在 `daily_report.yml` 中）：

     * `skip_crawl`（默认 `false`）：若勾选 `true`，则跳过耗时爬虫，直接基于现有数据库重新评估并生成报告与建议。

     * `force_advice_refresh`（默认 `false`）：若勾选 `true`，强制重新生成一份新的综合建议版本。

     * `max_pro_jobs`（默认留空使用 `config.yaml` 配置）：可临时指定本次最多调用 DeepSeek Pro 评分的岗位数。

   * 点击绿色的 **Run workflow** 按钮启动任务。

5. **识别运行状态**：

   * 🟡 **黄色旋转圆圈（In progress / Queued）**：正在运行或排队等待。

   * 🟢 **绿色对勾（Success）**：全部步骤执行成功。

   * 🔴 **红色叉号（Failure）**：某个关键步骤出错中断。

   * ⚪ **灰色感叹号/斜杠（Cancelled / Skipped）**：被手动取消或跳过。

6. **查看各步骤与报错日志**：

   * 点击该次运行记录标题 → 点击左侧或中间的 **`run`** 作业（Job）。

   * 页面会列出每个步骤（`Checkout` → `Install dependencies` → `Run pipeline` → `Upload report artifact` → `Commit DB back to main` → `Deploy report to gh-pages`）。

   * 若某一步骤标红，点击展开该步骤，直接滑动到底部查看 `[ERROR]` 或 `Traceback` 报错原因。

### 2. GitHub 运行后的 4 种结果查看方式（不只是看日志！）

> **重要机制说明**：GitHub Actions 的运行机器（Runner）使用的是**临时文件系统**，任务一结束虚拟机会立刻销毁。因此本项目专门设计了以下 **3 重持久化机制 + 1 重即时通知**，确保运行结果永久保存：

#### 方式 1：直接在手机/电脑收到飞书群机器人摘要推送（最省心）

* 每次 `Daily Recruitment Report` 运行结束后，飞书机器人会自动向你的飞书群发送一张富文本卡片，包含：

  * **运行状态与耗时**、**本轮抓取总数**、**新增岗位数**、**更新岗位数**、**去重数量**、**通过筛选数**、**AI 评分成功/失败数**及**综合建议版本状态**。

  * **高价值推荐岗位列表**（公司、原始职位名称、匹配分数、推荐理由/优势、主要技能缺口、直达投递链接）。

  * 点击卡片底部的 **「📄 完整报告」** 即可在手机或电脑浏览器打开完整交互网页。

#### 方式 2：在 GitHub Actions 页面直接下载报告压缩包（Artifact，无需配置域名）

* 即使你没有配置飞书或 GitHub Pages，每次运行结束时，`Upload report and run summary artifact` 步骤都会把生成的网页报告打包上传：

  1. 点进当次成功的 Actions 运行记录主页（Summary 页）。

  2. 向下滚动到页面底部的 **Artifacts** 区域。

  3. 点击下载 **`career-agent-report`** 压缩包（默认保留 **30 天**）。

  4. 解压后双击里面的 **`index.html`**，即可在本地浏览器完整查看所有岗位、三级目录与综合建议！同时包内还附带 `latest_run_summary.json`（运行指标）和 `run.log`（运行日志）。

#### 方式 3：通过 GitHub Pages / Cloudflare Pages 在线网页查看

* 工作流的 `Deploy report to gh-pages` 步骤会自动把 `reports/index.html` 发布到仓库的 `gh-pages` 分支：

  * **启用 GitHub Pages**：进入仓库 `Settings` → 左侧 `Pages` → `Build and deployment` 下 `Source` 选择 `Deploy from a branch`，`Branch` 选择 `gh-pages` / `/ (root)` 并点击 `Save`。几分钟后即可通过 `https://<你的GitHub用户名>.github.io/<仓库名>/index.html` 随时访问（前提是仓库为 Public，或你有 GitHub Pro/Student 权限）。

  * **或者接入 Cloudflare Pages（支持私有仓库免费部署）**：按 `README.md` 第五节绑定 `gh-pages` 分支后，通过 `https://你的项目名.pages.dev/index.html` 访问。

#### 方式 4：通过 Git 提交记录检查数据变更

* 每次运行结束后，`Commit DB back to main` 步骤会自动向 `main` 分支提交一条形如 `chore: update crawler state 2026-10-17 [skip ci]` 的记录，持久化保存更新后的 `data/jobs.db`、`data/advice_state.json` 与 `outputs/latest_run_summary.json`。

### 3. 本地数据与 GitHub 数据的区别及同步方法（必读）

很多同学容易混淆“本地电脑上的文件”和“GitHub 云端运行的文件”，请务必明确以下核心事实：

1. **两边的数据不会天然自动同步**：

   * 你在自己电脑上双击 `启动.bat` 读写的是**你电脑硬盘上的 `data/jobs.db` 和 `profile.yaml`**。

   * GitHub Actions 每天自动运行读写的是 **GitHub 云端仓库 `main` 分支里的 `data/jobs.db` 和 `profile.yaml`**。

   * **GitHub Actions 运行成功 ≠ 你自己电脑上的 `data/jobs.db` 已经自动变新了！**

2. **如何把 GitHub 云端最新抓取的数据同步回本地电脑？**
   在本地项目根目录打开终端，运行以下命令拉取云端最新数据库与建议状态，然后生成本地报告：

   ```
   # 1. 拉取 GitHub 云端最新提交的 jobs.db 和 advice_state.json
   git pull --no-rebase origin main
   
   # 2. 基于拉取下来的最新数据库生成并打开本地报告（零 Token）
   python view_all.py
   
   ```

3. **如何把你在本地修改的 `profile.yaml` 同步到 GitHub 云端生效？**
   只提交配置文件本身（切勿把 `.env` 密钥或未整理的大文件强推上去）：

   ```
   git add profile.yaml
   git commit -m "chore(profile): 更新个人技术栈与职业偏好"
   git push origin main
   
   ```

   推送后，GitHub 上的 `Sync report (applications)` 工作流会自动触发，按你最新的 `profile.yaml` 更新综合建议与报告！

### 4. GitHub Secrets 与 Variables 配置指南

在 GitHub 仓库网页端，依次点击：**`Settings` → 左侧 `Secrets and variables` → `Actions`**：

#### （1）配置 Secrets（加密凭证，点击 `New repository secret`）

| Secret 准确名称（必须完全一致） | 是否必填 | 用途说明 | 获取方式 | 缺失时的系统行为 | 
| ----- | ----- | ----- | ----- | ----- | 
| **`DEEPSEEK_API_KEY`** | 推荐必填 | 调用 DeepSeek Flash 进行模糊岗位分流、调用 DeepSeek Pro 进行 A 档岗位精析及生成 AI 增强版综合建议。 | 登录 [DeepSeek 开放平台](https://platform.deepseek.com/) 创建 API Key。 | 爬虫抓取、入库、多标签分类、本地五维评分与本地实证综合建议**仍能正常完成**，但无法执行 Pro 深度评分，运行摘要会标注 `AI评分跳过(未配置DEEPSEEK_API_KEY)`。 | 
| **`FEISHU_WEBHOOK`** | 选填 | 在定时任务结束或异常时，向你的飞书群发送运行摘要与高分岗位卡片。 | 在飞书群 → 设置 → 群机器人 → 添加机器人 → **自定义机器人** → 复制 Webhook 地址（安全设置建议勾选“自定义关键词”并填入 `AI校招情报`）。 | 不会报错崩溃，日志打印 `未设置 FEISHU_WEBHOOK，跳过推送`，报告依然保存在 Artifacts 和 `gh-pages`。 | 

#### （2）配置 Variables（公开变量，切换到 `Variables` 标签页点击 `New repository variable`）

| Variable 准确名称 | 是否必填 | 用途说明 | 示例值 | 
| ----- | ----- | ----- | ----- | 
| **`REPORT_BASE_URL`** | 选填 | 飞书消息底部「📄 完整报告」的跳转网址前缀。 | `https://你的用户名.github.io/career-agent` 或 `https://你的项目.pages.dev` | 

> **安全铁律**：修改 Secret 后不会自动重跑旧任务，请手动去 `Actions` 点一次 `Run workflow` 验证；系统所有日志均已做脱敏处理，绝不会在日志中打印你的 API Key 或 Webhook URL。

### 5. 自动运行的启停、修改频率与防重复消耗 Token 指南

1. **查看最近一次运行时间与状态**：打开仓库 `Actions` 页面，列表第一行即为最近一次运行的时间、触发方式（`scheduled` 定时 / `workflow_dispatch` 手动）与结果状态。

2. **修改自动运行频率（以及时区换算规则）**：

   * 打开 `.github/workflows/daily_report.yml`，找到第 6 行：

     ```
     on:
       schedule:
         - cron: "0 22 * * *"
     
     ```

   * **时区换算公式**：GitHub Actions 的 `cron` 严格按 **UTC 时间**解释，**北京时间（`Asia/Shanghai`）= UTC 时间 + 8 小时**。

     * 当前 `"0 22 * * *"` 代表 **UTC 22:00 = 北京时间次日早上 06:00**（考虑到 GitHub Actions 公共队列通常有 30\~120 分钟延迟，实际推送会落在早上 06:30\~08:00 之间）。

     * 若想改为每周一、周三、周五早上 08:00（即 UTC 00:00）运行一次，可改为：`- cron: "0 0 * * 1,3,5"`。

     * 若想改为每天中午 12:30（即 UTC 04:30）运行，可改为：`- cron: "30 4 * * *"`。

3. **暂停或恢复定时自动运行**：

   * 在 GitHub 仓库进入 `Actions` → 左侧点击 `Daily Recruitment Report` → 点击右上角的 **`···`（更多操作）** 按钮 → 选择 **`Disable workflow`** 即可一键暂停定时任务。

   * 需要恢复时，回到同一页面点击 **`Enable workflow`** 即可。

4. **如何判断“本次无新增岗位”是否正常？**

   * 如果飞书收到提示：`今日无相关新增岗位（成功抓取 N 家公司，共扫描 M 条在招岗位，均已在库中刷新活跃时间）`，说明爬虫工作完全正常，只是这些企业今天没有发布新职位。

   * 反之，如果提示：`⚠️ 抓取异常：成功公司数为 0`，才代表网络或爬虫配置出现故障。

5. **如何避免重复运行导致重复消耗 AI Token 与飞书刷屏？**

   * **岗位评分指纹锁**：`analyzer.py` 会计算每个岗位的 `(jd_fingerprint, profile_fingerprint, model, analysis_version)`。只要岗位 JD 原文和你 `profile.yaml` 中的核心能力未变，即使你一天手动点 10 次运行，也**不会重复调用 DeepSeek Pro**。

   * **综合建议版本锁**：`job_filters.py` 会比对 `profile_version` 与 `market_data_fingerprint`。输入未变时直接复用 `data/advice_state.json` 中的最新有效建议，**零 Token 消耗**。

   * **飞书同日防刷屏锁**：`notifier.py` 会记录当日推送指纹（`data/notify_state.json`）。同一天内若多次运行且无新增高分岗位，会自动发送简短心跳通知，不再重复刷屏 20 条相同的历史岗位。

## 四、个人职业需求变化时，应该修改什么文件？

### 1. 核心原则：日常修改个人需求，只需改根目录下的 `profile.yaml`！

我们已经完成了彻底的**配置与代码分离**。当你升入高年级、学了新技能、做完新项目、或者改变了职业兴趣和城市偏好时，**绝对不需要修改任何 `.py` 代码文件**，只需打开项目根目录的 **`profile.yaml`** 修改保存即可。

| 需求类型 | 所在文件与位置 | 性质分类 | 修改后影响的模块 | 
| ----- | ----- | ----- | ----- | 
| 学历、学校/专业、目标毕业年份 (`target_cohort`)、当前求职阶段 (`job_type`) | **`profile.yaml`** 顶部基础信息区 | **个人配置** | 资格过滤（如本科自动拦截硕博专属岗）、目标届别与实习轨道匹配、综合建议的分层规划。 | 
| 已掌握技能 (`skills`)、真实项目经历 (`project_evidence`)、学习目标 (`learning_targets`) | **`profile.yaml`** 能力与证据区 | **个人配置** | 本地五维评分、DeepSeek A/B/C 分流与 Pro 评分、综合建议的技能匹配度与缺口分析。 | 
| 重点探索方向 (`directions`) vs 一般兴趣支线 (`optional_interest_branches`) | **`profile.yaml`** 方向偏好区 | **个人配置** | 推荐排序权重、方向对比优先级、综合建议的方向权衡（**绝不会因此删除或停止采集其他类别岗位**）。 | 
| WLB/成长/稳定性偏好 (`preferences`)、意向城市 (`preferred_cities`)、硬性排除词 (`excluded_title_keywords`) | **`profile.yaml`** 约束与偏好区 | **个人配置** | WLB 风险扣分/加分幅度、城市过滤提示、非目标岗位硬性剔除、综合建议的偏好契合度。 | 
| 评分五维权重 (`score_component_limits`)、推荐/考虑分数线 (`score_thresholds`) | **`profile.yaml`** 评分标准区 | **个人配置** | 岗位总分合成上限（合计须为 100）及「推荐 / 考虑 / 不推荐」标签划分。 | 
| 企业校招网址名单 (`companies`)、AI 模型名称与单次预算上限 | `config.yaml` | **系统规则** | 爬虫抓取范围与 API 限流保护（仅增删企业或调模型时修改）。 | 
| 多标签职业分类体系、动态季节判定、版本差异检测与回退逻辑 | `job_filters.py` / `analyzer.py` / `reporter.py` | **代码逻辑** | 系统底层引擎，日常使用完全无需触碰。 | 

### 2. 实战修改示例（可直接对照操作）

#### 示例 A：将某个方向从「重点探索」降级为「一般兴趣」（或反之）

假设你原本把 `AI 应用工程与企业级智能体落地` 放在主力探索方向，现在想把它改为一般兴趣支线，只需在 `profile.yaml` 中移动该行：

```
# 修改前：位于重点探索列表 directions 中
directions:
  - 后端服务、企业级平台与分布式系统工程 (C++/Go/Python/Java)
  - 跨平台桌面客户端、办公软件与工业软件研发 (C++/Qt/SDK/Windows&Mac)
  - AI 应用工程与企业级智能体落地 (Agent/RAG/AI全栈工程，非算法科研岗)

optional_interest_branches:
  - 图形学与实时渲染 (OpenGL/GLSL/3D视景/工业CAD几何——作为个人兴趣与工程审美补充)

```

```
# 修改后：移入 optional_interest_branches（一般兴趣支线）
directions:
  - 后端服务、企业级平台与分布式系统工程 (C++/Go/Python/Java)
  - 跨平台桌面客户端、办公软件与工业软件研发 (C++/Qt/SDK/Windows&Mac)

optional_interest_branches:
  - 图形学与实时渲染 (OpenGL/GLSL/3D视景/工业CAD几何——作为个人兴趣与工程审美补充)
  - AI 应用工程与企业级智能体落地 (Agent/RAG/AI全栈工程，作为支线观察)

```

* **会发生什么变化（影响范围：`recompute_ranking_and_advice`）**：

  1. 系统自动检测到 `directions` 与 `optional_interest_branches` 发生变更，生成新的配置版本号（如 `v-a1b2c3d4`），并在配置变更日志中记录：`将「AI 应用工程...」从重点探索调整为一般兴趣支线`。

  2. **无需重新爬所有网站**：系统保留全部已采集的 AI 岗位（你依然可以在左侧目录或类别筛选栏点开 `AI 与机器学习工程` 查看），但在综合建议模块中会自动更新方向优先级对比，并在页面顶部显示本次变化摘要。

#### 示例 B：学完一门新技能（如 `MySQL / Redis` 或 `Qt6`）并完成一个新项目

当你大二下学期补齐了 MySQL 与 Qt6，只需在 `profile.yaml` 的 `skills` 和 `project_evidence` 中各加一行：

```
skills:
  - C++基础 (面向对象/STL/内存管理基础)
  - Qt6 跨平台桌面开发与信号槽机制          # <-- 新增技能
  - MySQL 关系型数据库索引设计与事务基础    # <-- 新增技能

project_evidence:
  - 使用 C++17 与 Qt6 完成跨平台三维模型检视与性能分析桌面工具  # <-- 新增真实项目证据

```

* **会发生什么变化（影响范围：`reanalyze_jobs_and_advice`）**：

  1. 下次运行 `查看.bat` 或 `启动.bat` 时，系统识别到核心能力与项目证据升级。

  2. 原先提示 `【技能缺口】需补充 MySQL / Qt` 的客户端与后端岗位匹配分会自动上升，缺失技能提示消除，且「综合建议」模块会自动生成新版本，在「与上一版本相比发生了什么变化」栏目中明确列出因你补齐 MySQL/Qt6 而新解锁的高匹配方向！

## 五、新增模块：「技能栈与实现项目推荐」与「社区口碑 WLB 证据审核」使用与维护指南

### 1. 两个新增模块在网页报告中的位置
打开 `reports/index.html`（或双击 `查看.bat`），在左侧导航栏「发现」分组下新增了两个独立模块入口：
- **🛠️ 技能栈与实现项目 (`#skill-projects`)**：
  - **层级 A（高频技能概览）**：展示全库去重后的计算机岗位 Top 16 规范化技能热度表（区分高频必需、常见加分、特定要求，并剔除单岗多城重复发布偏差）及 10 大岗位类别的专属技能分布与常见组合。
  - **层级 B（技能栈对应项目）**：展示 7 个面向本科生工程能力训练的可验证项目（含项目目标、核心功能、关键工程任务、教材知识落点、可复现验收标准、工作量估算前提、开源参考许可证与自研边界、以及“现有基础 ≠ 学习成本 ≠ 工作内容兴趣”三分离评估）。
  - **层级 C（实践建议）**：提供 P0（通用高保值底座）/ P1（方向进阶代表作）/ P2（跨语言与工业控制探索）分阶段建议与五大防坑守则。
- **⚖️ 社区 WLB 证据审核 (`#wlb-audit`)**：
  - 严格分离展示每家重点企业的 **① 招聘事实 (`jd_facts`)**、**② 社区经验 (`community_experiences`，含原始链接、发布时间、四级匹配等级与“热度/相关性/可信度/代表性”四维评估)**、**③ 模型审慎推断与面试核实问题清单 (`model_inferences`)**。
- **岗位大卡片底部协同栏**：在每个推荐岗位卡片底部自动关联对应的能力训练项目与该公司社区 WLB 结论，辅助你同时回答“岗位是否值得关注、做什么项目训练能力、公司 WLB 有何证据支持或反驳”三个问题。

### 2. 如何启用、关闭或手动刷新模块
在 `profile.yaml` 底部可随时调整两个模块的配置：
```yaml
skill_project_module:
  enabled: true                  # 设为 false 可关闭技能栈与项目推荐模块
  min_category_samples: 15       # 类别最小独立岗位样本数阈值
  min_overall_samples: 30        # 全库最小独立岗位样本数阈值
  allow_ai_enhancement: false    # 默认 false（0 Token 消耗）

wlb_community_module:
  enabled: true                  # 设为 false 可关闭社区 WLB 审核模块
  cache_ttl_days: 14             # 社区证据缓存有效期（天）
  max_companies_per_run: 20      # 单次运行最多审核公司数
  allow_live_search: false       # 是否启用外部公开搜索 API
  allow_ai_summary: false        # 默认 false（0 Token 消耗）
```
- **手动强制刷新命令**：
  - 强制刷新技能统计与项目推荐：`FORCE_SKILL_PROJECTS_REFRESH=1 python view_all.py`
  - 强制刷新社区 WLB 审核：`FORCE_WLB_REFRESH=1 python view_all.py`
  - 本地管理服务接口刷新：访问 `http://localhost:5000/api/skill-projects/refresh` 或 `http://localhost:5000/api/wlb-audit/refresh`。

### 3. 日常维护：如何扩展技能词典、项目蓝图与社区证据
- **新增技能同义词或工程项目蓝图**：编辑 `skill_projects.py` 中的 `SKILL_SYNONYM_CATALOG` 或 `PROJECT_BLUEPRINT_CATALOG`，运行 `FORCE_SKILL_PROJECTS_REFRESH=1 python view_all.py`。
- **新增公司别名、修改搜索关键词或补充社区讨论证据**：编辑 `wlb_community.py` 中的 `COMPANY_ALIASES` / `build_wlb_search_queries()`，或在 `data/community_wlb_seed.json` 中追加公开讨论链接与摘录，运行 `FORCE_WLB_REFRESH=1 python view_all.py`。
