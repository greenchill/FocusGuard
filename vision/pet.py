# -*- coding: utf-8 -*-
"""
Tamagotchi-style pet. The user sets its survivability to match their goals.

Health rises while you focus and drops on distractions. On neglect the pet gets sad
and the streak resets, but it does NOT die - it recovers as soon as you get back to work.

Pure state model (no GUI). Saved to data/pet.json.
"""
import os
import json
import time

from .paths import DATA_DIR

PET_PATH = os.path.join(DATA_DIR, "pet.json")

MOODS = ("happy", "neutral", "sad", "sleeping")
XP_PER_LEVEL = 100


class PetState:
    def __init__(self, name="Buddy", species="cat", survivability=5,
                 daily_goal_minutes=120):
        self.name = name
        self.species = species
        self.survivability = max(1, min(10, int(survivability)))  # 1..10
        self.daily_goal_minutes = int(daily_goal_minutes)
        self.health = 80.0            # 0..100
        self.xp = 0
        self.level = 1
        self.mood = "neutral"
        self.last_update = time.time()

    # (note)
    def regen_per_sec(self):
        # (note)
        return 0.15 + 0.03 * self.survivability

    def decay_per_event(self):
        # (note)
        return 18.0 / self.survivability

    def idle_decay_per_sec(self):
        # (note)
        return 0.05 + 0.02 * (10 - self.survivability)

    # (note)
    def on_focus_tick(self, seconds=1.0):
        self.health = min(100.0, self.health + self.regen_per_sec() * seconds)
        self._refresh_mood()

    def on_distraction(self):
        self.health = max(0.0, self.health - self.decay_per_event())
        self._refresh_mood()
        return self.health

    def on_idle(self, seconds=1.0):
        self.health = max(0.0, self.health - self.idle_decay_per_sec() * seconds)
        self._refresh_mood()

    def add_focus_xp(self, minutes):
        """    ;  .  True  level-up."""
        before = self.level
        self.xp += int(max(0, minutes))
        while self.xp >= XP_PER_LEVEL:
            self.xp -= XP_PER_LEVEL
            self.level += 1
        return self.level > before

    def on_neglect(self):
        """Daily goal missed / long neglect: sadness, but with a health floor."""
        self.health = max(25.0, self.health - 15.0)  # floor 25 - pet never dies
        self.mood = "sad"

    def _refresh_mood(self):
        if self.health >= 70:
            self.mood = "happy"
        elif self.health >= 40:
            self.mood = "neutral"
        else:
            self.mood = "sad"

    def health_fraction(self):
        return self.health / 100.0

    # (note)
    def to_dict(self):
        return {
            "name": self.name, "species": self.species,
            "survivability": self.survivability,
            "daily_goal_minutes": self.daily_goal_minutes,
            "health": round(self.health, 2), "xp": self.xp, "level": self.level,
            "mood": self.mood, "last_update": self.last_update,
        }

    def save(self):
        try:
            from .paths import atomic_write_json
            atomic_write_json(PET_PATH, self.to_dict())
        except Exception as e:
            print(f"[Pet] Could not save: {e}")

    @classmethod
    def load(cls, name="Buddy", species="cat", survivability=5, daily_goal_minutes=120):
        pet = cls(name, species, survivability, daily_goal_minutes)
        if os.path.exists(PET_PATH):
            try:
                with open(PET_PATH, "r", encoding="utf-8") as f:
                    d = json.load(f)
                pet.health = float(d.get("health", pet.health))
                pet.xp = int(d.get("xp", 0))
                pet.level = int(d.get("level", 1))
                pet.mood = d.get("mood", "neutral")
                pet.last_update = float(d.get("last_update", time.time()))
                # (note)
            except Exception:
                pass
        return pet
