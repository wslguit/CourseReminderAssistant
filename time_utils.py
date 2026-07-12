from datetime import datetime


SUPPORTED_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def parse_datetime(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in SUPPORTED_DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_datetime(value, output_format="%m-%d %H:%M", fallback="暂无"):
    parsed = parse_datetime(value)
    return parsed.strftime(output_format) if parsed else fallback
