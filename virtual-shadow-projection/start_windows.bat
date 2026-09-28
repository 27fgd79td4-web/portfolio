@echo off
REM ============================================================
REM  Virtual Shadow Projection - Windows uchun bir marta bosib ishga tushirish
REM  Birinchi marta: Python muhitini yaratadi va kutubxonalarni o'rnatadi.
REM  Keyingi safar: darhol ishga tushadi.
REM  Qo'shimcha parametrlar: start_windows.bat --demo  yoki  --room garaj.jpg
REM ============================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if exist ".venv\Scripts\python.exe" goto run

set "PY="
for %%V in (3.12 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; exit(0 if (3,9)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [!] Python 3.10-3.12 topilmadi. O'rnatishga harakat qilinmoqda...
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [x] Avtomatik o'rnatilmadi. https://www.python.org/downloads/release/python-3119/ dan
        echo     Python 3.11 ni o'rnating ^("Add python.exe to PATH" belgisini qo'ying^).
        pause
        exit /b 1
    )
    echo [i] Python o'rnatildi. Bu faylni QAYTA ishga tushiring.
    pause
    exit /b 0
)

echo [i] Virtual muhit yaratilmoqda (%PY%)...
%PY% -m venv .venv || (echo [x] venv yaratilmadi & pause & exit /b 1)
echo [i] Kutubxonalar o'rnatilmoqda (1-3 daqiqa)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo [x] O'rnatishda xato & pause & exit /b 1)

:run
".venv\Scripts\python.exe" virtual_shadow.py %*
if errorlevel 1 pause
