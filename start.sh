#!/usr/bin/env bash
# iPodFS starten (macOS / Linux)
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "Python 3 nicht gefunden."; exit 1; }

echo
echo "  iPodFS - iPod touch per USB verwalten, ohne iTunes"
echo "  =================================================="
echo

if [ ! -d .venv ]; then
  echo "  Richte die Umgebung ein (einmalig) ..."
  "$PY" -m venv .venv
fi

echo "  Pruefe Abhaengigkeiten ..."
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

if [ "$(uname)" = "Linux" ] && [ ! -e /var/run/usbmuxd ]; then
  echo
  echo "  Hinweis: usbmuxd laeuft nicht. Unter Debian/Ubuntu:"
  echo "      sudo apt install usbmuxd"
  echo
fi

echo "  Starte - der Browser oeffnet sich gleich."
echo
exec .venv/bin/python -m ipodfs "$@"
