# -*- coding: utf-8 -*-
"""Focus session state machine (Pomodoro / Timer / Off)."""
import time


class Session:
    """Timer state machine. Returns transition events from update()."""

    def __init__(self, scfg):
        self.mode = scfg.get("mode", "pomodoro")
        self.focus_s = max(1, int(scfg.get("focus_minutes", 50))) * 60
        self.break_s = max(1, int(scfg.get("break_minutes", 10))) * 60
        self.timer_s = max(1, int(scfg.get("timer_minutes", 25))) * 60
        self.cycles = int(scfg.get("cycles", 0))
        self.pause_on_break = bool(scfg.get("pause_detection_on_break", True))
        now = time.time()
        self.cycle = 1
        if self.mode == "off":
            self.state = "focus"            # endless focus, no countdown
            self.duration = None
        elif self.mode == "timer":
            self.state = "focus"
            self.duration = self.timer_s
        else:                               # pomodoro
            self.mode = "pomodoro"
            self.state = "focus"
            self.duration = self.focus_s
        self.state_started = now

    def remaining(self, now):
        if self.duration is None:
            return None
        return max(0, int(self.duration - (now - self.state_started)))

    def is_focus(self):
        return self.state == "focus"

    def is_break(self):
        return self.state == "break"

    def is_done(self):
        return self.state == "done"

    def detection_active(self):
        if self.state == "focus":
            return True
        if self.state == "break":
            return not self.pause_on_break
        return False                        # done: stop watching

    def update(self, now):
        """Returns the transition event name, or None."""
        if self.duration is None or self.state == "done":
            return None
        if (now - self.state_started) < self.duration:
            return None

        # the current phase ran out of time
        if self.mode == "timer":
            self.state = "done"
            self.duration = None
            return "session_done"

        # pomodoro
        if self.state == "focus":
            self.state = "break"
            self.duration = self.break_s
            self.state_started = now
            return "break_start"
        else:  # break ended
            if self.cycles > 0 and self.cycle >= self.cycles:
                self.state = "done"
                self.duration = None
                return "session_done"
            self.cycle += 1
            self.state = "focus"
            self.duration = self.focus_s
            self.state_started = now
            return "focus_start"


def fmt_mmss(seconds):
    if seconds is None:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
