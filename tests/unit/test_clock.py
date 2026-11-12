from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.clock import FixedClock, SystemClock

TZ = ZoneInfo("America/Sao_Paulo")


def test_fixed_clock_converts_to_clinic_local_time() -> None:
    clock = FixedClock(datetime(2026, 11, 12, 12, 0, 30, 999, tzinfo=TZ))
    clock.set(datetime(2026, 11, 12, 15, 0, 30, 999, tzinfo=UTC))
    assert clock.local_now() == datetime(2026, 11, 12, 12, 0, 30)
    assert clock.today().isoformat() == "2026-11-12"


def test_fixed_clock_advance() -> None:
    clock = FixedClock(datetime(2026, 11, 12, 23, 30, tzinfo=TZ))
    clock.advance(timedelta(hours=1))
    assert clock.today().isoformat() == "2026-11-13"


def test_fixed_clock_requires_timezone() -> None:
    with pytest.raises(ValueError, match="fuso"):
        FixedClock(datetime(2026, 11, 12))
    clock = FixedClock(datetime(2026, 11, 12, tzinfo=TZ))
    with pytest.raises(ValueError, match="fuso"):
        clock.set(datetime(2026, 11, 12))


def test_system_clock_is_aware() -> None:
    now = SystemClock(TZ).now()
    assert now.tzinfo is TZ
