@echo off
setlocal
title iPodFS
cd /d "%~dp0"

echo.
echo   iPodFS - iPod touch per USB verwalten, ohne iTunes
echo   ==================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo   Python ist nicht installiert oder nicht im PATH.
    echo   Download: https://www.python.org/downloads/
    echo   Beim Installieren "Add python.exe to PATH" ankreuzen.
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo   Richte die Umgebung ein ^(einmalig, dauert kurz^) ...
    python -m venv .venv
    if errorlevel 1 goto fehler
)

echo   Pruefe Abhaengigkeiten ...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto fehler

echo   Starte - der Browser oeffnet sich gleich.
echo.
".venv\Scripts\python.exe" -m ipodfs %*
goto ende

:fehler
echo.
echo   Die Einrichtung ist fehlgeschlagen.
pause
exit /b 1

:ende
endlocal
