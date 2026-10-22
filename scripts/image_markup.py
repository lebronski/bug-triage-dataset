#!/usr/bin/env python3
"""Разметка изображений (Лабораторная работа №5).

Конвейер:
  auto      — шаг 3: полуавтоматическая предразметка 30 скриншотов. Распознавание текста
              Tesseract OCR (LSTM-модели rus+eng) + правила: классы изображения (категория,
              компонент, тип экрана, устройство, тема), области ERROR_TEXT / STATUS_INDICATOR /
              STACK_TRACE / DEFECT_AREA / PII с текстом и сущностями
              -> annotations/images/markup/auto/predictions.json
  final     — шаг 4: ручная разметка разметчика (annotations/images/markup/manual.json):
              рамки, нарисованные разметчиком, подгоняются вплотную к содержимому
              (fit: content / raw / words / chars), сущности получают смещения в ocr_text
              -> annotations/images/screenshots_coco.json (основной формат, COCO)
  export    — шаг 6: та же разметка в форматах Label Studio JSON (задачи для импорта с
              annotations + predictions), Pascal VOC XML, YOLO, CSV
  lsf       — шаги 1, 4, 5: открыть каждую задачу в редакторе разметки Label Studio
              (Label Studio Frontend, конфигурация images.xml), сохранить скриншоты редактора
              и сверить разметку, выгруженную из редактора, с исходной (round-trip)
  overlay   — шаг 5: нарисовать итоговые области на изображениях для визуальной проверки
  agree     — шаг 5: сверка со старыми эталонами (разметка вслепую), IoU и совпадение меток
  analyze   — анализ разметки и качества предразметки -> docs/image_markup_analysis.md + графики
  ls-import — создать проект в запущенном Label Studio и загрузить задачи с разметкой через API

Зависимости: pillow, pytesseract (+ tesseract-ocr с языками rus и eng), matplotlib, playwright.

Примеры:
  python scripts/image_markup.py auto --tessdata /path/to/tessdata
  python scripts/image_markup.py final && python scripts/image_markup.py export
  python scripts/image_markup.py lsf --lsf /path/to/label_studio/frontend/dist/lsf
  python scripts/image_markup.py analyze
  python scripts/image_markup.py ls-import --url http://localhost:8080 --token <API token>
"""
import argparse
import csv
import json
import os
import re
import sys
import threading
from collections import Counter, defaultdict
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "images" / "screenshots"
OUT = ROOT / "annotations" / "images" / "markup"
COCO_PATH = ROOT / "annotations" / "images" / "screenshots_coco.json"
SCHEMA_PATH = ROOT / "annotation_schema" / "schema.json"
LS_CONFIG = ROOT / "annotation_schema" / "label_studio" / "images.xml"
DOCS = ROOT / "docs"
ANNOTATOR = "lebowskiybig"
GOLD = {"database/database_01_sqlite_database_locked.png", "payment/payment_03_double_charge.png"}
REGION_ORDER = ["ERROR_TEXT", "STACK_TRACE", "STATUS_INDICATOR", "DEFECT_AREA", "UI_ELEMENT", "PII", "CHART"]
COLORS = {"ERROR_TEXT": "#e6194b", "STACK_TRACE": "#3cb44b", "STATUS_INDICATOR": "#4363d8", "DEFECT_AREA": "#f58231",
          "UI_ELEMENT": "#911eb4", "PII": "#46a0b0", "CHART": "#b8860b"}
# атрибуты областей: имя в COCO -> имя элемента управления в Label Studio
LS_ATTR = {("DEFECT_AREA", "defect_type"): "defect_type", ("DEFECT_AREA", "expected_text"): "expected_text",
           ("UI_ELEMENT", "kind"): "ui_kind", ("PII", "kind"): "pii_kind", ("STATUS_INDICATOR", "kind"): "status_kind",
           ("CHART", "chart_kind"): "chart_kind", ("CHART", "trend"): "trend"}
IMAGE_LABELS = ["category", "component", "screen_type", "is_defect", "severity", "device", "theme"]


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def all_images():
    return sorted(str(p.relative_to(SHOTS)).replace(os.sep, "/") for p in SHOTS.glob("*/*.png"))


# ------------------------------------------------------------------ геометрия
def ink_bbox(img, box, thr=30):
    """Рамка содержимого внутри box: пиксели, отличающиеся от фона (цвет, который чаще всего
    встречается на периметре box) больше чем на thr по любому каналу."""
    x, y, w, h = [int(round(v)) for v in box]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(img.width, x + w), min(img.height, y + h)
    px = img.load()
    per = Counter()
    for i in range(x0, x1):
        per[px[i, y0]] += 1
        per[px[i, y1 - 1]] += 1
    for j in range(y0, y1):
        per[px[x0, j]] += 1
        per[px[x1 - 1, j]] += 1
    bg = per.most_common(1)[0][0]
    xs, ys = [], []
    for j in range(y0, y1):
        for i in range(x0, x1):
            p = px[i, j]
            if max(abs(p[0] - bg[0]), abs(p[1] - bg[1]), abs(p[2] - bg[2])) > thr:
                xs.append(i)
                ys.append(j)
    if not xs:
        raise ValueError(f"в рамке {box} нет содержимого")
    return [min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1], bg


def word_boxes(img, box, thr=30):
    """Разбить строку на слова по вертикальным промежуткам без содержимого."""
    (x, y, w, h), bg = ink_bbox(img, box, thr)
    px = img.load()
    cols = []
    for i in range(x, x + w):
        cols.append(any(max(abs(px[i, j][k] - bg[k]) for k in range(3)) > thr for j in range(y, y + h)))
    gap_min = max(3, round(h * 0.22))
    words, start, gap = [], None, 0
    for k, ink in enumerate(cols):
        if ink:
            if start is None:
                start = k
            elif gap >= gap_min:
                words.append((start, last))
                start = k
            last, gap = k, 0
        else:
            gap += 1
    if start is not None:
        words.append((start, last))
    out = []
    for a, b in words:  # высота каждого слова — по его пикселям (фон тот же, что у строки)
        rows = [j for j in range(y, y + h)
                if any(max(abs(px[i, j][k] - bg[k]) for k in range(3)) > thr for i in range(x + a, x + b + 1))]
        out.append([x + a, rows[0], b - a + 1, rows[-1] - rows[0] + 1])
    return out


def union(boxes):
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return [x0, y0, x1 - x0, y1 - y0]


