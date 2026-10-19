@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel% equ 0 (
    python -m unittest discover -s tests -p "test_*.py" -v
) else (
    py -3 -m unittest discover -s tests -p "test_*.py" -v
)
echo.
pause
