@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   NapCat OneBot 配置写入
echo ============================================
echo.
echo   将 NapCat 网络配置设为反向 WebSocket
echo   连接: ws://127.0.0.1:8080/onebot/v11/ws
echo.

set "ROOT=%~dp0"

REM 自动查找 NapCat 目录（支持任意版本号）
set "SHELL="
for /d %%D in ("%ROOT%qqbot\napcat\NapCat.*.Shell") do (
    if exist "%%D\NapCatWinBootMain.exe" set "SHELL=%%D"
)

if not defined SHELL (
    echo [错误] 未找到 NapCat，目录: %ROOT%qqbot\napcat\NapCat.*.Shell
    echo   请先下载 NapCat 解压到 qqbot\napcat\ 目录
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
    echo   操作：双击 qqbot\napcat\NapCat.*.Shell\NapCatWinBootMain.exe
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
