"""Cron expression parser."""


def parse_cron_field(field, lo, hi):
    """Expand one cron field to a frozenset of matching integers.

    Supports: * 5 1-5 */15 0-30/5 1,3,5
    """
    result = set()
    for part in field.split(','):
        if '/' in part:
            range_part, step = part.split('/', 1)
            step = int(step)
            start, end = (lo, hi) if range_part == '*' else map(int, range_part.split('-'))
            result.update(range(start, end + 1, step))
        elif part == '*':
            result.update(range(lo, hi + 1))
        elif '-' in part:
            start, end = map(int, part.split('-'))
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    return frozenset(result)


def parse_cron(expr):
    """Parse a 5-field cron expression into a tuple of five frozensets.

    Fields: minute hour day-of-month month day-of-week
    Day-of-week: 0 and 7 both mean Sunday.
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f'cron expression must have 5 fields, got {len(fields)}: {expr!r}')
    minute, hour, dom, month, dow = fields
    return (
        parse_cron_field(minute, 0, 59),
        parse_cron_field(hour,   0, 23),
        parse_cron_field(dom,    1, 31),
        parse_cron_field(month,  1, 12),
        parse_cron_field(dow,    0,  7),
    )


def cron_matches(parsed, dt):
    """Return True if datetime dt matches the parsed cron tuple."""
    minute_set, hour_set, dom_set, month_set, dow_set = parsed
    # Python weekday: Mon=0..Sun=6 → cron: Sun=0, Mon=1..Sat=6
    cron_dow = (dt.weekday() + 1) % 7
    effective_dow = set(dow_set)
    if 7 in effective_dow:   # 7 is an alias for Sunday (0)
        effective_dow.add(0)
    return (
        dt.minute in minute_set
        and dt.hour in hour_set
        and dt.day in dom_set
        and dt.month in month_set
        and cron_dow in effective_dow
    )