def fit_box(img, rough, fit):
    if fit == "raw":
        return [int(v) for v in rough]
    if fit == "content":
        return ink_bbox(img, rough)[0]
    if "words" in fit:
        ws = word_boxes(img, rough)
        n = fit.get("n", len(fit["line"].split()))  # n — если знаки препинания отделились от слов
        if len(ws) != n:
            raise ValueError(f"найдено слов {len(ws)}, в строке {n}: {fit['line']!r}")
        i, j = fit["words"]
        return union(ws[i:j + 1])
    if "chars" in fit and fit.get("mono"):
        # вся строка моноширинная и начинается с первого символа: ширина символа = ширина строки / длина
        (x, y, w, h), _ = ink_bbox(img, rough)
        line = fit["line"]
        cw = w / len(line)
        start = -1
        for _ in range(fit.get("occ", 0) + 1):
            start = line.index(fit["chars"], start + 1)
        x0, x1 = x + round(start * cw), x + round((start + len(fit["chars"])) * cw)
        return [x0, y, x1 - x0, h]
    if "chars" in fit:
        # подстрока строки: строка делится на слова по промежуткам, затем внутри крайних слов
        # граница ставится по ширине символа этого слова (моноширинный шрифт)
        line = fit["line"]
        toks, pos = [], 0
        for t in line.split():
            a = line.index(t, pos)
            toks.append((a, a + len(t)))
            pos = a + len(t)
        ws = word_boxes(img, rough)
        if len(ws) != len(toks):
            raise ValueError(f"найдено слов {len(ws)}, в строке {len(toks)}: {line!r}")
        start = -1
        for _ in range(fit.get("occ", 0) + 1):
            start = line.index(fit["chars"], start + 1)
        end = start + len(fit["chars"])
        k0 = next(k for k, (a, b) in enumerate(toks) if a <= start < b)
        k1 = next(k for k, (a, b) in enumerate(toks) if a < end <= b)
        cw0 = ws[k0][2] / (toks[k0][1] - toks[k0][0])
        cw1 = ws[k1][2] / (toks[k1][1] - toks[k1][0])
        x0 = ws[k0][0] + round((start - toks[k0][0]) * cw0)
        x1 = ws[k1][0] + round((end - toks[k1][0]) * cw1)
        y0 = min(w[1] for w in ws[k0:k1 + 1])
        y1 = max(w[1] + w[3] for w in ws[k0:k1 + 1])
        return [x0, y0, x1 - x0, y1 - y0]
    raise ValueError(f"неизвестный fit {fit}")


