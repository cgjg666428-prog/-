@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 study.py gui
) else (
  python study.py gui
)
if errorlevel 1 (
  echo 启动失败，请查看上方提示及 README.md。需要 Python 3.10 或以上版本。
  pause
)
