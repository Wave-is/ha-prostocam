# ProstoCAM for Home Assistant

[![Validate](https://github.com/Wave-is/ha-prostocam/actions/workflows/validate.yml/badge.svg)](https://github.com/Wave-is/ha-prostocam/actions/workflows/validate.yml)
[![Tests](https://github.com/Wave-is/ha-prostocam/actions/workflows/tests.yml/badge.svg)](https://github.com/Wave-is/ha-prostocam/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)

[Українською](#українською) · [English](#english)

---

## Українською

Інтеграція передає в [ProstoCAM](https://prosto.cam) події охоронних датчиків вашого Home Assistant: двері, вікна, рух,
протікання, дим, газ, стан охоронної панелі. ProstoCAM прив'язує датчик до камер: відчинилися двері — у боті кадр з камери
біля дверей. Працюють будь-які датчики, які бачить Home Assistant (Tuya, Zigbee, Z-Wave, ESPHome, Sonoff, Ajax через SIA…),
і ваші власні автоматизації постановки на охорону.

Нічого не треба писати в `configuration.yaml`, відкривати порти чи давати нам доступ до Home Assistant: інтеграція сама
надсилає події на сервер ProstoCAM.

### Встановлення через HACS

Поки інтеграції немає в стандартному каталозі HACS, додайте репозиторій вручну:

1. HACS → меню ⋮ (угорі праворуч) → **Custom repositories**.
2. Repository: `https://github.com/Wave-is/ha-prostocam`, Type: **Integration** → **Add**.
3. Знайдіть у HACS **ProstoCAM** → **Download** → перезапустіть Home Assistant
   (Налаштування → Система → ⋮ → Перезапустити).

Без HACS: скопіюйте теку `custom_components/prostocam` у `config/custom_components/` і перезапустіть Home Assistant.

### Підключення

1. У веб-кабінеті ProstoCAM: **Розумний дім → Home Assistant → Підключити**. З'явиться код із 8 знаків (діє 10 хвилин).
2. У Home Assistant: **Налаштування → Пристрої та служби → Додати інтеграцію → ProstoCAM**, введіть код.
3. Через кілька секунд датчики з'являться в кабінеті ProstoCAM — **вимкненими**. Увімкніть потрібні й прив'яжіть до камер.

Якщо кабінет скаже, що зв'язок втрачено (наприклад, ви натиснули «Перепідключити»), Home Assistant попросить новий код —
датчики і прив'язки камер залишаться.

**Параметри** інтеграції (Налаштування → Пристрої та служби → ProstoCAM → Налаштувати): які типи сутностей і які класи
бінарних датчиків показувати в кабінеті. За замовчуванням — охоронні бінарні датчики, охоронні панелі й сирени.

### Що надсилається, а що — ні

Надсилається:

- **перелік** сутностей обраних типів: `entity_id`, назва, клас датчика, зона (area), виробник і модель пристрою,
  інтеграція-джерело;
- **зміни стану** лише тих датчиків, які ви **увімкнули в кабінеті**: було → стало і час зміни;
- **режим охоронних панелей** (під охороною / знято / тривога), хто його змінив (ім'я з панелі, до 16 знаків) і режим при старті;
- **пульс** раз на хвилину: версії Home Assistant та інтеграції.

Не надсилається: інші сутності й атрибути, історія, камери і зображення Home Assistant, координати, користувачі, паролі
й токени Home Assistant. ProstoCAM не може нічого вмикати чи змінювати у вашому Home Assistant — зв'язок лише в один бік.

Токен підключення зберігається в Home Assistant і не потрапляє в діагностику.

---

## English

The integration sends events of the security sensors of your Home Assistant to [ProstoCAM](https://prosto.cam): doors,
windows, motion, leaks, smoke, gas and the state of the alarm panel. ProstoCAM links a sensor to cameras: the door opens —
the bot sends a snapshot from the camera next to it. Any sensor Home Assistant sees works (Tuya, Zigbee, Z-Wave, ESPHome,
Sonoff, Ajax via SIA…), as well as your own arming automations.

No `configuration.yaml` editing, no open ports, no access to your Home Assistant for us: the integration pushes the events to
the ProstoCAM server itself.

### Installation with HACS

Until the integration is in the default HACS catalog, add the repository manually:

1. HACS → ⋮ menu (top right) → **Custom repositories**.
2. Repository: `https://github.com/Wave-is/ha-prostocam`, Type: **Integration** → **Add**.
3. Find **ProstoCAM** in HACS → **Download** → restart Home Assistant (Settings → System → ⋮ → Restart).

Without HACS: copy `custom_components/prostocam` into `config/custom_components/` and restart Home Assistant.

### Pairing

1. In the ProstoCAM web account: **Smart home → Home Assistant → Connect**. An 8-character code appears (valid 10 minutes).
2. In Home Assistant: **Settings → Devices & services → Add integration → ProstoCAM**, enter the code.
3. In a few seconds the sensors appear in the ProstoCAM web account — **switched off**. Switch on the ones you need and link
   them to cameras.

If the connection is reset in the web account (for example with Reconnect), Home Assistant asks for a new code; sensors and
camera links stay.

**Options** (Settings → Devices & services → ProstoCAM → Configure): which entity types and binary sensor classes are listed in
the web account. By default: security binary sensors, alarm panels and sirens.

### What is sent and what is not

Sent:

- the **list** of entities of the chosen types: `entity_id`, name, device class, area, device manufacturer and model,
  source integration;
- **state changes** only of the sensors you **switched on in the web account**: from → to and the time of the change;
- the **mode of alarm panels** (armed / disarmed / triggered), who changed it (the name from the panel, up to 16 characters) and
  the mode at start;
- a **heartbeat** every minute: Home Assistant and integration versions.

Not sent: any other entity or attribute, history, Home Assistant cameras and images, locations, users, passwords and tokens
of Home Assistant. ProstoCAM can not switch or change anything in your Home Assistant — the link is one-way.

The connection token is stored in Home Assistant and is redacted from diagnostics.

### Reliability

Events that can not be delivered (no internet, server maintenance) stay in a queue (up to 1000 events) and are retried with a
growing pause of up to a minute; the queue survives a restart of Home Assistant. Transitions from or to `unavailable` /
`unknown` are not events and are not sent.

### Development

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

```bash
pip install -r requirements_test.txt
pytest
```

## License

MIT
