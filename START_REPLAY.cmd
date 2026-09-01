@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 quickstart.py replay
) else (
  python quickstart.py replay
)
if errorlevel 1 pause
