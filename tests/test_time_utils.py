from time_utils import format_datetime, parse_datetime


def test_common_time_parser_supports_three_formats():
    assert parse_datetime("2026-07-12").strftime("%Y-%m-%d %H:%M:%S") == "2026-07-12 00:00:00"
    assert parse_datetime("2026-07-12 08:30").strftime("%Y-%m-%d %H:%M:%S") == "2026-07-12 08:30:00"
    assert parse_datetime("2026-07-12 08:30:45").strftime("%Y-%m-%d %H:%M:%S") == "2026-07-12 08:30:45"


def test_datetime_formatter_returns_fallback_for_invalid_value():
    assert format_datetime("not-a-date") == "暂无"
    assert format_datetime(None) == "暂无"
