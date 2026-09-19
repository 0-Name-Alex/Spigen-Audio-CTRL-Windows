@echo off
echo ============================================================
echo  Spigen Audio CTRL - Windows EXE Builder
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found in PATH.
    echo Install Python from https://python.org and tick "Add to PATH"
    pause & exit /b 1
)

:: Install / upgrade deps
echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt --quiet
python -m pip install pyinstaller --quiet

:: Build
echo [2/3] Building EXE with PyInstaller...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --icon=assets\icon.ico ^
    --add-data "assets;assets" ^
    --name "SpigenAudioCTRL" ^
    --clean ^
    spigen_audio_ctrl\__main__.py

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Build failed. See output above.
    pause & exit /b 1
)

echo [3/3] Done!
echo.
echo The EXE is at:  dist\SpigenAudioCTRL.exe
echo.
pause
