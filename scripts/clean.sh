#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Nettoyage artefacts locaux"
rm -rf build dist __pycache__ .ruff_cache .pytest_cache .mypy_cache
python3 - <<'PY'
from pathlib import Path
import shutil

root = Path(".").resolve()

for pycache in root.rglob("__pycache__"):
    if pycache.is_dir():
        shutil.rmtree(pycache, ignore_errors=True)

for pyc in root.rglob("*.pyc"):
    try:
        pyc.unlink()
    except FileNotFoundError:
        pass
PY

echo "==> OK"
