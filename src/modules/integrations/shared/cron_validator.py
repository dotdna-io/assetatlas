# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Cron Expression Validation and Utilities

Provides validation, parsing, and next run calculation for cron expressions.
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from croniter import croniter
import pytz


# Preset cron expressions
CRON_PRESETS = {
    '@hourly': '0 * * * *',
    '@daily': '0 0 * * *',
    '@weekly': '0 0 * * 0',
    '@monthly': '0 0 1 * *',
    '@yearly': '0 0 1 1 *',
    '@annually': '0 0 1 1 *'
}

# Cron field descriptions
CRON_DESCRIPTIONS = {
    '*/1': 'every',
    '*/2': 'every 2',
    '*/5': 'every 5',
    '*/10': 'every 10',
    '*/15': 'every 15',
    '*/30': 'every 30',
    '0': 'at :00',
    '*': 'every'
}


def is_valid_cron(cron_expression: str) -> bool:
    """
    Validate a cron expression.

    Args:
        cron_expression: Cron expression to validate (5-field format or preset)

    Returns:
        True if valid, False otherwise

    Examples:
        >>> is_valid_cron("0 * * * *")
        True
        >>> is_valid_cron("@hourly")
        True
        >>> is_valid_cron("invalid")
        False
    """
    if not cron_expression or not isinstance(cron_expression, str):
        return False

    # Check if it's a preset
    if cron_expression.lower() in CRON_PRESETS:
        return True

    # Validate using croniter
    try:
        croniter(cron_expression)
        return True
    except (ValueError, KeyError):
        return False


def expand_preset(cron_expression: str) -> str:
    """
    Expand a cron preset to full cron expression.

    Args:
        cron_expression: Cron expression or preset

    Returns:
        Expanded cron expression

    Examples:
        >>> expand_preset("@hourly")
        "0 * * * *"
        >>> expand_preset("0 * * * *")
        "0 * * * *"
    """
    if cron_expression.lower() in CRON_PRESETS:
        return CRON_PRESETS[cron_expression.lower()]
    return cron_expression


def calculate_next_run(cron_expression: str, timezone: str = 'UTC',
                       base_time: Optional[datetime] = None) -> Optional[datetime]:
    """
    Calculate the next run time for a cron expression.

    Args:
        cron_expression: Cron expression or preset
        timezone: Timezone for calculation (default: UTC)
        base_time: Base time for calculation (default: now)

    Returns:
        Next run time as timezone-aware datetime, or None if invalid

    Examples:
        >>> next_run = calculate_next_run("0 * * * *", "UTC")
        >>> isinstance(next_run, datetime)
        True
    """
    try:
        # Expand presets
        cron_expr = expand_preset(cron_expression)

        # Get timezone
        tz = pytz.timezone(timezone)

        # Get base time
        if base_time is None:
            base_time = datetime.now(tz)
        elif base_time.tzinfo is None:
            base_time = tz.localize(base_time)
        else:
            base_time = base_time.astimezone(tz)

        # Calculate next run
        cron = croniter(cron_expr, base_time)
        next_run = cron.get_next(datetime)

        return next_run

    except (ValueError, KeyError, pytz.UnknownTimeZoneError):
        return None


def get_cron_description(cron_expression: str) -> str:
    """
    Get a human-readable description of a cron expression.

    Args:
        cron_expression: Cron expression or preset

    Returns:
        Human-readable description

    Examples:
        >>> get_cron_description("0 * * * *")
        "Every hour at :00"
        >>> get_cron_description("@daily")
        "Daily at midnight"
    """
    # Handle presets
    preset_descriptions = {
        '@hourly': 'Every hour at :00',
        '@daily': 'Daily at midnight',
        '@weekly': 'Weekly on Sunday at midnight',
        '@monthly': 'Monthly on the 1st at midnight',
        '@yearly': 'Yearly on January 1st at midnight',
        '@annually': 'Yearly on January 1st at midnight'
    }

    if cron_expression.lower() in preset_descriptions:
        return preset_descriptions[cron_expression.lower()]

    # Parse cron expression
    try:
        cron_expr = expand_preset(cron_expression)
        parts = cron_expr.split()

        if len(parts) != 5:
            return "Custom schedule"

        minute, hour, day, month, weekday = parts

        # Build description
        desc_parts = []

        # Frequency
        if minute == '*' and hour == '*':
            desc_parts.append("Every minute")
        elif hour == '*':
            if minute == '0':
                desc_parts.append("Every hour at :00")
            elif minute.startswith('*/'):
                interval = minute[2:]
                desc_parts.append(f"Every {interval} minutes")
            else:
                desc_parts.append(f"Every hour at :{minute}")
        elif day == '*' and month == '*' and weekday == '*':
            if minute == '0':
                if hour.startswith('*/'):
                    interval = hour[2:]
                    desc_parts.append(f"Every {interval} hours")
                else:
                    desc_parts.append(f"Daily at {hour}:00")
            else:
                desc_parts.append(f"Daily at {hour}:{minute}")
        else:
            desc_parts.append("Custom schedule")

        # Day/month/weekday specifics
        if weekday != '*' and weekday != '0-6':
            days = {
                '0': 'Sunday', '1': 'Monday', '2': 'Tuesday',
                '3': 'Wednesday', '4': 'Thursday', '5': 'Friday',
                '6': 'Saturday', '7': 'Sunday'
            }
            if weekday in days:
                desc_parts.append(f"on {days[weekday]}")

        if day != '*' and month == '*':
            desc_parts.append(f"on day {day}")

        if month != '*':
            months = {
                '1': 'January', '2': 'February', '3': 'March',
                '4': 'April', '5': 'May', '6': 'June',
                '7': 'July', '8': 'August', '9': 'September',
                '10': 'October', '11': 'November', '12': 'December'
            }
            if month in months:
                desc_parts.append(f"in {months[month]}")

        return ' '.join(desc_parts)

    except Exception:
        return "Custom schedule"


