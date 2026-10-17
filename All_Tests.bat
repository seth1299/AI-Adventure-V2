@echo off
setlocal
chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "REPO_ROOT=%%~fI"
pushd "%REPO_ROOT%"
echo ============================================================
echo All_Tests - complete unittest discovery
echo Started: %DATE% %TIME%
echo Command: .venv\Scripts\python.exe -u -m unittest discover -s tests -t . -v
echo Includes every unittest package under tests\.
echo ============================================================
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Project interpreter not found at .venv\Scripts\python.exe
    popd
    exit /b 2
)
set "PYTHONUNBUFFERED=1"
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" -u -m unittest discover -s "tests" -t . -v
set "TEST_EXIT=%ERRORLEVEL%"
echo.
echo Finished: %DATE% %TIME%
if "%TEST_EXIT%"=="0" (echo Result: PASSED) else (echo Result: FAILED with exit code %TEST_EXIT%)
popd
exit /b %TEST_EXIT%
