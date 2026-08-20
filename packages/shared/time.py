from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Normalise les valeurs SQLite naïves; PostgreSQL renvoie déjà TIMESTAMPTZ."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
