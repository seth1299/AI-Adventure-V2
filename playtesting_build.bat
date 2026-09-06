@echo off
setlocal

cd /d "%~dp0"

set "APP_NAME=AI Adventure Playtesting"
set "ENTRYPOINT=main.py"
if not defined PYTHON set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "PYGAME_HIDE_SUPPORT_PROMPT=1"
set "APP_ICON=ai_adventure\data\app_icon.ico"
set "PLAYTESTING_RUNTIME_HOOK=ai_adventure\app\pyinstaller_playtesting_runtime.py"
set "PLAYTESTING_REQUIREMENTS=playtesting_requirements.txt"
set "WINDOW_MODE=--noconsole"
if defined PLAYTESTING_BUILD_CONSOLE set "WINDOW_MODE=--console"

echo Building %APP_NAME% from "%CD%"
echo Gemini, TTS, narration, and background music are excluded from this build.
echo.

if not exist "%ENTRYPOINT%" (
    echo ERROR: Could not find "%ENTRYPOINT%".
    exit /b 1
)

if not exist "%PYTHON%" (
    echo ERROR: Could not find the build Python interpreter: "%PYTHON%"
    echo Create the project virtual environment or set PYTHON explicitly before building.
    exit /b 1
)

if not exist "%APP_ICON%" (
    echo ERROR: Missing application icon: "%APP_ICON%"
    exit /b 1
)

if not exist "%PLAYTESTING_RUNTIME_HOOK%" (
    echo ERROR: Missing playtesting runtime hook: "%PLAYTESTING_RUNTIME_HOOK%"
    exit /b 1
)

if not exist "%PLAYTESTING_REQUIREMENTS%" (
    echo ERROR: Missing playtesting requirements: "%PLAYTESTING_REQUIREMENTS%"
    exit /b 1
)

"%PYTHON%" -m pip install --disable-pip-version-check --no-input --quiet -r "%PLAYTESTING_REQUIREMENTS%"
if errorlevel 1 (
    echo ERROR: Failed to install playtesting requirements.
    exit /b 1
)

"%PYTHON%" -m PyInstaller --log-level ERROR --noconfirm %WINDOW_MODE% --onefile --clean --name "%APP_NAME%" --add-data "%APP_ICON%;ai_adventure\data" --icon "%APP_ICON%" --runtime-hook "%PLAYTESTING_RUNTIME_HOOK%" --exclude-module "ai_adventure.ai.gemini_service" --exclude-module "ai_adventure.audio.narration" --exclude-module "ai_adventure.audio.sound_manager" --exclude-module "ai_adventure.audio.tts" --exclude-module "ai_adventure.audio.tts.tts_manager" --exclude-module "google" --exclude-module "google.genai" --exclude-module "rapidfuzz" --exclude-module "pygame" --exclude-module "pygame_ce" --exclude-module "kokoro_onnx" --exclude-module "pykokoro" --exclude-module "onnxruntime" --exclude-module "soundfile" --exclude-module "edge_tts" --exclude-module "piper" --exclude-module "piper_tts" --exclude-module "pyttsx3" --exclude-module "espeakng_loader" --exclude-module "phonemizer" --exclude-module "language_tags" --hidden-import "PySide6.QtCore" --hidden-import "PySide6.QtGui" --hidden-import "PySide6.QtWidgets" "%ENTRYPOINT%"

if errorlevel 1 (
    echo.
    echo Playtesting build failed.
    exit /b 1
)

echo.
echo Playtesting build complete: "dist\%APP_NAME%.exe"
exit /b 0
