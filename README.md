# NDI Manager

Application desktop Python (UI native macOS via PyObjC) pour detecter et manipuler des sources NDI sur le reseau local.

## Compatibilite

- macOS 13+ uniquement
- Apple Silicon uniquement (`arm64`, puces M)

## Fonctionnalites principales

- Detection des sources NDI (nom, IP, URL)
- Refresh automatique + manuel
- Mini-preview sur la source selectionnee
- Outils reseau (check network, ping)
- Actions rapides (copy IP/URL, ouverture Web UI, ouverture NDI Tools)

## Prerequis (nouvelle machine)

1. Mac Apple Silicon avec macOS 13 ou plus recent
2. Python 3 installe
3. NDI SDK (ou NDI Tools) installe pour macOS
   - Le binaire attendu est: `/Library/NDI SDK for Apple/lib/macOS/libndi.dylib`
4. Xcode Command Line Tools (recommande)

## Installation du projet sur un nouveau Mac

```bash
git clone <URL_DU_REPO>
cd Ndi-Manager
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt
```

## Lancer en mode developpement

```bash
source .venv/bin/activate
python3 main.py
```

## Lint Python (equivalent ESLint)

Le plus simple a maintenir ici: **Ruff**.

```bash
./scripts/lint.sh
```

Ruff verifie rapidement les erreurs courantes, style, imports et bonnes pratiques.

## Exporter en application macOS (.app)

### Methode recommandee (script)

```bash
./scripts/build_app.sh
```

Sortie generee:

- `dist/NDI Manager.app`

Tu peux ensuite copier ce `.app` sur un autre Mac compatible (macOS 13+ et puce M).

### Methode manuelle (si besoin)

```bash
rm -rf build dist
source .venv/bin/activate
python3 setup.py py2app
```

## Nettoyer le projet local

```bash
./scripts/clean.sh
```

## Distribution (optionnel mais recommande)

Pour une distribution externe, prevoir:

- signature du binaire (`codesign`)
- notarization Apple (si partage hors usage local)

Ces etapes ne sont pas necessaires pour un usage local ou interne de test.

## Depannage rapide

- Erreur NDI au lancement:
  - verifier la presence de `libndi.dylib` au chemin attendu
  - ou definir `NDI_SDK_DIR` si SDK installe ailleurs

Exemple:

```bash
export NDI_SDK_DIR="/Library/NDI SDK for Apple"
python3 main.py
```

- L'application refuse de demarrer:
  - verifier que la machine est bien en `arm64` (Apple Silicon)
  - verifier la version macOS (13+)

## Structure du projet (essentiel)

- `main.py`: logique applicative et UI macOS
- `setup.py`: packaging py2app
- `requirements.txt`: dependances Python
- `scripts/build_app.sh`: build complet (clean + verification + export .app)
- `scripts/lint.sh`: lint Python avec Ruff
- `scripts/clean.sh`: nettoyage artefacts locaux
- `Media/ndi-manager.icns`: icone de l'application
- `docs/AGENT.md`: doc interne sur l'architecture et les decisions
