# Bug Triage Dataset — мультимодальные данные для автоматической классификации баг-репортов

Набор неструктурированных данных для будущей системы ИИ, которая по входящему
баг-репорту автоматически определяет **категорию дефекта и компонент системы**
(Backend, Frontend, Auth-service, Billing, Database), **критичность**
(Blocker, Critical, Major, Minor) и команду-исполнителя.

Репозиторий ведётся в рамках лабораторных работ по учебному пособию
«Структурирование, разметка и обогащение данных» (Муромцев Д.И., Шилин И.А.,
Исаев И.В.):

* **ЛР №1 «Подготовка неструктурированных данных»** — сбор данных трёх
  модальностей (`data/`, `scripts/collect_*`, `docs/data_card.md`);
* **ЛР №2 «Проектирование аннотационной схемы»** — аннотационная схема, её
  документация, конфигурации разметки и эталонные аннотации
  (`annotation_schema/`, `annotations/`, `docs/annotation_schema.md`) —
  см. раздел [Аннотационная схема и аннотации](#аннотационная-схема-и-аннотации);
* **ЛР №3 «Разметка текстовых данных»** — многоуровневая разметка 10 текстовых
  документов (по одному на объект) в INCEpTION: баг-репорт → абзац/раздел →
  предложение → токен → часть речи и лемма → сущности со ссылками DBpedia → связи;
  анализ разметки и схема 1.1 (`annotations/text/markup/`, `scripts/text_markup.py`,
  `docs/text_markup_analysis.md`) — см. раздел [Разметка текстовых данных](#разметка-текстовых-данных-лр3);
* **ЛР №5 «Разметка изображений»** — разметка всех 30 скриншотов в Label Studio: классы и признаки
  изображения → области (рамки) с атрибутами и связями → дословный текст области и сущности в нём;
  предразметка Tesseract OCR + правила, проверка, экспорт в COCO, Label Studio JSON, Pascal VOC, YOLO, CSV;
  схема 1.2 (`annotations/images/`, `scripts/image_markup.py`, `docs/image_markup_analysis.md`) — см. раздел
  [Разметка изображений](#разметка-изображений-лр5).

Данные собраны в **трёх модальностях** для **10 объектов** — категорий
программных дефектов:

| № | Категория (`id`) | Описание | Компонент |
|---|---|---|---|
| 1 | `crash` | Падения и необработанные исключения | Backend |
| 2 | `ui_layout` | Вёрстка и отображение интерфейса | Frontend |
| 3 | `authentication` | Аутентификация и авторизация | Auth-service |
| 4 | `payment` | Ошибки оплаты и биллинга | Billing |
| 5 | `database` | Ошибки базы данных и транзакций | Database |
| 6 | `network` | Сеть и соединения | Backend |
| 7 | `performance` | Производительность и зависания | Backend |
| 8 | `memory` | Утечки памяти и OutOfMemory | Backend |
| 9 | `localization` | Локализация и кодировки | Frontend |
| 10 | `security` | Уязвимости и TLS/сертификаты | Backend |

| Модальность | Что это | Объём |
|---|---|---|
| Текст | реальные баг-репорты из открытых трекеров + фрагменты реальных системных логов | 100 репортов (по 10 на категорию), 12 лог-файлов |
| Изображения | скриншоты воспроизведённых дефектов | 30 PNG (по 3 на категорию) |
| Аудио | голосовые обращения пользователей в поддержку | 20 WAV (по 2 на категорию), 4,4 мин |

---

## Структура репозитория

```
bug-triage-dataset/
├── README.md                     ← этот файл
├── LICENSE                       ← лицензии кода и данных
├── requirements.txt              ← зависимости Python
├── .gitignore
├── data/                         ← ДАННЫЕ (по подпапке на модальность, внутри — по категориям)
│   ├── text/
│   │   ├── bug_reports/<категория>/<проект>-<id>.json   ← 1 файл = 1 баг-репорт
│   │   ├── bug_reports.csv       ← все 100 репортов одной таблицей
│   │   ├── logs/<категория>/<система>_<категория>.log  ← фрагменты логов LogHub
│   │   └── logs.csv              ← откуда взят каждый фрагмент (файл, строки, regex)
│   ├── images/
│   │   ├── screenshots/<категория>/<категория>_NN_<что>.png
│   │   └── images.csv            ← описание каждого скриншота и способ получения
│   ├── audio/
│   │   ├── voice_reports/<категория>/<категория>_NN.wav
│   │   └── audio.csv             ← длительность, голос, эталонная расшифровка
│   └── checksums.sha256          ← контрольные суммы всех файлов данных
├── annotation_schema/            ← АННОТАЦИОННАЯ СХЕМА (ЛР2)
│   ├── schema.json               ← метки, атрибуты, уровни, форматы — единый источник
│   └── label_studio/             ← конфигурации Label Studio (4 шт.) и задачи с предразметкой
├── annotations/                  ← АННОТАЦИИ (по подпапке на модальность)
│   ├── README.md                 ← что где лежит, эталонный набор, статусы
│   ├── VERIFICATION.md           ← журнал верификации
│   ├── text/reports/<категория>/<report_id>.json  ← баг-репорты: классы, разделы, сущности, связи
│   ├── text/conllu/<report_id>.conllu             ← те же репорты в CoNLL-U (BIO)
│   ├── text/logs/<категория>/<лог>.jsonl          ← логи: событие на каждую строку
│   ├── text/markup/                               ← ЛР3: многоуровневая разметка текстовых документов
│   │   ├── corpus.json, documents/<объект>.txt    ← отбор текстов (по документу на объект)
│   │   ├── auto/ → manual_edits.json → json/      ← предразметка, ручные правки, итоговая разметка
│   │   ├── tsv/, conllu/                          ← WebAnno TSV 3.3 (INCEpTION) и CoNLL-U
│   │   └── inception/                             ← проект INCEpTION, слои, экспорт XMI
│   ├── images/screenshots_coco.json               ← ЛР5: разметка 30 скриншотов в формате COCO (основной)
│   ├── images/markup/                             ← ЛР5: manual.json (ручная разметка), auto/ (предразметка),
│   │                                                labelstudio/, voc/, yolo/, regions.csv
│   └── audio/<категория>/<имя>.TextGrid + .json   ← аудио: сегменты, расшифровка, сущности
├── scripts/                      ← КОД сбора и проверки данных
│   ├── download_sources.sh       ← скачивание исходных датасетов (git clone)
│   ├── categories.py             ← справочник 10 категорий и правила отнесения
│   ├── collect_text.py           ← отбор баг-репортов и фрагментов логов
│   ├── make_screenshots.py       ← тестовый стенд + съёмка скриншотов дефектов
│   ├── synth_audio.py            ← синтез голосовых обращений
│   ├── validate_dataset.py       ← проверка корректности загрузки (шаг 4.3)
│   ├── annotation_tools.py       ← конфигурации разметки, проверка аннотаций, CoNLL-U, согласованность
│   ├── text_markup.py            ← ЛР3: отбор текстов, предразметка, правки, экспорт, INCEpTION, анализ
│   └── image_markup.py           ← ЛР5: предразметка OCR, итоговая разметка, экспорт, Label Studio, анализ
└── docs/                         ← ДОКУМЕНТАЦИЯ
    ├── data_card.md              ← описание полей и особенностей данных
    ├── validation_report.md      ← результат последней проверки данных
    ├── annotation_schema.md      ← аннотационная схема: требования, уровни, форматы, верификация
    ├── annotation_validation_report.md ← результат последней проверки аннотаций
    ├── text_markup_analysis.md   ← ЛР3: анализ разметки текста, зависимости между уровнями
    ├── image_markup_analysis.md  ← ЛР5: процесс, проверка и анализ разметки изображений
    ├── image_markup_stats.md     ← ЛР5: статистика разметки и качества предразметки (генерируется)
    ├── img/                      ← иллюстрации разметки
    └── presentation/             ← презентации к лабораторным (PPTX)
```

Во всех модальностях используются одинаковые имена категорий, поэтому данные
об одном объекте легко найти в каждой из них: например, всё про утечки памяти
лежит в `data/text/bug_reports/memory/`, `data/text/logs/memory/`,
`data/images/screenshots/memory/` и `data/audio/voice_reports/memory/`.

---

## Источники данных

| Модальность | Источник | Ссылка | Лицензия |
|---|---|---|---|
| Текст — баг-репорты | **GitBugs** (Patil, 2025): выгрузки из Apache Jira (Spark, Hadoop, HBase, Cassandra), Mozilla Bugzilla (Firefox, Thunderbird, SeaMonkey) и GitHub Issues (VS Code) | https://github.com/av9ash/gitbugs, статья: https://arxiv.org/abs/2504.09651 | CC BY 4.0 |
| Текст — логи | **LogHub** (Zhu et al., ISSRE 2023): логи OpenSSH, Linux, Hadoop, HDFS, Spark, Zookeeper, OpenStack, Android, Thunderbird | https://github.com/logpai/loghub | бесплатно для исследований и обучения |
| Изображения | собственный тестовый стенд «TaskFlow» (вымышленный сервис), браузер Chromium через Playwright | `scripts/make_screenshots.py` | CC BY 4.0 (автор репозитория) |
| Аудио | синтез речи **espeak-ng** по сценариям обращений в поддержку | https://github.com/espeak-ng/espeak-ng | записи — CC BY 4.0; движок — GPLv3 |

Дополнительно просмотрены, но не использованы: BugRepo (https://github.com/logpai/bugrepo)
— те же проекты, что в GitBugs, но более старые (до 2018 г.); OwlEye
(https://github.com/20200501/OwlEye) — датасет скриншотов с UI-дефектами, но
изображения хранятся в Git LFS и были недоступны для загрузки.

**Почему изображения и аудио получены так.** Скриншоты к баг-репортам в
публичных трекерах лежат во внешних хранилищах (GitHub user-attachments,
вложения Bugzilla) и не входят в открытые датасеты, а записи звонков в
поддержку содержат персональные данные и не публикуются. Поэтому:

* **скриншоты** сделаны так, как их делает QA-инженер: дефект *реально
  воспроизводится* на тестовом стенде и фиксируется браузером. Всё, что на них
  видно, — настоящее поведение программ: страницы ошибок Chromium
  (`ERR_NAME_NOT_RESOLVED`, `ERR_CONNECTION_REFUSED`, `ERR_CERT_AUTHORITY_INVALID`),
  трейсбеки Python и Node.js, исключения SQLite, выполнившийся XSS,
  «кракозябры» при неверной кодировке, двойное списание при двойном клике,
  рост JS-кучи при утечке и т.д.;
* **аудио** синтезировано: в `audio.csv` записан исходный текст каждого
  обращения — это эталонная расшифровка для будущей проверки распознавания речи.
  Синтетическая природа записей указана явно; при появлении реальных записей
  их можно добавить в те же папки.

---

## Описание кода

| Скрипт | Назначение |
|---|---|
| `scripts/download_sources.sh` | клонирует GitBugs и LogHub в папку `sources/` (она в `.gitignore`) |
| `scripts/categories.py` | 10 категорий, привязка к компонентам, регулярные выражения для отнесения репорта к категории по заголовку |
| `scripts/collect_text.py` | читает 8 выгрузок трекеров (~107 тыс. репортов), убирает пустые/слишком длинные описания и автоматические отчёты CI о нестабильных тестах, относит репорт к категории, отбирает по 10 на категорию (не более 3 из одного проекта, разные приоритеты, `seed=42`), вырезает из логов LogHub строки по категориям |
| `scripts/make_screenshots.py` | поднимает локальные HTTP/HTTPS-серверы стенда, запускает ошибочный код, открывает страницы в Chromium и сохраняет 30 скриншотов + `images.csv` |
| `scripts/synth_audio.py` | синтезирует 20 обращений (5 голосов, разный темп), приводит к WAV 16 кГц моно с полосой телефонного канала (200–3800 Гц), пишет `audio.csv` |
| `scripts/validate_dataset.py` | проверяет корректность данных (см. ниже) и пишет `docs/validation_report.md` |
| `scripts/text_markup.py` | ЛР3: собирает документы объектов (`corpus`), предразмечает все уровни (`auto`), применяет ручные правки (`final`), выгружает TSV 3.3 и CoNLL-U (`export`), создаёт проект INCEpTION и загружает разметку (`inception`), сверяет выгрузку INCEpTION с исходной (`roundtrip`), проверяет по схеме (`validate`), строит анализ (`analyze`) |
| `scripts/image_markup.py` | ЛР5: предразметка скриншотов Tesseract OCR + правила (`auto`), итоговая разметка с подгонкой рамок к содержимому (`final`), экспорт в Label Studio JSON, Pascal VOC, YOLO, CSV (`export`), открытие задач в редакторе Label Studio и сверка выгрузки (`lsf`), наложение областей на изображения (`overlay`), сверка с эталонами (`agree`), анализ (`analyze`), загрузка в запущенный Label Studio (`ls-import`) |
| `scripts/annotation_tools.py` | ЛР2: генерирует конфигурации Label Studio из схемы (`configs`), задачи с предразметкой (`tasks`), проверяет аннотации (`validate`), выгружает CoNLL-U (`conllu`), считает согласованность разметчиков (`agree`) |

### Установка и запуск

```bash
# 1. Зависимости
python -m pip install -r requirements.txt
python -m playwright install chromium
# для аудио: espeak-ng и ffmpeg (Ubuntu: sudo apt install espeak-ng ffmpeg; Windows: установщики с GitHub)
# для скриншотов памяти/падений Node.js: node >= 18

# 2. Исходные датасеты
bash scripts/download_sources.sh

# 3. Сбор данных (каждый шаг можно запускать отдельно)
python scripts/collect_text.py --sources sources --out data/text --per-category 10
python scripts/make_screenshots.py --out data/images/screenshots
python scripts/synth_audio.py --out data/audio/voice_reports

# 4. Проверка
python scripts/validate_dataset.py --update-checksums   # после пересборки данных
python scripts/validate_dataset.py                      # проверка уже загруженных данных

# 5. Аннотации (ЛР2; нужен только pillow)
python scripts/annotation_tools.py configs    # конфигурации Label Studio из schema.json
python scripts/annotation_tools.py tasks      # задачи Label Studio с предразметкой
python scripts/annotation_tools.py validate   # проверка аннотаций -> docs/annotation_validation_report.md
python scripts/annotation_tools.py conllu     # annotations/text/conllu/

# 6. Разметка текстовых данных (ЛР3; нужны nltk с данными punkt_tab,
#    averaged_perceptron_tagger_eng, wordnet и matplotlib)
python scripts/text_markup.py corpus      # документы объектов
python scripts/text_markup.py auto        # предразметка всех уровней
python scripts/text_markup.py final       # + ручные правки -> annotations/text/markup/json/
python scripts/text_markup.py validate && python scripts/text_markup.py export
java -jar inception-app-webapp-38.4-standalone.jar   # INCEpTION на http://localhost:8080
python scripts/text_markup.py inception --password <пароль admin>   # проект + документы
python scripts/text_markup.py roundtrip --password <пароль admin>   # сверка выгрузки
python scripts/text_markup.py analyze     # docs/text_markup_analysis.md + графики

# 7. Разметка изображений (ЛР5; нужны tesseract-ocr с языками rus и eng, pytesseract, matplotlib)
python scripts/image_markup.py auto --tessdata <папка с rus.traineddata и eng.traineddata>
python scripts/image_markup.py final      # manual.json -> annotations/images/screenshots_coco.json
python scripts/image_markup.py export     # Label Studio JSON, Pascal VOC, YOLO, CSV
python scripts/annotation_tools.py validate
python scripts/image_markup.py analyze    # docs/image_markup_stats.md + графики
# Label Studio: проект и задачи с разметкой
label-studio start   # с LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true и LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=<корень репозитория>
python scripts/image_markup.py ls-import --url http://localhost:8080 --token <токен>
```

Скрипты проверялись на Python 3.13, Playwright 1.56 (Chromium 141), Node.js 22,
espeak-ng 1.53, Tesseract 5 (модели tessdata_best rus и eng), Label Studio Frontend 1.7.3.

---

## Примеры данных

### Текст — баг-репорт (`data/text/bug_reports/memory/hbase-13375331.json`, сокращено)

```json
{
  "report_id": "hbase-13375331",
  "category": "memory",
  "component_hint": "Backend",
  "source_project": "hbase",
  "tracker": "Apache Jira",
  "summary": "Memory leak from thrift server hashMap",
  "description": "I found when the scanner does not closed successfully, it remains forever in the hashMap that stores the state of the scanner. ...",
  "priority_original": "Minor",
  "severity_prelabel": "Minor",
  "license": "CC BY 4.0 (GitBugs, Patil 2025)"
}
```

Поля `category`, `component_hint` и `severity_prelabel` — **предразметка**,
полученная автоматически (по ключевым словам и по исходному приоритету
трекера). Это «примерная разметка» для полуавтоматической разметки в
Label Studio (см. задание 2); окончательные метки ставит разметчик.

### Текст — лог (`data/text/logs/authentication/openssh_authentication.log`)

```
Dec 10 06:55:46 LabSZ sshd[24200]: Invalid user webmaster from 173.234.31.186
Dec 10 06:55:46 LabSZ sshd[24200]: pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost=173.234.31.186
Dec 10 06:55:48 LabSZ sshd[24200]: Failed password for invalid user webmaster from 173.234.31.186 port 38926 ssh2
```

### Изображения

| `payment_03_double_charge.png` | `localization_01_mojibake_cp1251.png` |
|---|---|
| ![Двойное списание](data/images/screenshots/payment/payment_03_double_charge.png) | ![Кракозябры](data/images/screenshots/localization/localization_01_mojibake_cp1251.png) |
| Кнопка «Оплатить» не блокируется — двойной клик создал два платежа | Страница в UTF-8 отдана с `charset=windows-1251` |

| `database_01_sqlite_database_locked.png` | `security_03_reflected_xss.png` |
|---|---|
| ![database is locked](data/images/screenshots/database/database_01_sqlite_database_locked.png) | ![XSS](data/images/screenshots/security/security_03_reflected_xss.png) |
| `sqlite3.OperationalError: database is locked` при оплате заказа | Отражённый XSS: внедрённый скрипт прочитал `document.cookie` |

### Аудио (`data/audio/audio.csv`, первая строка)

| file | duration_sec | voice | transcript |
|---|---|---|---|
| `crash/crash_01.wav` | 17.49 | ru | «Здравствуйте. Приложение закрывается само, как только я открываю заказ номер сорок два…» |

Формат: WAV, PCM 16 бит, моно, 16 кГц.

---

## Аннотационная схема и аннотации

Аннотационная схема (ЛР2) определяет, что и как размечается в данных всех трёх
модальностей. Полное описание — **[docs/annotation_schema.md](docs/annotation_schema.md)**,
машиночитаемая версия — [annotation_schema/schema.json](annotation_schema/schema.json).

**Состав.** Одни и те же метки используются во всех модальностях:

| Группа | Метки |
|---|---|
| классы обращения | `ticket_type`, `category` (10 категорий + other), `component` (Backend, Frontend, Auth-service, Billing, Database), `severity` (Blocker, Critical, Major, Minor); команда-исполнитель вычисляется из компонента |
| структура репорта | разделы `TITLE`, `DESCRIPTION`, `STEPS`, `EXPECTED`, `ACTUAL`, `ENVIRONMENT`, `STACK_TRACE`, `CODE`, `NOTES`; элементы `STEP`, `FRAME` |
| сущности | `EXCEPTION`, `ERROR_MESSAGE`, `CODE_LOCATION`, `ERROR_CODE`, `ENV`, `UI_ELEMENT`, `METRIC`, `PII` (с атрибутами `kind`, `normalized`, `affected`) |
| связи | `HAS_MESSAGE`, `THROWN_AT`, `OBSERVED_IN` |
| изображения | области `ERROR_TEXT`, `STACK_TRACE`, `STATUS_INDICATOR`, `DEFECT_AREA`, `UI_ELEMENT`, `PII`, `CHART` (с 1.2) + текст области; связи `AFFECTS`, `EXPLAINS`, `CAUSED_BY` (с 1.2) |
| аудио | сегменты `GREETING`, `PROBLEM`, `ERROR_DETAILS`, `STEPS`, `ENVIRONMENT`, `IMPACT`, `CLOSING` + транскрипция |

**Уровни аннотаций:**

| Модальность | Уровень 1 | Уровень 2 | Уровень 3 |
|---|---|---|---|
| Структура документа | Тикет (классы) | Раздел | Шаг / кадр стека |
| Текст | Сущность | Атрибуты сущности | Связи |
| Логи | Файл | Событие (строка) | Сущности в строке |
| Изображения | Изображение (класс экрана) | Область | Текст и сущности в области |
| Аудио | Запись (классы) | Сегмент речи | Сущность в транскрипции |

**Форматы и размещение:**

| Модальность | Формат | Папка |
|---|---|---|
| баг-репорты | JSON со смещениями (совместим с Label Studio) + CoNLL-U | `annotations/text/reports/`, `annotations/text/conllu/` |
| логи | JSON Lines | `annotations/text/logs/` |
| изображения | COCO JSON (CVAT, Label Studio) | `annotations/images/` |
| аудио | Praat TextGrid + JSON | `annotations/audio/` |

**Эталонный набор.** Сейчас в `annotations/` лежат эталоны: 5 баг-репортов,
1 лог (40 строк), 2 скриншота (9 областей), 2 аудиозаписи — всего 122 сущности.
Они служат образцами для разметчиков; остальные данные размечаются в ЛР 3–5
(в ЛР5 размечены все 30 скриншотов, два эталона сохранили статус `gold`).
Список эталонов и что каждый показывает — в [annotations/README.md](annotations/README.md).

| Разметка скриншота `database_01` | Разметка скриншота `payment_03` |
|---|---|
| ![](docs/img/annotation_example_database_01.png) | ![](docs/img/annotation_example_payment_03.png) |

Пример — баг-репорт `spark-13289561` (фрагмент):

```json
{"labels": {"ticket_type": "bug", "category": "database", "component": "Database", "severity": "Major", "confidence": "low"},
 "entities": [
   {"id": "e5", "label": "EXCEPTION", "text": "org.apache.spark.sql.catalyst.parser.ParseException", "attributes": {"normalized": "ParseException"}},
   {"id": "e6", "label": "ERROR_MESSAGE", "text": "DataType time(7) is not supported.(line 1, pos 43)"}],
 "relations": [{"from": "e5", "to": "e6", "type": "HAS_MESSAGE"}]}
```

**Инструмент разметки** — Label Studio (выбран в задании 2): конфигурации
`annotation_schema/label_studio/*.xml` генерируются из схемы, задачи
`tasks_*.json` содержат предразметку (категория, компонент, критичность из полей
трекера), которую разметчик только исправляет.

**Верификация.** Автоматическая проверка каждой аннотации по схеме
(`annotation_tools.py validate`, [последний отчёт](docs/annotation_validation_report.md)),
перекрёстная разметка 20 % данных двумя разметчиками с порогами согласованности
(каппа Коэна ≥ 0,8 для категории, F1 сущностей ≥ 0,8, IoU ≥ 0,7 для рамок,
WER расшифровки ≤ 10 %), выборочная проверка каждого 5-го файла, журнал —
[annotations/VERIFICATION.md](annotations/VERIFICATION.md).

---

## Разметка текстовых данных (ЛР3)

**Тексты (шаг 1).** Для каждого из 10 объектов собран один документ из 2–4 баг-репортов этой категории
объёмом 1956–2414 символов (≈0,5–1 страница). Каждый репорт прочитан вручную: в документ попадают только
репорты, которые действительно описывают объект. Из 36 прочитанных кандидатов у 7 категория, выставленная
по ключевым словам, оказалась ошибочной или спорной (`annotations/text/markup/corpus.json`).

**Инструмент (шаг 2).** INCEpTION 38.4. Проект `lr3-bug-triage-text` со своими слоями создаётся из схемы
(`text_markup.py inception`), документы загружаются через Remote API в формате WebAnno TSV 3.3. Выгрузка
из INCEpTION совпадает с исходной разметкой без потерь.

**Уровни и теги (шаги 3–5):**

| Уровень | Что | Объём |
|---|---|---|
| 1. Документ | документ объекта и баг-репорты с классами (тип, категория, компонент, критичность) | 10 документов, 27 репортов |
| 2. Абзац | абзац и тип раздела (TITLE, STEPS, ACTUAL, EXPECTED, ENVIRONMENT…) | 191 абзац |
| 3. Токены и части речи | предложения, токены, UPOS + XPOS (Penn), леммы | 371 предложение, 3915 токенов |
| 4. Сущности | 14 типов, в том числе новые PRODUCT, TECHNOLOGY, SYMPTOM, FEATURE, LOCALE, ISSUE_REF; 72 % названий связаны с DBpedia | 407 сущностей |
| 5. Связи | SYMPTOM_OF, CAUSED_BY, OBSERVED_IN, VERSION_OF, HAS_MESSAGE, THROWN_AT | 87 связей |

Разметка полуавтоматическая. Предразметка (NLTK + правила + словарь DBpedia) даёт для сущностей
F1 = 0,88 относительно итоговой разметки. Разметчик проверил все документы и внёс 249 правок
(`annotations/text/markup/manual_edits.json`). DBpedia Spotlight из рабочей среды был недоступен
(robots.txt запрещает автоматические запросы), поэтому ссылки DBpedia проставлены по словарю ресурсов.

| Разметка в INCEpTION (`performance`) | Части речи и разделы (`memory`) |
|---|---|
| ![](docs/img/lr3_inception_performance.png) | ![](docs/img/lr3_inception_pos.png) |

**Анализ и обновление схемы (шаг 6)** — [docs/text_markup_analysis.md](docs/text_markup_analysis.md). Найденные
зависимости между уровнями:

* тип раздела предсказывает тип сущности: ENV почти только в ENVIRONMENT, UI_ELEMENT — в STEPS, симптомы — в TITLE и ACTUAL;
* симптомы в EXPECTED отрицаются («Firefox does not freeze») → атрибут `SYMPTOM.polarity`;
* ОС и браузер из User-Agent в отчётах Thunderbird — окружение автора, а не продукта → `ENV.source`;
* раздел и сущность исправляют ошибки теггера: повелительные глаголы в STEPS, глаголы-симптомы →
  точность UPOS с 88,9 % до 97,7 %;
* авторы называют причины симптомов → связь `CAUSED_BY`; для локализации нужна сущность `LOCALE`.

Аннотационная схема обновлена до версии 1.1 (обратно совместима, эталоны ЛР2 проходят проверку):
[раздел 8 описания схемы](docs/annotation_schema.md#8-многоуровневая-разметка-текста-лр3).

---

## Разметка изображений (ЛР5)

Подробно — **[docs/image_markup_analysis.md](docs/image_markup_analysis.md)**, статистика —
[docs/image_markup_stats.md](docs/image_markup_stats.md).

**Инструмент и данные (шаг 1).** Label Studio, конфигурация `annotation_schema/label_studio/images.xml`
генерируется из схемы. Все 30 скриншотов размечены в редакторе разметки Label Studio; задачи для импорта
с разметкой и предразметкой — `annotations/images/markup/labelstudio/tasks.json`
(`image_markup.py ls-import` создаёт проект в запущенном Label Studio). Выгрузка из редактора совпадает
с исходной разметкой во всех 30 задачах.

**Классы и признаки (шаг 2):**

| Уровень | Что | Объём |
|---|---|---|
| 1. Изображение | `category`, `component`, `screen_type` + новые признаки `is_defect`, `severity`, `device`, `theme` | 30 изображений (27 с дефектом, 3 — ожидаемое поведение) |
| 2. Область | рамки `ERROR_TEXT` 33, `DEFECT_AREA` 28, `PII` 26, `STATUS_INDICATOR` 18, `UI_ELEMENT` 15, `STACK_TRACE` 9, `CHART` 2; связи между областями | 131 область, 29 связей |
| 3. Деталь | дословный текст области, атрибуты (`defect_type`, `expected_text`, `kind`, `trend`…), сущности `ERROR_MESSAGE`, `ERROR_CODE`, `EXCEPTION`, `METRIC` | 114 текстов, 80 сущностей |

**Предразметка (шаг 3).** Tesseract OCR 5 (LSTM, rus+eng) + правила: тип экрана, устройство и тема
определены верно для всех 30 изображений, категория — для 29; области — точность 0,69, полнота 0,32
(IoU ≥ 0,5), ошибки OCR в тексте — 2,5 %. Текстовые области предразметка находит, визуальные
(элементы интерфейса, графики, перекрытия) — нет, их разметчик рисует сам.

| Неверная сумма и NaN (`payment_02`) | IDOR: чужой счёт (`security_02`) | Переполнение на мобильном (`ui_layout_01`) |
|---|---|---|
| ![](docs/img/lr5_markup_payment_02_invoice_nan_total.png) | ![](docs/img/lr5_markup_security_02_idor_foreign_invoice.png) | ![](docs/img/lr5_markup_ui_layout_01_button_overflow.png) |

**Проверка (шаг 5).** Проверка по схеме (`annotation_tools.py validate`) — без ошибок; каждое
изображение проверено визуально с наложенными областями (`image_markup.py overlay`); эталоны ЛР2
(`database_01`, `payment_03`) размечены заново вслепую: классы, метки и тексты совпали полностью,
расхождения рамок разобраны в [журнале верификации](annotations/VERIFICATION.md).

**Экспорт (шаг 6):** COCO JSON (основной, `annotations/images/screenshots_coco.json`), Label Studio JSON,
Pascal VOC XML, YOLO, CSV (`annotations/images/markup/`).

**Изменения аннотационной схемы — версия 1.2**
([раздел 9 описания схемы](docs/annotation_schema.md#9-разметка-изображений-лр5)): признаки изображения
`is_defect`, `severity`, `device`, `theme`; область `CHART`; `STATUS_INDICATOR.kind`; типы дефектов
`injected_content`, `data_exposure`, `threshold_exceeded` и `expected_text`; `PII` `address`, `organization`;
связи между областями `AFFECTS`, `EXPLAINS`, `CAUSED_BY`; правило совпадения рамок «IoU ≥ 0,7 или стороны
±5 px». Главные выводы анализа: тип экрана определяет набор областей; компонент нельзя выводить из категории
(6 из 30 изображений); три экрана с ошибкой — не дефекты.

**Обновление данных.** Четыре скриншота страниц ошибки 500 (`crash_03`, `database_01`–`03`) пересняты тем
же скриптом `make_screenshots.py`: в трейсбеке на экране был виден локальный путь к рабочей папке, теперь
стенд запускается из нейтральной папки `/home/qa/bug-triage-dataset`. Остальное на скриншотах не изменилось,
контрольные суммы обновлены.

---

## Проверка корректности данных

`scripts/validate_dataset.py` проверяет, что для каждой категории есть данные
всех трёх модальностей; каждый JSON читается и содержит обязательные поля;
каждый PNG открывается и не повреждён; каждый WAV — моно 16 кГц длительностью
больше 2 с; CSV-метаданные описывают все файлы и только существующие;
SHA-256 всех 166 файлов данных совпадают с `data/checksums.sha256`.
Результат последней проверки — в [docs/validation_report.md](docs/validation_report.md).

---

## Лицензия

* Код (`scripts/`) — MIT.
* Баг-репорты — CC BY 4.0, источник GitBugs (Patil A. *GitBugs: Bug Reports for
  Duplicate Detection, Retrieval Augmented Generation, Triage, and More*, 2025).
* Фрагменты логов — по условиям LogHub (свободно для исследовательских и
  учебных целей, со ссылкой на Zhu J. et al. *Loghub: A Large Collection of
  System Log Datasets for AI-driven Log Analytics*, ISSRE 2023).
* Скриншоты и аудиозаписи — CC BY 4.0.
* Аннотации (`annotations/`) и аннотационная схема — CC BY 4.0.

Подробнее — в [LICENSE](LICENSE).

## Контакты

Автор: lebowskiybig — lebowskiybig@gmail.com.
Вопросы и предложения — через Issues репозитория или по почте.

## Презентация

* ЛР2 «Проектирование аннотационной схемы» (шаг 8) — слайды о процессе
  проектирования схемы, требованиях, уровнях, форматах и верификации:
  [docs/presentation/LR2_annotation_schema.pptx](docs/presentation/LR2_annotation_schema.pptx).
* ЛР3 «Разметка текстовых данных» (шаг 8) — презентация ЛР2, дополненная слайдами о процессе
  разметки текста, анализе результатов и изменениях схемы:
  [docs/presentation/LR3_text_markup.pptx](docs/presentation/LR3_text_markup.pptx).
* ЛР5 «Разметка изображений» (шаг 8) — презентация ЛР3, дополненная слайдами о процессе разметки
  изображений, анализе результатов и изменениях схемы 1.2:
  [docs/presentation/LR5_image_markup.pptx](docs/presentation/LR5_image_markup.pptx).
