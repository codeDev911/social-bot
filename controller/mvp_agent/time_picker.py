# app/services/time_picker.py
from datetime import datetime, timedelta, timezone

def pick_best_times(platform_cfg: dict):
    now = datetime.now(timezone(timedelta(hours=5, minutes=0)))  # PKT
    slots = []
    for day_offset in range(1, 4):
        day = now + timedelta(days=day_offset)
        for w in platform_cfg["time_windows_local"]:
            start = datetime.combine(day.date(), datetime.fromisoformat(day.isoformat()).time()).replace(
                hour=int(w[0].split(":")[0]), minute=int(w[0].split(":")[1]), tzinfo=day.tzinfo
            )
            slots.append({"iso": start.isoformat(), "label": start.strftime("%a %I:%M %p PKT")})
            if len(slots) >= 3:
                return slots
    return slots
