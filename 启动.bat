@echo off
cd /d "%~dp0"
echo ============================================
echo   [Career Agent] Running full pipeline...
echo   Crawl -> Filter -> Analyze -> Feishu Push
echo ============================================
python main.py
echo.
pause