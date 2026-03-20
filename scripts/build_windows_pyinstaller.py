from __future__ import annotations

import glob
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT_DIR / "windows" / "main_windows.py"


def _default_runtime_dirs() -> list[Path]:
    return [
        Path(r"C:\Program Files\NDI\NDI 5 Runtime"),
        Path(r"C:\Program Files (x86)\NDI\NDI 5 Runtime"),
    ]


def _find_windows_dll_and_dir() -> tuple[str | None, Path | None]:
    if platform.architecture()[0] != "64bit":
        return None, None

    dll_name = "Processing.NDI.Lib.x64.dll"

    env_dir = os.environ.get("NDILIB_REDIST_FOLDER") or os.environ.get("NDI_RUNTIME_DIR") or os.environ.get(
        "NDI_REDIST_DIR"
    )
    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(_default_runtime_dirs())

    # Si une variable NDI_SDK_DIR est donnée, on tente un sous-dossier "redist"
    ndi_sdk_dir = os.environ.get("NDI_SDK_DIR")
    if ndi_sdk_dir:
        candidates.append(Path(ndi_sdk_dir) / "redist")

    for d in candidates:
        if not d.exists() or not d.is_dir():
            continue
        p = d / dll_name
        if p.exists():
            return dll_name, d

    return dll_name, None


def main():
    if platform.system().lower() != "windows":
        print("Erreur: exécute ce script sur Windows.")
        sys.exit(1)

    if not ENTRYPOINT.exists():
        raise FileNotFoundError(f"Entry point introuvable: {ENTRYPOINT}")

    distpath = ROOT_DIR / "dist_windows"
    workpath = ROOT_DIR / "build_windows"

    distpath.mkdir(parents=True, exist_ok=True)
    workpath.mkdir(parents=True, exist_ok=True)

    dll_name, runtime_dir = _find_windows_dll_and_dir()

    add_binaries: list[str] = []
    if runtime_dir:
        # On embarque tout le dossier runtime pour éviter des dépendances manquantes.
        for dll in glob.glob(str(runtime_dir / "*.dll")):
            add_binaries.extend(["--add-binary", f"{dll}.;*."])  # placeholder to be replaced below

    # PyInstaller a besoin de "path;dest". On reconstruit correctement la liste.
    add_binaries = []
    if runtime_dir:
        for dll in glob.glob(str(runtime_dir / "*.dll")):
            dll_path = Path(dll)
            add_binaries.extend(["--add-binary", f"{dll_path};."])

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        f"--distpath={distpath}",
        f"--workpath={workpath}",
        "--name",
        "NDI Manager",
    ]
    cmd.extend(add_binaries)
    cmd.append(str(ENTRYPOINT))

    print("Commande PyInstaller:")
    print(" ".join(cmd))

    subprocess.check_call(cmd)

    print(f"OK. Sortie: {distpath / 'NDI Manager'}")


if __name__ == "__main__":
    main()

