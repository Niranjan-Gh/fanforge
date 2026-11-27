"""Hardware-independent safety and configuration helpers."""
import json
import math
import os
import tempfile

MIN_PCT = 20
SAFE_TEMP = 85
MAX_SAMPLE_AGE = 3
ARMED_KEY = "__enabled_headers__"
LINK_KEY = "__linked__"


def valid_temperature(value):
    return value if value is not None and math.isfinite(value) and 0 < value <= 150 else None


def fresh_temperature(value, started, now, last_poll):
    if now - started > MAX_SAMPLE_AGE or (
        last_poll is not None and now - last_poll > MAX_SAMPLE_AGE
    ):
        return None
    return valid_temperature(value)


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    validated = {}
    for key, value in data.items():
        if key == LINK_KEY:
            if type(value) is not bool:
                raise ValueError("Invalid linked setting")
        elif key == ARMED_KEY:
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.startswith("/") for item in value
            ):
                raise ValueError("Invalid enabled headers")
            value = sorted(set(value))
        else:
            if not isinstance(value, list) or not 2 <= len(value) <= 81:
                raise ValueError(f"Invalid curve for {key}")
            previous = 19
            for point in value:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError(f"Invalid point for {key}")
                t, pct = point
                if not (type(t) is int and previous < t <= 100 and
                        type(pct) is int and MIN_PCT <= pct <= 100):
                    raise ValueError(f"Invalid point for {key}")
                previous = t
        validated[key] = value
    return validated


def write_config(path, data):
    data = validate_config(data)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".fanforge-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Header:
    """Tracks ownership conservatively when a driver call may partially succeed."""

    def __init__(self, write, release):
        self.write = write
        self.release = release
        self.owned = False
        self.current = None
        self.fault = ""

    def apply(self, target, emergency=False):
        if self.fault:
            self.restore()
            return
        if self.current is not None and not emergency:
            target = max(target, self.current - 2)
        target = math.ceil(target)
        if target == self.current and not emergency:
            return
        self.owned = True
        try:
            self.write(target)
        except Exception as exc:
            self.fault = f"Write failed: {exc}"
            self.restore()
        else:
            self.current = target

    def restore(self):
        self.current = None
        if self.owned:
            try:
                self.release()
            except Exception:
                return False
            self.owned = False
        return True
