#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPYCACHEPREFIX="$PWD/.pycache"
export PYTHONDONTWRITEBYTECODE=1

echo "==> NDI Manager build (macOS 13+ / Intel x86_64)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Erreur: ce script doit etre lance sur macOS."
  exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "Erreur: ce script cible uniquement Intel (x86_64)."
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  echo "Erreur: environnement virtuel .venv introuvable."
  echo "Cree-le avec:"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source ".venv/bin/activate"

python3 --version
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "==> Nettoyage build precedent"
rm -rf build dist

echo "==> Verification syntaxe"
PYTHONPYCACHEPREFIX="$PWD/.pycache" PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile main.py setup.py

echo "==> Generation .app"
python3 setup.py py2app

APP_PATH="dist/NDI Manager.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Erreur: build termine sans generer '$APP_PATH'."
  exit 1
fi

echo "==> OK: application generee"
echo "    $ROOT_DIR/$APP_PATH"

