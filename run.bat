@echo off
title Spigen Audio CTRL
echo Starting Spigen Audio CTRL...
python -m spigen_audio_ctrl
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to start. Make sure Python 3.11+ is installed
    echo and you have run:  pip install -r requirements.txt
    pause
)
