@echo off
setlocal enabledelayedexpansion

set "config=%~1"
if "%config%"=="" set "config=Release"

set "rmvl_prefix=%~2"
set "rmvl_dir="
if not "%rmvl_prefix%"=="" (
    if not exist "%rmvl_prefix%" (
        echo [FAIL] RMVL install prefix not found: %rmvl_prefix%
        exit /b 1
    )
    for /r "%rmvl_prefix%" %%i in (RMVLConfig.cmake) do (
        if exist "%%~fi" if not defined rmvl_dir for %%j in ("%%~dpi.") do set "rmvl_dir=%%~fj"
    )
    if not defined rmvl_dir (
        echo [FAIL] RMVLConfig.cmake not found under: %rmvl_prefix%
        exit /b 1
    )
    echo [INFO] Found RMVL package config: !rmvl_dir!\RMVLConfig.cmake
)

for %%i in ("%~dp0..") do set "ws=%%~fi"
cd /d "%ws%"
if errorlevel 1 (
    echo [FAIL] Failed to enter workspace
    exit /b 1
)

if exist "build" (
    echo [INFO] Removing existing build directory ...
    rmdir /s /q "build"
    if errorlevel 1 (
        echo [FAIL] Failed to remove existing build directory
        exit /b 1
    )
)

echo [INFO] Configure project ...
mkdir build
if errorlevel 1 (
    echo [FAIL] Failed to create build directory
    exit /b 1
)
cd build
if errorlevel 1 (
    echo [FAIL] Failed to enter build directory
    exit /b 1
)

if defined rmvl_dir (
    cmake .. -DRMVL_DIR="!rmvl_dir!"
) else (
    cmake ..
)
if errorlevel 1 (
    echo [FAIL] Failed to configure project
    exit /b 1
)
echo [PASS] Configure project done

echo [INFO] Build project ...
cmake --build . --config "%config%"
if errorlevel 1 (
    echo [FAIL] Failed to build project
    exit /b 1
)
echo [PASS] Build project done

for %%m in (inc link) do (
    echo [INFO] Run %%m ...
    set "exe="
    if exist "%%m.exe" set "exe=%%m.exe"
    if not defined exe if exist "%config%\%%m.exe" set "exe=%config%\%%m.exe"
    if not defined exe (
        echo [FAIL] Executable %%m.exe not found - build may have failed
        exit /b 1
    )
    "!exe!"
    if errorlevel 1 (
        echo [FAIL] Failed to run %%m
        exit /b 1
    )
)

echo [PASS] Run all deployment test done
