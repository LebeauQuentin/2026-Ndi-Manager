#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  echo "Erreur: environnement virtuel .venv introuvable."
  echo "Cree-le avec: python3 -m venv .venv"
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Erreur: .venv existe mais .venv/bin/python est introuvable."
  exit 1
fi

if ! .venv/bin/python -m ruff --version >/dev/null 2>&1; then
  echo "==> Installation de Ruff dans .venv"
  .venv/bin/python -m pip install ruff
fi

.venv/bin/python -m ruff check .
