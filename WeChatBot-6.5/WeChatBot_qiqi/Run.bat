@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PRODUCT_NAME=QIQI 微信 Bot"
set "PRODUCT_VERSION=6.9"
set "PROJECT_NAME=iwyxdxl/WeChatBot_WXAUTO_SE"
set "REPAIR_MODE=0"
if /I "%~1"=="repair" set "REPAIR_MODE=1"
if /I "%~1"=="/repair" set "REPAIR_MODE=1"
if /I "%~1"=="--repair" set "REPAIR_MODE=1"
title %PRODUCT_NAME% %PRODUCT_VERSION%
echo "🟢 %PRODUCT_NAME% %PRODUCT_VERSION%"
if "!REPAIR_MODE!"=="1" echo "🛠️ 已启用依赖修复模式"

:: ---------------------------
:: 检查微信版本
:: ---------------------------
:: 依次检测 Weixin 和 WeChat 注册表路径,优先 Weixin
:: ---------------------------
set "wxversion="
rem 优先依次检测 Weixin 和 WeChat 的 DisplayVersion
for %%K in (
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Weixin"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\WeChat"
) do (
    for /f "tokens=2,*" %%i in ('reg query %%K /v DisplayVersion 2^>nul ^| find "DisplayVersion"') do (
        set "wxversion=%%j"
        set "RegPath=%%K"
        goto :found_wxversion
    )
)
if not defined wxversion (
    echo "⚠️ 警告：未检测到微信安装或无法读取注册表！"
    echo "⚠️这可能是由于以下原因："
    echo "⚠️1. 微信未正确安装"
    echo "⚠️2. 注册表访问权限不足"
    echo "⚠️3. 微信版本过老或过新"
    echo "⚠️4. 您使用的是便携版微信"
    echo.
    echo "⚠️程序将跳过微信版本检查并继续运行。"
    echo "⚠️如果程序启动后无法控制微信,请下载微信4.1.2版本：https://www.123865.com/s/tB1wvd-LWaAA"
    echo.
    echo "🔄3秒后自动继续..."
    timeout /t 3 /nobreak >nul
    goto :check_python
)
:found_wxversion

if not defined wxversion (
    echo "⚠️警告：无法获取微信版本号！"
    echo "⚠️程序将跳过微信版本检查并继续运行,但建议检查微信安装状态。"
    echo "⚠️如果程序启动后无法控制微信,请下载微信4.1.2版本：https://www.123865.com/s/tB1wvd-LWaAA"
    echo.
    echo "🔄3秒后自动继续..."
    timeout /t 3 /nobreak >nul
    goto :check_python
)

:: 解析主版本号
for /f "tokens=1 delims=." %%a in ("!wxversion!") do (
    set "major=%%a"
)

:: 只判断主版本
if !major! lss 3 (
    echo "❌当前微信版本 !wxversion!,版本过低！"
    echo "⚠️请下载微信4.1.2版本"
    echo "⚠️下载地址：https://www.123865.com/s/tB1wvd-LWaAA"
    echo.
    echo "🔄如果您确信已经安装了正确版本的微信,请按下键盘任意键继续运行程序,否则关闭窗口退出。"
    pause
    goto :check_python
)
rem 移除4.x版本检查，现在支持4.1.2版本

echo "✅ 微信版本检查通过：!wxversion!"

:check_python

:: ---------------------------
:: 检查 Python 是否安装
:: ---------------------------
echo 🔍 检查Python环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo "❌ Python 未安装或未添加到系统PATH！"
    echo "请前往官网下载并安装 Python 3.9-3.12 版本"
    echo "下载地址：https://www.python.org/downloads/"
    echo "⚠️ 安装时请勾选"Add Python to PATH"选项"
    pause
    exit /b 1
)

:: 获取 Python 版本
for /f "tokens=2,*" %%i in ('python --version 2^>^&1') do set "pyversion=%%i"
echo "检测到Python版本：%pyversion%"

:: 解析版本号
for /f "tokens=1,2,3 delims=." %%a in ("%pyversion%") do (
    set "py_major=%%a"
    set "py_minor=%%b"
    set "py_patch=%%c"
)

:: 检查主版本号
if "%py_major%" neq "3" (
    echo "❌ 不支持的Python主版本：%pyversion%"
    echo "支持版本：Python 3.9-3.12"
    echo "当前版本：Python %pyversion%"
    pause
    exit /b 1
)

:: 检查次版本号范围 (3.9-3.12)
if %py_minor% lss 9 (
    echo "❌ Python版本过低：%pyversion%"
    echo "最低要求：Python 3.9"
    echo "当前版本：Python %pyversion%"
    echo "请升级Python版本"
    pause
    exit /b 1
)
if %py_minor% gtr 12 (
    echo "❌ Python版本过高：%pyversion%"       
    echo "支持版本：Python 3.9-3.12"
    echo "当前版本：Python %pyversion%"
    echo "可能存在兼容性问题,建议降级"
    pause
    exit /b 1
)

