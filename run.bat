@echo off
echo ====================================
echo m3uGenius - Run Application
echo ====================================
echo.

if not exist venv (
    echo ERROR: Virtual environment not found!
    echo Please run setup_venv.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python app.py
pause
