@echo off
REM ============================================================
REM  Virtual Shadow Projection - Windows uchun bir marta bosib ishga tushirish
REM  Birinchi marta: kerak bo'lsa Python 3.11 ni o'zi yuklab o'rnatadi,
REM  virtual muhit yaratadi va kutubxonalarni o'rnatadi.
REM  Keyingi safar: darhol ishga tushadi.
REM  Qo'shimcha parametrlar: start_windows.bat --demo  yoki  --room garaj.jpg
REM ============================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if exist ".venv\Scripts\python.exe" goto run

set "PY="
set "LOCALPY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

REM 1) Oldin shu skript o'rnatgan (yoki PATH'da bo'lmagan) Python 3.11
if exist "%LOCALPY%" set "PY="%LOCALPY%""

REM 2) py launcher orqali 3.12 / 3.11 / 3.10
if not defined PY (
    for %%V in (3.12 3.11 3.10) do (
        if not defined PY (
            py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
        )
    )
)

REM 3) PATH'dagi python (mos versiya bo'lsa)
if not defined PY (
    python -c "import sys; exit(0 if (3,9)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1 && set "PY=python"
)

REM 4) Topilmadi -> python.org dan yuklab, jimgina o'rnatamiz (admin shart emas)
if not defined PY (
    echo [i] Python 3.11 topilmadi. python.org dan yuklab olinmoqda ^(~25 MB^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile \"$env:TEMP\python-3.11.9-amd64.exe\""
    if not exist "%TEMP%\python-3.11.9-amd64.exe" (
        echo [x] Yuklab bo'lmadi. Internetni tekshiring yoki qo'lda o'rnating:
        echo     https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
        pause
        exit /b 1
    )
    echo [i] Python o'rnatilmoqda ^(1-2 daqiqa, oyna chiqmaydi^)...
    "%TEMP%\python-3.11.9-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0
    if not exist "%LOCALPY%" (
        echo [x] Python o'rnatilmadi. Yuqoridagi havoladan qo'lda o'rnating
        echo     ^("Add python.exe to PATH" belgisini qo'ying^) va bu faylni qayta ishga tushiring.
        pause
        exit /b 1
    )
    set "PY="%LOCALPY%""
    echo [i] Python o'rnatildi.
)

echo [i] Virtual muhit yaratilmoqda...
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo [x] Virtual muhit yaratilmadi.
    pause
    exit /b 1
)
echo [i] Kutubxonalar o'rnatilmoqda ^(2-5 daqiqa, internet kerak^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [x] Kutubxonalarni o'rnatishda xato. .venv papkasini o'chirib, qayta urinib ko'ring.
    rmdir /s /q .venv
    pause
    exit /b 1
)

:run
echo [i] Dastur ishga tushmoqda... ^(chiqish: Q yoki Esc^)
".venv\Scripts\python.exe" virtual_shadow.py %*
if errorlevel 1 pause