def calculate_next_runs(cron_expression: str, timezone: str = 'UTC',
                        count: int = 5, base_time: Optional[datetime] = None) -> list:
    """
    Calculate multiple next run times for a cron expression.

    Args:
        cron_expression: Cron expression or preset
        timezone: Timezone for calculation (default: UTC)
        count: Number of next runs to calculate (default: 5)
        base_time: Base time for calculation (default: now)

    Returns:
        List of timezone-aware datetime objects for next runs

    Examples:
        >>> next_runs = calculate_next_runs("0 * * * *", "UTC", 3)
        >>> len(next_runs)
        3
    """
    try:
        # Expand presets
        cron_expr = expand_preset(cron_expression)

        # Get timezone
        tz = pytz.timezone(timezone)

        # Get base time
        if base_time is None:
            base_time = datetime.now(tz)
        elif base_time.tzinfo is None:
            base_time = tz.localize(base_time)
        else:
            base_time = base_time.astimezone(tz)

        # Calculate next runs
        cron = croniter(cron_expr, base_time)
        next_runs = []
        for _ in range(count):
            next_runs.append(cron.get_next(datetime))

        return next_runs

    except (ValueError, KeyError, pytz.UnknownTimeZoneError):
        return []


def validate_cron_with_details(cron_expression: str, timezone: str = 'UTC') -> Dict[str, Any]:
    """
    Validate cron expression and return detailed information.

    Args:
        cron_expression: Cron expression or preset
        timezone: Timezone for next run calculation

    Returns:
        Dictionary with validation results and details

    Examples:
        >>> result = validate_cron_with_details("0 * * * *")
        >>> result['valid']
        True
        >>> 'next_run' in result
        True
    """
    # Expand presets
    expanded = expand_preset(cron_expression)

    # Validate
    valid = is_valid_cron(cron_expression)

    if not valid:
        return {
            'valid': False,
            'error': 'Invalid cron expression',
            'message': 'Please provide a valid cron expression (e.g., "0 * * * *") or preset (e.g., "@hourly")'
        }

    # Calculate next runs
    next_run = calculate_next_run(cron_expression, timezone)

    # Get next few runs
    next_runs = []
    if next_run:
        try:
            cron = croniter(expanded, next_run)
            for _ in range(5):
                next_runs.append(cron.get_next(datetime))
        except Exception:
            pass

    # Get description
    description = get_cron_description(cron_expression)

    return {
        'valid': True,
        'original': cron_expression,
        'expanded': expanded,
        'description': description,
        'next_run': next_run.isoformat() if next_run else None,
        'next_5_runs': [dt.isoformat() for dt in next_runs],
        'timezone': timezone
    }


def validate_timezone(timezone: str) -> bool:
    """
    Validate a timezone string.

    Args:
        timezone: Timezone identifier (e.g., "America/New_York")

    Returns:
        True if valid, False otherwise

    Examples:
        >>> validate_timezone("UTC")
        True
        >>> validate_timezone("America/New_York")
        True
        >>> validate_timezone("Invalid/Timezone")
        False
    """
    try:
        pytz.timezone(timezone)
        return True
    except pytz.UnknownTimeZoneError:
        return False


def get_common_timezones() -> list:
    """
    Get list of common timezones.

    Returns:
        List of common timezone identifiers
    """
    return [
        'UTC',
        'America/New_York',
        'America/Chicago',
        'America/Denver',
        'America/Los_Angeles',
        'Europe/London',
        'Europe/Paris',
        'Europe/Berlin',
        'Asia/Tokyo',
        'Asia/Shanghai',
        'Asia/Dubai',
        'Australia/Sydney',
        'Pacific/Auckland'
    ]


# Example presets for UI
SCHEDULE_PRESETS = [
    {'value': '@hourly', 'label': 'Every hour', 'cron': '0 * * * *'},
    {'value': '0 */2 * * *', 'label': 'Every 2 hours', 'cron': '0 */2 * * *'},
    {'value': '0 */4 * * *', 'label': 'Every 4 hours', 'cron': '0 */4 * * *'},
    {'value': '0 */6 * * *', 'label': 'Every 6 hours', 'cron': '0 */6 * * *'},
    {'value': '0 */12 * * *', 'label': 'Every 12 hours', 'cron': '0 */12 * * *'},
    {'value': '@daily', 'label': 'Daily at midnight', 'cron': '0 0 * * *'},
    {'value': '0 2 * * *', 'label': 'Daily at 2 AM', 'cron': '0 2 * * *'},
    {'value': '0 6 * * *', 'label': 'Daily at 6 AM', 'cron': '0 6 * * *'},
    {'value': '@weekly', 'label': 'Weekly (Sunday)', 'cron': '0 0 * * 0'},
    {'value': '0 0 * * 1', 'label': 'Weekly (Monday)', 'cron': '0 0 * * 1'},
    {'value': '@monthly', 'label': 'Monthly (1st)', 'cron': '0 0 1 * *'},
]
