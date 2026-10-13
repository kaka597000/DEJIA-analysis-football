from datetime import datetime, timezone


def utc(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone is required")
    return dt.astimezone(timezone.utc)


def stamp():
    return datetime.now(timezone.utc).isoformat()
