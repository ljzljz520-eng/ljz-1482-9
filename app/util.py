import hashlib
import json
import secrets
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix="id"):
    return f"{prefix}_{secrets.token_hex(10)}"


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj):
    return sha256_text(canonical(obj))


def parse_json_array(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def is_expired(valid_until, at=None):
    if not valid_until:
        return True
    moment = at or datetime.now(timezone.utc)
    try:
        value = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value < moment
    except Exception:
        return True
