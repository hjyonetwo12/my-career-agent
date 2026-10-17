@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo   AI校招情报系统 - 完整抓取、评分与飞书推送模式
echo   执行链路：抓取岗位 - 清洗去重 - AI评分 - 综合建议更新 - 生成报告
echo   运行日志将实时显示并同步保存至 outputs\run.log
echo ========================================================
echo.

if "%DEEPSEEK_API_KEY%"=="" (
    echo [提示] 系统环境变量未检测到 DEEPSEEK_API_KEY，程序将自动读取根目录 .env 文件。
    echo        若 .env 中也未配置，仍可正常抓取入库与生成本地实证建议，仅跳过 DeepSeek Pro 评分。
    echo.
)

python main.py
if errorlevel 1 (
    echo.
    echo [错误] 运行异常中断，请查看上方报错提示或打开 outputs\run.log 排查（参考 docs\USER_GUIDE.md）。
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   运行成功！最新报告已保存至 reports\index.html
echo   运行摘要已保存至 outputs\latest_run_summary.json
echo ========================================================
pause
