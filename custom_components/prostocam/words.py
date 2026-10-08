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
        "webrtc_failed": "WebRTC is not available for this camera now; it plays over HLS",
        "exports": "Exported clips",
        "export_pending": "The clip is still being prepared",
        "export_failed": "ProstoCAM could not prepare this clip",
        "ask_one": "Found 1 event at {time}: {camera}, {kind}.",
        "ask_many": "Found {count} {noun}, the last one at {time}: {camera}, {kind}.",
        "ask_more": "Found at least {count} {noun}, the last one at {time}: {camera}, {kind}.",
        "ask_nothing": "Nothing found.",
        "ask_unclear": "I did not understand part of the question: {words}.",
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
        "webrtc_failed": "WebRTC для цієї камери зараз недоступний; відео піде через HLS",
        "exports": "Вивантажені кліпи",
        "export_pending": "Кліп ще готується",
        "export_failed": "ProstoCAM не зміг підготувати цей кліп",
        "ask_one": "Знайшов 1 подію о {time} — {camera}, {kind}.",
        "ask_many": "Знайшов {count} {noun}, остання о {time} — {camera}, {kind}.",
        "ask_more": "Знайшов щонайменше {count} {noun}, остання о {time} — {camera}, {kind}.",
        "ask_nothing": "Нічого не знайшов.",
        "ask_unclear": "Не зрозумів частину питання: {words}.",
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
        "webrtc_failed": "WebRTC для этой камеры сейчас недоступен; видео пойдёт через HLS",
        "exports": "Выгруженные клипы",
        "export_pending": "Клип ещё готовится",
        "export_failed": "ProstoCAM не смог подготовить этот клип",
        "ask_one": "Нашёл 1 событие в {time} — {camera}, {kind}.",
        "ask_many": "Нашёл {count} {noun}, последнее в {time} — {camera}, {kind}.",
        "ask_more": "Нашёл не меньше {count} {noun}, последнее в {time} — {camera}, {kind}.",
        "ask_nothing": "Ничего не нашёл.",
        "ask_unclear": "Не понял часть вопроса: {words}.",
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
        "webrtc_failed": "WebRTC за тази камера сега не е достъпен; видеото ще върви през HLS",
        "exports": "Изтеглени клипове",
        "export_pending": "Клипът още се подготвя",
        "export_failed": "ProstoCAM не успя да подготви този клип",
        "ask_one": "Намерих 1 събитие в {time} — {camera}, {kind}.",
        "ask_many": "Намерих {count} {noun}, последното в {time} — {camera}, {kind}.",
        "ask_more": "Намерих поне {count} {noun}, последното в {time} — {camera}, {kind}.",
        "ask_nothing": "Нищо не намерих.",
        "ask_unclear": "Не разбрах част от въпроса: {words}.",
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


# Forms of "event" after a number: one, few (2–4), many (Slavic); one, other elsewhere.
EVENT_NOUNS: dict[str, tuple[str, ...]] = {
    "en": ("event", "events"),
    "uk": ("подію", "події", "подій"),
    "ru": ("событие", "события", "событий"),
    "bg": ("събитие", "събития"),
}


def events_noun(language: str | None, count: int) -> str:
    """«3 події», «5 подій», «21 подію»: the noun after a number of events."""
    forms = EVENT_NOUNS[language_of(language)]
    if len(forms) == 2:
        return forms[0] if count == 1 else forms[1]
    if count % 10 == 1 and count % 100 != 11:
        return forms[0]
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return forms[1]
    return forms[2]
