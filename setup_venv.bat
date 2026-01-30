@echo off
echo ====================================
echo m3uGenius - Setup Virtual Environment
echo ====================================
echo.

if exist venv (
    echo Virtual environment already exists.
    echo.
    goto :activate
)

echo Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo ERROR: Failed to create venv. Python may not be installed.
    pause
    exit /b 1
)

:activate
echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ====================================
echo Setup complete!
echo To run the app:
echo   1. call venv\Scripts\activate.bat
echo   2. python app.py
echo ====================================
pause
