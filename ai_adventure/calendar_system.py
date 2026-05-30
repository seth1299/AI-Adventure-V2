from __future__ import annotations

from typing import Any


MINUTES_PER_DAY = 24 * 60
DEFAULT_START_ELAPSED_MINUTES = 8 * 60

DEFAULT_CALENDAR_SETTINGS: dict[str, Any] = {
    "days_per_week": 7,
    "weeks_per_month": 4,
    "months_per_year": 12,
    "seasons_per_year": 4,
    "day_names": [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ],
    "month_names": [
        "Month 1",
        "Month 2",
        "Month 3",
        "Month 4",
        "Month 5",
        "Month 6",
        "Month 7",
        "Month 8",
        "Month 9",
        "Month 10",
        "Month 11",
        "Month 12",
    ],
    "seasons": [
        {"name": "Spring", "weather_hint": "spring"},
        {"name": "Summer", "weather_hint": "summer"},
        {"name": "Autumn", "weather_hint": "autumn"},
        {"name": "Winter", "weather_hint": "winter"},
    ],
    "time_display": "narrative",
}


def normalize_calendar_settings(raw_settings: Any) -> dict[str, Any]:
    """Returns clean calendar settings with sensible defaults."""

    if not isinstance(raw_settings, dict):
        raw_settings = {}

    days_per_week = _bounded_int(raw_settings.get("days_per_week"), 7, 1, 14)
    weeks_per_month = _bounded_int(raw_settings.get("weeks_per_month"), 4, 1, 12)
    months_per_year = _bounded_int(raw_settings.get("months_per_year"), 12, 1, 24)
    seasons_per_year = _bounded_int(raw_settings.get("seasons_per_year"), 4, 1, 12)

    day_names = _normalize_names(
        raw_settings.get("day_names"),
        days_per_week,
        DEFAULT_CALENDAR_SETTINGS["day_names"],
        "Day",
    )
    month_names = _normalize_names(
        raw_settings.get("month_names"),
        months_per_year,
        DEFAULT_CALENDAR_SETTINGS["month_names"],
        "Month",
    )
    seasons = _normalize_seasons(raw_settings.get("seasons"), seasons_per_year)
    time_display = str(raw_settings.get("time_display", "narrative")).strip().lower()

    if time_display not in {"narrative", "12_hour", "24_hour"}:
        time_display = "narrative"

    return {
        "days_per_week": days_per_week,
        "weeks_per_month": weeks_per_month,
        "months_per_year": months_per_year,
        "seasons_per_year": seasons_per_year,
        "day_names": day_names,
        "month_names": month_names,
        "seasons": seasons,
        "time_display": time_display,
    }


