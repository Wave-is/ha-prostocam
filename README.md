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

Ефір відкривається у звичайній картці камери: з 1.0 — через **WebRTC** (затримка менше секунди) у камер, для яких
сервер ProstoCAM його ввімкнув, у решти — HLS (затримка кілька секунд). go2rtc для цих камер не використовується.

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

### WebRTC, «Спитати архів», «Вивантажити кліп» (з 1.0)

Потрібен сервер ProstoCAM із протоколом 4; нових позначок доступу не треба (WebRTC — це «Ефір», архів і кліпи — «Записи»).

- **WebRTC.** Камера, для якої сервер увімкнув WebRTC, грає в картці через WebRTC: відео йде від медіавузла ProstoCAM
  прямо в браузер, Home Assistant лише передає пропозицію й відповідь. Не вдалося (вузол вимкнений, кодек H.265, мережа)
  — картка покаже помилку, а камера сама перейде на HLS: до наступного читання каталогу (5 хв) або на годину. Звук AAC
  браузер по WebRTC не грає — відео без звуку. Запис і кадри працюють як раніше.
- **Спитати архів** — дія `prostocam.ask_archive` з відповіддю: питання простими словами
  («коли сьогодні хтось підходив до хвіртки?», «машини вчора ввечері») → `answer` (коротка фраза мовою Home Assistant:
  «Знайшов 3 події, остання о 14:05 — Вхід, людина») і `events` (камера, час, вид, `snapshot_url` — кадр через ваш
  Home Assistant, `clip` — кліп у «Медіа»). Шукає той самий пошук, що рядок пошуку кабінету: ШІ-кредити не витрачаються.
  Чого сервер не зрозумів — названо в `unsupported` і у фразі («Не зрозумів частину питання: …»), це не «нічого не було».
- **Assist.** Намір `ProstoCamAskArchive` (слот `question`) бачать голосові агенти з LLM; для звичайного Assist додайте
  речення в `config/custom_sentences/uk/prostocam.yaml`:

  ```yaml
  language: "uk"
  intents:
    ProstoCamAskArchive:
      data:
        - sentences:
            - "знайди в архіві {question}"
            - "спитай архів {question}"
  lists:
    question:
      wildcard: true
  ```
- **Вивантажити кліп** — дія `prostocam.export_clip`: камера, початок (час Home Assistant), тривалість 1…600 с,
  «чекати файл». Те саме завдання, що «Вивантажити» в кабінеті: Home Assistant чекає готовності (до 5 хвилин) і повертає
  `url` (коротке одноразове посилання — брати безпосередньо перед надсиланням), `sha256`, `size_bytes`, `expires_at`
  і `media_content_id`; та сама інформація — у події `prostocam_export_ready`. Вивантаження видно в
  **Медіа → ProstoCAM → камера → Вивантажені кліпи**; кожне відтворення бере свіже посилання.
- **Змінити підключення** (Налаштування → Пристрої та служби → ProstoCAM → ⋮ → Переналаштувати): інший сервер або новий
  код для того самого підключення; датчики, камери й автоматизації лишаються.

**Приклад автоматизації:** тривога «людина» вночі → вивантажити 60 с з моменту тривоги й надіслати посилання:

```yaml
triggers:
  - trigger: event
    event_type: prostocam_alarm
    event_data: { event_type: person }
actions:
  - action: prostocam.export_clip
    data:
      camera: camera.vkhid
      start: "{{ (now() - timedelta(seconds=10)).isoformat() }}"
      duration: 60
    response_variable: clip
  - action: notify.mobile_app_phone
    data:
      message: "Кліп готовий: {{ clip.url }} (SHA-256 {{ clip.sha256[:12] }}…)"
```

### Як оновлюються дані, обмеження, якщо щось не так

