# -*- coding: utf-8 -*-
"""
noise_player.py — looping background brown-noise player for focus sessions.

Plays sounds/brownnoise.mp4 on a loop via QtMultimedia (QMediaPlayer + QAudioOutput,
ffmpeg backend). The GameController drives it: play() during a focus phase, pause()
at a break / when the Pomodoro is paused (position is kept so it CONTINUES after the
break), and stop() when the session ends. Looping means a focus phase longer than the
1-hour file keeps going seamlessly; pausing at a phase boundary cuts it cleanly.

Fully guarded: if QtMultimedia or the audio device is unavailable, every method is a
silent no-op so the rest of the app is unaffected.
"""
import os

try:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    _MM_OK = True
except Exception:  # QtMultimedia missing (e.g. trimmed build) — degrade gracefully
    _MM_OK = False

from vision.paths import SOUNDS_DIR

NOISE_FILE = os.path.join(SOUNDS_DIR, "brownnoise.mp4")


class BrownNoisePlayer:
    """Looping brown-noise player with enabled-master + play/pause(keep position)/stop."""

    def __init__(self, path: str = NOISE_FILE, volume: float = 0.6):
        self._enabled = False           # the Settings/dashboard 'Brown noise' toggle
        self._player = None
        self._audio = None
        self._volume = float(volume)
        self._ok = _MM_OK and os.path.exists(path)
        if not self._ok:
            return
        try:
            self._audio = QAudioOutput()
            self._audio.setVolume(self._clamp(self._volume))
            self._player = QMediaPlayer()
            self._player.setAudioOutput(self._audio)
            self._player.setSource(QUrl.fromLocalFile(path))
            # Loop forever so a long focus phase (e.g. 70 min) never runs out.
            self._player.setLoops(QMediaPlayer.Loops.Infinite)
        except Exception:
            self._ok = False
            self._player = None
            self._audio = None

    # -------------------------------------------------------------- #
    @staticmethod
    def _clamp(v):
        return max(0.0, min(1.0, float(v)))

    @property
    def available(self) -> bool:
        return bool(self._ok and self._player is not None)

    def set_enabled(self, on: bool) -> None:
        """Master on/off (the 'Brown noise' preference). Off stops playback."""
        self._enabled = bool(on)
        if not self._enabled:
            self.stop()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_volume(self, volume_0_1: float) -> None:
        self._volume = self._clamp(volume_0_1)
        if self._audio is not None:
            try:
                self._audio.setVolume(self._volume)
            except Exception:
                pass

    # -------------------------------------------------------------- #
    def play(self) -> None:
        """Start/RESUME playback (from the current position) — used at focus start."""
        if not self.available or not self._enabled:
            return
        try:
            self._player.play()
        except Exception:
            pass

    def pause(self) -> None:
        """Pause but KEEP the position, so focus continues seamlessly after a break."""
        if not self.available:
            return
        try:
            self._player.pause()
        except Exception:
            pass

    def stop(self) -> None:
        """Stop and reset to the start — used when the whole session ends."""
        if self._player is None:
            return
        try:
            self._player.stop()
        except Exception:
            pass
