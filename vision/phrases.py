# -*- coding: utf-8 -*-
"""Cat phrase bank (EN): one line per event. Pure data + a single picker.

Port of core/phrases.py from FocusGuard, translated to English for our UI.
Placeholders: {name} {level} {mult} {badge}.
"""
import random

PHRASES = {
    "summon": [
        "Hi! I'm {name}!", "You called? :3", "On duty!",
        "Let's focus together!", "I'm here, I'm here!",
    ],
    "phone": [
        "Phone! Put it down!", "Nope. Pocket. Now.", "The scroll can wait!",
        "Eyes on the screen, human!", "Drop it!",
    ],
    "away": [
        "Back to the screen!", "Hey, I'm right here!", "Focus up, friend!",
        "The screen misses you!", "Caught that wandering gaze!",
    ],
    "posture": [
        "Sit up straight!", "Posture check!", "Shoulders back!",
        "Be a proud cat, not a shrimp!",
    ],
    "break_start": [
        "Break time!", "Stretch those paws!", "You earned it!", "Drink some water!",
    ],
    "focus_start": [
        "Let's go!", "Round two!", "Back at it!", "We've got this!",
    ],
    "session_done": [
        "Great job!", "Crushed it!", "I'm proud of you!", "Time for a victory nap!",
    ],
    "levelup": [
        "Level {level}!", "I got stronger!", "We're growing!", "Ding! Lv.{level}!",
    ],
    "combo": [
        "Combo x{mult}!", "On fire! x{mult}", "Unstoppable! x{mult}!",
    ],
    "petting": [
        "Purr-r...", "Mrp!", "Pet me more!", "*happy purring*", "Hee, that tickles!",
    ],
    "badge": [
        "Reward: {badge}!", "New trophy: {badge}!", "Shiny! {badge}!",
    ],
    "idle_sad": [
        "I miss focusing...", "Just one session? Please?", "It's so quiet here...",
    ],
}


def say_for(event, **kw):
    options = PHRASES.get(event) or ["..."]
    line = random.choice(options)
    try:
        return line.format(**kw)
    except Exception:
        return line
