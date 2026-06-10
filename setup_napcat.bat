@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   NapCat OneBot 配置写入
echo ============================================
echo.
echo   将 NapCat 网络配置设为反向 WebSocket
echo   连接: ws://127.0.0.1:8080/onebot/v11/ws
echo.

set "ROOT=%~dp0"
set "SHELL=%ROOT%qqbot\napcat\NapCat.44498.Shell"

if not exist "%SHELL%\NapCatWinBootMain.exe" (
    echo [错误] 未找到 NapCatWinBootMain.exe: %SHELL%
    pause
    exit /b 1
)

REM 查找 versions/*/resources/app/napcat/config 目录
set "CFG="
for /d %%V in ("%SHELL%\versions\*") do (
    if exist "%%V\resources\app\napcat\config" (
        set "CFG=%%V\resources\app\napcat\config"
    )
)

if "%CFG%"=="" (
    echo [错误] 未找到 NapCat config 目录
    echo   请先启动一次 NapCat 扫码登录，让它生成配置后再运行本脚本
    echo   操作：双击 qqbot\napcat\NapCat.44498.Shell\NapCatWinBootMain.exe
    pause
    exit /b 1
)

echo   配置目录: %CFG%

REM 查找已有的 onebot11_*.json
set "TARGET="
for %%F in ("%CFG%\onebot11_*.json") do set "TARGET=%%F"

if "%TARGET%"=="" (
    set /p QQ="请输入 QQ 号: "
    set "TARGET=%CFG%\onebot11_!QQ!.json"
    echo   将创建: %TARGET%
) else (
    echo   将覆盖: %TARGET%
)

copy /y "%ROOT%qqbot\napcat_onebot_config.json" "%TARGET%" >nul
if %errorlevel% neq 0 (
    echo [错误] 写入失败
    pause
    exit /b 1
)

echo.
echo [OK] 配置已写入
echo   现在可以运行 start_all.bat 启动
echo.
pause
