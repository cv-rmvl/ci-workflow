@echo off
setlocal enabledelayedexpansion

REM Get the directory of the script and go up one level
for %%i in ("%~dp0..") do set "ws=%%~fi"

cd /d "%ws%"

REM Remove build directory if it exists
if exist "build" (
    echo [INFO] Removing existing build directory ...
    rmdir /s /q "build"
)

echo [INFO] Configure project ...
mkdir build
cd build

cmake ..
if %errorlevel% neq 0 (
    echo [ERROR] Failed to configure project
    exit /b 1
)
echo [PASS] Configure project done

echo [INFO] Build project ...
cmake --build .
if %errorlevel% neq 0 (
    echo [ERROR] Failed to build project
    exit /b 1
)
echo [PASS] Build project done

REM Run executables using for loop
for %%m in (inc) do (
    echo [INFO] Run %%m ...
    if not exist "%%m.exe" (
        echo [ERROR] Executable %%m.exe not found
        exit /b 1
    )
    %%m.exe
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to run %%m
        exit /b 1
    )
)

echo [PASS] Run all deployment test done