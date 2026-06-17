# -*- coding: utf-8 -*-
"""
Pleasant synthesized chimes (replaces harsh winsound square-wave beeps).

Tones are soft sine waves with an attack/decay envelope and a touch of overtone,
generated with numpy and played through sounddevice. If audio output is unavailable
we fall back to the old winsound patterns so alerts are never silently lost.
"""
import threading

import numpy as np

try:
    import sounddevice as sd
except Exception:
    sd = None

from .paths import IS_WINDOWS
from . import detection as _det

SR = 22050


def synth_tone(freq, dur, vol=0.35, sr=SR):
    """One soft chime note: sine + quiet octave overtone, smooth envelope."""
    n = max(1, int(sr * dur))
    t = np.linspace(0.0, dur, n, endpoint=False)
    wave = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2 * t)
    attack = min(0.015, dur * 0.2)
    a = np.clip(t / attack, 0, 1)
    d = np.clip((dur - t) / (dur * 0.7), 0, 1) ** 1.5
    return (wave * a * d * vol).astype(np.float32)


def synth_event(notes, gap=0.03, vol=0.35):
    """Concatenate (freq, dur) notes into one buffer. freq<=0 inserts silence."""
    chunks = []
    for freq, dur in notes:
        if freq <= 0:
            chunks.append(np.zeros(int(SR * dur), np.float32))
        else:
            chunks.append(synth_tone(freq, dur, vol))
        chunks.append(np.zeros(int(SR * gap), np.float32))
    return np.concatenate(chunks) if chunks else np.zeros(1, np.float32)


# Gentle, distinct motifs (pentatonic-ish, no shrill squares).
EVENTS = {
    "phone":      [(659, 0.14), (523, 0.22)],                     # firm down step
    "away":       [(587, 0.16), (494, 0.20)],                     # soft "hey"
    "posture":    [(392, 0.18), (440, 0.22)],                     # low nudge up
    "break":      [(523, 0.14), (659, 0.14), (784, 0.26)],        # major lift
    "focus":      [(784, 0.12), (659, 0.12), (523, 0.22)],        # settle in
    "reward":     [(523, 0.12), (659, 0.12), (784, 0.12), (1047, 0.34)],
    "summon":     [(880, 0.09), (1175, 0.14)],                    # cute blip
    "petting":    [(1319, 0.07), (1568, 0.10)],                   # tiny chirp
    "combo":      [(659, 0.10), (831, 0.10), (988, 0.18)],
}

_WINSOUND_FALLBACK = {
    "phone": _det.PHONE_PATTERN, "away": _det.AWAY_PATTERN,
    "posture": _det.POSTURE_PATTERN, "break": _det.BREAK_START_PATTERN,
    "focus": _det.BREAK_END_PATTERN, "reward": _det.REWARD_PATTERN,
    "summon": _det.SUMMON_PATTERN, "petting": _det.SUMMON_PATTERN,
    "combo": _det.REWARD_PATTERN,
}


class ChimePlayer:
    """Named-event chime player; thread-safe, never overlaps itself."""

    def __init__(self, enabled=True, volume=0.35):
        self.enabled = enabled
        self.volume = float(volume)
        self._busy = False
        self._lock = threading.Lock()
        self._cache = {}
        self._sd_ok = sd is not None

    def _buffer(self, event):
        if event not in self._cache:
            self._cache[event] = synth_event(EVENTS.get(event, EVENTS["away"]),
                                             vol=self.volume)
        return self._cache[event]

    def play(self, event, force=False):
        if not self.enabled:
            return
        with self._lock:
            if self._busy and not force:
                return
            self._busy = True

        def run():
            try:
                if self._sd_ok:
                    try:
                        sd.play(self._buffer(event), SR, blocking=True)
                    except Exception:
                        self._sd_ok = False     # device vanished -> fall back
                if not self._sd_ok and IS_WINDOWS:
                    import winsound
                    for f, d in _WINSOUND_FALLBACK.get(event, []):
                        if f <= 0:
                            import time; time.sleep(d / 1000)
                        else:
                            winsound.Beep(int(f), int(d))
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=run, daemon=True).start()
