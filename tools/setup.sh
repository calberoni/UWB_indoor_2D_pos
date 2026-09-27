#!/usr/bin/env bash
# Prepara el Mac para compilar el firmware y ejecutar el bridge.
# Se puede repetir: solo instala lo que falta.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

step() { printf '\n== %s\n' "$1"; }

step "arduino-cli"
if ! command -v arduino-cli >/dev/null; then
    if ! command -v brew >/dev/null; then
        echo "error: falta Homebrew (https://brew.sh) para instalar arduino-cli"
        exit 1
    fi
    brew install arduino-cli
fi
arduino-cli core update-index >/dev/null
arduino-cli core list | grep -q '^arduino:mbed_nano' || arduino-cli core install arduino:mbed_nano
arduino-cli lib list | grep -q '^ArduinoBLE' || arduino-cli lib install ArduinoBLE

step "Driver DW3000"
tools/fetch_driver.sh

step "Python"
PY=""
for candidate in python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null; then
        PY=$candidate
        break
    fi
done
if [ -z "$PY" ]; then
    echo "error: hace falta Python 3.11 o posterior (brew install python@3.13)"
    exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r bridge/requirements.txt

step "Versiones"
arduino-cli version
arduino-cli core list | grep '^arduino:mbed_nano'
.venv/bin/python --version
echo "driver $(cat firmware/lib/decadriver/.commit)"

# El compilador y el programador que distribuye Arduino para este core son
# binarios x86_64: en un Mac con Apple Silicon necesitan Rosetta 2.
step "Toolchain"
GCC=$(find "$HOME/Library/Arduino15/packages/arduino/tools/arm-none-eabi-gcc" \
    -name arm-none-eabi-gcc -type f 2>/dev/null | head -1)
if [ -n "$GCC" ] && "$GCC" --version >/dev/null 2>&1; then
    "$GCC" --version | head -1
else
    echo "aviso: el compilador de Arduino no se puede ejecutar en este Mac."
    echo "       Instala Rosetta 2 y repite make setup:"
    echo "         softwareupdate --install-rosetta --agree-to-license"
    exit 2
fi

echo
echo "Todo listo."
