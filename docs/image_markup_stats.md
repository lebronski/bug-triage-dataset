# Анализ разметки изображений (ЛР5)

Сгенерировано `python scripts/image_markup.py analyze` по итоговой разметке (`annotations/images/screenshots_coco.json`) и предразметке (`annotations/images/markup/auto/predictions.json`).

## 1. Объём разметки

| Показатель | Значение |
|---|---:|
| изображений | 30 |
| областей (рамок) | 131 |
| в среднем областей на изображение | 4.4 |
| областей с текстом (`ocr_text`) | 114 |
| сущностей в тексте областей (уровень 3) | 80 |
| связей между областями | 29 |
| изображений с дефектом / ожидаемое поведение | 27 / 3 |

### Области по меткам

| Метка | Областей | Средняя доля площади кадра, % | Атрибуты |
|---|---:|---:|---|
| `ERROR_TEXT` | 33 | 1.5 |  |
| `STACK_TRACE` | 9 | 24.3 |  |
| `STATUS_INDICATOR` | 18 | 0.4 | http_status 7, exit_code 3, net_error 3, counter 2, loading 2, timer 1 |
| `DEFECT_AREA` | 28 | 2.0 | untranslated 7, wrong_format 5, wrong_value 3, overflow 3, mojibake 2, injected_content 2, duplicate 1, endless_loading 1, threshold_exceeded 1, data_exposure 1, overlap 1, wrong_layer 1 |
| `UI_ELEMENT` | 15 | 1.3 | button 10, field 4, other 1 |
| `PII` | 26 | 0.4 | host 12, card 4, person_name 3, username 2, email 2, organization 1, address 1, token 1 |
| `CHART` | 2 | 17.8 | growth 2, line 1, bar 1 |

![Области по категориям](img/lr5_regions_by_category.png)

### Классы изображений (уровень 1)

* **component**: Backend — 12, Frontend — 8, Billing — 4, Auth-service — 3, Database — 3
* **screen_type**: ui_screen — 16, error_page — 5, console — 4, browser_error — 3, api_response — 2
* **severity**: Critical — 10, Major — 9, Blocker — 5, null (не дефект) — 3, Minor — 3
* **device**: desktop — 28, mobile — 2
* **theme**: light — 26, dark — 4

### Сущности в тексте областей (уровень 3)

| Сущность | Количество | Примеры |
|---|---:|---|
| `ERROR_MESSAGE` | 26 | `Unauthorized`, `Неверный логин или пароль`, `The access token expired at 2026-10-17T09:12:44Z`, `Forbidden` |
| `ERROR_CODE` | 19 | `401`, `invalid_token`, `403`, `1` |
| `EXCEPTION` | 18 | `TypeError`, `AttributeError`, `OperationalError`, `sqlite3.OperationalError` |
| `METRIC` | 17 | `2`, `15 минут`, `5 минут`, `31` |

### Связи между областями

| Связь | Откуда → куда | Количество |
|---|---|---:|
| `EXPLAINS` | STATUS_INDICATOR → ERROR_TEXT | 12 |
| `AFFECTS` | DEFECT_AREA → UI_ELEMENT | 7 |
| `EXPLAINS` | ERROR_TEXT → UI_ELEMENT | 3 |
| `CAUSED_BY` | DEFECT_AREA → UI_ELEMENT | 2 |
| `EXPLAINS` | STATUS_INDICATOR → DEFECT_AREA | 2 |
| `EXPLAINS` | STATUS_INDICATOR → CHART | 1 |
| `EXPLAINS` | ERROR_TEXT → DEFECT_AREA | 1 |
| `CAUSED_BY` | DEFECT_AREA → DEFECT_AREA | 1 |

### Типы экрана × метки областей

| Тип экрана | ERROR_TEXT | STACK_TRACE | STATUS_INDICATOR | DEFECT_AREA | UI_ELEMENT | PII | CHART |
|---|---:|---:|---:|---:|---:|---:|---:|
| `api_response` | 2 | 0 | 0 | 1 | 0 | 4 | 0 |
| `browser_error` | 6 | 0 | 3 | 0 | 0 | 1 | 0 |
| `console` | 4 | 5 | 3 | 0 | 0 | 11 | 0 |
| `error_page` | 13 | 4 | 5 | 0 | 0 | 2 | 0 |
| `ui_screen` | 8 | 0 | 7 | 27 | 15 | 8 | 2 |

## 2. Качество полуавтоматической предразметки (шаг 3)

Предразметка: Tesseract OCR 5 (LSTM, языки rus+eng) находит строки текста и их рамки, правила по регулярным выражениям назначают метки областей, сущности и классы изображения. Сравнение с итоговой ручной разметкой:

| Что | Значение |
|---|---:|
| точность класса `category` | 97% |
| точность класса `component` | 80% |
| точность класса `screen_type` | 100% |
| точность класса `device` | 100% |
| точность класса `theme` | 100% |
| точность класса `is_defect` | 90% |
| области: точность / полнота / F1 при IoU ≥ 0.5 | 0.69 / 0.32 / 0.44 |
| области: точность / полнота / F1 при IoU ≥ 0.7 | 0.56 / 0.26 / 0.35 |
| средний IoU совпавших областей | 0.88 |
| CER текста совпавших областей (доля ошибочных символов OCR) | 2.5% |
| сущности (метка + текст) в совпавших областях: найдено верно / всего в разметке | 15 / 80 |

| Метка | в разметке | в предразметке | совпало (IoU ≥ 0,5) |
|---|---:|---:|---:|
| `ERROR_TEXT` | 33 | 26 | 16 |
| `STACK_TRACE` | 9 | 7 | 1 |
| `STATUS_INDICATOR` | 18 | 8 | 7 |
| `DEFECT_AREA` | 28 | 6 | 4 |
| `UI_ELEMENT` | 15 | 0 | 0 |
| `PII` | 26 | 14 | 14 |
| `CHART` | 2 | 0 | 0 |

![Качество предразметки](img/lr5_preannotation_quality.png)

Исправленные разметчиком классы изображений:

| Изображение | Класс | Предразметка | Итог |
|---|---|---|---|
| `localization_01_mojibake_cp1251` | component | Frontend | Backend |
| `memory_03_js_heap_growth` | component | Backend | Frontend |
| `payment_02_invoice_nan_total` | component | Billing | Frontend |
| `performance_03_long_task_kanban` | component | Backend | Frontend |
| `security_01_cert_self_signed` | component | Backend | Billing |
| `security_02_idor_foreign_invoice` | category | ui_layout | security |
| `security_02_idor_foreign_invoice` | component | Frontend | Billing |