- **Оновлення.** Тривоги, стан камер і охорони приходять одразу (живий канал `/v2/stream`); каталог камер — раз на
  5 хвилин; «Не турбувати» — раз на 5 хвилин; виходи відлякування — раз на добу; рахунок — раз на 15 хвилин. Події
  датчиків Home Assistant ідуть на сервер одразу, пульс — раз на хвилину.
- **Пристрої.** Будь-які камери ProstoCAM (Hikvision, Dahua, ONVIF, RTSP…) — Home Assistant бачить їх через сервер, а не
  напряму; будь-які охоронні датчики Home Assistant.
- **Обмеження.** WebRTC — лише там, де сервер його ввімкнув; без звуку; H.265 браузер може не прийняти (тоді HLS).
  Одночасно — до 16 камер в ефірі на підключення. Запис і вивантаження — у межах тарифу й терміну зберігання. Сервер
  протоколу нижче 4 — без WebRTC, питань до архіву й вивантажень (інтеграція працює як 0.3).
- **Якщо щось не так.** «Ремонт» підказує, де дати доступ або оновити інтеграцію; «Перепідключити» у кабінеті → Home
  Assistant попросить новий код; **Завантажити діагностику** (сторінка інтеграції → ⋮) — без токенів, адрес ефіру,
  посилань на файли й текстів питань. Ефір не відкривається через WebRTC — камера сама перейде на HLS; HLS теж ні —
  перевірте, що камера «на зв'язку» (діагностичний датчик).
- **Видалення.** Налаштування → Пристрої та служби → ProstoCAM → ⋮ → Видалити; потім у кабінеті ProstoCAM
  «Відключити» — токен перестане діяти. Через HACS видаліть і саму інтеграцію.

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
  attributes carry the AI class, confidence, `label` ("Person · 87 %" in the language of Home Assistant; no percent when
  the camera detected it by itself and nobody measured it), event number, time and `test`; handy for automations;
- **Last alarm frame** — an `image` entity: the frame taken when the alarm arrived;
- **Connectivity** — whether the camera is online for ProstoCAM (diagnostic sensor).

Events arrive at once: Home Assistant keeps its own connection to ProstoCAM (no open ports), reconnects after an outage
and reads what it missed.

**Choosing the cameras.** In the ProstoCAM web account: **Smart Home → Home Assistant → "What Home Assistant will see"** —
tick cameras and snapshots, events, live video, and "All my cameras" or only the chosen ones, then press **Save access**.
No new code is needed: within a minute Home Assistant adds or removes the cameras and sensors itself. What is not allowed
does not appear, and **Settings → Repairs** says where to give the access. Remember: everyone with access to your Home
Assistant sees these cameras.

Live video opens in the usual camera card: since 1.0 over **WebRTC** (below a second of delay) for the cameras the
ProstoCAM server switched it on for, HLS (a few seconds) for the others. go2rtc is not used for these cameras.

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

### WebRTC, ask the archive, export a clip (since 1.0)

Needs a ProstoCAM server of protocol 4; no new access ticks (WebRTC is "Live video", questions and clips are "Recordings").

- **WebRTC.** A camera the server switched WebRTC on for plays over WebRTC in the card: the video goes from the ProstoCAM
  media node straight to the browser, Home Assistant only passes the offer and the answer. If it fails (switched off on
  the node, an H.265 codec, the network) the card shows the error and the camera falls back to HLS by itself — until the
  next catalog read (5 min) or for an hour. Browsers do not play AAC over WebRTC: the video has no sound. Recording and
  snapshots work as before.
- **Ask the archive** — the action `prostocam.ask_archive` with a response: a question in plain words ("when did someone
  come to the gate today?", "cars yesterday evening") → `answer` (a short phrase in the language of Home Assistant:
  "Found 3 events, the last one at 14:05: Gate, person") and `events` (camera, time, kind, `snapshot_url` — the frame
  through your Home Assistant, `clip` — the clip in Media). The same search as the search line of the web account: no
  AI credits are spent. What the server did not understand is named in `unsupported` and in the phrase — that is not
  "nothing happened".
