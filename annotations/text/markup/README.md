# Многоуровневая разметка текстовых данных (ЛР3)

Один документ на каждый из 10 объектов (категорий дефектов). Схема — v1.1,
[раздел 8](../../../docs/annotation_schema.md#8-многоуровневая-разметка-текста-лр3); анализ —
[docs/text_markup_analysis.md](../../../docs/text_markup_analysis.md).

| Путь | Что | Как получено |
|---|---|---|
| `corpus.json` | состав документов: репорты, их смещения, предразметка и итоговые классы; отклонённые кандидаты с причинами | `text_markup.py corpus` |
| `documents/<объект>.txt` | текст документа (репорты через пустую строку) | `corpus` |
| `auto/<объект>.json` | автоматическая предразметка всех уровней (`upos_tagger` — исходный тег NLTK) | `auto` |
| `manual_edits.json` | ручные правки разметчика: сущности, атрибуты, разделы, связи, части речи | вручную |
| `json/<объект>.json` | **итоговая разметка** (основной формат), `meta.preannotation_quality` — сравнение с предразметкой | `final` |
| `tsv/<объект>.tsv` | WebAnno TSV 3.3 для импорта в INCEpTION/WebAnno | `export` |
| `conllu/<объект>.conllu` | CoNLL-U: LEMMA, UPOS, XPOS; MISC: `NER` (BIO), `MSG` (ERROR_MESSAGE), `KB` (DBpedia) | `export` |
| `inception/lr3-bug-triage-text.zip` | экспорт проекта INCEpTION (слои, теги, документы, разметка) | `inception` |
| `inception/project_layers.json` | описание слоёв и наборов тегов, построенное из схемы | `inception` |
| `inception/xmi/<объект>.zip` | UIMA CAS XMI + TypeSystem, экспорт из INCEpTION | `inception` |
| `inception/export_tsv/<объект>.tsv` | TSV, выгруженный из INCEpTION (для сверки `roundtrip`) | `roundtrip` |

Как открыть в INCEpTION: *Administration → Import project* → `inception/lr3-bug-triage-text.zip`
или `python scripts/text_markup.py inception --password <пароль>` для запущенного сервера.
Статус разметки — `draft` (проверена одним разметчиком), перекрёстная проверка — следующая итерация
([VERIFICATION.md](../../VERIFICATION.md)).
