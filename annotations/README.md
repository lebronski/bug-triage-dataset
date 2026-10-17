# Аннотации

Разметка данных из `data/` по аннотационной схеме v1.1 (эталоны ЛР2 размечены по v1.0, она совместима с v1.1)
([описание](../docs/annotation_schema.md), [schema.json](../annotation_schema/schema.json)).
Для каждой модальности — своя папка; внутри папки повторяется деление на категории, как в `data/`.

| Папка | Модальность | Формат | Что сейчас лежит |
|---|---|---|---|
| `text/reports/<category>/` | баг-репорты | JSON со смещениями (уровни 1–3) | 5 эталонов |
| `text/conllu/` | баг-репорты | CoNLL-U, BIO-метки в MISC — генерируется командой `conllu` | 5 файлов |
| `text/logs/<category>/` | логи | JSON Lines, строка = событие | 1 эталон (40 строк) |
| `images/` | скриншоты | COCO JSON (`screenshots_coco.json`) | 2 эталона, 9 областей |
| `audio/<category>/` | голосовые обращения | Praat TextGrid + JSON | 2 эталона |
| `text/markup/` | текстовые документы объектов (ЛР3) | JSON со смещениями, WebAnno TSV 3.3, UIMA CAS XMI, CoNLL-U | 10 документов: 27 репортов, 3915 токенов, 407 сущностей, 87 связей (`draft`) |

Файл аннотации называется так же, как файл данных: `data/text/bug_reports/memory/hbase-13375331.json`
→ `annotations/text/reports/memory/hbase-13375331.json` (папка — по **размеченной** категории, она может
отличаться от предразметки), `data/audio/voice_reports/crash/crash_02.wav` → `annotations/audio/crash/crash_02.TextGrid`.

## Эталонный набор (gold)

Эталоны размечены вручную и используются для обучения разметчиков и проверки их качества
(раздел 6 схемы). Каждый показывает типичный или трудный случай:

| Файл | Что показывает |
|---|---|
| `text/reports/crash/spark-13547998.json` | трассировка стека: `FRAME`, `THROWN_AT`; компонент исправлен Backend → Database |
| `text/reports/payment/firefox-1844035.json` | структурированный репорт со всеми разделами; `ENV` с `affected=yes/no` |
| `text/reports/memory/hbase-13375331.json` | короткий репорт без трассировки; предложение исправления → `NOTES` |
| `text/reports/database/spark-13289561.json` | `EXCEPTION` + `ERROR_MESSAGE` + `HAS_MESSAGE`; разметка `{color}` вне границ; `confidence=low` |
| `text/reports/other/thunderbird-1643899.json` | ошибочная предразметка `payment` → `not_a_bug`, `other`, `severity=null` |
| `text/logs/authentication/openssh_authentication.jsonl` | события входа: `invalid_user`, `auth_failure`, `break_in_attempt`; PII (ip, host, username) |
| `images/screenshots_coco.json`: `database_01` | страница ошибки 500: `ERROR_TEXT`, `STATUS_INDICATOR`, вложенная область в `STACK_TRACE` |
| `images/screenshots_coco.json`: `payment_03` | логический дефект: `DEFECT_AREA` (duplicate), `UI_ELEMENT`, `PII` (карта) |
| `audio/crash/crash_02.*` | сегменты GREETING → PROBLEM → ERROR_DETAILS → STEPS; нормализация «пятьсот» → 500 |
| `audio/ui_layout/ui_layout_01.*` | сегмент ENVIRONMENT; ENV `device` с `affected=no` |

Наглядно — разметка скриншотов: [database_01](../docs/img/annotation_example_database_01.png),
[payment_03](../docs/img/annotation_example_payment_03.png).

## Статусы

`meta.status`: `draft` — размечено, не проверено; `reviewed` — проверено вторым участником;
`gold` — эталон. Разметка остальных данных выполняется в лабораторных работах 3–5.

## Как добавлять разметку

```bash
# конфигурации Label Studio и задачи с предразметкой
python scripts/annotation_tools.py configs
python scripts/annotation_tools.py tasks

# после разметки: проверка по схеме и выгрузка CoNLL-U
python scripts/annotation_tools.py validate
python scripts/annotation_tools.py conllu

# согласованность двух разметчиков
python scripts/annotation_tools.py agree --a ../annotations_A --b ../annotations_B
```

Процедура проверки и результаты — в [VERIFICATION.md](VERIFICATION.md).
