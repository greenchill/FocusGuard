# -*- coding: utf-8 -*-
"""
   ,  (streak)   ().

  data/stats.json:
  { "days": { "2026-06-01": {focus_seconds, distractions, phone_events,
                              posture_bad_seconds, sessions_done} },
    "badges": ["first_session", "streak_3", ...] }
"""
import os
import json
import datetime

from .paths import DATA_DIR

STATS_PATH = os.path.join(DATA_DIR, "stats.json")

BADGES = {
    "first_session": "First session",
    "streak_3": "3-day streak",
    "streak_7": "7-day streak",
    "streak_30": "30-day streak",
    "goal_day": "Daily goal met",
    "focus_10h": "10 hours of focus total",
}


def _today():
    return datetime.date.today().isoformat()


def _empty_day():
    return {"focus_seconds": 0, "distractions": 0, "phone_events": 0,
            "posture_bad_seconds": 0, "sessions_done": 0}


class Stats:
    def __init__(self, data=None):
        self.data = data or {"days": {}, "badges": []}

    # (note)
    @classmethod
    def load(cls):
        if os.path.exists(STATS_PATH):
            try:
                with open(STATS_PATH, "r", encoding="utf-8") as f:
                    return cls(json.load(f))
            except Exception:
                pass
        return cls()

    def save(self):
        try:
            from .paths import atomic_write_json
            atomic_write_json(STATS_PATH, self.data)
        except Exception as e:
            print(f"[Stats] Could not save: {e}")

    # (note)
    def day(self, date=None):
        date = date or _today()
        self.data.setdefault("days", {})
        if date not in self.data["days"]:
            self.data["days"][date] = _empty_day()
        else:
            # (note)
            for k, v in _empty_day().items():
                self.data["days"][date].setdefault(k, v)
        return self.data["days"][date]

    def add_focus_seconds(self, seconds, date=None):
        self.day(date)["focus_seconds"] += int(seconds)

    def add_distraction(self, date=None):
        self.day(date)["distractions"] += 1

    def add_phone_event(self, date=None):
        self.day(date)["phone_events"] += 1

    def add_posture_bad_seconds(self, seconds, date=None):
        self.day(date)["posture_bad_seconds"] += int(seconds)

    def add_session_done(self, date=None):
        self.day(date)["sessions_done"] += 1

    # (note)
    def focus_minutes(self, date=None):
        return self.day(date)["focus_seconds"] // 60

    def total_focus_seconds(self):
        return sum(d.get("focus_seconds", 0) for d in self.data.get("days", {}).values())

    def last_n_days(self, n=7):
        """ (date_iso, focus_minutes)   n   ."""
        today = datetime.date.today()
        out = []
        for i in range(n - 1, -1, -1):
            d = (today - datetime.timedelta(days=i)).isoformat()
            secs = self.data.get("days", {}).get(d, {}).get("focus_seconds", 0)
            out.append((d, secs // 60))
        return out

    def streak(self, daily_goal_minutes):
        """   ( /)   ."""
        goal = max(1, int(daily_goal_minutes))
        days = self.data.get("days", {})
        today = datetime.date.today()
        # (note)
        # (note)
        start = 0
        if days.get(today.isoformat(), {}).get("focus_seconds", 0) // 60 >= goal:
            start = 0
        else:
            start = 1
        count = 0
        i = start
        while True:
            d = (today - datetime.timedelta(days=i)).isoformat()
            if days.get(d, {}).get("focus_seconds", 0) // 60 >= goal:
                count += 1
                i += 1
            else:
                break
        return count

    # (note)
    def award(self, badge):
        self.data.setdefault("badges", [])
        if badge not in self.data["badges"]:
            self.data["badges"].append(badge)
            return True
        return False

    def has_badge(self, badge):
        return badge in self.data.get("badges", [])

    def evaluate_badges(self, daily_goal_minutes):
        """     ( /)."""
        newly = []
        if self.day()["sessions_done"] >= 1 and self.award("first_session"):
            newly.append("first_session")
        if self.focus_minutes() >= daily_goal_minutes and self.award("goal_day"):
            newly.append("goal_day")
        s = self.streak(daily_goal_minutes)
        for thr, badge in ((3, "streak_3"), (7, "streak_7"), (30, "streak_30")):
            if s >= thr and self.award(badge):
                newly.append(badge)
        if self.total_focus_seconds() >= 10 * 3600 and self.award("focus_10h"):
            newly.append("focus_10h")
        return newly
