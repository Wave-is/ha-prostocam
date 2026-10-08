"""Words of ProstoCAM that Home Assistant has no translations for: media folders, alarm labels."""

from __future__ import annotations

from typing import Any

# Folder and event names of the media browser and the alarm labels (no HA translations exist for them).
WORDS: dict[str, dict[str, str]] = {
    "en": {
        "events": "Events",
        "archive": "Archive",
        "today": "Today",
        "motion": "Motion",
        "person": "Person",
        "vehicle": "Vehicle",
        "animal": "Animal",
        "other": "Event",
        "test": "Test alarm",
        "no_clip": "There is no clip of this event",
        "no_record": "There is no record for this hour",
    },
    "uk": {
        "events": "Події",
        "archive": "Архів",
        "today": "Сьогодні",
        "motion": "Рух",
        "person": "Людина",
        "vehicle": "Транспорт",
        "animal": "Тварина",
        "other": "Подія",
        "test": "Тестова тривога",
        "no_clip": "Кліпу цієї події немає",
        "no_record": "Запису за цю годину немає",
    },
    "ru": {
        "events": "События",
        "archive": "Архив",
        "today": "Сегодня",
        "motion": "Движение",
        "person": "Человек",
        "vehicle": "Транспорт",
        "animal": "Животное",
        "other": "Событие",
        "test": "Тестовая тревога",
        "no_clip": "Клипа этого события нет",
        "no_record": "Записи за этот час нет",
    },
    "bg": {
        "events": "Събития",
        "archive": "Архив",
        "today": "Днес",
        "motion": "Движение",
        "person": "Човек",
        "vehicle": "Превозно средство",
        "animal": "Животно",
        "other": "Събитие",
        "test": "Тестова аларма",
        "no_clip": "Няма клип на това събитие",
        "no_record": "Няма запис за този час",
    },
}


def language_of(language: str | None) -> str:
    """The language of Home Assistant as a two-letter tag the server knows (en if unknown)."""
    tag = (language or "en").split("-")[0].lower()
    return tag if tag in WORDS else "en"


def word(language: str | None, key: str) -> str:
    """One word in the language of Home Assistant (English, then the key itself, if missing)."""
    return WORDS[language_of(language)].get(key, WORDS["en"].get(key, key))


def confidence_percent(confidence: Any) -> int | None:
    """The confidence as a whole percent; None when nobody measured it.

    A camera's own detection (an ISUP/ONVIF "person") carries no score: the server
    sends 0 then, and "Person · 0 %" would be a lie about a measurement.
    """
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    percent = round(float(confidence) * 100)
    return percent if 0 < percent <= 100 else None


def alarm_label(language: str | None, kind: Any, confidence: Any = None) -> str:
    """«Людина · 87 %»: the kind of the alarm and, when measured, the confidence."""
    key = kind if isinstance(kind, str) else "other"
    text = word(language, key) if key in WORDS["en"] else key
    percent = confidence_percent(confidence)
    return f"{text} · {percent} %" if percent is not None else text
