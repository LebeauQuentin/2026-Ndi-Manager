import platform

from setuptools import setup

APP = ["main.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": True,
    # Icône de l'app.
    "iconfile": "media/ndi-manager.icns",
    "plist": {
        "CFBundleName": "NDI Manager",
        "CFBundleDisplayName": "NDI Manager",
        "CFBundleIdentifier": "com.example.ndimanager",
        "CFBundleVersion": "1.0.0",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
    },
}

if platform.system().lower() != "darwin":
    raise RuntimeError("Ce packaging est supporte uniquement sur macOS.")

machine = platform.machine().lower()
if machine not in {"arm64", "x86_64"}:
    raise RuntimeError(
        "Ce packaging cible uniquement Apple Silicon (arm64) ou Intel (x86_64)."
    )

mac_ver, _, _ = platform.mac_ver()
try:
    mac_major = int(mac_ver.split(".")[0]) if mac_ver else 0
except ValueError:
    mac_major = 0

if mac_major < 13:
    raise RuntimeError("Ce packaging requiert macOS 13+.")

OPTIONS["arch"] = machine

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

