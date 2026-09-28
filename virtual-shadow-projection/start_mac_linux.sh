#!/usr/bin/env bash
# ============================================================
#  Virtual Shadow Projection - macOS / Linux uchun ishga tushirish
#  Birinchi marta: .venv yaratadi va kutubxonalarni o'rnatadi.
#  Qo'shimcha parametrlar:  ./start_mac_linux.sh --demo   yoki  --room garaj.jpg
# ============================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    PY=""
    for c in python3.12 python3.11 python3.10 python3; do
        if command -v "$c" >/dev/null 2>&1 && \
           "$c" -c 'import sys; exit(0 if (3,9)<=sys.version_info[:2]<=(3,12) else 1)' 2>/dev/null; then
            PY="$c"; break
        fi
    done
    if [ -z "$PY" ]; then
        echo "[x] Python 3.9-3.12 topilmadi."
        echo "    macOS:  brew install python@3.11"
        echo "    Ubuntu: sudo apt install python3.11 python3.11-venv"
        exit 1
    fi
    echo "[i] Virtual muhit yaratilmoqda ($PY)..."
    "$PY" -m venv .venv
    echo "[i] Kutubxonalar o'rnatilmoqda (1-3 daqiqa)..."
    .venv/bin/python -m pip install --upgrade pip >/dev/null
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python virtual_shadow.py "$@"
