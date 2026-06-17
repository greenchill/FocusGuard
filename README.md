# FocusGuard

A desktop focus companion with a pixel-cat pet. Your webcam watches for the usual
focus-killers — **phone in hand, eyes off the screen, slumped posture** — and the cat
reacts. Run a Pomodoro session, optionally loop calming **brown noise**, and (if you
want) hard-block distracting websites while you focus. Everything runs **100% locally**;
no video is ever recorded, uploaded, or shared.

> Made by **David Kitunov**.

## Features

- **Pixel-cat companion** — a 16-pose sprite cat reacts to distractions. During a focus
  session it hops onto your **desktop** as a floating pet: drag it around, let it
  **perch on the top edge of any app window**, click it for a speech bubble, right-click
  → *Send home* (or the paw button / `Ctrl+Alt+P`).
- **Pomodoro timer** with a dual-handle ring dial; focus/break lengths editable in
  Settings or by dragging the dial. The remaining time floats next to the desktop cat.
- **Webcam focus tracking (optional)** — MediaPipe FaceLandmarker (gaze), Pose
  (posture) and an EfficientDet phone detector, all on-device. Turn it off with
  **"Use camera"** to run as a plain Pomodoro timer that still credits your focus time.
- **Brown noise** — loop calming background noise while you focus. It pauses on breaks
  and continues afterwards, and stops when the session ends. Toggle it on the main
  screen or in Settings.
- **Pause = camera off** — pausing a session releases the webcam (the LED goes dark) and
  pauses the noise; resuming re-opens everything.
- **Distracting-site blocking (optional, needs admin)** — redirects the domains you list
  via the Windows `hosts` file during focus. **Your sites are never left blocked** — the
  block is cleared on breaks, on stop, on exit, and even after a crash on next launch.
- **Privacy-first** — local-only processing, an explicit first-run camera consent, and a
  camera-off avatar whenever the webcam isn't live.

## Running from source

Requires **Python 3.12** (Windows).

```bat
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Or double-click **`run_gui.bat`** (it finds the project-local `.venv`, then `py -3`,
then `python` on PATH; the console stays open so any error is visible).

## Building a standalone app + installer

Build the frozen app (PyInstaller, via `focusguard.spec`):

```bat
.venv\Scripts\python -m PyInstaller --noconfirm focusguard.spec
```

This produces `dist\FocusGuard\` (`FocusGuard.exe` + `_internal\`, ~440 MB) carrying the
detection models, sprite sheet, brown-noise audio, theme and icon. `config.json`,
`data\`, and `blocklist.txt` are created next to the .exe on first run.

Build the compact installer ([Inno Setup 6.3+](https://jrsoftware.org/isdl.php)):

```bat
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

→ `installer_output\FocusGuard-Setup.exe` (~183 MB) — a per-user install (no UAC), with
Start-Menu/desktop shortcuts and an uninstaller.

### Notes
- Phone detection uses MediaPipe **EfficientDet** by default — no PyTorch. Installing
  the optional `ultralytics` enables a YOLOv8 detector but pulls in ~360 MB of torch;
  the app falls back to EfficientDet automatically when it's absent.
- The site-block needs administrator rights to edit `hosts`. Without admin, FocusGuard
  tells you and leaves `hosts` untouched. Use **"Restart as administrator"** in Settings.
- Real camera names in Settings come from `pygrabber` (DirectShow).

## Tech

Python · PyQt6 · MediaPipe · OpenCV · NumPy · sounddevice · QtMultimedia · PyInstaller.