- **Assist.** LLM voice agents see the intent `ProstoCamAskArchive` (slot `question`); for the plain Assist add sentences
  to `config/custom_sentences/en/prostocam.yaml`:

  ```yaml
  language: "en"
  intents:
    ProstoCamAskArchive:
      data:
        - sentences:
            - "search the archive for {question}"
            - "ask the archive {question}"
  lists:
    question:
      wildcard: true
  ```
- **Export a clip** — the action `prostocam.export_clip`: camera, start (Home Assistant time), duration 1…600 s, "wait
  for the file". The same job as "Export" in the web account: Home Assistant waits until it is ready (up to 5 minutes)
  and returns `url` (a short-lived one-time link — take it right before sending), `sha256`, `size_bytes`,
  `expires_at` and `media_content_id`; the same comes as the event `prostocam_export_ready`. Exports are listed in
  **Media → ProstoCAM → camera → Exported clips**; every playback takes a fresh link.
- **Reconfigure** (Settings → Devices & services → ProstoCAM → ⋮ → Reconfigure): another server or a new code for the
  same connection; sensors, cameras and automations stay.

**Example automation:** a "person" alarm → export 60 s from the alarm and send the link:

```yaml
triggers:
  - trigger: event
    event_type: prostocam_alarm
    event_data: { event_type: person }
actions:
  - action: prostocam.export_clip
    data:
      camera: camera.gate
      start: "{{ (now() - timedelta(seconds=10)).isoformat() }}"
      duration: 60
    response_variable: clip
  - action: notify.mobile_app_phone
    data:
      message: "The clip is ready: {{ clip.url }} (SHA-256 {{ clip.sha256[:12] }}…)"
```

### Data updates, limitations, troubleshooting, removal

- **Data updates.** Alarms, camera status and arming arrive at once (the live channel `/v2/stream`); the camera catalog
  every 5 minutes; do not disturb every 5 minutes; deterrence outputs once a day; the account every 15 minutes. Sensor
  events of Home Assistant go to the server at once, the heartbeat every minute.
- **Supported devices.** Any camera of ProstoCAM (Hikvision, Dahua, ONVIF, RTSP…) — Home Assistant sees it through the
  server, not directly; any security sensor of Home Assistant.
- **Known limitations.** WebRTC only where the server switched it on; no sound; a browser may refuse H.265 (then HLS).
  Up to 16 cameras live at a time per connection. Recordings and exports within the tariff and its retention. A server of
  a protocol below 4 has no WebRTC, questions or exports (the integration works as 0.3).
- **Troubleshooting.** Repairs say where to give access or to update; Reconnect in the web account → Home Assistant asks
  for a new code; **Download diagnostics** (integration page → ⋮) — without tokens, live addresses, file links or the
  text of questions. Live video does not open over WebRTC — the camera falls back to HLS itself; HLS does not either —
  check that the camera is online (the diagnostic sensor).
- **Removal.** Settings → Devices & services → ProstoCAM → ⋮ → Delete; then Disconnect in the ProstoCAM web account — the
  token stops working. Remove the integration itself in HACS.

### Reliability

Events that can not be delivered (no internet, server maintenance) stay in a queue (up to 1000 events) and are retried with a
growing pause of up to five minutes; the queue survives a restart of Home Assistant. Each event tells the server how
long it waited, so an event delivered too late is only logged and does not wake the cameras. Transitions from or to `unavailable` /
`unknown` are not events and are not sent.

### Development

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

```bash
pip install -r requirements_test.txt
pytest   # fails under 95 % coverage (pyproject.toml)
```

The rules of the Home Assistant Integration Quality Scale and where the integration stands are listed in
[`quality_scale.yaml`](custom_components/prostocam/quality_scale.yaml) (target: Gold).

## License

MIT
