from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from croniter import croniter


def compute_next_run(cron_expr: str, tz_name: str = "UTC") -> datetime:
    """
    Given a cron expression like "0 */6 * * *" and an IANA timezone name
    like "Asia/Karachi", returns the next UTC datetime this should fire.
    """
    # Defensive check for empty or invalid timezone inputs from forms
    if not tz_name or tz_name.strip() == "":
        tz_name = "UTC"

    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        # Fallback to UTC gracefully if the database or user provides a junk string
        print(f"Warning: Timezone '{tz_name}' not found. Falling back to UTC.")
        tz = ZoneInfo("UTC")

    now_local = datetime.now(tz)
    itr = croniter(cron_expr, now_local)
    next_local = itr.get_next(datetime)
    
    return next_local.astimezone(ZoneInfo("UTC"))