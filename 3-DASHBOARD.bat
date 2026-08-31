@echo off
title PriceTrail console
cd /d "%~dp0"
echo.
echo   Starting the console. Your browser will open.
echo   Close this window when you are done.
echo.
where py >nul 2>nul
if %errorlevel%==0 ( py dashboard.py & goto done )
where python >nul 2>nul
if %errorlevel%==0 ( python dashboard.py & goto done )
echo   Python not found. Run 1-SETUP.bat first.
pause
:done
