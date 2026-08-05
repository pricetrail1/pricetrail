@echo off
title PriceTrail - preview the website
cd /d "%~dp0"

echo.
echo   Building your website with SAMPLE data...
echo   The prices will be fake. There is an orange warning bar to remind you.
echo.
echo   Your browser will open in a moment.
echo   Come back here and press Ctrl+C when you want to stop.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -m pricetrail.publish --demo --serve
    goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m pricetrail.publish --demo --serve
    goto done
)

echo   Python not found. Run 1-SETUP.bat first.

:done
echo.
pause
