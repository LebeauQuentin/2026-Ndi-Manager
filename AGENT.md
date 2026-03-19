# NDI Manager - Agent Guide

## Objectif du projet

`NDI Manager` est une application desktop Python avec interface native macOS (PyObjC), permettant de:
- detecter les sources NDI sur le reseau local;
- afficher les informations utiles (nom, IP, URL);
- faire des actions rapides (copy IP/URL, ping, ouverture Web UI, mini-preview);
- verifier rapidement les conditions reseau pour des profils NDI.

## Cible plateforme (etat actuel)

Cette version est volontairement limitee a:
- macOS 13 (Ventura) ou plus recent;
- Apple Silicon uniquement (`arm64`, puces M1/M2/M3/...).

Restrictions appliquees dans:
- `main.py`: verification au lancement de l'application;
- `setup.py`: verification au build + metadata `LSMinimumSystemVersion=13.0` + `arch=arm64`.

## Dependances principales

- `pyobjc`: UI native Cocoa/AppKit depuis Python.
- `psutil`: stats reseau et interfaces.
- `py2app`: packaging en application `.app`.
- `ruff`: lint Python (equivalent ESLint cote Python).
- NDI SDK (`libndi.dylib`) attendu par defaut dans:
  `/Library/NDI SDK for Apple/lib/macOS/libndi.dylib`
  (modifiable via la variable d'environnement `NDI_SDK_DIR`).

## Architecture fonctionnelle

- Point d'entree: `main.py`.
- Controle plateforme au demarrage: `_validate_supported_platform_or_exit()`.
- Couche NDI:
  - `NDIWrapper`: chargement de `libndi.dylib`, discovery des sources, receiver, capture frame.
- Couche UI:
  - `AppDelegate`: creation fenetre, table des sources, actions utilisateur.
  - `TableDataSource`: source de donnees pour `NSTableView`.
- Outils:
  - test reseau (`check_network_report`);
  - ping via sous-processus;
  - ouverture URLs/outils externes.

## Build macOS (Apple Silicon)

Pre-requis:
- Python sur Mac Apple Silicon;
- dependances installees (`pip install -r requirements.txt`);
- NDI SDK installe.

Commande build recommandee:

```bash
./scripts/build_app.sh
```

Sortie attendue:
- application dans `dist/NDI Manager.app`.

## Qualite code

- Lancer le lint:

```bash
./scripts/lint.sh
```

- Nettoyer les artefacts locaux:

```bash
./scripts/clean.sh
```

## Comportement en cas de non compatibilite

L'application quitte immediatement si:
- OS non-macOS;
- architecture differente de `arm64`;
- version macOS inferieure a 13.

## Piste pour future adaptation Windows

Objectif futur: prise en charge Windows sans casser la base macOS.

Strategie recommandee:
1. extraire un coeur metier non-UI (NDI discovery, parsing IP, checks reseau);
2. isoler la couche UI par plateforme:
   - `ui/macos/` (existant);
   - `ui/windows/` (future implementation);
3. centraliser les verifications plateforme dans un module unique;
4. remplacer les appels specifiques (`Cocoa`, `NSWorkspace`, etc.) par abstractions;
5. choisir un packager Windows (ex: `PyInstaller`) lorsque la couche UI Windows est prete.

## Notes maintenance

- Ne pas committer `build/` et `dist/` (artefacts de build).
- Conserver les messages d'erreur explicites pour l'utilisateur final.
- Garder les checks plateforme stricts tant que la version Windows n'est pas implementee.