def build_calendar_snapshot(
    elapsed_minutes: int,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds derived calendar fields for one elapsed-minute value."""

    clean_settings = normalize_calendar_settings(settings)
    clean_elapsed = max(0, _safe_int(elapsed_minutes, DEFAULT_START_ELAPSED_MINUTES))
    day_index = clean_elapsed // MINUTES_PER_DAY
    time_of_day_minutes = clean_elapsed % MINUTES_PER_DAY
    days_per_week = int(clean_settings["days_per_week"])
    weeks_per_month = int(clean_settings["weeks_per_month"])
    months_per_year = int(clean_settings["months_per_year"])
    days_per_month = days_per_week * weeks_per_month
    days_per_year = days_per_month * months_per_year
    year_index = day_index // days_per_year if days_per_year else 0
    day_of_year = day_index % days_per_year if days_per_year else 0
    month_index = day_of_year // days_per_month if days_per_month else 0
    day_of_month_index = day_of_year % days_per_month if days_per_month else 0
    day_of_week_index = day_index % days_per_week if days_per_week else 0
    week_of_month = (day_of_month_index // days_per_week) + 1
    season_index = min(
        int(month_index * int(clean_settings["seasons_per_year"]) / months_per_year),
        int(clean_settings["seasons_per_year"]) - 1,
    )
    season = clean_settings["seasons"][season_index]
    day_name = clean_settings["day_names"][day_of_week_index]
    month_name = clean_settings["month_names"][month_index]
    time_label = format_time_of_day(
        time_of_day_minutes,
        str(clean_settings["time_display"]),
    )
    date_label = f"{day_name}, {month_name} {day_of_month_index + 1}, Year {year_index + 1}"

    return {
        "elapsed_minutes": clean_elapsed,
        "absolute_day": day_index + 1,
        "year": year_index + 1,
        "month_index": month_index,
        "month_number": month_index + 1,
        "month_name": month_name,
        "week_of_month": week_of_month,
        "day_of_month": day_of_month_index + 1,
        "day_of_week_index": day_of_week_index,
        "day_of_week_name": day_name,
        "season_index": season_index,
        "season_name": season["name"],
        "season_hint": season["weather_hint"],
        "time_of_day_minutes": time_of_day_minutes,
        "time_label": time_label,
        "date_label": date_label,
        "display_label": f"{date_label}, {time_label}",
        "days_per_month": days_per_month,
        "days_per_year": days_per_year,
        "days_per_week": days_per_week,
        "weeks_per_month": weeks_per_month,
        "months_per_year": months_per_year,
        "seasons_per_year": int(clean_settings["seasons_per_year"]),
        "time_display": str(clean_settings["time_display"]),
        "settings": clean_settings,
    }


def resolve_starting_elapsed_minutes(
    raw_starting_calendar: Any,
    settings: dict[str, Any] | None = None,
    *,
    default_elapsed_minutes: int = DEFAULT_START_ELAPSED_MINUTES,
) -> int:
    """
    Resolves AI-provided starting calendar hints to elapsed minutes.

    The AI may provide an exact elapsed_minutes value, or softer fields such as
    season_name, season_hint, month_name, month_number, day_of_month, and
    time_of_day_minutes. This keeps opening prose and the displayed calendar in
    sync without requiring the model to perform calendar math perfectly.
    """

    if not isinstance(raw_starting_calendar, dict):
        return default_elapsed_minutes

    explicit_elapsed = raw_starting_calendar.get("elapsed_minutes")

    if explicit_elapsed is not None:
        return max(0, _safe_int(explicit_elapsed, default_elapsed_minutes))

    clean_settings = normalize_calendar_settings(settings)
    days_per_month = int(clean_settings["days_per_week"]) * int(clean_settings["weeks_per_month"])
    months_per_year = int(clean_settings["months_per_year"])
    month_index = _resolve_month_index(raw_starting_calendar, clean_settings)

    if month_index is None:
        month_index = _resolve_month_index_for_season(raw_starting_calendar, clean_settings)

    if month_index is None:
        return default_elapsed_minutes

    day_of_month = _bounded_int(
        raw_starting_calendar.get("day_of_month"),
        1,
        1,
        days_per_month,
    )
    year = _bounded_int(raw_starting_calendar.get("year"), 1, 1, 9999)
    time_of_day_minutes = _bounded_int(
        raw_starting_calendar.get("time_of_day_minutes"),
        DEFAULT_START_ELAPSED_MINUTES,
        0,
        MINUTES_PER_DAY - 1,
    )
    absolute_day_index = (
        (year - 1) * months_per_year * days_per_month
        + month_index * days_per_month
        + day_of_month
        - 1
    )

    return absolute_day_index * MINUTES_PER_DAY + time_of_day_minutes


def _resolve_month_index(
    raw_starting_calendar: dict[str, Any],
    settings: dict[str, Any],
) -> int | None:
    """Resolves month_name/month_number hints to a zero-based month index."""

    months_per_year = int(settings["months_per_year"])
    month_number = raw_starting_calendar.get("month_number")

    if month_number is not None:
        clean_month_number = _bounded_int(month_number, 1, 1, months_per_year)
        return clean_month_number - 1

    month_name = str(raw_starting_calendar.get("month_name", "")).strip().casefold()

    if not month_name:
        return None

    for index, name in enumerate(settings["month_names"]):
        if str(name).strip().casefold() == month_name:
            return index

    return None


def _resolve_month_index_for_season(
    raw_starting_calendar: dict[str, Any],
    settings: dict[str, Any],
) -> int | None:
    """Resolves season_name/season_hint to the first month in that season."""

    season_name = str(raw_starting_calendar.get("season_name", "")).strip().casefold()
    season_hint = str(raw_starting_calendar.get("season_hint", "")).strip().casefold()

    if not season_name and not season_hint:
        return None

    target_season_index: int | None = None

    for index, season in enumerate(settings["seasons"]):
        if season_name and str(season["name"]).strip().casefold() == season_name:
            target_season_index = index
            break

        if season_hint and str(season["weather_hint"]).strip().casefold() == season_hint:
            target_season_index = index
            break

    if target_season_index is None:
        return None

    months_per_year = int(settings["months_per_year"])
    seasons_per_year = int(settings["seasons_per_year"])

    for month_index in range(months_per_year):
        season_index = min(
            int(month_index * seasons_per_year / months_per_year),
            seasons_per_year - 1,
        )

        if season_index == target_season_index:
            return month_index

    return None


def build_month_grid(
    calendar_snapshot: dict[str, Any],
    month_offset: int = 0,
) -> dict[str, Any]:
    """Builds a fixed custom-calendar month grid for display."""

    settings = normalize_calendar_settings(calendar_snapshot.get("settings", {}))
    days_per_week = int(settings["days_per_week"])
    weeks_per_month = int(settings["weeks_per_month"])
    months_per_year = int(settings["months_per_year"])
    days_per_month = days_per_week * weeks_per_month
    current_absolute_month = (
        (int(calendar_snapshot.get("year", 1)) - 1) * months_per_year
        + int(calendar_snapshot.get("month_index", 0))
    )
    target_absolute_month = max(0, current_absolute_month + int(month_offset))
    target_year = (target_absolute_month // months_per_year) + 1
    target_month_index = target_absolute_month % months_per_year
    target_month_name = settings["month_names"][target_month_index]
    target_start_day = target_absolute_month * days_per_month
    current_absolute_day = int(calendar_snapshot.get("absolute_day", 1))
    rows: list[list[dict[str, Any]]] = []

    for week_index in range(weeks_per_month):
        row: list[dict[str, Any]] = []

        for day_index in range(days_per_week):
            day_of_month = week_index * days_per_week + day_index + 1
            absolute_day = target_start_day + day_of_month
            row.append(
                {
                    "day_of_month": day_of_month,
                    "absolute_day": absolute_day,
                    "day_name": settings["day_names"][day_index],
                    "is_current_day": absolute_day == current_absolute_day,
                }
            )

        rows.append(row)

    return {
        "month_offset": target_absolute_month - current_absolute_month,
        "year": target_year,
        "month_index": target_month_index,
        "month_number": target_month_index + 1,
        "month_name": target_month_name,
        "day_names": settings["day_names"],
        "weeks_per_month": weeks_per_month,
        "days_per_week": days_per_week,
        "rows": rows,
    }


def format_time_of_day(minutes: int, display_format: str) -> str:
    """Formats minutes after midnight according to the selected display style."""

    clean_minutes = max(0, _safe_int(minutes, 0)) % MINUTES_PER_DAY
    hour = clean_minutes // 60
    minute = clean_minutes % 60

    if display_format == "24_hour":
        return f"{hour:02d}:{minute:02d}"

    if display_format == "12_hour":
        suffix = "A.M." if hour < 12 else "P.M."
        display_hour = hour % 12 or 12
        return f"{display_hour}:{minute:02d} {suffix}"

    if 5 <= hour < 8:
        return "Dawn"
    if 8 <= hour < 12:
        return "Morning"
    if 12 <= hour < 17:
        return "Afternoon"
    if 17 <= hour < 21:
        return "Evening"
    return "Night"


def month_start_day_index(year: int, month_index: int, settings: dict[str, Any]) -> int:
    """Returns zero-based absolute day index for the start of a month."""

    clean_settings = normalize_calendar_settings(settings)
    days_per_month = int(clean_settings["days_per_week"]) * int(clean_settings["weeks_per_month"])
    months_per_year = int(clean_settings["months_per_year"])
    clean_year = max(1, year)
    clean_month = max(0, min(month_index, months_per_year - 1))
    return ((clean_year - 1) * months_per_year + clean_month) * days_per_month


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Converts a value to a bounded integer."""

    try:
        clean_value = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, clean_value))


