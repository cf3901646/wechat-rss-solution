@echo off
chcp 65001 >nul
title WeChat Knowledge Base - Distribution Packager

echo ============================================================
echo       微信公众号知识库助手 - 一键生成离线分发包 (ZIP)
echo ============================================================
echo.
echo [*] 正在准备打包...
echo [*] 将自动排除个人微信登录凭据 (保护隐私)、运行缓存与虚拟环境...
echo.

set "SRC_DIR=%~dp0.."
set "DIST_ZIP=%SRC_DIR%\微信公众号知识库助手_开箱版.zip"
set "DIST_TMP=%SRC_DIR%\_dist_temp"

:: 清理旧临时目录和旧压缩包
if exist "%DIST_TMP%" rd /s /q "%DIST_TMP%"
if exist "%DIST_ZIP%" del /f /q "%DIST_ZIP%"

mkdir "%DIST_TMP%"
mkdir "%DIST_TMP%\data"
mkdir "%DIST_TMP%\output"
mkdir "%DIST_TMP%\output\articles"
mkdir "%DIST_TMP%\logs"

:: 复制必要代码与配置目录
xcopy "%SRC_DIR%\config" "%DIST_TMP%\config" /E /I /Q /Y >nul
xcopy "%SRC_DIR%\docker" "%DIST_TMP%\docker" /E /I /Q /Y >nul
xcopy "%SRC_DIR%\scripts" "%DIST_TMP%\scripts" /E /I /Q /Y >nul
xcopy "%SRC_DIR%\driver" "%DIST_TMP%\driver" /E /I /Q /Y >nul
xcopy "%SRC_DIR%\core" "%DIST_TMP%\core" /E /I /Q /Y >nul
xcopy "%SRC_DIR%\static" "%DIST_TMP%\static" /E /I /Q /Y >nul
xcopy "%SRC_DIR%\windows" "%DIST_TMP%\windows" /E /I /Q /Y >nul

:: 复制根目录关键文件
copy /y "%SRC_DIR%\Dockerfile" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\docker-compose.yml" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\.dockerignore" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\docker_start.bat" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\docker_stop.bat" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\docker_start.sh" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\docker_stop.sh" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\requirements.txt" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\LICENSE" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\启动知识库助手.bat" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\全自动搜索并爬取.bat" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\启动搜一搜监听助手.bat" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\安装嗅探证书.bat" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\关闭系统代理.bat" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\使用教程_Windows版.md" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\DOCKER_GUIDE.md" "%DIST_TMP%\" >nul
copy /y "%SRC_DIR%\README.md" "%DIST_TMP%\" >nul

:: 使用 PowerShell 将其压缩为 zip 文件
powershell -NoProfile -Command "Compress-Archive -Path '%DIST_TMP%\*' -DestinationPath '%DIST_ZIP%' -Force"

:: 清理临时目录
rd /s /q "%DIST_TMP%"

echo.
echo ============================================================
echo [成功] 开箱分发包已打包完成！
echo 文件路径: %DIST_ZIP%
echo.
echo 此压缩包特点：
echo 1. 已安全剔除微信登录凭据与私有 Token (保护隐私安全)；
echo 2. 已剔除历史文章缓存与无用虚拟环境，体积轻盈，极速分发；
echo 3. 解压后双击 docker_start.bat 或 启动知识库助手.bat 即可直接使用！
echo ============================================================
echo.
pause
