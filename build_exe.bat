@echo off
title Build Spigen Audio CTRL EXE
echo Installing PyInstaller...
pip install pyinstaller

echo.
echo Building standalone executable...
pyinstaller ^
    --onefile ^
    --windowed ^
    --name "Spigen Audio CTRL" ^
    --add-data "spigen_audio_ctrl;spigen_audio_ctrl" ^
    --hidden-import bleak ^
    --hidden-import bleak.backends.winrt ^
    --hidden-import customtkinter ^
    -i NONE ^
    spigen_audio_ctrl/__main__.py

echo.
echo Done! The .exe is in the dist\ folder.
pause
