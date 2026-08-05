@echo off
title PriceTrail setup
cd /d "%~dp0"

echo.
echo   PriceTrail setup
echo   ================
echo.

REM The "py" launcher ships with Python on Windows and is the most reliable
REM way to find it. Fall back to "python" if it isn't there.
where py >nul 2>nul
if %errorlevel%==0 (
    py start.py
    goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python start.py
    goto done
)

echo   Python is not installed yet.
echo.
echo   1. Go to  python.org/downloads
echo   2. Click the big yellow Download button
echo   3. IMPORTANT: on the first install screen, tick the box that says
echo      "Add python.exe to PATH" at the bottom. It is easy to miss and
echo      nothing will work without it.
echo   4. Finish the install, then double-click this file again.
echo.

:done
echo.
echo   Press any key to close this window.
pause >nul
