@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo   AI校招情报系统 - 快速查看与配置更新模式（零 Token）
echo   读取本地数据库 data\jobs.db 与个人配置 profile.yaml
echo   自动检测配置变化、更新综合建议并打开 reports\index.html
echo ========================================================
echo.

python view_all.py
if errorlevel 1 (
    echo.
    echo [错误] 报告生成未完成，请检查 profile.yaml 格式或先运行一次 启动.bat。
    pause
    exit /b 1
)

echo.
pause
