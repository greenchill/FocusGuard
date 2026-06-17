# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for FocusGuard (onedir build, with app icon).

Bundles the heavy ML stack (MediaPipe graphs/models, Ultralytics, OpenCV,
sounddevice/PortAudio, pygrabber/comtypes) plus our own data files:
  - models/            -> the .task / .pt / .tflite detection models (read-only);
  - assets/cat_sheet.png -> the 16-pose sprite cat;
  - style.qss          -> the dark theme.
User-writable files (config.json, data/, blocklist.txt) are NOT bundled; the
frozen app writes them next to FocusGuard.exe (see vision/paths.py & firewall.py).

Build:  <venv python> -m PyInstaller --noconfirm focusguard.spec
Output: dist/FocusGuard/FocusGuard.exe
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Ship ONLY the models actually used at runtime (MediaPipe face/pose/hand landmarkers +
# the EfficientDet phone detector). yolov8n.pt is intentionally dropped — phone detection
# runs on EfficientDet (no torch/ultralytics), see vision/phone_detector.py.
datas = [
    ("models/face_landmarker.task", "models"),
    ("models/pose_landmarker_lite.task", "models"),
    ("models/hand_landmarker.task", "models"),
    ("models/efficientdet_lite0.tflite", "models"),
    ("assets/cat_sheet.png", "assets"),
    ("assets/profilecameraoff.png", "assets"),  # camera-off placeholder avatar
    ("sounds/brownnoise.mp4", "sounds"),       # looping focus brown noise (QtMultimedia)
    ("style.qss", "."),
    ("app_icon.ico", "."),
]
binaries = []
hiddenimports = ["pygrabber", "pygrabber.dshow_graph", "comtypes"]

# Pull data files / dynamic libs / submodules for the packages PyInstaller can't
# fully trace on its own (MediaPipe ships binary graphs + models; sounddevice needs
# the PortAudio DLL). NOTE: ultralytics is deliberately NOT collected — nothing imports
# it at runtime (the phone detector falls back to MediaPipe EfficientDet), and collecting
# it dragged in torch/torchvision/scipy/polars (~600 MB of dead weight).
for _pkg in ("mediapipe", "sounddevice", "comtypes"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# Our own package, in case any module is imported lazily.
hiddenimports += collect_submodules("vision")

# QtMultimedia (brown-noise playback) needs its plugins + the ffmpeg backend DLLs, which
# are loaded DYNAMICALLY and PyInstaller's static analysis can miss. Collect them
# explicitly from the PyQt6 Qt6 dir so audio works in the frozen app.
import os as _os, glob as _glob
from PyInstaller.utils.hooks import get_module_file_attribute as _gmfa
try:
    _pyqt6 = _os.path.dirname(_gmfa("PyQt6"))
    _qt6_bin = _os.path.join(_pyqt6, "Qt6", "bin")
    _qt6_mm_plugins = _os.path.join(_pyqt6, "Qt6", "plugins", "multimedia")
    for _pat in ("av*.dll", "sw*.dll", "Qt6Multimedia*.dll", "Qt6Network*.dll"):
        for _f in _glob.glob(_os.path.join(_qt6_bin, _pat)):
            binaries.append((_f, _os.path.join("PyQt6", "Qt6", "bin")))
    for _f in _glob.glob(_os.path.join(_qt6_mm_plugins, "*.dll")):
        binaries.append((_f, _os.path.join("PyQt6", "Qt6", "plugins", "multimedia")))
except Exception:
    pass

# Keep the heavy unused stack OUT of the bundle. Verified at runtime: the app loads only
# mediapipe / cv2 / numpy / matplotlib (matplotlib is required by `import mediapipe`).
# Everything below is either never imported or only referenced from a guarded lazy import
# that degrades gracefully (phone detector: ultralytics -> EfficientDet fallback).
excludes = [
    # ML stack pulled in only by ultralytics (which we don't use).
    "torch", "torchvision", "torchaudio", "ultralytics",
    "polars", "scipy", "pandas",
    "onnx", "onnxruntime",
    "sympy", "mpmath", "networkx",
    "numba", "llvmlite",
    "IPython", "jupyter", "notebook", "tensorboard",
    # Big Qt modules we never use (the app is QtWidgets/QtGui/QtCore/QtSvg only).
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineQuick",
    "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuick3D", "PyQt6.QtQuickWidgets",
    "PyQt6.QtMultimediaWidgets",   # QtMultimedia itself is USED (brown-noise playback)
    "PyQt6.Qt3DCore", "PyQt6.Qt3DRender", "PyQt6.Qt3DExtras",
    "PyQt6.Qt3DAnimation", "PyQt6.Qt3DInput", "PyQt6.Qt3DLogic",
    "PyQt6.QtCharts", "PyQt6.QtDataVisualization", "PyQt6.QtGraphs",
    "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtPositioning", "PyQt6.QtLocation",
    "PyQt6.QtBluetooth", "PyQt6.QtNfc", "PyQt6.QtSerialPort", "PyQt6.QtSerialBus",
    "PyQt6.QtSensors", "PyQt6.QtWebSockets", "PyQt6.QtWebChannel",
    "PyQt6.QtRemoteObjects", "PyQt6.QtTest", "PyQt6.QtSql", "PyQt6.QtDesigner",
    "PyQt6.QtHelp", "PyQt6.QtSpatialAudio", "PyQt6.QtTextToSpeech",
    "PyQt6.QtNetworkAuth",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

# ONEDIR build (default, reliable): the .exe + its libs live in dist/FocusGuard/.
# The cat icon is embedded via icon=.
#
# ONEFILE alternative (single self-extracting FocusGuard.exe, slower start): replace
# this EXE(...) + COLLECT(...) pair with a single EXE that takes a.binaries + a.datas:
#     exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FocusGuard",
#               runtime_tmpdir=None, console=False, icon="app_icon.ico")
# (and delete the COLLECT below). Note: onefile needs a clean Python install — on
# some machines the build's bootloader step fails to load native modules (e.g.
# _sha512); onedir avoids that.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FocusGuard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app; set True to see startup errors while debugging
    disable_windowed_traceback=False,
    icon="app_icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FocusGuard",
)
