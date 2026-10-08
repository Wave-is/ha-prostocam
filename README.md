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
  інтеграція-джерело, рівень заряду батареї пристрою (коли сервер ProstoCAM його приймає);
- **зміни стану** лише тих датчиків, які ви **увімкнули в кабінеті**: було → стало і час зміни;
- **режим охоронних панелей** (під охороною / знято / тривога), хто його змінив (ім'я з панелі, до 16 знаків) і режим при старті;
- **пульс** раз на хвилину: версії Home Assistant та інтеграції.

Події, які не вдалося доставити (немає інтернету, роботи на сервері), чекають у черзі (до 1000) і переживають
перезапуск Home Assistant. Кожна подія каже серверу, скільки чекала: запізніла подія лише пишеться в журнал і не будить камери.

Не надсилається: інші сутності й атрибути, історія, камери і зображення Home Assistant, координати, користувачі, паролі
й токени Home Assistant. ProstoCAM не може нічого вмикати чи змінювати у вашому Home Assistant — крім охоронної панелі, яку ви самі вибрали для
синхронізації з охороною ProstoCAM (з 0.3, за замовчуванням вимкнено).

Токен підключення зберігається в Home Assistant і не потрапляє в діагностику.

### Камери ProstoCAM у Home Assistant (з 0.2)

Камери, які ви дозволили, з'являються в Home Assistant самі — кожна окремим пристроєм (назва, виробник, модель, місце):

- **камера** — ефір у картці (HLS через вбудований `stream`) і кадр «зараз» (оновлюється не частіше разу на 10 с);
- **Рух**, **Людина**, **Транспорт** — бінарні датчики, вмикаються на 30 с після тривоги камери цього виду;
- **Тривога** — подія (`event`) на кожну тривогу: тип `motion` / `person` / `vehicle` / `animal` / `other` / `test`,
  в атрибутах — клас від ШІ, впевненість, номер події, час, `test`; зручно для автоматизацій;
- **Останній кадр тривоги** — зображення (`image`): кадр, знятий у момент тривоги;
- **Зв'язок** — чи камера на зв'язку з ProstoCAM (діагностичний датчик).

Події приходять одразу: Home Assistant сам тримає з'єднання з ProstoCAM (без відкритих портів), після обриву
перепідключається й дочитує пропущене.

**Як вибрати камери.** У кабінеті ProstoCAM: **Розумний дім → Home Assistant → «Що бачитиме Home Assistant»** — позначте
«Камери й кадр», «Події», «Ефір» і «Усі мої камери» або «Лише вибрані» (список камер), натисніть **«Зберегти доступ»**.
Нового коду не треба: протягом хвилини Home Assistant сам додасть або прибере камери й датчики. Чого не дозволено —
того в Home Assistant не буде, а в **Налаштування → Ремонт** з'явиться підказка, де дати доступ. Пам'ятайте: камери
побачить кожен, хто має доступ до вашого Home Assistant.

Ефір відкривається у звичайній картці камери (HLS, затримка кілька секунд). WebRTC (go2rtc) для цих камер поки не
пропонується — він обривав би ефір через 5 хвилин.

### «Медіа», охорона, кнопки і рахунок (з 0.3)

Потрібен сервер ProstoCAM із протоколом 3. Кожну можливість абонент вмикає окремою позначкою в кабінеті
(**Розумний дім → Home Assistant → «Що бачитиме Home Assistant»**) — за замовчуванням вони вимкнені. Чого не дозволено —
того в Home Assistant немає, а в **Налаштування → Ремонт** є підказка, де дати доступ.

- **Кадр тривоги** — «Останній кадр тривоги» тепер показує кадр **самої події** (збережений у мить тривоги), а не «зараз».
- **Медіа** (записи, `archive:read`): **Медіа → ProstoCAM → камера → день → події** (мініатюра — кадр події, клік —
  кліп) і **→ Архів → день → година**. Кожне відтворення бере свіжу одноразову адресу; кадри йдуть через ваш
  Home Assistant, токен ProstoCAM браузер не бачить.
- **Охорона ProstoCAM** (`arming:write`) — охоронна панель з режимами «Вдома», «Ніч», «Нікого немає» і зняттям,
  ті самі перевірки готовності, що в кабінеті. Камера не готова — помилка й сповіщення зі списком камер; поставити
  попри це — дія **«Поставити ProstoCAM попри неготовність»** (`prostocam.arm_anyway`). Після тривоги під охороною
  панель 2 хвилини показує «тривога».
  **Синхронізація з охороною Home Assistant** (Параметри інтеграції, за замовчуванням вимкнена): виберіть свою панель
  (наприклад, Ajax через SIA) — постановка чи зняття з будь-якого боку переходить на інший. Від петель захищено:
  зміну, зроблену самим Home Assistant, ProstoCAM позначає й назад не віддзеркалюється, а той самий режим сервер
  просто відповідає «без змін». Панель, що вимагає код, не може йти за ProstoCAM без коду.
- **Кнопки камери** (`actions:write`): **«Перевірити тривогу»** (тестова тривога всіма каналами), **«Не турбувати»**
  (вимкнено / на годину / до ранку — 07:00 за часом Home Assistant), **«Відлякати»** — лише в камер із сиреною чи
  світлом і лише після свіжої тривоги, **«Перевірити ШІ (витрачає кредит)»** — вимкнена за замовчуванням: увімкніть
  її в налаштуваннях сутності; перше натискання лише називає ціну, кредит витрачає друге натискання протягом 30 с.
- **Рахунок** (`account:read`): баланс, тариф, ШІ-кредити, дата наступного списання, стан рахунку (раз на 15 хвилин);
  рахунок обмежено за несплату — підказка в «Ремонті».
- **Дії** для автоматизацій: `prostocam.mute` (хвилини, 0 — зняти), `prostocam.test_alarm`, `prostocam.verify_ai`
  (поле `spend_credit` обов'язкове: без нього кредит не витрачається, відповідь назве ціну; повертає підсумок ШІ),
  `prostocam.arm_anyway`.
- **Події шини** `prostocam_alarm` (камера, вид, номер події, шлях кадру) і `prostocam_ai_verdict` (підсумок ШІ).

**Готове сповіщення на телефон із кадром і текстом ШІ** — blueprint
[`alarm_notify.yaml`](blueprints/automation/prostocam/alarm_notify.yaml):
**Налаштування → Автоматизації → Blueprints → Імпортувати** й вставте
`https://github.com/Wave-is/ha-prostocam/blob/main/blueprints/automation/prostocam/alarm_notify.yaml`. Виберіть телефон,
камери й види тривог. Повідомлення йде одразу з кадром події; текст ШІ (після «Перевірити ШІ») оновлює те саме
повідомлення.

Пам'ятайте: охороною, кнопками й записами зможе користуватися кожен, хто має доступ до вашого Home Assistant.

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
  source integration, battery level of the device (when the ProstoCAM server accepts it);
- **state changes** only of the sensors you **switched on in the web account**: from → to and the time of the change;
- the **mode of alarm panels** (armed / disarmed / triggered), who changed it (the name from the panel, up to 16 characters) and
  the mode at start;
- a **heartbeat** every minute: Home Assistant and integration versions.

Not sent: any other entity or attribute, history, Home Assistant cameras and images, locations, users, passwords and tokens
of Home Assistant. ProstoCAM can not switch or change anything in your Home Assistant — except the alarm panel you chose
yourself to sync with the ProstoCAM arming (since 0.3, off by default).

The connection token is stored in Home Assistant and is redacted from diagnostics.

### ProstoCAM cameras in Home Assistant (since 0.2)

The cameras you allowed appear in Home Assistant by themselves, each as a device (name, manufacturer, model, place):

- **camera** — live video in the camera card (HLS through the built-in `stream`) and the snapshot "now" (refreshed at most
  every 10 s);
- **Motion**, **Person**, **Vehicle** — binary sensors, on for 30 s after an alarm of the camera of that kind;
- **Alarm** — an `event` entity for every alarm: type `motion` / `person` / `vehicle` / `animal` / `other` / `test`, the
  attributes carry the AI class, confidence, event number, time and `test`; handy for automations;
- **Last alarm frame** — an `image` entity: the frame taken when the alarm arrived;
- **Connectivity** — whether the camera is online for ProstoCAM (diagnostic sensor).

Events arrive at once: Home Assistant keeps its own connection to ProstoCAM (no open ports), reconnects after an outage
and reads what it missed.

**Choosing the cameras.** In the ProstoCAM web account: **Smart Home → Home Assistant → "What Home Assistant will see"** —
tick cameras and snapshots, events, live video, and "All my cameras" or only the chosen ones, then press **Save access**.
No new code is needed: within a minute Home Assistant adds or removes the cameras and sensors itself. What is not allowed
does not appear, and **Settings → Repairs** says where to give the access. Remember: everyone with access to your Home
Assistant sees these cameras.

Live video opens in the usual camera card (HLS, a few seconds of delay). WebRTC (go2rtc) is not offered for these cameras
yet — it would cut the stream after 5 minutes.

### Media, arming, buttons and the account (since 0.3)

Needs a ProstoCAM server of protocol 3. The subscriber switches each part on with its own tick in the web account
(**Smart Home → Home Assistant → "What Home Assistant will see"**); none is on by default. What is not allowed does not
appear, and **Settings → Repairs** says where to give the access.

- **Alarm frame** — "Last alarm frame" now shows the frame of the **event itself** (saved at the moment of the alarm),
  not "now".
- **Media** (recordings, `archive:read`): **Media → ProstoCAM → camera → day → events** (thumbnail — the frame of the
  event, click — the clip) and **→ Archive → day → hour**. Every playback takes a fresh one-time address; frames go
  through your Home Assistant, the browser never sees the ProstoCAM token.
- **ProstoCAM security** (`arming:write`) — an alarm panel with Home, Night, Away and disarm, with the same readiness
  check as the web account. A camera not ready — an error and a notification listing the cameras; arm anyway with the
  action **"Arm ProstoCAM anyway"** (`prostocam.arm_anyway`). After an alarm while armed the panel shows "triggered"
  for 2 minutes.
  **Sync with the Home Assistant alarm panel** (integration options, off by default): choose your panel (for example
  Ajax via SIA) — arming or disarming on either side follows on the other. Loops are prevented: a change made by this
  Home Assistant is marked by ProstoCAM and not mirrored back, and the same mode is answered "unchanged" by the server.
  A panel that needs a code can not follow ProstoCAM without one.
- **Camera buttons** (`actions:write`): **Test alarm** (through every channel), **Do not disturb** (off / for an hour /
  until the morning — 07:00 Home Assistant time), **Deter** — only for cameras with a siren or light and only after a
  fresh alarm, **Check with AI (spends a credit)** — disabled by default: enable the entity; the first press only names
  the price, a second press within 30 s spends the credit.
- **Account** (`account:read`): balance, tariff, AI credits, next charge date, account status (every 15 minutes); an
  account restricted for an unpaid balance raises a repair.
- **Actions** for automations: `prostocam.mute` (minutes, 0 — off), `prostocam.test_alarm`, `prostocam.verify_ai` (the
  field `spend_credit` is required: without it no credit is spent and the reply names the price; returns the AI result),
  `prostocam.arm_anyway`.
- **Bus events** `prostocam_alarm` (camera, kind, event number, frame path) and `prostocam_ai_verdict` (the AI result).

**Ready phone notification with the frame and the AI text** — the blueprint
[`alarm_notify.yaml`](blueprints/automation/prostocam/alarm_notify.yaml): **Settings → Automations → Blueprints →
Import** and paste
`https://github.com/Wave-is/ha-prostocam/blob/main/blueprints/automation/prostocam/alarm_notify.yaml`. Choose the phone,
cameras and alarm kinds. The notification goes out at once with the frame of the event; the AI text (after Check with AI)
updates the same notification.

Remember: everyone with access to your Home Assistant can use the arming, buttons and recordings you allow.

### Reliability

Events that can not be delivered (no internet, server maintenance) stay in a queue (up to 1000 events) and are retried with a
growing pause of up to five minutes; the queue survives a restart of Home Assistant. Each event tells the server how
long it waited, so an event delivered too late is only logged and does not wake the cameras. Transitions from or to `unavailable` /
`unknown` are not events and are not sent.

### Development

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

```bash
pip install -r requirements_test.txt
pytest
```

## License

MIT
