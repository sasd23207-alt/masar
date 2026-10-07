@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo تشغيل مِسار ... افتح المتصفح على http://127.0.0.1:8787
python server.py
pause
