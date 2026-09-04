@echo off
REM Builds VideoDownloader.exe from desktop_app.py.
REM Run this ON WINDOWS, inside this web_application folder, with a
REM Python virtual environment activated that has requirements.txt
REM installed (pip install -r requirements.txt).

pyinstaller --onefile --windowed --name VideoDownloader ^
    --add-data "templates;templates" ^
    desktop_app.py

echo.
echo Done. The executable is at dist\VideoDownloader.exe
