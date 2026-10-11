@echo off
setlocal

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python ne naiden. Ustanovite s python.org ili otklyuchite alias Microsoft Store.
    pause
    exit /b 1
)

echo Ispolzuetsya: %PY%
rem zakryvaem zapushchennye kopii, inache PyInstaller ne mozhet perepisat exe
taskkill /F /IM WaveX.exe >nul 2>nul
%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail
%PY% -m PyInstaller --noconsole --onefile --name "WaveX" --icon "icon.ico" --add-data "icon.ico;." --collect-all customtkinter main.py
if errorlevel 1 goto fail

copy /y dist\WaveX.exe "%USERPROFILE%\Desktop\WaveX.exe" >nul

echo.
echo Gotovo: dist\WaveX.exe
echo Kopiya na rabochem stole: %USERPROFILE%\Desktop\WaveX.exe
pause
exit /b 0

:fail
echo.
echo SBORKA NE UDALAS - sm. oshibki vyshe.
pause
exit /b 1
