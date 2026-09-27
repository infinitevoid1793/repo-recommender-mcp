"""Parsing for the timestamps GitHub returns."""

from datetime import datetime


def parse_time(value: str) -> datetime:
    """GitHub returns RFC 3339 with a 'Z' suffix, which fromisoformat handles
    natively on Python 3.11+."""
    return datetime.fromisoformat(value)
