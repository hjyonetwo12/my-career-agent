@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ============================================================
echo   AI校招情报系统 - 完整抓取与分析流水线
echo ============================================================

set "PY_CMD="
where python >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"
if defined PY_CMD goto :RUN_MAIN
where py >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if defined PY_CMD goto :RUN_MAIN
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY_CMD="%LocalAppData%\Programs\Python\Python312\python.exe""
if defined PY_CMD goto :RUN_MAIN
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY_CMD="%LocalAppData%\Programs\Python\Python311\python.exe""
if defined PY_CMD goto :RUN_MAIN
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PY_CMD="%LocalAppData%\Programs\Python\Python310\python.exe""
if defined PY_CMD goto :RUN_MAIN

echo [错误] 未检测到 Python 环境，请先安装 Python 并勾选 Add Python to PATH。
pause
exit /b 1

:RUN_MAIN
echo 正在启动完整流水线: %PY_CMD% main.py ...
%PY_CMD% main.py
echo.
pause
exit /b 0
