#!/usr/bin/env python3
"""Многоуровневая разметка текстовых данных (Лабораторная работа №3).

Конвейер:
  corpus    — шаг 1: собрать по одному текстовому документу на каждый из 10 объектов
              (категорий дефектов) из отобранных баг-репортов, близкого объёма
  auto      — шаги 3–5: автоматическая предразметка всех уровней
              (документ → баг-репорт → абзац/раздел → предложение → токен → часть речи,
              лемма → именованные сущности со ссылками на DBpedia → связи)
  final     — шаг 5: применить ручные правки разметчика (annotations/text/markup/manual_edits.json)
              к предразметке и получить итоговую разметку
  export    — сохранить итоговую разметку в форматах WebAnno TSV 3.3 (импорт в INCEpTION / WebAnno)
              и CoNLL-U (UPOS, XPOS, лемма, BIO-метки сущностей)
  inception — шаг 2: собрать проект INCEpTION (слои + теги) и загрузить в него документы
              с разметкой через Remote API запущенного INCEpTION
  analyze   — шаг 6: статистика и зависимости между уровнями разметки -> docs/text_markup_analysis.md
  validate  — проверка итоговой разметки по схеме
  spotlight — (необязательно) сравнить связывание сущностей с DBpedia Spotlight, если сервис доступен

Зависимости: nltk (+ данные punkt_tab, averaged_perceptron_tagger_eng, wordnet), matplotlib.

Примеры:
  python scripts/text_markup.py corpus
  python scripts/text_markup.py auto && python scripts/text_markup.py final
  python scripts/text_markup.py export
  python scripts/text_markup.py inception --url http://localhost:8080 --user admin --password ***
  python scripts/text_markup.py analyze
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "text" / "bug_reports"
SCHEMA_PATH = ROOT / "annotation_schema" / "schema.json"
OUT = ROOT / "annotations" / "text" / "markup"
DOCS = ROOT / "docs"
ANNOTATOR = "lebowskiybig"

# ------------------------------------------------------------------ шаг 1
# Отбор сделан вручную: репорты прочитаны, в документ объекта попадают только репорты,
# которые действительно описывают этот объект (предразметка категорий по ключевым словам
# ошибается). Итоговые классы каждого репорта проставлены разметчиком по критериям схемы.
# (report_id, ticket_type, category, component, severity, confidence)
SELECTION = {
    "authentication": [
        ("thunderbird-1825223", "bug", "authentication", "Auth-service", "Major", "high"),
        ("hadoop-13513595", "feature_request", "authentication", "Auth-service", None, "low"),
    ],
    "crash": [
        ("hadoop-13544320", "bug", "crash", "Auth-service", "Critical", "high"),
        ("vscode-198363", "bug", "crash", "Frontend", "Minor", "high"),
    ],
    "database": [
        ("firefox-1842613", "bug", "database", "Database", "Major", "low"),
        ("spark-13553213", "bug", "database", "Database", "Major", "high"),
    ],
    "localization": [
        ("firefox-1946605", "bug", "localization", "Frontend", "Minor", "high"),
        ("thunderbird-1777670", "bug", "localization", "Frontend", "Minor", "high"),
        ("firefox-1866316", "bug", "localization", "Frontend", "Minor", "high"),
    ],
    "memory": [
        ("firefox-1635681", "bug", "memory", "Frontend", "Major", "high"),
        ("hbase-13375331", "bug", "memory", "Backend", "Minor", "high"),
        ("thunderbird-1737223", "bug", "memory", "Frontend", "Critical", "high"),
    ],
    "network": [
        ("vscode-20567", "bug", "network", "Backend", "Critical", "low"),
        ("spark-13335362", "feature_request", "network", "Backend", None, "high"),
        ("thunderbird-1757761", "bug", "network", "Frontend", "Minor", "high"),
    ],
    "payment": [
        ("firefox-1851799", "bug", "payment", "Billing", "Minor", "high"),
        ("firefox-1910229", "bug", "payment", "Billing", "Major", "low"),
        ("thunderbird-1803720", "bug", "payment", "Billing", "Major", "low"),
    ],
    "performance": [
        ("thunderbird-1857818", "bug", "performance", "Frontend", "Major", "high"),
        ("firefox-1715640", "bug", "performance", "Frontend", "Critical", "high"),
        ("thunderbird-1859093", "bug", "performance", "Frontend", "Major", "high"),
    ],
    "security": [
        ("cassandra-13426900", "bug", "security", "Database", "Blocker", "high"),
        ("firefox-1668045", "bug", "security", "Frontend", "Major", "low"),
        ("cassandra-13537599", "bug", "security", "Database", "Blocker", "high"),
        ("firefox-1737574", "bug", "security", "Frontend", "Minor", "high"),
    ],
    "ui_layout": [
        ("thunderbird-1736244", "bug", "ui_layout", "Frontend", "Major", "low"),
        ("firefox-1741198", "bug", "ui_layout", "Frontend", "Minor", "high"),
    ],
}

# Репорты, которые были прочитаны при отборе, но не включены в документ своего объекта.
REJECTED = {
    "firefox-1741198": "предразметка network ошибочна: дефект вёрстки кнопок панели Network Settings — перенесён в документ ui_layout",
    "firefox-1625967": "предразметка security ошибочна: наложение ссылок в уведомлении — это ui_layout",
    "firefox-1866677": "предразметка payment спорна: дефект вызван функцией перевода страницы (localization), PayPal лишь пострадавший сайт",
    "firefox-1768671": "предразметка payment ошибочна: имя файла PDF при сохранении, к оплате не относится",
    "thunderbird-1729485": "предразметка crash ошибочна: после сбоя ОС почта не загружается — скорее network",
    "firefox-1831327": "предразметка ui_layout ошибочна: неверная подсказка кнопки, вёрстка не нарушена",
    "thunderbird-1858445": "предразметка ui_layout спорна: ошибка разбора CSS в тесте, без видимого дефекта",
    "thunderbird-1643899": "not_a_bug (эталон ЛР2)",
    "firefox-1895114": "только номера кадров стека, связного текста почти нет",
    "spark-13442742": "в основном код на Python, мало связного текста",
}
REPORT_SEP = "\n\n"
TARGET_CHARS = (1800, 2600)   # ~0,5–1 страницы текста (шаг 1.3)


def load_report(report_id):
    hit = next(DATA.glob(f"*/{report_id}.json"))
    return json.loads(hit.read_text(encoding="utf-8"))


def cmd_corpus(args):
    (OUT / "documents").mkdir(parents=True, exist_ok=True)
    corpus = {"description": "ЛР3: по одному текстовому документу на каждый объект (категорию дефекта). "
                             "Документ = баг-репорты категории, соединённые пустой строкой; текст репорта = summary + \\n\\n + description.",
              "target_chars": TARGET_CHARS, "documents": [], "rejected_on_selection": REJECTED}
    for cat, reports in SELECTION.items():
        parts, units, pos = [], [], 0
        for rid, ttype, category, comp, sev, conf in reports:
            rec = load_report(rid)
            text = rec["summary"] + "\n\n" + rec["description"].rstrip()
            if parts:
                pos += len(REPORT_SEP)
            units.append({"report_id": rid, "start": pos, "end": pos + len(text),
                          "source_file": str(next(DATA.glob(f"*/{rid}.json")).relative_to(ROOT)),
                          "source_project": rec.get("source_project"), "tracker": rec.get("tracker"),
                          "prelabels": {"category": rec.get("category"), "component_hint": rec.get("component_hint"),
                                        "severity_prelabel": rec.get("severity_prelabel")},
                          "labels": {"ticket_type": ttype, "category": category, "component": comp,
                                     "severity": sev, "confidence": conf}})
            parts.append(text)
            pos += len(text)
        doc = REPORT_SEP.join(parts)
        assert all(doc[u["start"]:u["end"]].startswith(load_report(u["report_id"])["summary"]) for u in units)
        (OUT / "documents" / f"{cat}.txt").write_text(doc, encoding="utf-8")
        corpus["documents"].append({"doc_id": cat, "category": cat, "file": f"documents/{cat}.txt",
                                    "chars": len(doc), "words": len(doc.split()), "reports": units})
    (OUT / "corpus.json").write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    sizes = [d["chars"] for d in corpus["documents"]]
    for d in corpus["documents"]:
        print(f'{d["doc_id"]:15s} {d["chars"]:5d} симв. {d["words"]:4d} слов, репортов: {len(d["reports"])}')
    print(f"объём: {min(sizes)}–{max(sizes)} символов, max/min = {max(sizes)/min(sizes):.2f}")


# ------------------------------------------------------------------ токенизация
TOKEN_RE = re.compile(r"""
 (?P<url>(?:https?://|www\.)[^\s<>()\[\]"'`“”]+[^\s<>()\[\]"'`“”.,;:!?*])
|(?P<about>about:[A-Za-z][\w#-]*)
|(?P<email>[\w.+-]+@[\w-]+\.[\w.-]*\w)
|(?P<cve>CVE-\d{4}-\d+)
|(?P<issue>\b[A-Z][A-Z]+-\d+\b)
|(?P<ident>[A-Za-z_$][\w$]*(?:(?:\.|::|\#|/|\$)[A-Za-z_$0-9][\w$-]*)+(?:\(\))?(?::\d+)?)
|(?P<ver>\d+(?:\.\d+)+[a-z]*\d*(?:-[a-z]+)?|\d+[a-z]+\d+\w*)
|(?P<neg>[A-Za-z]+(?=n't\b)|n't\b)
|(?P<clitic>'(?:s|m|ve|re|ll|d)\b)
|(?P<word>[^\W_]+(?:[-_][^\W_]+)*_?)
|(?P<punct>\.\.\.|\*\*|```|[^\s\w])
""", re.X)

PENN2UPOS = {
    "CC": "CCONJ", "CD": "NUM", "DT": "DET", "EX": "PRON", "FW": "X", "IN": "ADP", "JJ": "ADJ",
    "JJR": "ADJ", "JJS": "ADJ", "LS": "X", "MD": "AUX", "NN": "NOUN", "NNS": "NOUN", "NNP": "PROPN",
    "NNPS": "PROPN", "PDT": "DET", "POS": "PART", "PRP": "PRON", "PRP$": "PRON", "RB": "ADV",
    "RBR": "ADV", "RBS": "ADV", "RP": "ADP", "SYM": "SYM", "TO": "PART", "UH": "INTJ", "VB": "VERB",
    "VBD": "VERB", "VBG": "VERB", "VBN": "VERB", "VBP": "VERB", "VBZ": "VERB", "WDT": "PRON",
    "WP": "PRON", "WP$": "PRON", "WRB": "ADV", "$": "SYM", "#": "SYM", "``": "PUNCT", "''": "PUNCT",
    "(": "PUNCT", ")": "PUNCT", ",": "PUNCT", ".": "PUNCT", ":": "PUNCT",
}
AUX_LEMMAS = {"be", "have", "do", "will", "would", "can", "could", "shall", "should", "may", "might", "must"}
CLITIC_LEMMA = {"n't": "not", "'m": "be", "'re": "be", "'ve": "have", "'ll": "will", "'d": "would"}
SYMBOLS = set("+=<>|~^%&@$#*/\\")


def _nltk():
    import nltk
    from nltk.stem import WordNetLemmatizer
    return nltk, WordNetLemmatizer()


def tokenize(text, start, end):
    toks = []
    for m in TOKEN_RE.finditer(text, start, end):
        if m.lastgroup == "ident" and re.fullmatch(r"[A-Za-z]+(?:/[A-Za-z]+)+", m.group()):
            for p in re.finditer(r"[A-Za-z]+|/", m.group()):  # Freezing/Crashing, import/export
                toks.append({"start": m.start() + p.start(), "end": m.start() + p.end(), "text": p.group(),
                             "kind": "word" if p.group() != "/" else "punct"})
            continue
        toks.append({"start": m.start(), "end": m.end(), "text": m.group(), "kind": m.lastgroup})
    return toks


def is_code_line(line):
    s = line.strip()
    return bool(re.match(r"(at |\{code|\{noformat|```|[{}\[\]]|#|//|\$ |case class |[\w.]+\s*=|CACHE |SELECT |"
                         r"enable_\w+:|\w+::\w+|\"[\w.-]+\":)", s)) or s.endswith(";") or s.endswith("{")


def paragraphs(text, start, end):
    """Абзацы = блоки, разделённые пустой строкой; блок кода (``` / {code} / {noformat}) не разрывается."""
    out, pos, buf_start = [], start, None
    blocks = []
    for m in re.finditer(r"\n[ \t]*\n+|\n(?=h\d\.\s)", text[start:end]):
        blocks.append((pos, start + m.start()))
        pos = start + m.end()
    blocks.append((pos, end))
    merged, open_fence = [], False
    for b in blocks:
        chunk = text[b[0]:b[1]]
        fences = len(re.findall(r"```|\{code[^}]*\}|\{noformat\}", chunk))
        if merged and open_fence:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
        if fences % 2 == 1:
            open_fence = not open_fence
    for s, e in merged:
        seg = text[s:e]
        ls = len(seg) - len(seg.lstrip())
        rs = len(seg.rstrip())
        if seg.strip():
            out.append((s + ls, s + rs))
    return out


HEADINGS = [
    (r"(steps to reproduce|str|how to reproduce|reproducible steps|preconditions|prerequisites|\[?prerequisites\]?)", "STEPS"),
    (r"(actual results?|actual behaviou?r|actual)", "ACTUAL"),
    (r"(what happened)", "DESCRIPTION"),
    (r"(expected results?|expected behaviou?r|expected)", "EXPECTED"),
    (r"(user agent|affected versions|found in|tested platforms|affected platforms|vs ?code version|os version|version)", "ENVIRONMENT"),
    (r"(stack trace)", "STACK_TRACE"),
    (r"(buggy code|example sql|for example)", "CODE"),
    (r"(notes|other notes|additional notes|regression range|root cause|mitigation|credit|note)", "NOTES"),
]


def heading_section(par_text):
    first = par_text.strip().split("\n", 1)[0]
    clean = re.sub(r"[*\[\]#]|^h\d\.\s*|^\s*[-•]\s+", "", first).strip().lower()
    for rx, lab in HEADINGS:
        if re.fullmatch(rx + r"\s*:?", clean) or re.match(rx + r"\s*:", clean):
            return lab
    return None


def sentences(nltk, text, p_start, p_end):
    """Предложения внутри абзаца: строки-пункты списка, заголовки и строки кода — отдельные
    предложения; обычный текст с переносами строк склеивается и делится punkt."""
    lines, pos = [], p_start
    for line in text[p_start:p_end].split("\n"):
        lines.append((pos, pos + len(line)))
        pos += len(line) + 1
    groups = []
    for s, e in lines:
        line = text[s:e]
        if not line.strip():
            continue
        new = (not groups or re.match(r"\s*(\d+[.)]|[-*•]|#|\(\d+\)|\*\*|\[|at |>)", line)
               or is_code_line(line) or text[groups[-1][0]:groups[-1][1]].rstrip().endswith((":", ".", "!", "?", "```"))
               or is_code_line(text[groups[-1][0]:groups[-1][1]]) or heading_section(line))
        if new:
            groups.append([s, e])
        else:
            groups[-1][1] = e
    out = []
    from nltk.tokenize.punkt import PunktTokenizer
    punkt = PunktTokenizer("english")
    for s, e in groups:
        seg = text[s:e]
        lead = len(seg) - len(seg.lstrip())
        if is_code_line(seg):
            out.append((s + lead, s + len(seg.rstrip())))
            continue
        pieces = [(s + a, s + b) for a, b in punkt.span_tokenize(seg)]
        merged = []
        for a, b in pieces:
            if merged and re.fullmatch(r"\s*(\d+[.)]|[-*•])\s*", text[merged[-1][0]:merged[-1][1]]):
                merged[-1] = (merged[-1][0], b)   # «1.» + «Open Thunderbird» — одно предложение-пункт
            else:
                merged.append((a, b))
        out += merged
    return out


def tag_tokens(nltk, lem, toks):
    words = [t["text"] for t in toks]
    tags = nltk.pos_tag(words) if words else []
    for t, (_, xpos) in zip(toks, tags):
        k, w = t["kind"], t["text"]
        if k in ("url", "email", "about"):
            xpos = "ADD"
            upos = "X"
        elif k in ("ident",):
            upos, xpos = "X", "NNP" if xpos.startswith("NN") else xpos
            if re.search(r"[/.]", w) and re.search(r"\d", w) and re.match(r"[A-Za-z]+/\d", w):
                upos = "PROPN"  # Firefox/112.0 — название с версией
        elif k in ("cve", "issue"):
            upos, xpos = "PROPN", "NNP"
        elif k == "ver":
            upos, xpos = "NUM", "CD"
        elif k == "punct":
            upos = "SYM" if w in SYMBOLS else "PUNCT"
            xpos = xpos if xpos in PENN2UPOS and PENN2UPOS[xpos] in ("PUNCT", "SYM") else ("SYM" if upos == "SYM" else ".")
        else:
            upos = PENN2UPOS.get(xpos, "X")
        # лемма
        lw = w.lower()
        if w in CLITIC_LEMMA or lw in CLITIC_LEMMA:
            lemma = CLITIC_LEMMA.get(lw, lw)
        elif lw == "'s":
            lemma = "be" if xpos == "VBZ" else "'s"
        elif upos in ("VERB", "AUX"):
            lemma = lem.lemmatize(lw, "v")
        elif upos == "NOUN":
            lemma = lem.lemmatize(lw, "n")
        elif upos == "ADJ":
            lemma = lem.lemmatize(lw, "a")
        elif upos == "ADV":
            lemma = lem.lemmatize(lw, "r")
        elif upos in ("PROPN", "X", "NUM", "SYM", "PUNCT"):
            lemma = w
        else:
            lemma = lw
        if upos == "AUX" and xpos == "MD":
            lemma = lw if lw not in ("ca", "wo") else {"ca": "can", "wo": "will"}[lw]
        t["upos"], t["xpos"], t["lemma"] = upos, xpos, lemma
        t["upos_tagger"] = upos
    return toks


SCONJ = {"because", "if", "whether", "although", "though", "while", "unless", "that"}
IMPERATIVE_POS = ("PROPN", "ADJ", "NOUN", "X", "ADV")


def post_rules(doc, lem):
    """Правила, использующие другие уровни разметки (найдены при анализе, шаг 6):
    раздел абзаца (уровень 2) и сущности (уровень 4) уточняют часть речи (уровень 3)."""
    from nltk.corpus import wordnet as wn
    toks = doc["tokens"]
    par_sec = {p["id"]: p["section"] for p in doc["paragraphs"]}
    sent_par = {s["id"]: s["paragraph"] for s in doc["sentences"]}
    ent_at = {}
    for e in doc["entities"]:
        for t in toks:
            if e["start"] <= t["start"] and t["end"] <= e["end"]:
                ent_at.setdefault(t["id"], e)
    fired = Counter()
    for i, t in enumerate(toks):
        nxt = next((x for x in toks[i + 1:i + 4] if x["sentence"] == t["sentence"] and x["upos"] not in ("ADV", "PART")), None)
        prev = toks[i - 1] if i and toks[i - 1]["sentence"] == t["sentence"] else None
        lw = t["text"].lower()
        # 1) отрицание — PART (UD)
        if lw in ("not", "n't"):
            t["upos"] = "PART"; fired["neg_part"] += 1; continue
        # 2) be — всегда AUX; have/do — AUX только перед причастием/глаголом
        if t["lemma"] == "be" and t["upos"] == "VERB":
            t["upos"] = "AUX"; fired["be_aux"] += 1
        if t["lemma"] in ("have", "do") and t["upos"] in ("VERB", "AUX"):
            aux = nxt is not None and (nxt["xpos"] in ("VBN",) if t["lemma"] == "have" else nxt["xpos"] in ("VB",)) \
                or (t["lemma"] == "do" and nxt is not None and nxt["text"].lower() in ("not", "n't"))
            if any(x["text"].lower() in ("not", "n't") for x in toks[i + 1:i + 2]) and t["lemma"] == "do":
                aux = True
            new = "AUX" if aux else "VERB"
            if new != t["upos"]:
                fired["have_do"] += 1
            t["upos"] = new
        # 3) to перед именной группой — ADP
        if lw == "to" and t["xpos"] == "TO" and nxt and nxt["upos"] in ("DET", "NOUN", "PRON", "PROPN", "NUM", "ADJ"):
            t["upos"] = "ADP"; fired["to_adp"] += 1
        # 4) подчинительные союзы
        if lw in SCONJ and t["xpos"] == "IN" and not (lw == "because" and nxt and nxt["text"] == "of"):
            t["upos"] = "SCONJ"; fired["sconj"] += 1
        # 5) повелительное наклонение в шагах воспроизведения (уровень 2 -> 3)
        sec = par_sec.get(sent_par.get(t["sentence"]))
        sent_toks = [x for x in toks if x["sentence"] == t["sentence"]]
        lead = next((x for x in sent_toks if not re.fullmatch(r"\d+|[-*•.):(]|\*\*", x["text"])), None)
        first = lead is not None and lead["id"] == t["id"]
        sent_txt = doc["text"][sent_toks[0]["start"]:sent_toks[-1]["end"]]
        if sec == "STEPS" and first and not heading_section(sent_txt) and t["text"][:1].isupper() and t["upos"] in IMPERATIVE_POS \
                and t["id"] not in ent_at and wn.synsets(lw, pos="v"):
            t["upos"] = "VERB"
            t["xpos"] = "VBD" if lw.endswith("ed") else "VB"
            t["lemma"] = lem.lemmatize(lw, "v")
            fired["imperative_steps"] += 1
        # 6) симптом-глагол после подлежащего (уровень 4 -> 3)
        e = ent_at.get(t["id"])
        if e and e["label"] == "SYMPTOM" and t["upos"] in ("NOUN", "PROPN") and wn.synsets(lw, pos="v") \
                and (bool(re.search(r"(es|s|ed|ing)$", lw)) or (prev is not None and prev["upos"] in ("ADV", "AUX", "PART"))) \
                and (prev is None or prev["upos"] not in ("DET", "ADJ", "NOUN", "ADP")):
            t["upos"] = "VERB"
            t["xpos"] = {"s": "VBZ", "d": "VBD", "g": "VBG"}.get(lw[-1], "VB")
            t["lemma"] = lem.lemmatize(lw, "v")
            fired["symptom_verb"] += 1
        # 7) слова внутри названий (ENV/PRODUCT/TECHNOLOGY) — PROPN
        single = e is not None and e["start"] == t["start"] and e["end"] == t["end"]
        if e and e["label"] in ("ENV", "PRODUCT", "TECHNOLOGY", "LOCALE") and t["upos"] in ("SYM", "NOUN", "ADJ", "X", "VERB") \
                and t["kind"] == "word" and (re.search(r"[A-Z]", t["text"]) or single) and not re.search(r"\d", t["text"]):
            t["upos"], t["xpos"], t["lemma"] = "PROPN", "NNP", t["text"]; fired["name_propn"] += 1
        elif e and e["label"] == "ENV" and e["attributes"].get("kind") == "app_version" and t["kind"] == "word" \
                and (re.search(r"\d", t["text"]) or t["text"] == "x") and t["upos"] != "NUM":
            t["upos"], t["xpos"] = "NUM", "CD"; fired["version_num"] += 1
        if re.fullmatch(r"\d+", t["text"]) and t["upos"] != "NUM":
            t["upos"] = "NUM"; fired["digits_num"] += 1
        # 8) идентификаторы кода и имена исключений в тексте — PROPN (теггер принимает LdapGroupsMapping за VBG)
        if e and e["label"] in ("EXCEPTION", "CODE_LOCATION") and t["upos"] not in ("X", "PUNCT", "SYM", "PROPN", "NUM") \
                and t["kind"] == "word" and re.search(r"[A-Z].*[a-z]|[a-z][A-Z]", t["text"]):
            t["upos"], t["xpos"], t["lemma"] = "PROPN", "NNP", t["text"]; fired["code_propn"] += 1
        if lw == "please" and t["upos"] != "INTJ":
            t["upos"] = "INTJ"; fired["please"] += 1
    return fired


# ------------------------------------------------------------------ словари сущностей
DBR = "http://dbpedia.org/resource/"
# поверхностная форма (regex) -> (метка, kind, normalized, ресурс DBpedia)
GAZETTEER = [
    # PRODUCT — программный продукт или сервис, о котором идёт речь
    (r"Firefox(?: Nightly)?|Fx|FireFox|FIrefox", "PRODUCT", "application", "Firefox", "Firefox"),
    (r"Thunderbird|TB", "PRODUCT", "application", "Thunderbird", "Mozilla_Thunderbird"),
    (r"SeaMonkey|seamonkey", "PRODUCT", "application", "SeaMonkey", "SeaMonkey"),
    (r"VS ?Code|vscode(?:-insiders)?|VSCode|Insiders", "PRODUCT", "application", "Visual Studio Code", "Visual_Studio_Code"),
    (r"(?:Apache )?Cassandra|cassandra", "PRODUCT", "application", "Apache Cassandra", "Apache_Cassandra"),
    (r"Spark SQL", "PRODUCT", "framework", "Spark SQL", "Apache_Spark"),
    (r"Spark|Pyspark", "PRODUCT", "framework", "Apache Spark", "Apache_Spark"),
    (r"hadoop|Hadoop", "PRODUCT", "framework", "Apache Hadoop", "Apache_Hadoop"),
    (r"Guava(?: Cache)?", "PRODUCT", "library", "Guava", "Google_Guava"),
    (r"[Pp]ay[Pp]al(?:\.de)?", "PRODUCT", "service", "PayPal", "PayPal"),
    (r"Google Workspace", "PRODUCT", "service", "Google Workspace", "Google_Workspace"),
    (r"Google Drive", "PRODUCT", "service", "Google Drive", "Google_Drive"),
    (r"Chrome|IE", "PRODUCT", "application", None, None),
    (r"FreeBSD", "PRODUCT", "application", "FreeBSD", "FreeBSD"),
    (r"PDF\.js", "PRODUCT", "library", "PDF.js", "PDF.js"),
    (r"Marketplace", "PRODUCT", "service", "Visual Studio Marketplace", None),
    (r"Browser Toolbox", "PRODUCT", "tool", "Firefox Browser Toolbox", None),
    (r"Supernova", "PRODUCT", "release", "Thunderbird Supernova (115)", None),
    (r"Firefox Sync|Sync", "PRODUCT", "service", "Firefox Sync", "Firefox_Sync"),
    (r"npm|NodeJS|Node\.js", "PRODUCT", "tool", None, None),
    # TECHNOLOGY — протокол, стандарт, формат, язык
    (r"OAuth2|OAuth", "TECHNOLOGY", "protocol", "OAuth 2.0", "OAuth"),
    (r"CardDAV|CardDav", "TECHNOLOGY", "protocol", "CardDAV", "CardDAV"),
    (r"[Kk]erberos", "TECHNOLOGY", "protocol", "Kerberos", "Kerberos_(protocol)"),
    (r"RFC", "TECHNOLOGY", "standard", "RFC", "Request_for_Comments"),
    (r"JDBC", "TECHNOLOGY", "protocol", "JDBC", "Java_Database_Connectivity"),
    (r"SQL", "TECHNOLOGY", "language", "SQL", "SQL"),
    (r"JSON|jsonc", "TECHNOLOGY", "format", "JSON", "JSON"),
    (r"CSS", "TECHNOLOGY", "language", "CSS", "CSS"),
    (r"HTML|HMTL", "TECHNOLOGY", "language", "HTML", "HTML"),
    (r"DNS over HTTPS", "TECHNOLOGY", "protocol", "DNS over HTTPS", "DNS_over_HTTPS"),
    (r"DNS", "TECHNOLOGY", "protocol", "DNS", "Domain_Name_System"),
    (r"WebSocket", "TECHNOLOGY", "protocol", "WebSocket", "WebSocket"),
    (r"TLS|SSL", "TECHNOLOGY", "protocol", "TLS", "Transport_Layer_Security"),
    (r"Content Security Policy", "TECHNOLOGY", "mechanism", "Content Security Policy", "Content_Security_Policy"),
    (r"Cross-Origin|CORS|Same Origin Policy", "TECHNOLOGY", "mechanism", "CORS / Same-origin policy", "Cross-origin_resource_sharing"),
    (r"LDAP|ldap", "TECHNOLOGY", "protocol", "LDAP", "Lightweight_Directory_Access_Protocol"),
    (r"thrift", "TECHNOLOGY", "protocol", "Apache Thrift", "Apache_Thrift"),
    (r"RTL|bidi", "TECHNOLOGY", "standard", "Bidirectional text (RTL)", "Bidirectional_text"),
    (r"PDF", "TECHNOLOGY", "format", "PDF", "PDF"),
    (r"proxy", "TECHNOLOGY", "mechanism", "Proxy server", "Proxy_server"),
    (r"UDFs?|user defined functions", "TECHNOLOGY", "mechanism", "User-defined function", "User-defined_function"),
    (r"certificates?|certs", "TECHNOLOGY", "mechanism", "Public key certificate", "Public_key_certificate"),
    # LOCALE — язык/локаль интерфейса или контента (добавлено по результатам анализа, шаг 6)
    (r"English|Russian|Portuguese|german|German|pt-PT|zh-CN", "LOCALE", "language", None, None),
]
ENV_PATTERNS = [
    (r"Windows(?: NT)? \d+(?:\.\d+)?(?:x64| x64)?|Windows_NT x64 [\d.]+|Win64", "os", "Microsoft_Windows"),
    (r"mac ?OS(?: X)?(?: \d+(?:[._]\d+)*)?|Mac OS(?: X)?(?: \d+(?:[._]\d+)*)?|OSX \d+(?:\.\d+)*|MacOS|Mac(?= and)|Macintosh", "os", "MacOS"),
    (r"Ubuntu(?: \d+(?:\.\d+)?)?", "os", "Ubuntu"),
    (r"Fedora Core \d+(?: x86_64)?", "os", "Fedora_Linux"),
    (r"Linux x86_64|Linux", "os", "Linux"),
    (r"Darwin arm64 [\d.]+", "os", "Darwin_(operating_system)"),
    (r"X11", "os", "X_Window_System"),
    (r"Firefox/\d+[\d.]*|Firefox \d+[\d.]*(?: \(64-bit\))?|Chrome/[\d.]+|Safari/[\d.]+|SeaMonkey/[\d.]+|Gecko/\d+|OPR/[\d.]+", "browser", None),
    (r"(?<![\w.])\d+\.\d+(?:\.\d+)*(?:[ab]\d+|-insider)?(?: \(64-bit\))?(?![\w]|\.\d)|(?<![\w.])\d+\.\d+[ab]\d+|v\d{3}(?: Beta \d)?|Nightly \d+", "app_version", None),
]
METRIC_RE = r"(?<![\w.])(?:\d+(?:-\d+)?|\d+ to \d+)\+?\s?(?:MB|GB|KB|ms|seconds?|second|minutes?|s)\b(?: or more| at most)?"
SYMPTOMS = [
    (r"segfaults?|crash(?:es|ed|ing)?|Crashing|Crashes", "crash"),
    (r"[Ff]reez(?:es|ing|e)|froze(?: up)?|Freezing", "hang"),
    (r"(?:[Hh]uge |a huge )?[Mm]emory leak|leak", "resource_leak"),
    (r"(?:extremely |painfully |much )?slow(?:er|ly)?(?: start)?", "slowness"),
    (r"flickers?", "visual_defect"),
    (r"overlap(?:s)?|misplaced|not aligned|too close|not rendered|hidden", "visual_defect"),
    (r"not (?:fully )?translated|untranslated|nonsense|[Pp]oor translation quality|horribly wrong version", "wrong_output"),
    (r"[Ii]ncorrect message|misleading|[Bb]lank|[Bb]lack page|[Ii]nvalidated|re-read", "wrong_output"),
    (r"malformed", "data_corruption"),
    (r"[Nn]othing happens|does not fetch|not working|does not work|doesn't work|no longer works|stops to function|unusable|break(?:s)?|broke|fails?|failures?", "failure"),
    (r"execute arbitrary (?:code|commands)|[Rr]emote code execution|vulnerabilit(?:y|ies)|security issues", "vulnerability"),
]
UI_RE = (r"(?:[“\"][^”\"\n]{1,40}[”\"]\s+)?(?:(?:[A-Z][\w-]*|the|an?|urlbar shopping|close|radio|error|compose|"
         r"Address Book|address book|migration|Command|Network Settings|Settings|Import data|Paypal|PayPal|Quick|main)\s+)?"
         r"(?:buttons?|window|tab|panel|dialog|sidebar|menu option|menu|tooltip|scrollbar|pane|icon|Pallette|Filter|link)\b")
EXC_RE = r"(?:[a-z]+\.)*[A-Z]\w*(?:Exception|Error)\b|EXCEPTION_\w+|NS_ERROR_\w+"
ERR_CODE_RE = [(r"ECONNREFUSED|ERR_[A-Z_]+|NXDOMAIN", "net_error"), (r"CVE-\d{4}-\d+", "cve"),
               (r"NS_ERROR_\w+", "app_code")]
ISSUE_RE = r"(?:[Bb]ug |bug\s)\d{6,7}|\b(?:SPARK|CASSANDRA|HADOOP|HBASE)-\d+\b|(?<=\[)\d{6,7}(?:#c\d+)?(?=\])"
PII_RE = [(r"[\w.+-]+@[\w-]+\.[\w.-]*\w", "email"), (r"Omer Kaspi", "person_name")]
CODE_LOC_RE = (r"\b(?:org|com)\.[\w.]+(?:#\w+)?|\b\w+\.(?:java|scala|py|js|jsm|css|cpp|sh)(?::\d+)?\b|"
               r"\b[a-z]+[A-Z]\w*(?:\(\))?|\b[A-Z][a-z]+[A-Z]\w+\b")


def snap(tokens, s, e):
    """Расширить/сузить границы сущности до границ токенов."""
    inside = [t for t in tokens if t["end"] > s and t["start"] < e]
    if not inside:
        return None
    return inside[0]["start"], inside[-1]["end"]


PRODUCT_OF_PROJECT = {"firefox": "Firefox", "thunderbird": "Thunderbird", "seamonkey": "SeaMonkey",
                      "vscode": "VS Code", "spark": "Apache Spark", "hadoop": "Apache Hadoop",
                      "hbase": "Apache HBase", "cassandra": "Apache Cassandra"}
UI_KIND = [(r"button", "button"), (r"window|dialog|panel|Pallette", "dialog"), (r"menu", "menu"),
           (r"tab\b", "page"), (r"link", "link"), (r"list", "list")]
UA_NOISE = r"Mozilla/[\d.]+|Gecko/\d+|AppleWebKit/[\d.]+|Safari/[\d.]+|KHTML|Win64|Macintosh|Intel"
QUOTED_RE = r"`([^`\n]{6,300})`|'([^'\n]{6,300})'|“([^”\n]{6,300})”|\"([^\"\n]{6,300})\""
ERR_WORDS = r"(?i)error|failed|exception|refused|went wrong|not initialized|expired|blocked|malformed|unwrap"


def overlaps(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def nested_ok(a, b):
    """Внутри ERROR_MESSAGE допускаются вложенные сущности (код ошибки, исключение, продукт...)."""
    outer, inner = (a, b) if a["end"] - a["start"] >= b["end"] - b["start"] else (b, a)
    return (outer["label"] == "ERROR_MESSAGE" and inner["label"] != "ERROR_MESSAGE"
            and outer["start"] <= inner["start"] and inner["end"] <= outer["end"])


def code_spans(text):
    """Блоки кода: ```...```, {code}...{code}, {noformat}...{noformat}, {{...}} не считаем кодом."""
    spans = []
    for rx in (r"```.*?```", r"\{code[^}]*\}.*?\{code\}", r"\{noformat\}.*?\{noformat\}"):
        spans += [(m.start(), m.end()) for m in re.finditer(rx, text, re.S)]
    return spans


def auto_entities(text, tokens, pars, reports):
    found = []
    starts = {t["start"] for t in tokens}
    ends = {t["end"] for t in tokens}
    codes = code_spans(text) + [(p["start"], p["end"]) for p in pars if p["section"] in ("CODE",)]
    ua_lines = [(m.start(), m.end()) for m in re.finditer(r"(?im)^\s*(?:user ?agent|useragent):.*$", text)]

    def in_any(spans, s, e):
        return any(a <= s and e <= b for a, b in spans)

    def product_at(pos):
        for u in reports:
            if u["start"] <= pos < u["end"]:
                return PRODUCT_OF_PROJECT.get(u["source_project"], u["source_project"])

    def add(s, e, label, attrs, prio):
        # граница сущности должна совпадать с границами токенов (иначе это кусок URL, пути и т.п.)
        if s not in starts or e not in ends:
            return
        if in_any(codes, s, e) and label not in ("EXCEPTION", "ERROR_MESSAGE", "ERROR_CODE") and attrs.get("kind") != "frame":
            return
        found.append({"start": s, "end": e, "label": label, "attributes": attrs, "_p": prio})

    # кадры трассировки стека: at pkg.Class.method(File.java:123)
    for m in re.finditer(r"at ([\w.$]+)\(([\w.]+:\d+)\)", text):
        add(m.start(1), m.end(), "CODE_LOCATION", {"kind": "frame", "normalized": m.group(2)}, -1)
    # тексты ошибок в кавычках
    for m in re.finditer(QUOTED_RE, text):
        g = next(i for i in range(1, 5) if m.group(i))
        if re.search(ERR_WORDS, m.group(g)) and not re.fullmatch(r"[A-Z_]+", m.group(g)):
            add(m.start(g), m.end(g), "ERROR_MESSAGE", {}, -1)
    for rx, lab, kind, norm, kb in GAZETTEER:
        for m in re.finditer(rf"(?<![\w.:/-])(?:{rx})(?![\w]|\.\w)", text):
            a = {"kind": kind}
            if norm:
                a["normalized"] = norm
            if kb:
                a["kb_uri"] = DBR + kb
            add(m.start(), m.end(), lab, a, 2 if lab == "PRODUCT" else 3)
    for rx, kind, kb in ENV_PATTERNS:
        for m in re.finditer(rx, text):
            g = m.group()
            if re.fullmatch(UA_NOISE, g) or (kind == "app_version" and in_any(ua_lines, m.start(), m.end())):
                continue
            k = kind
            norm = g
            if kind == "browser" and not in_any(ua_lines, m.start(), m.end()) and "/" not in g:
                k = "app_version"          # «Firefox 74» в тексте — версия продукта
            if k == "app_version" and not re.match(r"[A-Za-z]", g):
                norm = f"{product_at(m.start())} {g}"
            norm = re.sub(r"Windows NT 10\.0", "Windows 10", norm)
            a = {"kind": k, "normalized": norm, "affected": "unknown"}
            if in_any(ua_lines, m.start(), m.end()):
                a["source"] = "user_agent"
            if kb:
                a["kb_uri"] = DBR + kb
            add(m.start(), m.end(), "ENV", a, 1 if kind != "app_version" else 4)
    for m in re.finditer(METRIC_RE, text):
        kind = "memory" if re.search(r"[MGK]B", m.group()) else "time"
        add(m.start(), m.end(), "METRIC", {"kind": kind, "normalized": m.group()}, 1)
    for rx, kind in SYMPTOMS:
        for m in re.finditer(rf"(?<![\w-])(?:{rx})(?![\w-])", text):
            add(m.start(), m.end(), "SYMPTOM", {"kind": kind}, 3)
    for m in re.finditer(UI_RE, text):
        g = re.sub(r"^(?i:the|an?|close)\s+", "", m.group())
        st = m.end() - len(g)
        kind = next((k for rx, k in UI_KIND if re.search(rx, g)), "other")
        add(st, m.end(), "UI_ELEMENT", {"kind": kind}, 4)
    for m in re.finditer(EXC_RE, text):
        add(m.start(), m.end(), "EXCEPTION", {"normalized": m.group().split(".")[-1]}, 0)
    for rx, kind in ERR_CODE_RE:
        for m in re.finditer(rx, text):
            add(m.start(), m.end(), "ERROR_CODE", {"kind": kind, "normalized": m.group()}, 0)
    for m in re.finditer(ISSUE_RE, text):
        add(m.start(), m.end(), "ISSUE_REF", {"normalized": re.sub(r"(?i)bug\s", "bug ", m.group())}, 0)
    for rx, kind in PII_RE:
        for m in re.finditer(rx, text):
            add(m.start(), m.end(), "PII", {"kind": kind}, 0)
    # ключи конфигурации: hadoop.security.group..., enable_user_defined_functions, http.proxy
    for m in re.finditer(r"(?<![\w./])(?:[a-z]+(?:\.[a-zA-Z_]+){1,}|[a-z]+(?:_[a-z]+){2,})(?![\w(/])", text):
        if re.search(r"\.(java|scala|py|js|jsm|css|cpp|com|org|net|de)$", m.group()):
            continue
        add(m.start(), m.end(), "CODE_LOCATION", {"kind": "config"}, 4)
    for m in re.finditer(CODE_LOC_RE, text):
        g = m.group()
        if re.search(r"\.(java|scala|py|js|jsm|css|cpp|sh)", g):
            kind = "file"
        elif g.startswith(("org.", "com.")) or g[0].isupper():
            kind = "class"
        elif g.endswith("()") or text[m.end():m.end() + 1] == "(":
            kind = "method"
        else:
            kind = "variable"
        add(m.start(), m.end(), "CODE_LOCATION", {"kind": kind}, 5)
    # разрешение пересечений: меньший приоритет важнее; при равенстве — длиннее
    found.sort(key=lambda x: (x["_p"], -(x["end"] - x["start"]), x["start"]))
    kept = []
    for f in found:
        if any(overlaps(f, k) and not nested_ok(f, k) for k in kept):
            continue
        kept.append(f)
    kept.sort(key=lambda x: x["start"])
    for k in kept:
        k.pop("_p")
        k["text"] = text[k["start"]:k["end"]]
    return kept


def section_at(sections, pos):
    for s in sections:
        if s["start"] <= pos < s["end"]:
            return s["label"]
    return None


def auto_relations(ents, sents):
    rels = []

    def sent_of(e):
        for i, (a, b) in enumerate(sents):
            if a <= e["start"] < b:
                return i
        return -1
    by_sent = defaultdict(list)
    for e in ents:
        by_sent[sent_of(e)].append(e)
    for si, es in by_sent.items():
        prods = [e for e in es if e["label"] in ("PRODUCT", "UI_ELEMENT")]
        for e in es:
            if e["label"] == "ENV" and e["attributes"].get("kind") == "app_version":
                prev = [p for p in es if p["label"] == "PRODUCT" and p["end"] <= e["start"]]
                if prev:
                    rels.append({"from": e["id"], "to": prev[-1]["id"], "type": "VERSION_OF"})
            if e["label"] == "SYMPTOM" and prods:
                tgt = min(prods, key=lambda p: abs(p["start"] - e["start"]))
                rels.append({"from": e["id"], "to": tgt["id"], "type": "SYMPTOM_OF"})
            if e["label"] == "EXCEPTION":
                msgs = [m for m in es if m["label"] == "ERROR_MESSAGE"]
                if msgs:
                    rels.append({"from": e["id"], "to": msgs[0]["id"], "type": "HAS_MESSAGE"})
    return rels


def build_auto(cat, corpus_doc, nltk, lem):
    text = (OUT / corpus_doc["file"]).read_text(encoding="utf-8")
    pars, sections, sents_all, tokens = [], [], [], []
    for u in corpus_doc["reports"]:
        cur = None
        for i, (ps, pe) in enumerate(paragraphs(text, u["start"], u["end"])):
            ptxt = text[ps:pe]
            h = heading_section(ptxt)
            if i == 0:
                lab = "TITLE"
            elif h:
                lab = cur = h
            elif re.search(r"^\s*at [\w.$]+\(", ptxt, re.M):
                lab = "STACK_TRACE"
            elif re.match(r"\s*(```|\{code|\{noformat)", ptxt) or all(is_code_line(l) for l in ptxt.split("\n") if l.strip()):
                lab = "CODE"
            elif cur and cur != "TITLE":
                lab = cur
            elif re.match(r"\s*1[.)] ", ptxt):
                lab = "STEPS"
            else:
                lab = "DESCRIPTION"
            if h and ptxt.strip().rstrip(":*").count("\n") == 0 and len(ptxt) < 40:
                lab = h  # отдельный заголовок раздела
            pars.append({"id": f"p{len(pars)+1}", "start": ps, "end": pe, "report_id": u["report_id"], "section": lab})
            ss = sentences(nltk, text, ps, pe)
            for a, b in ss:
                toks = tag_tokens(nltk, lem, tokenize(text, a, b))
                if not toks:
                    continue
                sid = f"s{len(sents_all)+1}"
                sents_all.append({"id": sid, "start": toks[0]["start"], "end": toks[-1]["end"], "paragraph": pars[-1]["id"]})
                for t in toks:
                    t["sentence"] = sid
                    tokens.append(t)
    for i, t in enumerate(tokens, 1):
        t["id"] = f"t{i}"
    ents = auto_entities(text, tokens, pars, corpus_doc["reports"])
    for i, e in enumerate(ents, 1):
        e["id"] = f"e{i}"
        e["section"] = section_at([{"start": p["start"], "end": p["end"], "label": p["section"]} for p in pars], e["start"])
    rels = auto_relations(ents, [(s["start"], s["end"]) for s in sents_all])
    doc = {"doc_id": cat, "text": text, "reports": corpus_doc["reports"], "paragraphs": pars,
           "sentences": sents_all, "tokens": tokens, "entities": ents, "relations": rels}
    doc["pos_rules_fired"] = dict(post_rules(doc, lem))
    return doc


def cmd_auto(args):
    nltk, lem = _nltk()
    corpus = json.loads((OUT / "corpus.json").read_text(encoding="utf-8"))
    (OUT / "auto").mkdir(parents=True, exist_ok=True)
    for d in corpus["documents"]:
        doc = build_auto(d["doc_id"], d, nltk, lem)
        doc["meta"] = {"schema_version": "1.1", "status": "auto", "created": str(date.today()),
                       "method": "предразметка: токенизатор (регулярные выражения) + NLTK punkt (предложения) + "
                                 "NLTK averaged perceptron (Penn) -> UPOS + WordNet (леммы) + словари и правила (сущности, DBpedia URI)"}
        (OUT / "auto" / f"{d['doc_id']}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        c = Counter(e["label"] for e in doc["entities"])
        print(f"{d['doc_id']:14s} абз={len(doc['paragraphs']):3d} предл={len(doc['sentences']):3d} ток={len(doc['tokens']):4d} "
              f"сущн={len(doc['entities']):3d} связи={len(doc['relations']):2d} {dict(c)}")


# ------------------------------------------------------------------ ручные правки и итоговая разметка
NEG_CUES = {"not", "n't", "no", "never", "without", "nothing"}
MODAL_CUES = {"should", "shouldn't", "would", "could", "can", "might", "must", "to", "so"}


def token_spans(doc):
    return {t["start"] for t in doc["tokens"]}, {t["end"] for t in doc["tokens"]}


def aligned_occurrences(doc, needle=None, regex=None):
    starts, ends = token_spans(doc)
    rx = regex if regex else re.escape(needle)
    return [(m.start(), m.end()) for m in re.finditer(rx, doc["text"], re.S)
            if m.start() in starts and m.end() in ends]


def find_entity(doc, spec):
    cands = [e for e in doc["entities"] if e["text"] == spec["text"] and
             (not spec.get("label") or e["label"] == spec["label"])]
    occ = spec.get("occ", 1)
    if occ == "all":
        return cands
    if len(cands) < occ:
        raise SystemExit(f'{doc["doc_id"]}: нет сущности {spec}')
    return [cands[occ - 1]]


def polarity(doc, e, sec):
    """observed — симптом наблюдается; negated — отрицается («no flicker», «does not freeze»);
    hypothetical — желаемое/условное поведение («should», раздел EXPECTED)."""
    toks = [t for t in doc["tokens"] if t["end"] <= e["start"]]
    sent = next((t["sentence"] for t in doc["tokens"] if t["start"] >= e["start"]), None)
    window = [t["text"].lower() for t in toks if t["sentence"] == sent][-5:]
    if NEG_CUES & set(window):
        return "negated"
    if sec == "EXPECTED" or MODAL_CUES & set(window[-3:]):
        return "hypothetical"
    return "observed"


def apply_edits(doc, ops):
    stats = Counter()
    n_manual = 0
    rels = doc["relations"]
    pars = {p["id"]: p for p in doc["paragraphs"]}
    for op in ops:
        kind = op["op"]
        if kind == "remove":
            for e in find_entity(doc, op):
                doc["entities"].remove(e)
                rels[:] = [r for r in rels if e["id"] not in (r["from"], r["to"])]
                stats["remove"] += 1
        elif kind == "set":
            for e in find_entity(doc, op):
                if "label" in op.get("new", {}):
                    e["label"] = op["new"]["label"]
                for k, v in op.get("attributes", {}).items():
                    if v is None:
                        e["attributes"].pop(k, None)
                    else:
                        e["attributes"][k] = v
                e["manual"] = True
                stats["set"] += 1
        elif kind == "add":
            occs = aligned_occurrences(doc, op.get("text"), op.get("regex"))
            want = range(len(occs)) if op.get("occ") == "all" else [o - 1 for o in op.get("occ_list", [op.get("occ", 1)])]
            for oi in want:
                if oi >= len(occs):
                    raise SystemExit(f'{doc["doc_id"]}: нет вхождения {op}')
                s_, e_ = occs[oi]
                n_manual += 1
                new = {"start": s_, "end": e_, "label": op["label"], "attributes": dict(op.get("attributes", {})),
                       "text": doc["text"][s_:e_], "id": f"m{n_manual}", "manual": True}
                for old in [x for x in doc["entities"] if overlaps(x, new) and not nested_ok(x, new)]:
                    doc["entities"].remove(old)
                    rels[:] = [r for r in rels if old["id"] not in (r["from"], r["to"])]
                    stats["replaced_by_add"] += 1
                doc["entities"].append(new)
                stats["add"] += 1
        elif kind == "section":
            pars[op["par"]]["section"] = op["section"]
            stats["section"] += 1
        elif kind == "rel":
            a = find_entity(doc, op["from"])[0]
            b = find_entity(doc, op["to"])[0]
            if any(r["from"] == a["id"] and r["to"] == b["id"] and r["type"] == op["type"] for r in rels):
                stats["rel_confirmed"] += 1   # связь уже есть в предразметке — подтверждена разметчиком
                continue
            rels.append({"from": a["id"], "to": b["id"], "type": op["type"], "manual": True})
            stats["rel_add"] += 1
        elif kind == "unrel":
            a = find_entity(doc, op["from"])[0]
            before = len(rels)
            rels[:] = [r for r in rels if not (r["from"] == a["id"] and (not op.get("type") or r["type"] == op["type"]))]
            stats["rel_remove"] += before - len(rels)
        elif kind == "pos":
            starts = [t for t in doc["tokens"] if t["text"] == op["text"]]
            t = starts[op.get("occ", 1) - 1]
            t.update({k: op[k] for k in ("upos", "xpos", "lemma") if k in op})
            t["manual"] = True
            stats["pos"] += 1
        else:
            raise SystemExit(f"неизвестная операция {op}")
    return stats


def cmd_final(args):
    corpus = {d["doc_id"]: d for d in json.loads((OUT / "corpus.json").read_text(encoding="utf-8"))["documents"]}
    edits = json.loads((OUT / "manual_edits.json").read_text(encoding="utf-8"))
    (OUT / "json").mkdir(parents=True, exist_ok=True)
    for cat in corpus:
        doc = json.loads((OUT / "auto" / f"{cat}.json").read_text(encoding="utf-8"))
        auto_ents = {(e["start"], e["end"], e["label"]) for e in doc["entities"]}
        auto_rels = len(doc["relations"])
        stats = apply_edits(doc, edits.get(cat, []))
        secs = [{"start": p["start"], "end": p["end"], "label": p["section"]} for p in doc["paragraphs"]]
        doc["entities"].sort(key=lambda e: (e["start"], -e["end"]))
        for e in doc["entities"]:
            e["section"] = section_at(secs, e["start"])
            e["report_id"] = next(u["report_id"] for u in doc["reports"] if u["start"] <= e["start"] < u["end"])
            if e["label"] == "SYMPTOM" and "polarity" not in e["attributes"]:
                e["attributes"]["polarity"] = polarity(doc, e, e["section"])
        final_ents = {(e["start"], e["end"], e["label"]) for e in doc["entities"]}
        tp = len(auto_ents & final_ents)
        doc["meta"] = {"schema_version": "1.1", "annotator": ANNOTATOR, "created": str(date.today()), "status": "draft",
                       "tool": "INCEpTION (проект LR3-bug-triage-text) + scripts/text_markup.py",
                       "method": "автоматическая предразметка (команда auto) + ручная проверка и правка разметчиком "
                                 "(annotations/text/markup/manual_edits.json)",
                       "manual_changes": dict(stats),
                       "preannotation_quality": {"auto_entities": len(auto_ents), "final_entities": len(final_ents),
                                                 "kept": tp, "precision": round(tp / max(len(auto_ents), 1), 3),
                                                 "recall": round(tp / max(len(final_ents), 1), 3),
                                                 "auto_relations": auto_rels, "final_relations": len(doc["relations"])}}
        doc["labels"] = {"category": cat, "object": cat}
        (OUT / "json" / f"{cat}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        q = doc["meta"]["preannotation_quality"]
        print(f"{cat:14s} сущн. авто {q['auto_entities']:3d} -> итог {q['final_entities']:3d} "
              f"(P={q['precision']:.2f} R={q['recall']:.2f}), связи {q['auto_relations']}->{q['final_relations']}, правки {dict(stats)}")


# ------------------------------------------------------------------ экспорт: WebAnno TSV 3.3 и CoNLL-U
POS_L = "de.tudarmstadt.ukp.dkpro.core.api.lexmorph.type.pos.POS"
LEMMA_L = "de.tudarmstadt.ukp.dkpro.core.api.segmentation.type.Lemma"
REPORT_L = "webanno.custom.BugReport"
SECTION_L = "webanno.custom.Section"
ENT_L = "webanno.custom.BugEntity"
REL_L = "webanno.custom.EntityRelation"
REPORT_F = ["report_id", "ticket_type", "category", "component", "severity", "confidence"]
ENT_F = ["label", "kind", "normalized", "affected", "polarity", "source", "kb_uri"]


def tsv_escape(v):
    v = str(v)
    for ch in ("\\", "[", "]", "|", "_", "->", "*", ";", "\t", "\n", "\r"):
        v = v.replace(ch, "\\" + ch)
    return v


def to_tsv(doc):
    toks, text = doc["tokens"], doc["text"]
    addr, tok_index = {}, {}
    sent_ids = {}
    for t in toks:
        sid = sent_ids.setdefault(t["sentence"], len(sent_ids) + 1)
        n = sum(1 for x in tok_index.values() if x[0] == sid) + 1
        tok_index[t["id"]] = (sid, n)
        addr[t["id"]] = f"{sid}-{n}"
    # span-аннотации -> покрываемые токены
    def cover(s, e):
        return [t["id"] for t in toks if t["start"] >= s and t["end"] <= e]
    spans = []   # (layer, uid, tokens, feature values)
    uid = 0
    for u in doc["reports"]:
        uid += 1
        spans.append((REPORT_L, uid, cover(u["start"], u["end"]), [u["report_id"]] + [u["labels"][k] for k in REPORT_F[1:]]))
    for p_ in doc["paragraphs"]:
        uid += 1
        spans.append((SECTION_L, uid, cover(p_["start"], p_["end"]), [p_["section"]]))
    ent_uid = {}
    for e in doc["entities"]:
        uid += 1
        ent_uid[e["id"]] = uid
        a = e["attributes"]
        spans.append((ENT_L, uid, cover(e["start"], e["end"]), [e["label"]] + [a.get(k) for k in ENT_F[1:]]))
    ent_by_id = {e["id"]: e for e in doc["entities"]}
    first_tok = {sp[1]: sp[2][0] for sp in spans if sp[2]}
    cols = defaultdict(lambda: defaultdict(list))   # token -> layer -> list of per-annotation feature lists
    for layer, u, tids, vals in spans:
        for tid in tids:
            cols[tid][layer].append([("*" if v in (None, "") else tsv_escape(v)) + f"[{u}]" for v in vals])
    rel_cols = defaultdict(list)
    for r in doc["relations"]:
        src, tgt = ent_uid[r["from"]], ent_uid[r["to"]]
        tgt_first = first_tok[tgt]
        rel_cols[tgt_first].append((tsv_escape(r["type"]), f"{addr[first_tok[src]]}[{src}_{tgt}]"))
    header = ["#FORMAT=WebAnno TSV 3.3",
              f"#T_SP={LEMMA_L}|value",
              f"#T_SP={POS_L}|PosValue|coarseValue",
              f"#T_SP={ENT_L}|" + "|".join(ENT_F),
              f"#T_SP={REPORT_L}|" + "|".join(REPORT_F),
              f"#T_SP={SECTION_L}|label",
              f"#T_RL={REL_L}|type|BT_{ENT_L}", "", ""]
    out = header[:]
    by_sent = defaultdict(list)
    for t in toks:
        by_sent[sent_ids[t["sentence"]]].append(t)
    nf = {ENT_L: len(ENT_F), REPORT_L: len(REPORT_F), SECTION_L: 1}
    for sid in sorted(by_sent):
        ts = by_sent[sid]
        stext = text[ts[0]["start"]:ts[-1]["end"]]
        for line in stext.split("\n"):
            out.append("#Text=" + line.replace("\\", "\\\\"))
        for t in ts:
            row = [addr[t["id"]], f'{t["start"]}-{t["end"]}', t["text"], tsv_escape(t["lemma"]),
                   tsv_escape(t["xpos"]), tsv_escape(t["upos"])]
            for layer in (ENT_L, REPORT_L, SECTION_L):
                anns = cols[t["id"]].get(layer)
                if not anns:
                    row += ["_"] * nf[layer]
                else:
                    row += ["|".join(a[i] for a in anns) for i in range(nf[layer])]
            rc = rel_cols.get(t["id"])
            row += ["|".join(x[0] for x in rc), "|".join(x[1] for x in rc)] if rc else ["_", "_"]
            out.append("\t".join(row))
        out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def to_conllu(doc):
    lines = [f"# newdoc id = {doc['doc_id']}", f"# object = {doc['doc_id']}"]
    ents = sorted([e for e in doc["entities"] if e["label"] != "ERROR_MESSAGE"], key=lambda e: (e["start"], -e["end"]))
    msgs = [e for e in doc["entities"] if e["label"] == "ERROR_MESSAGE"]
    par_of = {s["id"]: s["paragraph"] for s in doc["sentences"]}
    pars = {p["id"]: p for p in doc["paragraphs"]}
    rep_of = lambda pos: next(u for u in doc["reports"] if u["start"] <= pos < u["end"])
    by_sent = defaultdict(list)
    for t in doc["tokens"]:
        by_sent[t["sentence"]].append(t)
    last_par, last_rep = None, None
    for s in doc["sentences"]:
        ts = by_sent[s["id"]]
        u = rep_of(ts[0]["start"])
        if u["report_id"] != last_rep:
            lab = u["labels"]
            lines.append(f"# newpar report = {u['report_id']} | " + " ".join(f"{k}={lab[k]}" for k in REPORT_F[1:]))
            last_rep = u["report_id"]
        p_ = pars[par_of[s["id"]]]
        if p_["id"] != last_par:
            lines.append(f"# paragraph = {p_['id']} section={p_['section']}")
            last_par = p_["id"]
        lines.append(f"# sent_id = {doc['doc_id']}-{s['id']}")
        lines.append("# text = " + doc["text"][s["start"]:s["end"]].replace("\n", " "))
        for i, t in enumerate(ts, 1):
            misc = []
            e = next((e for e in ents if e["start"] <= t["start"] and t["end"] <= e["end"]), None)
            misc.append("NER=" + (("B-" if e["start"] == t["start"] else "I-") + e["label"] if e else "O"))
            m = next((m for m in msgs if m["start"] <= t["start"] and t["end"] <= m["end"]), None)
            if m:
                misc.append("MSG=" + ("B" if m["start"] == t["start"] else "I"))
            if e and e["attributes"].get("kb_uri") and e["start"] == t["start"]:
                misc.append("KB=" + e["attributes"]["kb_uri"].replace("http://dbpedia.org/resource/", "dbr:"))
            nxt = ts[i] if i < len(ts) else None
            if nxt is not None and nxt["start"] == t["end"]:
                misc.append("SpaceAfter=No")
            form = t["text"].replace(" ", " ")
            lines.append("\t".join([str(i), form, t["lemma"] or "_", t["upos"], t["xpos"], "_", "_", "_", "_", "|".join(misc)]))
        lines.append("")
    return "\n".join(lines) + "\n"


def load_final():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((OUT / "json").glob("*.json"))]


def cmd_export(args):
    for sub in ("tsv", "conllu"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    for doc in load_final():
        (OUT / "tsv" / f"{doc['doc_id']}.tsv").write_text(to_tsv(doc), encoding="utf-8")
        (OUT / "conllu" / f"{doc['doc_id']}.conllu").write_text(to_conllu(doc), encoding="utf-8")
        print(f"{doc['doc_id']}: tsv, conllu")


# ------------------------------------------------------------------ INCEpTION
PROJECT_NAME = "lr3-bug-triage-text"


def _feature(name, ftype="uima.cas.String", tagset=None, ui=None, desc=None, hover=True, visible=True):
    return {"name": name, "tag_set": tagset, "uiName": ui or name, "type": ftype, "enabled": True, "visible": visible,
            "include_in_hover": hover, "required": False, "remember": False, "hideUnconstraintFeature": False,
            "description": desc, "multi_value_mode": "NONE", "link_mode": "NONE", "link_type_name": None,
            "link_type_role_feature_name": None, "link_type_target_feature_name": None, "traits": None,
            "curatable": True, "rank": 0}


def _tagset(name, values, desc=None):
    return {"name": name, "description": desc, "language": "en",
            "tags": [{"tag_name": k, "tag_description": v} for k, v in values.items()], "create_tag": False}


def _layer(name, ui, ltype, features, attach=None, cross=True, overlap="ANY_OVERLAP", desc=None):
    return {"name": name, "features": features, "uiName": ui, "type": ltype, "description": desc, "enabled": True,
            "built_in": False, "readonly": False, "attach_type": {"name": attach} if attach else None,
            "attach_feature": None, "allow_stacking": ltype == "span", "cross_sentence": cross, "show_hover": True,
            "anchoring_mode": "TOKENS", "overlap_mode": overlap, "validation_mode": "ALWAYS",
            "lock_to_token_offset": False, "multiple_tokens": True, "linked_list_behavior": False,
            "on_click_javascript_action": None, "traits": None}


def inception_layers(schema):
    """Слои и наборы тегов проекта INCEpTION, построенные из annotation_schema/schema.json."""
    dl = schema["document_labels"]
    ts = {
        "BugEntity labels": _tagset("BugEntity labels", {k: v["description"] for k, v in schema["entity_labels"].items()}),
        "Section labels": _tagset("Section labels", schema["section_labels"]),
        "Relation types": _tagset("Relation types", {k: v["description"] for k, v in schema["relation_types"].items()}),
        "Category": _tagset("Category", dl["category"]["values"]),
        "Component": _tagset("Component", dl["component"]["values"]),
        "Severity": _tagset("Severity", dl["severity"]["values"]),
        "Ticket type": _tagset("Ticket type", dl["ticket_type"]["values"]),
        "Polarity": _tagset("Polarity", {"observed": "наблюдается", "negated": "отрицается", "hypothetical": "желаемое/условное"}),
    }
    rep = _layer(REPORT_L, "Bug report", "span", [
        _feature("report_id"), _feature("ticket_type", tagset=ts["Ticket type"], visible=False),
        _feature("category", tagset=ts["Category"]), _feature("component", tagset=ts["Component"], visible=False),
        _feature("severity", tagset=ts["Severity"]), _feature("confidence", visible=False)],
        overlap="NO_OVERLAP", desc="Уровень 1: баг-репорт внутри документа объекта и его классы")
    sec = _layer(SECTION_L, "Section (paragraph)", "span", [_feature("label", tagset=ts["Section labels"])],
                 overlap="NO_OVERLAP", desc="Уровень 2: абзац и тип раздела")
    ent = _layer(ENT_L, "Bug entity", "span", [
        _feature("label", tagset=ts["BugEntity labels"]), _feature("kind", visible=False), _feature("normalized", visible=False),
        _feature("affected", visible=False), _feature("polarity", tagset=ts["Polarity"], visible=False),
        _feature("source", visible=False), _feature("kb_uri", ui="DBpedia URI", visible=False)],
        cross=False, desc="Уровень 4: именованные сущности баг-репорта")
    rel = _layer(REL_L, "Entity relation", "relation", [
        _feature("type", tagset=ts["Relation types"]),
        _feature("Governor", f"rel-source:{ENT_L}", ui="Source", hover=False),
        _feature("Dependent", f"rel-target:{ENT_L}", ui="Target", hover=False)],
        attach=ENT_L, cross=True, overlap="ANY_OVERLAP", desc="Уровень 5: связи между сущностями")
    return [rep, sec, ent, rel], list(ts.values())


class Aero:
    def __init__(self, url, user, password):
        import base64
        self.url = url.rstrip("/") + "/api/aero/v1"
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(self, method, path, fields=None, files=None, raw=False):
        import urllib.request, uuid
        data, headers = None, {"Authorization": self.auth}
        if fields or files:
            b = uuid.uuid4().hex
            parts = []
            for k, v in (fields or {}).items():
                parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for k, (fname, content) in (files or {}).items():
                parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                             f'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b"\r\n")
            data = b"".join(parts) + f"--{b}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={b}"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req) as r:
            body = r.read()
        return body if raw else json.loads(body)


def cmd_inception(args):
    import io, zipfile
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    api = Aero(args.url, args.user, args.password)
    # 1) шаблон проекта: пустой проект -> экспорт -> добавляем свои слои и теги -> импорт
    for p_ in api.call("GET", "/projects")["body"]:
        if p_["name"] in (PROJECT_NAME, PROJECT_NAME + "-template"):
            api.call("DELETE", f"/projects/{p_['id']}")
    tmp = api.call("POST", f"/projects?name={PROJECT_NAME}-template&creator={args.user}")["body"]["id"]
    zbytes = api.call("GET", f"/projects/{tmp}/export.zip", raw=True)
    api.call("DELETE", f"/projects/{tmp}")
    zin = zipfile.ZipFile(io.BytesIO(zbytes))
    proj = json.loads(zin.read("exportedproject.json"))
    layers, tagsets = inception_layers(schema)
    proj.update({"name": PROJECT_NAME, "slug": PROJECT_NAME,
                 "description": "ЛР3 «Разметка текстовых данных»: 10 документов (по одному на категорию дефекта), "
                                "уровни: баг-репорт, абзац/раздел, предложение, токен, часть речи (UD + Penn), лемма, "
                                "сущности (схема v1.1, ссылки DBpedia), связи. Bug Triage Dataset."})
    proj["layers"] += layers
    proj["tag_sets"] += tagsets
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("exportedproject.json", json.dumps(proj, ensure_ascii=False))
    (OUT / "inception").mkdir(parents=True, exist_ok=True)
    (OUT / "inception" / "project_layers.json").write_text(
        json.dumps({"layers": layers, "tag_sets": tagsets}, ensure_ascii=False, indent=1), encoding="utf-8")
    pid = api.call("POST", "/projects/import", files={"file": ("project.zip", buf.getvalue())})["body"]["id"]
    print("проект INCEpTION:", PROJECT_NAME, "id", pid)
    # 2) документы с разметкой (WebAnno TSV 3.3) + аннотации разметчика
    for f in sorted((OUT / "tsv").glob("*.tsv")):
        r = api.call("POST", f"/projects/{pid}/documents",
                     fields={"name": f.stem, "format": "ctsv3"}, files={"content": (f.name, f.read_bytes())})
        did = r["body"]["id"]
        api.call("POST", f"/projects/{pid}/documents/{did}/annotations/{args.user}",
                 fields={"format": "ctsv3", "state": "COMPLETE"}, files={"content": (f.name, f.read_bytes())})
        print("  загружен", f.stem, "doc", did)
    # 3) экспорт обратно: проект целиком и XMI каждого документа (проверка round-trip)
    (OUT / "inception" / "xmi").mkdir(parents=True, exist_ok=True)
    for d in api.call("GET", f"/projects/{pid}/documents")["body"]:
        xmi = api.call("GET", f"/projects/{pid}/documents/{d['id']}/annotations/{args.user}?format=xmi", raw=True)
        (OUT / "inception" / "xmi" / f"{d['name']}.zip").write_bytes(xmi)
        print(f"  {d['name']}: выгружен UIMA CAS XMI")
    (OUT / "inception" / f"{PROJECT_NAME}.zip").write_bytes(api.call("GET", f"/projects/{pid}/export.zip", raw=True))
    print("экспорт проекта:", OUT / "inception" / f"{PROJECT_NAME}.zip")


def parse_tsv(text):
    """Разбор WebAnno TSV 3.3 (как его выгружает INCEpTION): span-слои -> {(layer, uid): {start,end,feats}},
    связи -> [(src_span, tgt_span, type)]. Порядок столбцов берётся из заголовка."""
    cols, layers = [], []
    for line in text.splitlines():
        m = re.match(r"#T_(SP|RL)=([^|]+)\|(.*)", line)
        if m:
            feats = m.group(3).split("|")
            layers.append((m.group(1), m.group(2), feats))
    spans, rows, rels = {}, [], []
    tok_span = {}
    for line in text.splitlines():
        if not re.match(r"\d+-\d+\t", line):
            continue
        parts = line.split("\t")
        tid, (b, e) = parts[0], map(int, parts[1].split("-"))
        rows.append((tid, b, e, parts))
    for tid, b, e, parts in rows:
        c = 3
        for kind, layer, feats in layers:
            vals = parts[c:c + len(feats)]
            c += len(feats)
            if kind == "SP":
                if all(v == "_" for v in vals):
                    continue
                n = len(vals[0].split("|"))
                for k in range(n):
                    fv = [v.split("|")[k] if v != "_" else "*" for v in vals]
                    mm = re.search(r"\[(\d+)\]$", fv[0])
                    uid = mm.group(1) if mm else f"t{tid}"
                    key = (layer, uid)
                    sp = spans.setdefault(key, {"start": b, "end": e, "feats": {}})
                    sp["end"] = max(sp["end"], e)
                    sp["start"] = min(sp["start"], b)
                    for f, v in zip(feats, fv):
                        v = re.sub(r"\[\d+\]$", "", v)
                        if v not in ("*", "_"):
                            sp["feats"][f] = re.sub(r"\\(.)", r"\1", v)
                    tok_span.setdefault((layer, tid), []).append(uid)
            else:
                if vals[0] == "_":
                    continue
                types, srcs = vals[0].split("|"), vals[-1].split("|")
                for ty, src in zip(types, srcs):
                    mm = re.match(r"(\d+-\d+)(?:\[(\d+)_(\d+)\])?", src)
                    s_uid = mm.group(2) if mm.group(2) and mm.group(2) != "0" else f"t{mm.group(1)}"
                    t_uid = mm.group(3) if mm.group(3) and mm.group(3) != "0" else f"t{tid}"
                    rels.append((s_uid, t_uid, re.sub(r"\\(.)", r"\1", ty)))
    return spans, rels


def compare_with_tsv(doc, tsv_text):
    spans, rels = parse_tsv(tsv_text)
    ents = {(v["start"], v["end"], v["feats"].get("label")) for (l, _), v in spans.items() if l == ENT_L}
    mine = {(e["start"], e["end"], e["label"]) for e in doc["entities"]}
    secs = {(v["start"], v["end"], v["feats"].get("label")) for (l, _), v in spans.items() if l == SECTION_L}
    mine_secs = set()
    for p_ in doc["paragraphs"]:
        tk = [t for t in doc["tokens"] if t["start"] >= p_["start"] and t["end"] <= p_["end"]]
        mine_secs.add((tk[0]["start"], tk[-1]["end"], p_["section"]))
    pos = {(v["start"], v["feats"].get("coarseValue")) for (l, _), v in spans.items() if l == POS_L}
    mine_pos = {(t["start"], t["upos"]) for t in doc["tokens"]}
    span_of = {uid: (v["start"], v["end"]) for (l, uid), v in spans.items() if l == ENT_L}
    rel_set = {(span_of.get(a), span_of.get(b), ty) for a, b, ty in rels}
    eid = {e["id"]: (e["start"], e["end"]) for e in doc["entities"]}
    mine_rel = {(eid[r["from"]], eid[r["to"]], r["type"]) for r in doc["relations"]}
    return {"entities": (len(ents & mine), len(mine)), "sections": (len(secs & mine_secs), len(mine_secs)),
            "pos": (len(pos & mine_pos), len(mine_pos)), "relations": (len(rel_set & mine_rel), len(mine_rel))}


def cmd_roundtrip(args):
    """Сравнить разметку, выгруженную из INCEpTION, с исходной (annotations/text/markup/json)."""
    api = Aero(args.url, args.user, args.password)
    pid = next(p_["id"] for p_ in api.call("GET", "/projects")["body"] if p_["name"] == PROJECT_NAME)
    docs = {d["doc_id"]: d for d in load_final()}
    ok = True
    for d in api.call("GET", f"/projects/{pid}/documents")["body"]:
        back = api.call("GET", f"/projects/{pid}/documents/{d['id']}/annotations/{args.user}?format=ctsv3", raw=True).decode("utf-8")
        (OUT / "inception" / "export_tsv").mkdir(parents=True, exist_ok=True)
        (OUT / "inception" / "export_tsv" / f"{d['name']}.tsv").write_text(back, encoding="utf-8")
        r = compare_with_tsv(docs[d["name"]], back)
        ok &= all(a == b for a, b in r.values())
        print(f"{d['name']:14s} " + "  ".join(f"{k}: {a}/{b}" for k, (a, b) in r.items()))
    print("round-trip без потерь" if ok else "ЕСТЬ РАСХОЖДЕНИЯ")


# ------------------------------------------------------------------ проверка
def cmd_validate(args):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    EL, RT, SL = schema["entity_labels"], schema["relation_types"], schema["section_labels"]
    upos = set(schema["text_markup"]["upos_tags"])
    errors = []
    for doc in load_final():
        d, text = doc["doc_id"], doc["text"]
        err = lambda m: errors.append(f"{d}: {m}")
        tstarts = {t["start"] for t in doc["tokens"]}
        tends = {t["end"] for t in doc["tokens"]}
        for t in doc["tokens"]:
            if text[t["start"]:t["end"]] != t["text"]:
                err(f"токен {t['id']} не совпадает с текстом")
            if t["upos"] not in upos:
                err(f"токен {t['id']}: неизвестный UPOS {t['upos']}")
        for p_ in doc["paragraphs"]:
            if p_["section"] not in SL:
                err(f"абзац {p_['id']}: неизвестный раздел {p_['section']}")
        seen = set()
        for e in doc["entities"]:
            w = f"сущность {e['id']} «{e['text'][:30]}»"
            if text[e["start"]:e["end"]] != e["text"]:
                err(w + ": текст не совпадает со смещениями")
            if e["start"] not in tstarts or e["end"] not in tends:
                err(w + ": границы не совпадают с токенами")
            if e["label"] not in EL:
                err(w + f": неизвестная метка {e['label']}"); continue
            if "\n" in e["text"]:
                err(w + ": сущность на нескольких строках")
            lim = schema["limits"]["entity_max_chars"]
            if len(e["text"]) > lim.get(e["label"], lim["default"]):
                err(w + ": длиннее допустимого")
            for k, spec in EL[e["label"]]["attributes"].items():
                v = e["attributes"].get(k)
                if spec.get("required") is True and v in (None, ""):
                    err(w + f": нет обязательного атрибута {k}")
                if v is not None and spec.get("type") == "enum" and v not in spec["values"]:
                    err(w + f": {k}={v} не из словаря")
            for k in e["attributes"]:
                if k not in EL[e["label"]]["attributes"]:
                    err(w + f": лишний атрибут {k}")
            key = (e["start"], e["end"], e["label"])
            if key in seen:
                err(w + ": дубликат")
            seen.add(key)
        for a in doc["entities"]:
            for b in doc["entities"]:
                if a is not b and overlaps(a, b) and not nested_ok(a, b) and a["start"] <= b["start"] and a["id"] < b["id"]:
                    err(f"пересекаются {a['label']} «{a['text']}» и {b['label']} «{b['text']}»")
        ent = {e["id"]: e for e in doc["entities"]}
        rs = set()
        for r in doc["relations"]:
            a, b = ent.get(r["from"]), ent.get(r["to"])
            if not a or not b:
                err(f"связь {r} ссылается на несуществующую сущность"); continue
            spec = RT.get(r["type"])
            if not spec:
                err(f"неизвестный тип связи {r['type']}"); continue
            if a["label"] not in spec["from"] or b["label"] not in spec["to"]:
                err(f"связь {r['type']}: {a['label']} -> {b['label']} не разрешена схемой")
            k = (r["from"], r["to"], r["type"])
            if k in rs:
                err(f"дубликат связи {k}")
            rs.add(k)
    if errors:
        print("\n".join(errors))
        sys.exit(f"ошибок: {len(errors)}")
    print("итоговая разметка ЛР3 соответствует схеме: ошибок нет")


# ------------------------------------------------------------------ анализ (шаг 6)
INK, INK2, MUTED, GRID, SURF, BLUE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb", "#2a78d6"
SECTION_ORDER = ["TITLE", "DESCRIPTION", "STEPS", "ACTUAL", "EXPECTED", "ENVIRONMENT", "STACK_TRACE", "CODE", "NOTES"]
CAT_ORDER = ["crash", "ui_layout", "authentication", "payment", "database", "network", "performance", "memory", "localization", "security"]


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "figure.facecolor": SURF, "axes.facecolor": SURF,
                         "savefig.facecolor": SURF, "axes.titlecolor": INK, "axes.titlesize": 11})
    return plt


def heatmap(path, rows, cols, M, title, fmt="{:d}", note=None):
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    plt = _plt()
    cmap = LinearSegmentedColormap.from_list("seq", ["#f2f6fc", "#9cc0ee", BLUE, "#123f7a"])
    A = np.array(M, dtype=float)
    fig, ax = plt.subplots(figsize=(0.62 * len(cols) + 2.4, 0.36 * len(rows) + 1.4))
    ax.imshow(A, cmap=cmap, aspect="auto", vmin=0, vmax=max(A.max(), 1))
    for i in range(len(rows)):
        for j in range(len(cols)):
            v = A[i, j]
            if v:
                ax.text(j, i, fmt.format(int(v) if fmt == "{:d}" else v), ha="center", va="center", fontsize=8,
                        color="#ffffff" if v > A.max() * 0.55 else INK)
    ax.set_xticks(range(len(cols)), cols, rotation=40, ha="right")
    ax.set_yticks(range(len(rows)), rows)
    ax.set_xticks([x - .5 for x in range(1, len(cols))], minor=True)
    ax.set_yticks([y - .5 for y in range(1, len(rows))], minor=True)
    ax.grid(which="minor", color=SURF, linewidth=2)
    ax.tick_params(which="both", length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title(title, loc="left", pad=10)
    if note:
        fig.text(0.01, 0.01, note, color=MUTED, fontsize=7.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def hbar(path, labels, values, title, xlabel, fmt="{:.2f}", xmax=None):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(6.4, 0.32 * len(labels) + 1.2))
    y = range(len(labels))
    ax.barh(y, values, height=0.62, color=BLUE)
    for i, v in zip(y, values):
        ax.text(v + (xmax or max(values)) * 0.01, i, fmt.format(v), va="center", fontsize=8, color=INK2)
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    ax.set_xlim(0, xmax or max(values) * 1.12)
    ax.set_xlabel(xlabel)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for k in ("top", "right", "left"):
        ax.spines[k].set_visible(False)
    ax.tick_params(length=0)
    ax.set_title(title, loc="left", pad=10)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def cmd_analyze(args):
    docs = {d["doc_id"]: d for d in load_final()}
    autos = {c: json.loads((OUT / "auto" / f"{c}.json").read_text(encoding="utf-8")) for c in docs}
    corpus = json.loads((OUT / "corpus.json").read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    labels = list(schema["entity_labels"])
    img = DOCS / "img"
    img.mkdir(parents=True, exist_ok=True)
    R = ["# Анализ многоуровневой разметки текстовых данных (ЛР3, шаг 6)", "",
         f"Создано командой `python scripts/text_markup.py analyze` ({date.today()}). Данные: "
         "`annotations/text/markup/json/` — итоговая разметка 10 документов (схема v1.1).", ""]
    # 1. корпус
    rows, tot = [], Counter()
    for c in CAT_ORDER:
        d = docs[c]
        n = {"chars": len(d["text"]), "reports": len(d["reports"]), "par": len(d["paragraphs"]), "sent": len(d["sentences"]),
             "tok": len(d["tokens"]), "ent": len(d["entities"]), "rel": len(d["relations"])}
        tot.update(n)
        rows.append([c, n["chars"], n["reports"], n["par"], n["sent"], n["tok"], n["ent"], n["rel"], f'{100 * n["ent"] / n["tok"]:.1f}'])
    rows.append(["**всего**", tot["chars"], tot["reports"], tot["par"], tot["sent"], tot["tok"], tot["ent"], tot["rel"],
                 f'{100 * tot["ent"] / tot["tok"]:.1f}'])
    R += ["## 1. Корпус и объём разметки", "",
          md_table(["документ (объект)", "символов", "репортов", "абзацев", "предложений", "токенов", "сущностей", "связей", "сущн. на 100 ток."], rows), ""]
    # 2. сущности x категория
    present = [l for l in labels if any(e["label"] == l for d in docs.values() for e in d["entities"])]
    M = [[sum(e["label"] == l for e in docs[c]["entities"]) for c in CAT_ORDER] for l in present]
    heatmap(img / "lr3_entities_by_category.png", present, CAT_ORDER, M, "Сущности по объектам (документам)")
    R += ["## 2. Типы сущностей × объекты", "", "![](img/lr3_entities_by_category.png)", ""]
    top = {}
    for c in CAT_ORDER:
        cnt = Counter((e["label"], e["attributes"].get("normalized") or e["text"]) for e in docs[c]["entities"]
                      if e["label"] in ("TECHNOLOGY", "SYMPTOM", "CODE_LOCATION", "EXCEPTION", "ERROR_CODE", "METRIC", "UI_ELEMENT", "LOCALE", "FEATURE"))
        top[c] = ", ".join(f"{t} ({l})" for (l, t), _ in cnt.most_common(4))
    R += [md_table(["объект", "характерные сущности (самые частые)"], [[c, top[c]] for c in CAT_ORDER]), ""]
    # 3. симптомы: kind x category, polarity x section
    kinds = schema["entity_labels"]["SYMPTOM"]["attributes"]["kind"]["values"]
    MK = [[sum(e["label"] == "SYMPTOM" and e["attributes"]["kind"] == k for e in docs[c]["entities"]) for c in CAT_ORDER] for k in kinds]
    heatmap(img / "lr3_symptom_kind_by_category.png", kinds, CAT_ORDER, MK, "SYMPTOM.kind по объектам")
    pol = ["observed", "negated", "hypothetical"]
    MP = [[sum(e["label"] == "SYMPTOM" and e["attributes"]["polarity"] == p_ and e["section"] == s_ for d in docs.values() for e in d["entities"])
           for s_ in SECTION_ORDER] for p_ in pol]
    sym_total = sum(map(sum, MP))
    exp_sym = sum(MP[i][SECTION_ORDER.index("EXPECTED")] for i in range(3))
    exp_non_obs = sum(MP[i][SECTION_ORDER.index("EXPECTED")] for i in (1, 2))
    R += ["## 3. Симптомы: вид и полярность", "", "![](img/lr3_symptom_kind_by_category.png)", "",
          md_table(["полярность \\ раздел"] + SECTION_ORDER, [[p_] + MP[i] for i, p_ in enumerate(pol)]), "",
          f"Всего симптомов: {sym_total}. В разделе EXPECTED {exp_sym} симптомов, из них {exp_non_obs} — "
          "не наблюдаемые (отрицание «does not freeze», «no flicker» или желаемое поведение). Без атрибута "
          "`polarity` классификатор считал бы «Firefox does not freeze» признаком зависания — поэтому атрибут добавлен в схему 1.1.", ""]
    # 4. сущности x раздел
    ME = [[sum(e["label"] == l and e["section"] == s_ for d in docs.values() for e in d["entities"]) for s_ in SECTION_ORDER] for l in present]
    heatmap(img / "lr3_entities_by_section.png", present, SECTION_ORDER, ME, "Сущности (уровень 4) по разделам абзацев (уровень 2)")
    R += ["## 4. Зависимость «раздел абзаца → тип сущности» (уровни 2 и 4)", "", "![](img/lr3_entities_by_section.png)", ""]
    # 5. POS: по разделам и в сущностях
    up = schema["text_markup"]["upos_tags"]
    sec_of_sent = {}
    for d in docs.values():
        ps = {p_["id"]: p_["section"] for p_ in d["paragraphs"]}
        for s_ in d["sentences"]:
            sec_of_sent[(d["doc_id"], s_["id"])] = ps[s_["paragraph"]]
    pos_sec = defaultdict(Counter)
    for d in docs.values():
        for t in d["tokens"]:
            pos_sec[sec_of_sent[(d["doc_id"], t["sentence"])]][t["upos"]] += 1
    main_pos = ["NOUN", "VERB", "PROPN", "ADJ", "NUM", "X", "PUNCT", "ADP", "DET", "PRON", "AUX"]
    MS = [[round(100 * pos_sec[s_][u] / max(sum(pos_sec[s_].values()), 1)) for s_ in SECTION_ORDER] for u in main_pos]
    heatmap(img / "lr3_pos_by_section.png", main_pos, SECTION_ORDER, MS, "Части речи (уровень 3) по разделам, % токенов раздела")
    pos_ent = defaultdict(Counter)
    for d in docs.values():
        for e in d["entities"]:
            if e["label"] == "ERROR_MESSAGE":
                continue
            for t in d["tokens"]:
                if e["start"] <= t["start"] and t["end"] <= e["end"]:
                    pos_ent[e["label"]][t["upos"]] += 1
    R += ["## 5. Зависимость «часть речи ↔ раздел ↔ сущность» (уровни 2, 3, 4)", "", "![](img/lr3_pos_by_section.png)", "",
          md_table(["сущность", "токенов", "частые UPOS"],
                   [[l, sum(pos_ent[l].values()), ", ".join(f"{u} {100 * n / sum(pos_ent[l].values()):.0f}%" for u, n in pos_ent[l].most_common(3))]
                    for l in present if pos_ent[l]]), ""]
    # 6. точность POS
    g, a = docs["memory"], autos["memory"]
    n = len(g["tokens"])
    acc_tag = sum(t["upos_tagger"] == x["upos"] for t, x in zip(a["tokens"], g["tokens"])) / n
    acc_rul = sum(t["upos"] == x["upos"] for t, x in zip(a["tokens"], g["tokens"])) / n
    fired = Counter()
    for d in autos.values():
        fired.update(d.get("pos_rules_fired", {}))
    changed = sum(t["upos"] != t["upos_tagger"] for d in autos.values() for t in d["tokens"])
    ntok = sum(len(d["tokens"]) for d in autos.values())
    R += ["## 6. Качество автоматической частеречной разметки", "",
          f"Эталон — документ `memory` ({n} токенов), все части речи проверены вручную. Точность UPOS: "
          f"теггер NLTK (Penn → UPOS) **{100 * acc_tag:.1f}%**, после межуровневых правил **{100 * acc_rul:.1f}%**. "
          f"Во всём корпусе правила изменили {changed} из {ntok} тегов ({100 * changed / ntok:.1f}%).", "",
          md_table(["правило", "срабатываний", "какой уровень использует"],
                   [[k, v, {"imperative_steps": "раздел STEPS (ур. 2)", "symptom_verb": "SYMPTOM (ур. 4)", "name_propn": "ENV/PRODUCT (ур. 4)",
                            "code_propn": "CODE_LOCATION/EXCEPTION (ур. 4)"}.get(k, "токен/контекст (ур. 3)")] for k, v in fired.most_common()]), ""]
    # 7. качество предразметки сущностей
    per = defaultdict(lambda: [0, 0, 0])
    for c in docs:
        A_ = {(e["start"], e["end"], e["label"]) for e in autos[c]["entities"]}
        F_ = {(e["start"], e["end"], e["label"]) for e in docs[c]["entities"]}
        for l in labels:
            al = {x for x in A_ if x[2] == l}; fl = {x for x in F_ if x[2] == l}
            per[l][0] += len(al & fl); per[l][1] += len(al); per[l][2] += len(fl)
    prow, f1s = [], []
    for l in present:
        tp, na, nf = per[l]
        P = tp / na if na else 0; Rr = tp / nf if nf else 0; F = 2 * P * Rr / (P + Rr) if P + Rr else 0
        prow.append([l, nf, na, f"{P:.2f}", f"{Rr:.2f}", f"{F:.2f}"]); f1s.append((l, F))
    tp = sum(v[0] for v in per.values()); na = sum(v[1] for v in per.values()); nf = sum(v[2] for v in per.values())
    P, Rr = tp / na, tp / nf
    hbar(img / "lr3_preannotation_f1.png", [l for l, _ in f1s], [f for _, f in f1s],
         "Качество автоматической предразметки сущностей (F1)", "F1 относительно итоговой разметки", xmax=1.05)
    edits = Counter()
    for d in docs.values():
        edits.update(d["meta"]["manual_changes"])
    R += ["## 7. Качество предразметки сущностей и объём ручной работы", "", "![](img/lr3_preannotation_f1.png)", "",
          md_table(["сущность", "в итоге", "в предразметке", "P", "R", "F1"], prow), "",
          f"Всего: P = {P:.2f}, R = {Rr:.2f}, F1 = {2 * P * Rr / (P + Rr):.2f}. Ручные правки: "
          + ", ".join(f"{k} — {v}" for k, v in edits.most_common()) + ".", ""]
    # 8. связи
    rt = Counter(); ends = Counter(); cross = 0; nrel = 0
    for d in docs.values():
        ent = {e["id"]: e for e in d["entities"]}
        sent = {t["start"]: t["sentence"] for t in d["tokens"]}
        for r in d["relations"]:
            a_, b_ = ent[r["from"]], ent[r["to"]]
            rt[r["type"]] += 1; ends[(r["type"], a_["label"], b_["label"])] += 1; nrel += 1
            cross += sent.get(a_["start"]) != sent.get(b_["start"])
    R += ["## 8. Связи между сущностями", "",
          md_table(["тип", "кол-во", "самые частые пары типов"],
                   [[t, n_, ", ".join([f"{a_}→{b_} ({k})" for (tt, a_, b_), k in ends.most_common() if tt == t][:3])] for t, n_ in rt.most_common()]), "",
          f"Связей всего {nrel}, из них между разными предложениями — {cross} ({100 * cross / nrel:.0f}%): "
          "поэтому слой связей в INCEpTION настроен с `cross_sentence = true`.", ""]
    # 9. окружение из User-Agent
    ua = [(d["doc_id"], u["source_project"], e) for d in docs.values() for e in d["entities"] if e["label"] == "ENV"
          and e["attributes"].get("source") == "user_agent" for u in d["reports"] if u["report_id"] == e["report_id"]]
    by_proj = defaultdict(Counter)
    for _, proj, e in ua:
        by_proj[proj][e["attributes"].get("affected", "unknown")] += 1
    R += ["## 9. Окружение из строки User-Agent", "",
          md_table(["проект", "ENV из UA", "affected=yes", "unknown"],
                   [[p_, sum(c.values()), c["yes"], c["unknown"]] for p_, c in sorted(by_proj.items())]), "",
          "В репортах Bugzilla поле User-Agent описывает браузер, из которого отправлен отчёт. Для Firefox это и есть "
          "окружение дефекта, а для Thunderbird — нет (в UA виден Firefox или Chrome автора). Поэтому в схему добавлен "
          "атрибут `ENV.source`, а браузер из UA в отчётах Thunderbird получает `affected=unknown`.", ""]
    # 10. структура по трекерам
    tr = defaultdict(Counter)
    for d in docs.values():
        for p_ in d["paragraphs"]:
            u = next(u for u in d["reports"] if u["report_id"] == p_["report_id"])
            tr[u["tracker"]][p_["section"]] += 1
    R += ["## 10. Трекер → структура документа (уровень 1 → уровень 2)", "",
          md_table(["трекер"] + SECTION_ORDER, [[t] + [c[s_] for s_ in SECTION_ORDER] for t, c in sorted(tr.items())]), "",
          "Шаблон Bugzilla даёт явные разделы STEPS/ACTUAL/EXPECTED, а в Jira и GitHub преобладают DESCRIPTION, CODE и NOTES. "
          "Значит, признаки на основе разделов надо нормировать по трекеру.", ""]
    # 11. DBpedia
    kb = [e for d in docs.values() for e in d["entities"] if e["label"] in ("PRODUCT", "TECHNOLOGY", "ENV", "LOCALE")]
    linked = [e for e in kb if e["attributes"].get("kb_uri")]
    res = Counter(e["attributes"]["kb_uri"].rsplit("/", 1)[-1] for e in linked)
    R += ["## 11. Связывание с DBpedia (семантический уровень)", "",
          f"Ссылку на ресурс DBpedia получили {len(linked)} из {len(kb)} сущностей PRODUCT/TECHNOLOGY/ENV/LOCALE "
          f"({100 * len(linked) / len(kb):.0f}%), различных ресурсов — {len(res)}. Самые частые: "
          + ", ".join(f"dbr:{k} ({v})" for k, v in res.most_common(10)) + ". Без ссылки остались версии продуктов "
          "(ENV app_version) и внутренние функции (Marketplace, Supernova), для которых в DBpedia нет отдельной статьи.", ""]
    # 12. предразметка категорий
    rej = corpus["rejected_on_selection"]
    wrong = [k for k, v in rej.items() if "ошибочна" in v or "спорна" in v]
    R += ["## 12. Предразметка категорий при отборе текстов", "",
          f"При отборе прочитано {sum(len(v) for v in SELECTION.values()) + len(rej) - 1} репортов-кандидатов; у {len(wrong)} "
          "из них категория, назначенная по ключевым словам заголовка (`scripts/categories.py`), оказалась ошибочной "
          "или спорной (например, «Network Settings panel» в заголовке дал категорию network для дефекта вёрстки). "
          "Ключевых слов заголовка недостаточно: нужны признаки SYMPTOM.kind, UI_ELEMENT и TECHNOLOGY из тела репорта.", ""]
    R += ["## 13. Выводы: найденные зависимости и изменения аннотационной схемы (шаг 6.2)", "",
          md_table(["найдено при анализе", "изменение схемы 1.0 → 1.1"], [
              ["тип раздела предсказывает тип сущности: ENV почти только в ENVIRONMENT/NOTES, UI_ELEMENT — в STEPS, SYMPTOM — в TITLE и ACTUAL (п. 4)",
               "раздел абзаца стал отдельным уровнем разметки (слой Section); правило: внутри CODE/цитат сущности не размечаются"],
              ["симптомы в EXPECTED и после отрицаний не наблюдаются («Firefox does not freeze») (п. 3)", "атрибут `SYMPTOM.polarity` = observed / negated / hypothetical"],
              ["ENV из User-Agent в отчётах Thunderbird — браузер автора, а не окружение продукта (п. 9)", "атрибут `ENV.source` = text / user_agent; affected=unknown для чужого браузера"],
              ["для локализации нет сущности языка; языки попадали в PRODUCT или терялись", "сущность `LOCALE` (код BCP 47, ссылка DBpedia)"],
              ["многие UI_ELEMENT на деле функции (Address Book, Payment methods, Translations) (п. 7: предразметка FEATURE R=0)", "сущность `FEATURE`; кандидат на автоматическое правило в следующей версии"],
              ["авторы называют причину симптома (memory leak → freezing, messageBody.css → not rendered) (п. 8)", "связь `CAUSED_BY`; `SYMPTOM_OF`, `VERSION_OF`; `OBSERVED_IN` принимает SYMPTOM и LOCALE"],
              ["коды ошибок и продукты встречаются внутри текста сообщения", "вложение сущностей разрешено только внутри ERROR_MESSAGE"],
              ["теггер ошибается на повелительных глаголах в STEPS, глаголах-симптомах и идентификаторах кода (п. 6)", "правила `text_markup.rules` (POS с учётом раздела и сущности): точность UPOS с 88,9% до 97,7%"],
              ["ключи конфигурации (hadoop.security…, --archive-command) — частая причина дефекта", "`CODE_LOCATION.kind = config`"],
              ["ссылки на другие тикеты (bug 1826149, SPARK-21551)", "сущность `ISSUE_REF` — для связей тикетов в графе знаний (ЛР6)"],
          ]), ""]
    (DOCS / "text_markup_analysis.md").write_text("\n".join(R) + "\n", encoding="utf-8")
    print("docs/text_markup_analysis.md + 5 графиков в docs/img/")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("corpus", "auto", "final", "export"):
        sub.add_parser(c)
    i = sub.add_parser("inception")
    i.add_argument("--url", default="http://localhost:8080")
    i.add_argument("--user", default="admin")
    i.add_argument("--password", required=True)
    r = sub.add_parser("roundtrip")
    r.add_argument("--url", default="http://localhost:8080")
    r.add_argument("--user", default="admin")
    r.add_argument("--password", required=True)
    sub.add_parser("validate")
    sub.add_parser("analyze")
    a = ap.parse_args()
    {"validate": cmd_validate, "analyze": cmd_analyze, "corpus": cmd_corpus, "auto": cmd_auto, "final": cmd_final, "export": cmd_export,
     "inception": cmd_inception, "roundtrip": cmd_roundtrip}[a.cmd](a)
