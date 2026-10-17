"""Справочник 10 объектов (категорий дефектов) проекта.

Каждая категория — это «объект» в терминах лабораторной работы: для неё
собираются тексты (баг-репорты и логи), изображения (скриншоты) и аудио
(голосовые баг-репорты). Поле component связывает категорию с компонентами
системы из постановки задачи (Backend, Frontend, Auth-service, Billing, Database).

Порядок в списке важен: репорт относится к первой категории, ключевые слова
которой нашлись в его заголовке (так снимается неоднозначность).
"""

CATEGORIES = [
    {
        "id": "payment",
        "name_ru": "Ошибки оплаты и биллинга",
        "component": "Billing",
        "summary_regex": r"\b(credit[- ]?cards?|payments?|billing|purchas\w*|paypal|card number|cvv)\b",
    },
    {
        "id": "security",
        "name_ru": "Уязвимости и TLS/сертификаты",
        "component": "Backend",
        "summary_regex": r"\b(security|vulnerab\w*|xss|csrf|injection|cve-\d+|ssl|tls|certificates?)\b",
    },
    {
        "id": "authentication",
        "name_ru": "Аутентификация и авторизация",
        "component": "Auth-service",
        "summary_regex": r"\b(log ?in|logon|authenticat\w*|kerberos|passwords?|credentials?|oauth\w*|sasl|keytab)\b",
    },
    {
        "id": "localization",
        "name_ru": "Локализация и кодировки",
        "component": "Frontend",
        "summary_regex": r"\b(locale|locali[sz]\w*|translat\w*|i18n|l10n|unicode|utf-?8|encoding|charset|non-ascii|cyrillic|rtl)\b",
    },
    {
        "id": "memory",
        "name_ru": "Утечки памяти и OutOfMemory",
        "component": "Backend",
        "summary_regex": r"(memory leak|outofmemory|\boom\b|out of memory|heap space|leaks? memory|leaking)",
    },
    {
        "id": "database",
        "name_ru": "Ошибки базы данных и транзакций",
        "component": "Database",
        "summary_regex": r"\b(database|jdbc|sqlite|deadlocks?|transactions?|metastore|postgres\w*|mysql)\b",
    },
    {
        "id": "network",
        "name_ru": "Сеть и соединения",
        "component": "Backend",
        "summary_regex": r"(connection (refused|reset|timed? ?out|closed|lost)|socket\w*|\bproxy\b|\bdns\b|network|unknownhost)",
    },
    {
        "id": "performance",
        "name_ru": "Производительность и зависания",
        "component": "Backend",
        "summary_regex": r"\b(slow\w*|performance|hangs?|hanging|freez\w*|latency|high cpu|100% cpu|sluggish)\b",
    },
    {
        "id": "crash",
        "name_ru": "Падения и необработанные исключения",
        "component": "Backend",
        "summary_regex": r"\b(crash\w*|segfault|nullpointerexception|npe|core dump|sigsegv|sigabrt)\b",
    },
    {
        "id": "ui_layout",
        "name_ru": "Вёрстка и отображение интерфейса",
        "component": "Frontend",
        "summary_regex": r"\b(layout|misalign\w*|overlap\w*|truncat\w*|cut off|font|scrollbars?|tooltips?|icons? (is|are)|button|alignment|css)\b",
    },
]

CATEGORY_IDS = [c["id"] for c in CATEGORIES]
