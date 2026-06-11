@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   微语多角色扮演 Skill + QQ Bot 环境部署
echo ============================================
echo.

REM ========== 1. 检测 Python ==========
echo [1/4] 检测 Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [错误] 未找到 Python 3.10+，请安装后重试
    echo   下载: https://www.python.org/downloads/
    echo   安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Python %%v OK

REM ========== 2. 创建虚拟环境 ==========
echo.
echo [2/4] 创建虚拟环境...
if exist "skill_qqbot\Scripts\python.exe" (
    echo   skill_qqbot\ 已存在，跳过
) else (
    python -m venv skill_qqbot
    if %errorlevel% neq 0 (
        echo   [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo   创建成功
)

REM ========== 3. 安装依赖 ==========
echo.
echo [3/4] 安装 Python 依赖...
call "skill_qqbot\Scripts\activate.bat"
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo   [错误] pip install 失败
    pause
    exit /b 1
)
echo   安装完成

REM 安装 Playwright 浏览器（微博抓取需要）
echo.
echo   安装 Playwright Chromium（微博抓取用）...
playwright install chromium -q 2>nul
if %errorlevel% neq 0 (
    echo   [提示] Playwright 浏览器安装跳过（可能已存在或网络问题）
    echo   如需微博抓取，请手动执行: skill_qqbot\Scripts\playwright install chromium
)

REM ========== 4. 创建 .env ==========
echo.
echo [4/4] 检查配置...
if not exist "qqbot\.env" (
    copy "qqbot\.env.example" "qqbot\.env" >nul 2>nul
    echo   已从模板创建 qqbot\.env
    echo   [!!] 请编辑 qqbot\.env 填入 DeepSeek API Key
) else (
    echo   qqbot\.env 已存在
)

REM ========== 检测 NapCat ==========
echo.
set "NC="
for /d %%D in ("%~dp0qqbot\napcat\NapCat.*.Shell") do (
    if exist "%%D\NapCatWinBootMain.exe" set "NC=%%D\NapCatWinBootMain.exe"
)
if defined NC (
    echo   NapCat: 已就绪
) else (
    echo   NapCat: 未安装
    echo     1. 下载 NapCat.OneKey.zip 并解压到 qqbot\napcat\
    echo     2. 下载: https://github.com/NapNeko/NapCatQQ/releases
)

echo.
echo ============================================
echo   环境部署完成！
echo ============================================
echo.
echo   接下来的步骤:
echo     1. 编辑 qqbot\.env 填入 API Key
echo     2. 首次运行需 NapCat 扫码登录 QQ
echo     3. 运行 setup_napcat.bat 写入连接配置
echo     4. 运行 start_all.bat 启动 Bot
echo.
echo   详细说明见 README.md
echo.
pause
