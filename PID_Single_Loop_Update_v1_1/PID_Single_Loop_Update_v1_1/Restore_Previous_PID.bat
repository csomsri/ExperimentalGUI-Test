@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Close the controller application before installing or restoring.
where python >nul 2>nul
if %errorlevel% equ 0 (
    python install_pid_update.py --restore
) else (
    py -3 install_pid_update.py --restore
)
echo.
pause