:: wxautox4 wheel 只提供 64 位 Intel/AMD Windows 构建
for /f %%i in ('python -c "import platform, struct; print(str(struct.calcsize(chr(80)) * 8) + chr(45) + platform.machine().lower())"') do set "py_arch=%%i"
if /I not "!py_arch!"=="64-amd64" if /I not "!py_arch!"=="64-x86_64" (
    echo "❌ 不支持的 Python 架构：!py_arch!"
    echo "请安装 64 位 Intel/AMD Python 3.9-3.12。"
    pause
    exit /b 1
)

echo "✅ Python版本检查通过：%pyversion% (满足3.9-3.12要求)"

:: ---------------------------
:: 检查 pip 是否存在
:: ---------------------------
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo "❌ pip 未安装,请先安装 pip。"
    pause
    exit /b 1
)

:: ---------------------------
:: 快速检查通用依赖，仅缺失、requirements 变化或修复模式时安装
:: ---------------------------
set "REQUIREMENTS_FILE=requirements.txt"
set "REQUIREMENTS_STAMP=.qiqi_requirements.sha256"
set "QIQI_REQUIRED_IMPORTS=flask,flask_cors,comtypes,colorama,PIL,typing_extensions,pyperclip,win32api,openai,pyautogui,werkzeug,psutil,filelock,requests,tenacity,bs4,lxml"

if not exist "!REQUIREMENTS_FILE!" (
    echo "❌ 未找到依赖清单：%CD%\!REQUIREMENTS_FILE!"
    echo "请确认 Run.bat 位于完整的项目目录中。"
    pause
    exit /b 1
)

set "REQUIREMENTS_HASH="
for /f "tokens=* delims=" %%H in ('certutil -hashfile "!REQUIREMENTS_FILE!" SHA256 2^>nul ^| findstr /R /X "[0-9A-Fa-f][0-9A-Fa-f]*"') do (
    if not defined REQUIREMENTS_HASH set "REQUIREMENTS_HASH=%%H"
)
set "REQUIREMENTS_HASH=!REQUIREMENTS_HASH: =!"

set "STORED_REQUIREMENTS_HASH="
set "REQUIREMENTS_CHANGED=0"
if defined REQUIREMENTS_HASH if exist "!REQUIREMENTS_STAMP!" (
    set /p "STORED_REQUIREMENTS_HASH=" < "!REQUIREMENTS_STAMP!"
    if /I not "!STORED_REQUIREMENTS_HASH!"=="!REQUIREMENTS_HASH!" set "REQUIREMENTS_CHANGED=1"
)

set "INSTALL_REASON="
if "!REPAIR_MODE!"=="1" set "INSTALL_REASON=用户启用了依赖修复模式"

if not defined INSTALL_REASON (
    echo "🔍 正在快速验证运行依赖..."
    call :CHECK_REQUIREMENTS
    if !errorlevel! neq 0 set "INSTALL_REASON=检测到依赖缺失、损坏或版本不符合要求"
)

if not defined INSTALL_REASON if "!REQUIREMENTS_CHANGED!"=="1" (
    set "INSTALL_REASON=requirements.txt 已发生变化"
)

if defined INSTALL_REASON goto :INSTALL_REQUIREMENTS

