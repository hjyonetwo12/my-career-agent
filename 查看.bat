@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ============================================================
echo   AI校招情报系统 - 快速查看模式 [零 Token]
echo ============================================================

set "PY_CMD="
where python >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"
if defined PY_CMD goto :RUN_SCRIPT

where py >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if defined PY_CMD goto :RUN_SCRIPT

if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY_CMD="%LocalAppData%\Programs\Python\Python312\python.exe""
if defined PY_CMD goto :RUN_SCRIPT
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY_CMD="%LocalAppData%\Programs\Python\Python311\python.exe""
if defined PY_CMD goto :RUN_SCRIPT
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PY_CMD="%LocalAppData%\Programs\Python\Python310\python.exe""
if defined PY_CMD goto :RUN_SCRIPT

echo [错误] 未检测到 Python 环境，请确认已安装 Python 并勾选 Add Python to PATH。
if exist "reports\index.html" goto :OPEN_STATIC
pause
exit /b 1

:RUN_SCRIPT
echo 正在使用 %PY_CMD% 生成并打开最新累计报告...
%PY_CMD% view_all.py
if errorlevel 1 goto :SCRIPT_ERROR
echo.
echo [完成] 报告已生成并在浏览器中打开。如未自动弹出，请双击打开 reports\index.html
pause
exit /b 0

:SCRIPT_ERROR
echo.
echo [警告] 运行 view_all.py 时发生异常，请查看上方报错信息。
if exist "reports\index.html" goto :OPEN_STATIC
pause
exit /b 1

:OPEN_STATIC
echo 正在直接为您打开现有的 reports\index.html 网页报告...
start "" "reports\index.html"
pause
exit /b 0
