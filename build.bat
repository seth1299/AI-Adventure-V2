@echo off
setlocal

cd /d "%~dp0"

set "APP_NAME=AI Adventure"
set "ENTRYPOINT=main.py"
set "PYTHON=python"
set "TTS_MODEL=ai_adventure\audio\tts\kokoro-v1.0.onnx"
set "TTS_VOICES=ai_adventure\audio\tts\voices-v1.0.bin"
set "APP_ICON=ai_adventure\data\app_icon.ico"

echo Building %APP_NAME% from "%CD%"
echo.

if not exist "%ENTRYPOINT%" (
    echo ERROR: Could not find "%ENTRYPOINT%".
    exit /b 1
)

if not exist "%TTS_MODEL%" (
    echo ERROR: Missing Kokoro ONNX model: "%TTS_MODEL%"
    exit /b 1
)

if not exist "%TTS_VOICES%" (
    echo ERROR: Missing Kokoro voices file: "%TTS_VOICES%"
    exit /b 1
)

%PYTHON% -m PyInstaller ^
    --log-level ERROR ^
    --noconfirm ^
    --noconsole ^
    --onefile ^
    --clean ^
    --windowed ^
    --name "%APP_NAME%" ^
    --add-data "ai_adventure\data\context;ai_adventure\data\context" ^
    --add-data "ai_adventure\data\alchemy;ai_adventure\data\alchemy" ^
    --add-data "ai_adventure\audio\music_tracks;ai_adventure\audio\music_tracks" ^
    --add-data "%TTS_MODEL%;ai_adventure\audio\tts" ^
    --add-data "%TTS_VOICES%;ai_adventure\audio\tts" ^
    --icon "%APP_ICON%" ^
    --collect-all "kokoro_onnx" ^
    --collect-all "onnxruntime" ^
    --collect-all "soundfile" ^
    --hidden-import "google.genai" ^
    --hidden-import "PySide6.QtCore" ^
    --hidden-import "PySide6.QtGui" ^
    --hidden-import "PySide6.QtWidgets" ^
    "%ENTRYPOINT%"

if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: "dist\%APP_NAME%\%APP_NAME%.exe"
exit /b 0
