from setuptools import setup

APP = ["main.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": True,
    # Icône personnalisée : place un fichier ndi-manager.icns
    # à la racine du projet (même dossier que ce setup.py).
    "iconfile": "ndi-manager.icns",
    "plist": {
        "CFBundleName": "NDI Manager",
        "CFBundleDisplayName": "NDI Manager",
        "CFBundleIdentifier": "com.example.ndimanager",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
    },
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