def _safe_int(value: Any, default: int) -> int:
    """Converts a value to int, falling back safely."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_names(
    raw_names: Any,
    expected_count: int,
    defaults: list[str],
    fallback_prefix: str,
) -> list[str]:
    """Normalizes a fixed-length list of names."""

    if isinstance(raw_names, str):
        names = [name.strip() for name in raw_names.split(",") if name.strip()]
    elif isinstance(raw_names, list):
        names = [str(name).strip() for name in raw_names if str(name).strip()]
    else:
        names = []

    for index in range(expected_count):
        if index < len(names):
            continue

        if index < len(defaults):
            names.append(str(defaults[index]))
        else:
            names.append(f"{fallback_prefix} {index + 1}")

    return names[:expected_count]


def _normalize_seasons(raw_seasons: Any, expected_count: int) -> list[dict[str, str]]:
    """Normalizes a fixed-length list of season dictionaries."""

    defaults = DEFAULT_CALENDAR_SETTINGS["seasons"]
    seasons: list[dict[str, str]] = []

    if isinstance(raw_seasons, list):
        for raw_season in raw_seasons:
            if not isinstance(raw_season, dict):
                continue

            name = str(raw_season.get("name", "")).strip()
            weather_hint = str(raw_season.get("weather_hint", "")).strip()

            if name:
                seasons.append(
                    {
                        "name": name,
                        "weather_hint": weather_hint or name.casefold(),
                    }
                )

    for index in range(expected_count):
        if index < len(seasons):
            continue

        if index < len(defaults):
            seasons.append(dict(defaults[index]))
        else:
            seasons.append(
                {
                    "name": f"Season {index + 1}",
                    "weather_hint": "temperate",
                }
            )

    return seasons[:expected_count]