def edge_dev(a, b):
    """Наибольшее расхождение сторон двух рамок, px."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[0] + a[2] - b[0] - b[2]), abs(a[1] + a[3] - b[1] - b[3]))


def same_box(a, b, iou_thr=0.7, tol=5):
    """Совпадение рамок по схеме 1.2: IoU ≥ 0,7 или все стороны в пределах допуска 5 px."""
    return iou(a, b) >= iou_thr or edge_dev(a, b) <= tol


def iou(a, b):
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0, min(ax1, bx1) - max(a[0], b[0]))
    ih = max(0, min(ay1, by1) - max(a[1], b[1]))
    inter = iw * ih
    u = a[2] * a[3] + b[2] * b[3] - inter
    return inter / u if u else 0.0


# ------------------------------------------------------------------ сущности
def place_entities(text, ents, where):
    out, used = [], Counter()
    for k, e in enumerate(ents, 1):
        label, frag, attrs = e[0], e[1], e[2]
        occ = e[3] if len(e) > 3 else 0
        pos = -1
        for _ in range(occ + 1):
            pos = text.find(frag, pos + 1)
            if pos < 0:
                raise ValueError(f"{where}: сущность {frag!r} не найдена в ocr_text")
        out.append({"id": f"e{k}", "label": label, "start": pos, "end": pos + len(frag), "text": frag,
                    "attributes": attrs})
    return out


# ------------------------------------------------------------------ шаг 3: предразметка
RE_EXC = re.compile(r"\b(?:[a-z_][\w.]*\.)?([A-Z]\w*(?:Error|Exception))\b")
RE_HTTP = re.compile(r"\b([1-5]\d\d)\s+(Unauthorized|Forbidden|Internal Server Error|Not Found|Bad Request)\b|"
                     r"\bStatus:\s*([1-5]\d\d)\b|\((\d{3}) card_declined\)")
RE_NET = re.compile(r"\b(?:NET::)?ERR_[A-Z_]+\b")
RE_EXIT = re.compile(r"\[exit code (\d+)\]")
RE_METRIC = re.compile(r"\b(\d+(?:[.,]\d+)?)\s?(мс|ms|МБ|MB|с|s|минут|часа)\b")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
RE_CARD = re.compile(r"\b\d{4}(?: \d{4}){3}\b|•{3,4} ?\d{4}")
RE_PROMPT = re.compile(r"\b(\w+@[\w-]+)(?=:\s?~)")
RE_KEY = re.compile(r"^[a-z]+(?:\.[a-z_]+){1,3}$")
RE_ERRWORDS = re.compile(r"(Error|Exception|FATAL|can.t be reached|refused|not private|Failed|отклон|не сохранен|"
                         r"Forbidden|Unauthorized|Неверный|недостаточно|списано|заблокирован|invalid|expired)", re.I)
RE_MOJI = re.compile(r"[РС][\u0080-¿Ё-Џё-џ‘-›°±µ¶·»«]")

CAT_RULES = [  # ключевые слова на экране -> категория (порядок важен)
    ("security", r"XSS|document\.cookie|not private|CERT_|session_user_id"),
    ("memory", r"heap|MemoryError|out of memory|куч"),
    ("database", r"sqlite|IntegrityError|database is locked|no such table|SQL"),
    ("network", r"ERR_NAME_NOT_RESOLVED|ERR_CONNECTION|Failed to fetch|синхронизац"),
    ("payment", r"card_declined|Оплат|Счёт|списан|₽"),
    ("authentication", r"Unauthorized|Forbidden|token|Вход|Логин|Пароль"),
    ("localization", r"settings\.\w|common\.actions|en-US|ru-RU|Р[°µ]Р"),
    ("performance", r"Загрузка|SLA|Long Task|время ответа|Время ответа"),
    ("crash", r"Traceback|TypeError|AttributeError|exit code"),
    ("ui_layout", r"."),
]
DEFAULT_COMPONENT = {"crash": "Backend", "ui_layout": "Frontend", "authentication": "Auth-service", "payment": "Billing",
                     "database": "Database", "network": "Backend", "performance": "Backend", "memory": "Backend",
                     "localization": "Frontend", "security": "Backend"}


def ocr_lines(img, lang="rus+eng"):
    import pytesseract
    scale = 2
    big = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    d = pytesseract.image_to_data(big, lang=lang, config="--psm 3", output_type=pytesseract.Output.DICT)
    lines = {}
    for i, t in enumerate(d["text"]):
        if not t.strip() or float(d["conf"][i]) < 0:
            continue
        k = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        x, y, w, h = [v // scale for v in (d["left"][i], d["top"][i], d["width"][i], d["height"][i])]
        L = lines.setdefault(k, {"words": [], "boxes": [], "conf": []})
        L["words"].append(t)
        L["boxes"].append([x, y, w, h])
        L["conf"].append(float(d["conf"][i]))
    return [{"text": " ".join(L["words"]), "bbox": union(L["boxes"]), "words": L["words"], "word_boxes": L["boxes"],
             "conf": sum(L["conf"]) / len(L["conf"])} for L in lines.values()]


def auto_entities(text):
    ents = []
    for m in RE_EXC.finditer(text):
        ents.append(("EXCEPTION", m.start(), m.end(), {"normalized": m.group(1)}))
    for m in RE_NET.finditer(text):
        ents.append(("ERROR_CODE", m.start(), m.end(), {"kind": "net_error", "normalized": m.group(0).replace("NET::", "")}))
    for m in RE_HTTP.finditer(text):
        g = next(i for i in (1, 3, 4) if m.group(i))
        ents.append(("ERROR_CODE", m.start(g), m.end(g), {"kind": "http_status", "normalized": m.group(g)}))
    for m in RE_EXIT.finditer(text):
        ents.append(("ERROR_CODE", m.start(1), m.end(1), {"kind": "exit_code", "normalized": m.group(1)}))
    for m in RE_METRIC.finditer(text):
        unit = m.group(2)
        kind = "memory" if unit in ("МБ", "MB") else "time"
        ents.append(("METRIC", m.start(), m.end(), {"kind": kind, "normalized": m.group(0)}))
    ents.sort(key=lambda e: e[1])
    keep, last = [], -1
    for e in ents:
        if e[1] >= last:
            keep.append(e)
            last = e[2]
    return [{"id": f"e{i}", "label": l, "start": a, "end": b, "text": text[a:b], "attributes": at}
            for i, (l, a, b, at) in enumerate(keep, 1)]


def predict_image(rel, img, lines):
    text = "\n".join(l["text"] for l in lines)
    from PIL import ImageStat
    mean = ImageStat.Stat(img.convert("L")).mean[0]
    theme = "dark" if mean < 90 else "light"
    device = "mobile" if img.width < 1000 else "desktop"
    if theme == "dark":
        screen = "console"
    elif re.search(r"Pretty-print|^\s*[{\"]", text, re.M) and re.search(r'"\w+":', text):
        screen = "api_response"
    elif re.search(r"can.t be reached|not private|ERR_", text):
        screen = "browser_error"
    elif re.search(r"Traceback|Request URL|\b[45]\d\d\b (Forbidden|Not Found)", text):
        screen = "error_page"
    else:
        screen = "ui_screen"
    cat = next(c for c, rx in CAT_RULES if re.search(rx, text))
    labels = {"category": cat, "component": DEFAULT_COMPONENT[cat], "screen_type": screen, "is_defect": "yes",
              "severity": None, "device": device, "theme": theme}
    regions, n = [], 0

    def add(label, box, ocr=None, **attrs):
        nonlocal n
        n += 1
        r = {"id": f"p{n}", "label": label, "bbox": [int(v) for v in box], "attributes": dict(attrs)}
        if ocr is not None:
            r["ocr_text"] = ocr
            r["entities"] = auto_entities(ocr)
        regions.append(r)

    # трассировка стека: от «Traceback» до строки с исключением; строки «at ...» Node.js
    i = 0
    while i < len(lines):
        t = lines[i]["text"]
        if t.startswith("Traceback"):
            j = i
            while j + 1 < len(lines) and not RE_EXC.match(lines[j]["text"]) and lines[j + 1]["bbox"][1] - lines[j]["bbox"][1] < 40:
                j += 1
            add("STACK_TRACE", union([l["bbox"] for l in lines[i:j + 1]]))
            i = j + 1
            continue
        if t.startswith("at ") and (i == 0 or not lines[i - 1]["text"].startswith("at ")):
            j = i
            while j + 1 < len(lines) and lines[j + 1]["text"].startswith("at "):
                j += 1
            add("STACK_TRACE", union([l["bbox"] for l in lines[max(0, i - 1):j + 1]]))
        i += 1
    for l in lines:
        t, b = l["text"], l["bbox"]
        if RE_NET.search(t) and len(t) < 40:
            add("STATUS_INDICATOR", b, t, kind="net_error")
        elif RE_EXIT.search(t):
            add("STATUS_INDICATOR", b, t, kind="exit_code")
        elif re.match(r"^[1-5]\d\d\s", t):
            add("STATUS_INDICATOR", b, t, kind="http_status")
        elif RE_ERRWORDS.search(t):
            add("ERROR_TEXT", b, t)
        if RE_KEY.match(t.strip()):
            add("DEFECT_AREA", b, t.strip(), defect_type="untranslated")
        if RE_MOJI.search(t) and len(RE_MOJI.findall(t)) >= 3:
            add("DEFECT_AREA", b, t, defect_type="mojibake")
        if "NaN" in t:
            k = l["words"].index(next(w for w in l["words"] if "NaN" in w))
            add("DEFECT_AREA", l["word_boxes"][k], l["words"][k], defect_type="wrong_value")
        for rx, kind in ((RE_EMAIL, "email"), (RE_CARD, "card"), (RE_PROMPT, "host")):
            for m in rx.finditer(t):
                # рамка — по словам строки, попавшим в найденный фрагмент
                pos, ws = 0, []
                for w, wb in zip(l["words"], l["word_boxes"]):
                    a = t.find(w, pos)
                    if a < m.end() and a + len(w) > m.start():
                        ws.append(wb)
                    pos = a + len(w)
                if ws:
                    add("PII", union(ws), m.group(1) if kind == "host" else m.group(0), kind=kind)
    return labels, regions


def cmd_auto(args):
    if args.tessdata:
        os.environ["TESSDATA_PREFIX"] = args.tessdata
    out = {"model_version": "tesseract-5-lstm-rus+eng + rules-1.0", "images": {}}
    for rel in all_images():
        img = Image.open(SHOTS / rel).convert("RGB")
        lines = ocr_lines(img)
        labels, regions = predict_image(rel, img, lines)
        out["images"][rel] = {"labels": labels, "regions": regions,
                              "ocr_lines": [{k: l[k] for k in ("text", "bbox", "conf")} for l in lines]}
        print(f"{rel}: {len(lines)} строк OCR, {len(regions)} областей, категория {labels['category']}")
    save_json(OUT / "auto" / "predictions.json", out)
    print(f"записан {(OUT / 'auto' / 'predictions.json').relative_to(ROOT)}")


# ------------------------------------------------------------------ шаг 4: итоговая разметка
def build_final():
    man = load_json(OUT / "manual.json")
    final = {}
    for rel, spec in man["images"].items():
        img = Image.open(SHOTS / rel).convert("RGB")
        regs = []
        for r in spec["regions"]:
            where = f"{rel} {r['id']}"
            try:
                box = fit_box(img, r["rough"], r.get("fit", "content"))
            except ValueError as e:
                raise SystemExit(f"{where}: {e}")
            reg = {"id": r["id"], "label": r["label"], "bbox": box, "attributes": dict(r.get("attributes", {}))}
            if "ocr_text" in r:
                reg["ocr_text"] = r["ocr_text"]
            if r.get("entities"):
                reg["entities"] = place_entities(r["ocr_text"], r["entities"], where)
            regs.append(reg)
        final[rel] = {"labels": spec["labels"], "regions": regs, "relations": spec.get("relations", []),
                      "note": spec.get("note"), "size": [img.width, img.height]}
    return man, final


def to_coco(man, final):
    s = load_json(SCHEMA_PATH)
    cats = list(s["image_labels"]["region_labels"])
    cat_id = {c: i for i, c in enumerate(cats, 1)}
    coco = {"info": {"description": "Bug Triage Dataset — разметка скриншотов (ЛР5, аннотационная схема v"
                     f"{s['version']})", "version": s["version"], "year": 2026, "contributor": ANNOTATOR,
                     "date_created": man["created"]},
            "licenses": [{"id": 1, "name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"}],
            "images": [], "categories": [{"id": cat_id[c], "name": c, "supercategory": "screen_region"} for c in cats],
            "annotations": []}
    aid = 0
    for iid, rel in enumerate(sorted(final), 1):
        f = final[rel]
        w, h = f["size"]
        ids = {}
        for r in f["regions"]:
            aid += 1
            ids[r["id"]] = aid
            at = dict(r["attributes"])
            if "ocr_text" in r:
                at = {"ocr_text": r["ocr_text"], **at}
            if r.get("entities"):
                at["entities"] = r["entities"]
            x, y, bw, bh = r["bbox"]
            coco["annotations"].append({"id": aid, "image_id": iid, "category_id": cat_id[r["label"]], "bbox": r["bbox"],
                                        "area": bw * bh, "iscrowd": 0, "attributes": at})
        attrs = {**f["labels"], "annotator": ANNOTATOR, "created": man["created"],
                 "status": "gold" if rel in GOLD else "draft",
                 "relations": [{"from": ids[a], "to": ids[b], "type": t} for a, b, t in f["relations"]]}
        if f.get("note"):
            attrs["note"] = f["note"]
        coco["images"].append({"id": iid, "file_name": rel, "width": w, "height": h, "license": 1, "attributes": attrs})
    return coco


def cmd_final(args):
    man, final = build_final()
    save_json(OUT / "final.json", final)
    coco = to_coco(man, final)
    save_json(COCO_PATH, coco)
    print(f"{len(final)} изображений, {len(coco['annotations'])} областей, "
          f"{sum(len(a['attributes'].get('entities', [])) for a in coco['annotations'])} сущностей, "
          f"{sum(len(i['attributes']['relations']) for i in coco['images'])} связей -> {COCO_PATH.relative_to(ROOT)}")


# ------------------------------------------------------------------ шаг 6: экспорт
def ls_results(f, regions, relations=(), with_image_labels=True):
    """Разметка одного изображения в формате результатов Label Studio."""
    w, h = f["size"]
    res = []
    if with_image_labels:
        for k in IMAGE_LABELS:
            v = f["labels"].get(k)
            if v is not None:
                res.append({"id": f"img_{k}", "from_name": k, "to_name": "image", "type": "choices",
                            "value": {"choices": [v]}})
    for r in regions:
        x, y, bw, bh = r["bbox"]
        geo = {"x": round(100 * x / w, 4), "y": round(100 * y / h, 4), "width": round(100 * bw / w, 4),
               "height": round(100 * bh / h, 4), "rotation": 0}
        base = {"id": r["id"], "to_name": "image", "original_width": w, "original_height": h, "image_rotation": 0}
        res.append({**base, "from_name": "region", "type": "rectanglelabels",
                    "value": {**geo, "rectanglelabels": [r["label"]]}})
        if r.get("ocr_text"):
            res.append({**base, "from_name": "ocr_text", "type": "textarea", "value": {**geo, "text": [r["ocr_text"]]}})
        for k, v in r.get("attributes", {}).items():
            ctl = LS_ATTR.get((r["label"], k))
            if not ctl:
                continue
            if ctl == "expected_text":
                res.append({**base, "from_name": ctl, "type": "textarea", "value": {**geo, "text": [v]}})
            else:
                res.append({**base, "from_name": ctl, "type": "choices", "value": {**geo, "choices": [v]}})
    for a, b, t in relations:
        res.append({"from_id": a, "to_id": b, "type": "relation", "direction": "right", "labels": [t]})
    return res


def cmd_export(args):
    man, final = build_final()
    pred = load_json(OUT / "auto" / "predictions.json") if (OUT / "auto" / "predictions.json").exists() else None
    # Label Studio: задачи для импорта (итоговая разметка = annotations, предразметка = predictions)
    tasks = []
    for k, rel in enumerate(sorted(final), 1):
        f = final[rel]
        # сущности уровня 3 (смещения в ocr_text) Label Studio в разметке изображения не хранит —
        # они передаются в данных задачи и остаются в COCO
        t = {"id": k, "data": {"image": f"/data/local-files/?d=data/images/screenshots/{rel}", "file": rel,
                               "entities_by_region": {r["id"]: r["entities"] for r in f["regions"] if r.get("entities")}},
             "annotations": [{"completed_by": 1, "result": ls_results(f, f["regions"], f["relations"]),
                              "ground_truth": rel in GOLD}]}
        if pred:
            p = pred["images"][rel]
            t["predictions"] = [{"model_version": pred["model_version"],
                                 "result": ls_results({"size": f["size"], "labels": p["labels"]}, p["regions"])}]
        tasks.append(t)
    save_json(OUT / "labelstudio" / "tasks.json", tasks)
    # Pascal VOC
    voc = OUT / "voc"
    voc.mkdir(parents=True, exist_ok=True)
    for rel in sorted(final):
        f = final[rel]
        w, h = f["size"]
        objs = []
        for r in f["regions"]:
            x, y, bw, bh = r["bbox"]
            at = "".join(f"\n      <{k}>{escape(str(v))}</{k}>" for k, v in r["attributes"].items())
            txt = f"\n      <ocr_text>{escape(r['ocr_text'])}</ocr_text>" if r.get("ocr_text") else ""
            objs.append(f"""  <object>
    <name>{r['label']}</name>
    <pose>Unspecified</pose>
    <truncated>0</truncated>
    <difficult>0</difficult>
    <bndbox><xmin>{x}</xmin><ymin>{y}</ymin><xmax>{x + bw}</xmax><ymax>{y + bh}</ymax></bndbox>
    <attributes>
      <region_id>{r['id']}</region_id>{txt}{at}
    </attributes>
  </object>""")
        lab = "".join(f"\n    <{k}>{escape(str(v))}</{k}>" for k, v in f["labels"].items() if v is not None)
        xml = f"""<annotation>
  <folder>{rel.split('/')[0]}</folder>
  <filename>{rel.split('/')[1]}</filename>
  <path>data/images/screenshots/{rel}</path>
  <source><database>Bug Triage Dataset</database></source>
  <size><width>{w}</width><height>{h}</height><depth>3</depth></size>
  <segmented>0</segmented>
  <image_labels>{lab}
  </image_labels>
{chr(10).join(objs)}
</annotation>
"""
        (voc / (Path(rel).stem + ".xml")).write_text(xml, encoding="utf-8")
    # YOLO
    yolo = OUT / "yolo"
    (yolo / "labels").mkdir(parents=True, exist_ok=True)
    cls = load_json(SCHEMA_PATH)["image_labels"]["region_labels"]
    cls = list(cls)
    (yolo / "classes.txt").write_text("\n".join(cls) + "\n", encoding="utf-8")
    for rel in sorted(final):
        f = final[rel]
        w, h = f["size"]
        rows = []
        for r in f["regions"]:
            x, y, bw, bh = r["bbox"]
            rows.append(f"{cls.index(r['label'])} {(x + bw / 2) / w:.6f} {(y + bh / 2) / h:.6f} {bw / w:.6f} {bh / h:.6f}")
        (yolo / "labels" / (Path(rel).stem + ".txt")).write_text("\n".join(rows) + "\n", encoding="utf-8")
    (yolo / "dataset.yaml").write_text(
        "# YOLO: пути к изображениям — data/images/screenshots/<категория>/<файл>.png, метки — labels/<файл>.txt\n"
        "path: ../../../..\ntrain: data/images/screenshots\nval: data/images/screenshots\n"
        f"nc: {len(cls)}\nnames: {json.dumps(cls)}\n", encoding="utf-8")
    # CSV
    with open(OUT / "regions.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["file", "category", "component", "screen_type", "is_defect", "severity", "region_id", "label",
                     "x", "y", "width", "height", "attributes", "ocr_text", "entities"])
        for rel in sorted(final):
            f = final[rel]
            for r in f["regions"]:
                wr.writerow([rel] + [f["labels"][k] or "" for k in ("category", "component", "screen_type", "is_defect", "severity")]
                            + [r["id"], r["label"], *r["bbox"], json.dumps(r["attributes"], ensure_ascii=False),
                               r.get("ocr_text", ""),
                               "; ".join(f"{e['label']}:{e['text']}" for e in r.get("entities", []))])
    print("записаны: labelstudio/tasks.json, voc/*.xml, yolo/labels/*.txt + classes.txt, regions.csv "
          f"({sum(len(f['regions']) for f in final.values())} областей)")


# ------------------------------------------------------------------ Label Studio Frontend
LSF_HTML = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/__lsf/css/main.css"><style>body{margin:0;font-family:sans-serif}</style></head>
<body><div id="label-studio"></div><script src="/__lsf/js/main.js"></script></body></html>"""


