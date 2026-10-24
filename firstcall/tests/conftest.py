from datetime import datetime, timedelta, timezone

import pytest

from fc.models import RawItem

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def raw(title, link, days=0, outlet="Test Wire", source="google_news"):
    return RawItem(source=source, query="test", title=f"{title} - {outlet}", link=link, outlet=outlet,
                   published=(T0 + timedelta(days=days)).isoformat(), fetched_at=T0 + timedelta(days=days, hours=1))


@pytest.fixture
def items():
    return [
        raw("CalPERS hires Evercore to run secondary sale of private equity stakes", "https://ex.com/a", 0),
        raw("CalPERS hires Evercore to run a secondary sale of PE stakes", "https://ex2.com/a?utm_source=x", 0),   # syndicated copy
        raw("CalPERS private equity allocation climbs to 14% against a 10% target", "https://ex.com/b", 2),
        raw("PSERS board to review private equity allocation", "https://ex.com/c", 3),
        raw("Pension Bros raises $20 million to modernise retirement plans", "https://ex.com/d", 3),
        raw("NYSTRS names new chief investment officer", "https://ex.com/e", 4),
        raw("Ohio SERS returns trail public markets", "https://ex.com/f", 5),
    ]