if not defined REQUIREMENTS_HASH (
    echo "⚠️ 无法计算 requirements.txt 指纹，本次仅依据导入和版本自检结果继续。"
) else if not exist "!REQUIREMENTS_STAMP!" (
    >"!REQUIREMENTS_STAMP!" echo(!REQUIREMENTS_HASH!
    if !errorlevel! neq 0 echo "⚠️ 无法保存依赖指纹；下次启动仍会执行快速自检。"
)
echo "✅ 运行依赖检查通过，已跳过 pip 安装"
goto :REQUIREMENTS_READY

:INSTALL_REQUIREMENTS
echo "🔄 正在安装依赖：!INSTALL_REASON!"
echo "📦 使用多源策略：阿里源(主) + 清华源(备)"
python -m pip install --disable-pip-version-check -r "!REQUIREMENTS_FILE!" -f ".\libs" ^
    --index-url https://mirrors.aliyun.com/pypi/simple/ ^
    --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple ^
    --trusted-host mirrors.aliyun.com ^
    --trusted-host pypi.tuna.tsinghua.edu.cn

if !errorlevel! neq 0 (
    echo "❌ 安装依赖失败。请检查网络、pip 输出及 !REQUIREMENTS_FILE!。"
    pause
    exit /b 1
)

call :CHECK_REQUIREMENTS
if !errorlevel! neq 0 (
    echo "❌ 依赖安装已结束，但导入或版本自检仍未通过。"
    echo "请运行 Run.bat /repair 重试，并检查上方 pip 错误信息。"
    pause
    exit /b 1
)

if defined REQUIREMENTS_HASH (
    >"!REQUIREMENTS_STAMP!" echo(!REQUIREMENTS_HASH!
    if !errorlevel! neq 0 echo "⚠️ 依赖已安装，但无法保存依赖指纹。"
)
echo "✅ 通用运行依赖安装并验证完成"

:REQUIREMENTS_READY

:: ---------------------------
:: 检查并按需安装定制版 wxautox4-wechatbot
:: ---------------------------
set "WXAUTOX_DISTRIBUTION=wxautox4-wechatbot"
set "WXAUTOX_REQUIRED_VERSION=40.1.10"
python -c "from importlib.metadata import version; import sys; sys.exit(0 if version('!WXAUTOX_DISTRIBUTION!') == '!WXAUTOX_REQUIRED_VERSION!' else 1)" >nul 2>&1
if !errorlevel! equ 0 (
    echo "✅ !WXAUTOX_DISTRIBUTION! !WXAUTOX_REQUIRED_VERSION! 已安装，跳过安装"
    goto :WXAUTOX_READY
)

echo "🔄 未检测到 wxautox4-wechatbot 40.1.10，准备安装对应本地版本..."
set "WHL_FILE="
if "!py_minor!"=="9" set "WHL_FILE=libs\wxautox4_wechatbot-40.1.10-cp39-cp39-win_amd64.whl"
if "!py_minor!"=="10" set "WHL_FILE=libs\wxautox4_wechatbot-40.1.10-cp310-cp310-win_amd64.whl"
if "!py_minor!"=="11" set "WHL_FILE=libs\wxautox4_wechatbot-40.1.10-cp311-cp311-win_amd64.whl"
if "!py_minor!"=="12" set "WHL_FILE=libs\wxautox4_wechatbot-40.1.10-cp312-cp312-win_amd64.whl"

if not defined WHL_FILE (
    echo "❌ 无法确定 Python !py_minor! 对应的 wxautox4-wechatbot 安装包"
    pause
    exit /b 1
)

if not exist "!WHL_FILE!" (
    echo "❌ 缺少 Python 3.!py_minor! 对应的本地安装包：!WHL_FILE!"
    echo "请重新下载完整安装包后再运行。"
    pause
    exit /b 1
)

echo "📦 安装文件: !WHL_FILE!"
python -m pip install "!WHL_FILE!" --no-deps
if !errorlevel! neq 0 (
    echo "❌ wxautox4-wechatbot 40.1.10 安装失败"
    pause
    exit /b 1
)

python -c "from importlib.metadata import version; import sys; sys.exit(0 if version('!WXAUTOX_DISTRIBUTION!') == '!WXAUTOX_REQUIRED_VERSION!' else 1)" >nul 2>&1
if !errorlevel! neq 0 (
    echo "❌ 安装后的 wxautox4-wechatbot 版本不是 40.1.10"
    pause
    exit /b 1
)
echo "✅ wxautox4-wechatbot 40.1.10 安装成功"

:WXAUTOX_READY

echo "🔍 正在验证微信控制组件可正常导入..."
python -c "from wxautox4_wechatbot import WeChat; from wxautox4_wechatbot.param import WxParam" >nul 2>&1
if !errorlevel! neq 0 (
    echo "❌ wxautox4-wechatbot !WXAUTOX_REQUIRED_VERSION! 已安装但无法导入。"
    echo "请确认正在使用 64 位 Python 3.!py_minor!，并重新运行 Run.bat 安装完整依赖。"
    pause
    exit /b 1
)
echo "✅ wxautox4-wechatbot !WXAUTOX_REQUIRED_VERSION! 导入验证通过"

:: 安装wxautox-wechatbot (旧版本微信用) - 定制版已注释
rem python -m pip install -U -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple wxautox-wechatbot
rem if !errorlevel! neq 0 (
rem     python -m pip install -U wxautox-wechatbot
rem )

:: 安装wxauto (备用) - 定制版已注释
rem python -m pip install -U -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple wxauto
rem if !errorlevel! neq 0 (
rem     python -m pip install -U wxauto
rem )

echo "✅ 所有依赖检查通过！"

:: 清屏
cls

:: ---------------------------
:: 检查程序更新
:: ---------------------------

echo "🟢 官方更新仓库尚未配置，已安全跳过自动更新。"
echo "新版本请关注 QQ 交流群 QQ_GROUP_ID_REMOVED，群主 QQ GROUP_OWNER_ID_REMOVED。"

:: 清屏
cls

:: ---------------------------
:: 启动程序
:: ---------------------------
echo "🟢 启动主程序..."
python config_editor.py
set "PROGRAM_EXIT_CODE=!errorlevel!"
exit /b !PROGRAM_EXIT_CODE!

:CHECK_REQUIREMENTS
python -c "import importlib, os, sys; from importlib.metadata import version; [importlib.import_module(name) for name in os.environ['QIQI_REQUIRED_IMPORTS'].split(',')]; sys.exit(0 if version('openai') == '1.84.0' else 1)" >nul 2>&1
exit /b !errorlevel!