class _Handler(SimpleHTTPRequestHandler):
    lsf_dir = None

    def translate_path(self, path):
        p = path.split("?", 1)[0]
        if p.startswith("/__lsf/"):
            return str(Path(self.lsf_dir) / p[len("/__lsf/"):])
        return super().translate_path(path)

    def do_GET(self):
        if self.path.startswith("/__page"):
            body = LSF_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, *a):
        pass


def _norm_result(res):
    """Привести результаты Label Studio к сравнимому виду (без служебных полей)."""
    out = set()
    for r in res:
        if r.get("type") == "relation":
            out.add(("rel", r["from_id"], r["to_id"], tuple(r.get("labels", []))))
            continue
        v = r["value"]
        # координаты в процентах переводятся в пиксели: редактор хранит проценты с другой точностью
        ow, oh = r.get("original_width", 100), r.get("original_height", 100)
        geo = (tuple(round(v[k] * d / 100) for k, d in (("x", ow), ("y", oh), ("width", ow), ("height", oh)))
               if "x" in v else ())
        val = v.get("rectanglelabels") or v.get("choices") or v.get("text")
        out.add((r.get("id") if geo else r["from_name"], r["from_name"], geo, tuple(val)))
    return out


def cmd_lsf(args):
    from playwright.sync_api import sync_playwright
    man, final = build_final()
    config = LS_CONFIG.read_text(encoding="utf-8")
    _Handler.lsf_dir = args.lsf
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    shots = set(args.shots.split(",")) if args.shots else set()
    exported, report = [], []
    (DOCS / "img").mkdir(exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_page(viewport={"width": 1500, "height": 1250}, device_scale_factor=1)
        for k, rel in enumerate(sorted(final), 1):
            f = final[rel]
            task = {"id": k, "data": {"image": f"{base}/data/images/screenshots/{rel}", "file": rel},
                    "annotations": [{"id": k, "result": ls_results(f, f["regions"], f["relations"])}], "predictions": []}
            page.goto(f"{base}/__page")
            page.evaluate("""([config, task]) => { window.__store = null;
                new LabelStudio('label-studio', {config, task,
                  interfaces: ['panel','update','controls','side-column','annotations:menu','infobar'],
                  user: {id: 1, pk: 1, firstName: 'lebowskiybig', lastName: ''},
                  onLabelStudioLoad: (ls) => { const a = ls.annotationStore.annotations[0];
                      if (a) ls.annotationStore.selectAnnotation(a.id); window.__store = ls; }}); }""", [config, task])
            page.wait_for_function("() => { const s = window.__store; const a = s && s.annotationStore.selected;"
                                   " const im = document.querySelector('img');"
                                   " return a && a.regionStore.regions.length > 0 && im && im.complete && im.naturalWidth > 0 }",
                                   timeout=30000)
            page.wait_for_timeout(700)
            got = page.evaluate("() => window.__store.annotationStore.selected.serializeAnnotation()")
            want = task["annotations"][0]["result"]
            a, b = _norm_result(want), _norm_result(got)
            report.append((rel, len(want), len(got), len(a - b), len(b - a)))
            exported.append({"id": k, "data": {"image": f"/data/local-files/?d=data/images/screenshots/{rel}"},
                             "annotations": [{"result": got}]})
            stem = Path(rel).stem
            if stem in shots:
                sel = args.select.get(stem) if isinstance(args.select, dict) else None
                if sel:
                    page.evaluate("""(rid) => { const a = window.__store.annotationStore.selected;
                        const r = a.regionStore.regions.find(x => x.cleanId === rid || x.id.startsWith(rid + '#') || x.id === rid);
                        if (r) a.selectArea(r); }""", sel)
                    page.wait_for_timeout(400)
                page.screenshot(path=str(DOCS / "img" / f"lr5_ls_{stem}.png"), full_page=True)
                print("скриншот редактора:", f"docs/img/lr5_ls_{stem}.png")
        br.close()
    srv.shutdown()
    save_json(OUT / "labelstudio" / "export_from_editor.json", exported)
    ok = all(x[3] == 0 and x[4] == 0 for x in report)
    lines = ["| Изображение | результатов задано | выгружено из редактора | потеряно | лишних |", "|---|---:|---:|---:|---:|"]
    lines += [f"| `{r}` | {a} | {b} | {c} | {d} |" for r, a, b, c, d in report]
    (OUT / "labelstudio" / "roundtrip.md").write_text(
        "# Сверка разметки, выгруженной из редактора Label Studio\n\n"
        f"Конфигурация: `annotation_schema/label_studio/images.xml`. Итог: "
        f"{'расхождений нет' if ok else 'есть расхождения'}.\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print("round-trip:", "без потерь" if ok else "ЕСТЬ РАСХОЖДЕНИЯ", f"({len(report)} задач)")
    if not ok:
        for x in report:
            if x[3] or x[4]:
                print("  ", x)


# ------------------------------------------------------------------ проверка: оверлеи и эталоны
def draw_regions(img, regions, relations=(), scale=1.0, font_size=13, labels=True):
    im = img.convert("RGB").copy()
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    d = ImageDraw.Draw(im, "RGBA")
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    centers = {}
    for r in regions:
        x, y, w, h = [v * scale for v in r["bbox"]]
        c = COLORS[r["label"]]
        rgb = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        d.rectangle([x, y, x + w, y + h], outline=rgb + (255,), width=2, fill=rgb + (28,))
        centers[r["id"]] = (x + w / 2, y + h / 2)
        if labels:
            tag = r["label"] + (f":{r['attributes'].get('kind') or r['attributes'].get('defect_type') or r['attributes'].get('chart_kind') or ''}"
                                if r.get("attributes") else "")
            tag = tag.rstrip(":")
            tw = d.textlength(tag, font=font)
            ty = y - font_size - 4 if y > font_size + 4 else y + h + 1
            d.rectangle([x, ty, x + tw + 6, ty + font_size + 3], fill=rgb + (230,))
            d.text((x + 3, ty + 1), tag, fill=(255, 255, 255), font=font)
    for a, b, t in relations:
        if a in centers and b in centers:
            d.line([centers[a], centers[b]], fill=(30, 30, 30, 200), width=2)
            mx, my = (centers[a][0] + centers[b][0]) / 2, (centers[a][1] + centers[b][1]) / 2
            d.text((mx + 3, my), t, fill=(30, 30, 30), font=font)
    return im


def cmd_overlay(args):
    _, final = build_final()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for rel, f in final.items():
        im = draw_regions(Image.open(SHOTS / rel), f["regions"], f["relations"])
        im.save(out / Path(rel).name)
    print(f"{len(final)} изображений с областями -> {out}")


def cmd_agree(args):
    """Сверка разметки вслепую с эталонами ЛР2 (COCO из предыдущего коммита или другой папки)."""
    gold = load_json(args.gold)
    _, final = build_final()
    names = {c["id"]: c["name"] for c in gold["categories"]}
    rows, tp_iou, tp_any, ng, nm, txt = [], 0, 0, 0, 0, 0
    for im in gold["images"]:
        rel = im["file_name"]
        g = [(names[a["category_id"]], a["bbox"], a["attributes"].get("ocr_text")) for a in gold["annotations"]
             if a["image_id"] == im["id"]]
        m = [(r["label"], r["bbox"], r.get("ocr_text")) for r in final[rel]["regions"]]
        # сверяются только метки, которые были в схеме эталона (новые области схемы 1.2 — не расхождение)
        m_cmp = [k for k, x in enumerate(m) if x[0] in names.values()]
        ng += len(g)
        used = set()
        for lab, b, t in g:
            cands = [k for k in m_cmp if m[k][0] == lab and k not in used]
            best = max(cands, key=lambda k: (iou(b, m[k][1]), -edge_dev(b, m[k][1])), default=None)
            if best is None:
                rows.append((rel, lab, b, None, 0.0, None, False))
                continue
            used.add(best)
            v, dev = iou(b, m[best][1]), edge_dev(b, m[best][1])
            tp_iou += v >= 0.7
            tp_any += same_box(b, m[best][1])
            txt += m[best][2] == t
            rows.append((rel, lab, b, m[best][1], v, dev, m[best][2] == t))
        extra = [k for k in m_cmp if k not in used]
        nm += len(used) + len(extra)
        for k in extra:
            rows.append((rel, m[k][0], None, m[k][1], 0.0, None, False))
        for k in ("category", "component", "screen_type"):
            rows.append((rel, f"класс {k}", im["attributes"][k], final[rel]["labels"][k], None, None, None))
    print("| Изображение | Метка | Эталон ЛР2 | Разметка ЛР5 | IoU | max отклонение стороны, px | текст совпал |")
    print("|---|---|---|---|---:|---:|---|")
    for r in rows:
        print(f"| `{Path(r[0]).stem}` | {r[1]} | {r[2]} | {r[3]} | {'' if r[4] is None else f'{r[4]:.2f}'} | "
              f"{'' if r[5] is None else r[5]} | {'' if r[6] is None else ('да' if r[6] else 'нет')} |")
    f1 = lambda t: 2 * t / (ng + nm) if ng + nm else 0
    print(f"\nобластей в эталоне {ng}, в разметке {nm}; тексты областей совпали: {txt}/{ng}")
    print(f"F1 при IoU ≥ 0,7: {f1(tp_iou):.3f}; F1 при IoU ≥ 0,7 или отклонении сторон ≤ 5 px: {f1(tp_any):.3f}")


# ------------------------------------------------------------------ анализ
def match(pred, gold, thr):
    used, pairs = set(), []
    for i, g in enumerate(gold):
        best = max(((iou(g["bbox"], p["bbox"]), k) for k, p in enumerate(pred)
                    if p["label"] == g["label"] and k not in used), default=(0, None))
        if best[0] >= thr:
            used.add(best[1])
            pairs.append((i, best[1], best[0]))
    return pairs


def cer(a, b):
    a, b = a or "", b or ""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(1, len(a))


def cmd_analyze(args):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    man, final = build_final()
    pred = load_json(OUT / "auto" / "predictions.json")
    imgs = sorted(final)
    regions = [(rel, r) for rel in imgs for r in final[rel]["regions"]]
    by_label = Counter(r["label"] for _, r in regions)
    by_cat_label = defaultdict(Counter)
    for rel, r in regions:
        by_cat_label[final[rel]["labels"]["category"]][r["label"]] += 1
    by_screen_label = defaultdict(Counter)
    for rel, r in regions:
        by_screen_label[final[rel]["labels"]["screen_type"]][r["label"]] += 1
    ents = [(rel, r, e) for rel, r in regions for e in r.get("entities", [])]
    rels = [(rel, a, b, t) for rel in imgs for a, b, t in final[rel]["relations"]]
    area = defaultdict(list)
    for rel, r in regions:
        w, h = final[rel]["size"]
        area[r["label"]].append(100 * r["bbox"][2] * r["bbox"][3] / (w * h))
    # качество предразметки
    lab_acc = {k: sum(pred["images"][x]["labels"][k] == final[x]["labels"][k] for x in imgs) / len(imgs)
               for k in ("category", "component", "screen_type", "device", "theme", "is_defect")}
    det = {}
    for thr in (0.5, 0.7):
        tp = npred = ngold = 0
        per = Counter(), Counter(), Counter()
        for x in imgs:
            g, p = final[x]["regions"], pred["images"][x]["regions"]
            pairs = match(p, g, thr)
            tp += len(pairs)
            npred += len(p)
            ngold += len(g)
            for gi, pi, _ in pairs:
                per[0][g[gi]["label"]] += 1
            for r in g:
                per[1][r["label"]] += 1
            for r in p:
                per[2][r["label"]] += 1
        det[thr] = (tp, npred, ngold, per)
    tp, npred, ngold, per = det[0.5]
    f1 = lambda t, a, b: 2 * t / (a + b) if a + b else 0.0
    pairs_all = [(x, final[x]["regions"][gi], pred["images"][x]["regions"][pi], v)
                 for x in imgs for gi, pi, v in match(pred["images"][x]["regions"], final[x]["regions"], 0.5)]
    cers = [cer(g.get("ocr_text"), p.get("ocr_text")) for _, g, p, _ in pairs_all if g.get("ocr_text") and p.get("ocr_text")]
    ious = [v for *_, v in pairs_all]
    # сущности: метка + текст, внутри совпавших областей
    etp = epred = egold = 0
    for _, g, p, _ in pairs_all:
        ge = Counter((e["label"], e["text"]) for e in g.get("entities", []))
        pe = Counter((e["label"], e["text"]) for e in p.get("entities", []))
        etp += sum((ge & pe).values())
    egold = len(ents)
    epred = sum(len(r.get("entities", [])) for x in imgs for r in pred["images"][x]["regions"])
    # графики
    img_dir = DOCS / "img"
    img_dir.mkdir(exist_ok=True)
    cats = sorted(by_cat_label)
    fig, ax = plt.subplots(figsize=(10, 4.6))
    bottom = [0] * len(cats)
    for lab in REGION_ORDER:
        vals = [by_cat_label[c][lab] for c in cats]
        ax.bar(cats, vals, bottom=bottom, label=lab, color=COLORS[lab])
        bottom = [a + b for a, b in zip(bottom, vals)]
    ax.set_ylabel("областей")
    ax.set_ylim(0, max(bottom) + 3)
    ax.set_title("Области на скриншотах по категориям дефектов")
    ax.legend(fontsize=8, ncol=4)
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    fig.savefig(img_dir / "lr5_regions_by_category.png", dpi=120)
    plt.close(fig)
    labs = REGION_ORDER
    fig, ax = plt.subplots(figsize=(9, 4))
    xs = range(len(labs))
    def prf(lab):
        tpl, gl, pl = per[0][lab], per[1][lab], per[2][lab]
        return (tpl / pl if pl else 0), (tpl / gl if gl else 0)
    ax.bar([i - 0.2 for i in xs], [prf(l)[0] for l in labs], 0.4, label="точность (precision)", color="#4363d8")
    ax.bar([i + 0.2 for i in xs], [prf(l)[1] for l in labs], 0.4, label="полнота (recall)", color="#f58231")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labs, rotation=20, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_title("Предразметка (OCR + правила) против ручной разметки, IoU ≥ 0,5")
    ax.legend()
    plt.tight_layout()
    fig.savefig(img_dir / "lr5_preannotation_quality.png", dpi=120)
    plt.close(fig)
    # отчёт
    L = []
    L.append("# Анализ разметки изображений (ЛР5)\n")
    L.append("Сгенерировано `python scripts/image_markup.py analyze` по итоговой разметке "
             "(`annotations/images/screenshots_coco.json`) и предразметке (`annotations/images/markup/auto/predictions.json`).\n")
    L.append("## 1. Объём разметки\n")
    L.append(f"| Показатель | Значение |\n|---|---:|\n| изображений | {len(imgs)} |\n| областей (рамок) | {len(regions)} |\n"
             f"| в среднем областей на изображение | {len(regions) / len(imgs):.1f} |\n"
             f"| областей с текстом (`ocr_text`) | {sum(1 for _, r in regions if r.get('ocr_text'))} |\n"
             f"| сущностей в тексте областей (уровень 3) | {len(ents)} |\n| связей между областями | {len(rels)} |\n"
             f"| изображений с дефектом / ожидаемое поведение | {sum(final[x]['labels']['is_defect'] == 'yes' for x in imgs)} / "
             f"{sum(final[x]['labels']['is_defect'] == 'no' for x in imgs)} |\n")
    L.append("### Области по меткам\n")
    L.append("| Метка | Областей | Средняя доля площади кадра, % | Атрибуты |\n|---|---:|---:|---|")
    for lab in REGION_ORDER:
        at = Counter()
        for _, r in regions:
            if r["label"] == lab:
                for k in ("kind", "defect_type", "chart_kind", "trend"):
                    if k in r["attributes"]:
                        at[r["attributes"][k]] += 1
        L.append(f"| `{lab}` | {by_label[lab]} | {sum(area[lab]) / len(area[lab]) if area[lab] else 0:.1f} | "
                 + ", ".join(f"{k} {v}" for k, v in at.most_common()) + " |")
    L.append("\n![Области по категориям](img/lr5_regions_by_category.png)\n")
    L.append("### Классы изображений (уровень 1)\n")
    for k in ("component", "screen_type", "severity", "device", "theme"):
        c = Counter("null (не дефект)" if final[x]["labels"][k] is None else final[x]["labels"][k] for x in imgs)
        L.append(f"* **{k}**: " + ", ".join(f"{a} — {b}" for a, b in c.most_common()))
    L.append("\n### Сущности в тексте областей (уровень 3)\n")
    ce = Counter(e["label"] for *_, e in ents)
    L.append("| Сущность | Количество | Примеры |\n|---|---:|---|")
    for lab, n in ce.most_common():
        ex = list(dict.fromkeys(e["text"] for *_, e in ents if e["label"] == lab))[:4]
        L.append(f"| `{lab}` | {n} | " + ", ".join(f"`{x}`" for x in ex) + " |")
    L.append("\n### Связи между областями\n")
    cr = Counter()
    for rel, a, b, t in rels:
        la = next(r["label"] for r in final[rel]["regions"] if r["id"] == a)
        lb = next(r["label"] for r in final[rel]["regions"] if r["id"] == b)
        cr[(t, la, lb)] += 1
    L.append("| Связь | Откуда → куда | Количество |\n|---|---|---:|")
    for (t, la, lb), n in cr.most_common():
        L.append(f"| `{t}` | {la} → {lb} | {n} |")
    L.append("\n### Типы экрана × метки областей\n")
    L.append("| Тип экрана | " + " | ".join(REGION_ORDER) + " |\n|---|" + "---:|" * len(REGION_ORDER))
    for st in sorted(by_screen_label):
        L.append(f"| `{st}` | " + " | ".join(str(by_screen_label[st][l]) for l in REGION_ORDER) + " |")
    L.append("\n## 2. Качество полуавтоматической предразметки (шаг 3)\n")
    L.append("Предразметка: Tesseract OCR 5 (LSTM, языки rus+eng) находит строки текста и их рамки, правила по "
             "регулярным выражениям назначают метки областей, сущности и классы изображения. Сравнение с итоговой "
             "ручной разметкой:\n")
    L.append("| Что | Значение |\n|---|---:|")
    for k, v in lab_acc.items():
        L.append(f"| точность класса `{k}` | {v:.0%} |")
    for thr in (0.5, 0.7):
        t_, p_, g_, _ = det[thr]
        L.append(f"| области: точность / полнота / F1 при IoU ≥ {thr} | {t_ / p_ if p_ else 0:.2f} / {t_ / g_ if g_ else 0:.2f} / {f1(t_, p_, g_):.2f} |")
    L.append(f"| средний IoU совпавших областей | {sum(ious) / len(ious) if ious else 0:.2f} |")
    L.append(f"| CER текста совпавших областей (доля ошибочных символов OCR) | {sum(cers) / len(cers) if cers else 0:.1%} |")
    L.append(f"| сущности (метка + текст) в совпавших областях: найдено верно / всего в разметке | {etp} / {egold} |")
    L.append("\n| Метка | в разметке | в предразметке | совпало (IoU ≥ 0,5) |\n|---|---:|---:|---:|")
    for lab in REGION_ORDER:
        L.append(f"| `{lab}` | {per[1][lab]} | {per[2][lab]} | {per[0][lab]} |")
    L.append("\n![Качество предразметки](img/lr5_preannotation_quality.png)\n")
    wrong = [(x, k, pred["images"][x]["labels"][k], final[x]["labels"][k]) for x in imgs
             for k in ("category", "component", "screen_type") if pred["images"][x]["labels"][k] != final[x]["labels"][k]]
    L.append("Исправленные разметчиком классы изображений:\n")
    L.append("| Изображение | Класс | Предразметка | Итог |\n|---|---|---|---|")
    for x, k, a, b in wrong:
        L.append(f"| `{Path(x).stem}` | {k} | {a} | {b} |")
    (DOCS / "image_markup_stats.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    # примеры итоговой разметки для документации
    for stem in args.examples.split(","):
        rel = next(x for x in imgs if Path(x).stem == stem)
        f = final[rel]
        im = draw_regions(Image.open(SHOTS / rel), f["regions"], f["relations"])
        bx = [r["bbox"] for r in f["regions"]]
        pad = 24
        crop = (max(0, min(b[0] for b in bx) - pad), max(0, min(b[1] for b in bx) - pad - 18),
                min(im.width, max(b[0] + b[2] for b in bx) + pad), min(im.height, max(b[1] + b[3] for b in bx) + pad))
        im.crop(crop).save(img_dir / f"lr5_markup_{stem}.png")
    print("записаны docs/image_markup_stats.md, docs/img/lr5_regions_by_category.png, docs/img/lr5_preannotation_quality.png")
    print(f"областей {len(regions)}, сущностей {len(ents)}, связей {len(rels)}; предразметка F1@0.5="
          f"{f1(*det[0.5][:3]):.2f}, F1@0.7={f1(*det[0.7][:3]):.2f}, классы {lab_acc}")


# ------------------------------------------------------------------ импорт в Label Studio
def cmd_ls_import(args):
    import urllib.request
    hdr = {"Authorization": f"Token {args.token}", "Content-Type": "application/json"}

    def call(method, path, body=None):
        req = urllib.request.Request(args.url.rstrip("/") + path, method=method, headers=hdr,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"null")
    proj = call("POST", "/api/projects", {"title": args.title, "label_config": LS_CONFIG.read_text(encoding="utf-8"),
                                          "description": "ЛР5: разметка скриншотов Bug Triage Dataset"})
    tasks = load_json(OUT / "labelstudio" / "tasks.json")
    for t in tasks:
        t.pop("id", None)
    call("POST", f"/api/projects/{proj['id']}/import", tasks)
    print(f"проект {proj['id']} создан, загружено задач: {len(tasks)}. Изображения открываются через "
          "/data/local-files: запустите Label Studio с LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true и "
          f"LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT={ROOT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auto")
    a.add_argument("--tessdata", help="папка с rus.traineddata и eng.traineddata")
    sub.add_parser("final")
    sub.add_parser("export")
    l = sub.add_parser("lsf")
    l.add_argument("--lsf", required=True, help="папка сборки Label Studio Frontend (js/main.js, css/main.css)")
    l.add_argument("--shots", default="", help="имена файлов (без .png) для скриншотов редактора, через запятую")
    l.add_argument("--select", type=json.loads, default={}, help='JSON {"имя": "r2"} — какую область выделить на скриншоте')
    o = sub.add_parser("overlay")
    o.add_argument("--out", default=str(ROOT / "annotations" / "images" / "markup" / "overlay"))
    g = sub.add_parser("agree")
    g.add_argument("--gold", required=True, help="COCO-файл с эталонами (например, из предыдущего коммита)")
    an = sub.add_parser("analyze")
    an.add_argument("--examples", default="payment_02_invoice_nan_total,security_02_idor_foreign_invoice,"
                    "ui_layout_01_button_overflow,crash_01_python_typeerror,performance_02_search_latency,"
                    "localization_02_missing_translation_keys")
    i = sub.add_parser("ls-import")
    i.add_argument("--url", default="http://localhost:8080")
    i.add_argument("--token", required=True)
    i.add_argument("--title", default="lr5-bug-triage-images")
    args = ap.parse_args()
    {"auto": cmd_auto, "final": cmd_final, "export": cmd_export, "lsf": cmd_lsf, "overlay": cmd_overlay,
     "agree": cmd_agree, "analyze": cmd_analyze, "ls-import": cmd_ls_import}[args.cmd](args)


if __name__ == "__main__":
    main()
