@echo off
cd /d "%~dp0"

if not exist ".venv\" (
    G:\px\python.exe -m venv .venv
)

call .venv\Scripts\activate.bat
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install gradio soundfile numpy

echo Fertig.
pause