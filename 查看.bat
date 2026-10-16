@echo off
cd /d "%~dp0"
echo ============================================
echo   [Career Agent] Generating local report...
echo   (Reading 34,000+ jobs, please wait 5-8s)
echo ============================================
python view_all.py
echo.
pause