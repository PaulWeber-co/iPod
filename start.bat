@echo off
title iPod Sync Tool
echo.
echo  ===================================
echo    iPod Sync Tool - Starte...
echo  ===================================
echo.
echo  Installiere Abhaengigkeiten...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo  FEHLER: Konnte Abhaengigkeiten nicht installieren.
    echo  Bitte stelle sicher, dass Python installiert ist.
    echo  Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
echo  OK!
echo.
echo  Starte iPod Sync Tool...
echo  (Fenster oeffnet sich gleich)
echo.
python app.py
if errorlevel 1 (
    echo.
    echo  Ein Fehler ist aufgetreten.
    pause
)
