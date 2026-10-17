#!/usr/bin/env python3
"""Инструменты аннотационной схемы (Лабораторная работа №2).

Все метки берутся из одного файла annotation_schema/schema.json, поэтому
конфигурации разметки, проверка и экспорт не расходятся между собой.

Команды:
  configs   — сгенерировать конфигурации Label Studio для всех модальностей
  tasks     — подготовить задачи Label Studio с предразметкой (полуавтоматическая разметка)
  validate  — проверить все аннотации в annotations/ на соответствие схеме
  conllu    — выгрузить разметку баг-репортов в CoNLL-U (BIO-метки в столбце MISC)
  agree     — посчитать согласованность двух разметчиков (каппа Коэна, F1, IoU, границы)

Примеры:
  python scripts/annotation_tools.py configs
  python scripts/annotation_tools.py validate
  python scripts/annotation_tools.py agree --a annotations_ivanov --b annotations_petrov
"""
import argparse
import csv
import json
import re
import sys
import wave
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "annotation_schema" / "schema.json"
LS_DIR = ROOT / "annotation_schema" / "label_studio"
DATA = ROOT / "data"
ANN = ROOT / "annotations"

PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46a0b0",
           "#f032e6", "#9a6324", "#808000", "#000075", "#a9a9a9"]


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def report_text(rec):
    """Текст, который размечается: заголовок + пустая строка + описание."""
    return rec["summary"] + "\n\n" + rec["description"]


def find_report(report_id):
    hits = list((DATA / "text" / "bug_reports").glob(f"*/{report_id}.json"))
    return json.loads(hits[0].read_text(encoding="utf-8")) if hits else None


# ---------------------------------------------------------------- configs
def _choices(name, to, values, required=True, extra=""):
    opts = "\n".join(f'    <Choice value={quoteattr(v)} hint={quoteattr(h)}/>'
                     for v, h in values.items())
    req = ' required="true"' if required else ""
    return (f'  <Choices name="{name}" toName="{to}" choice="single" showInline="true"{req}{extra}>\n'
            f"{opts}\n  </Choices>")


def _labels(name, to, labels, tag="Labels"):
    rows = "\n".join(f'    <Label value={quoteattr(k)} background="{PALETTE[i % len(PALETTE)]}" hint={quoteattr(v)}/>'
                     for i, (k, v) in enumerate(labels.items()))
    return f'  <{tag} name="{name}" toName="{to}">\n{rows}\n  </{tag}>'


def _region_choices(name, to, label_tag, label_value, values):
    vals = {v: v for v in values}
    inner = _choices(name, to, vals, required=False, extra=' perRegion="true"')
    return (f'  <View visibleWhen="region-selected" whenTagName="{label_tag}" whenLabelValue="{label_value}">\n'
            f'    <Header value="{label_value}: {name}" size="6"/>\n{inner}\n  </View>')


def entity_block(s, to, label_tag="entity"):
    ents = s["entity_labels"]
    parts = [_labels(label_tag, to, {k: v["description"] for k, v in ents.items()})]
    for lab, spec in ents.items():
        for attr, a in spec["attributes"].items():
            if a["type"] == "enum":
                parts.append(_region_choices(f"{lab.lower()}_{attr}", to, label_tag, lab, a["values"]))
    parts.append(f'  <View visibleWhen="region-selected" whenTagName="{label_tag}">\n'
                 f'    <TextArea name="normalized" toName="{to}" perRegion="true" editable="true" '
                 f'maxSubmissions="1" placeholder="normalized: каноническая запись (NullPointerException, macOS 12, 500)"/>\n'
                 f'  </View>')
    return parts


def build_text_config(s):
    d = s["document_labels"]
    p = ['<View>', '  <Header value="Баг-репорт $report_id ($source_project, $tracker)"/>',
         '  <Text name="text" value="$text"/>',
         '  <Header value="Уровень 1 — классы тикета" size="5"/>',
         _choices("ticket_type", "text", d["ticket_type"]["values"]),
         _choices("category", "text", d["category"]["values"]),
         _choices("component", "text", d["component"]["values"]),
         '  <View visibleWhen="choice-selected" whenTagName="ticket_type" whenChoiceValue="bug">',
         _choices("severity", "text", d["severity"]["values"]),
         '  </View>',
         _choices("confidence", "text", d["confidence"]["values"]),
         '  <Header value="Структура документа: разделы и элементы" size="5"/>',
         _labels("section", "text", s["section_labels"]),
         _labels("item", "text", s["item_labels"]),
         '  <Header value="Сущности, атрибуты, связи" size="5"/>']
    p += entity_block(s, "text")
    rel = "\n".join(f'    <Relation value="{k}"/>' for k in s["relation_types"])
    p += [f"  <Relations>\n{rel}\n  </Relations>", "</View>"]
    return "\n".join(p) + "\n"


def build_log_config(s):
    line = s["log_labels"]["line"]
    p = ['<View>', '  <Header value="$system — строка $line_no ($log_file)"/>',
         '  <Text name="line" value="$line"/>',
         _choices("level", "line", {v: v for v in line["level"]["values"]}),
         _choices("event_type", "line", {v: v for v in line["event_type"]["values"]}),
         _choices("is_anomaly", "line", {"true": "сбой или атака", "false": "штатная работа"})]
    p += entity_block(s, "line")
    p += ["</View>"]
    return "\n".join(p) + "\n"


def build_image_config(s):
    il = s["image_labels"]
    d = s["document_labels"]
    regions = {k: v["description"] for k, v in il["region_labels"].items()}
    p = ['<View>', '  <Header value="$file"/>',
         '  <Image name="image" value="$image" zoom="true" zoomControl="true"/>',
         '  <Header value="Уровень 1 — изображение" size="5"/>',
         _choices("category", "image", d["category"]["values"]),
         _choices("component", "image", d["component"]["values"]),
         _choices("screen_type", "image", il["image"]["screen_type"]["values"]),
         '  <Header value="Уровень 2 — области" size="5"/>',
         _labels("region", "image", regions, tag="RectangleLabels"),
         '  <View visibleWhen="region-selected" whenTagName="region">',
         '    <Header value="Уровень 3 — текст в области (дословно)" size="6"/>',
         '    <TextArea name="ocr_text" toName="image" perRegion="true" editable="true" maxSubmissions="1" rows="3"/>',
         '  </View>',
         _region_choices("defect_type", "image", "region", "DEFECT_AREA",
                         il["region_labels"]["DEFECT_AREA"]["attributes"]["defect_type"]),
         _region_choices("ui_kind", "image", "region", "UI_ELEMENT",
                         s["entity_labels"]["UI_ELEMENT"]["attributes"]["kind"]["values"]),
         _region_choices("pii_kind", "image", "region", "PII",
                         s["entity_labels"]["PII"]["attributes"]["kind"]["values"]),
         "</View>"]
    return "\n".join(p) + "\n"


def build_audio_config(s):
    al = s["audio_labels"]
    d = s["document_labels"]
    segs = {k: v for k, v in al["tiers"]["segment"]["labels"].items() if k}
    p = ['<View>', '  <Header value="$file"/>',
         '  <Audio name="audio" value="$audio" hotkey="space"/>',
         '  <Header value="Уровень 2 — сегменты речи и транскрипция" size="5"/>',
         _labels("segment", "audio", segs),
         '  <View visibleWhen="region-selected" whenTagName="segment">',
         '    <TextArea name="transcript" toName="audio" perRegion="true" editable="true" maxSubmissions="1" rows="2" placeholder="дословно, числа словами"/>',
         '  </View>',
         '  <Header value="Уровень 1 — запись" size="5"/>',
         _choices("category", "audio", d["category"]["values"]),
         _choices("component", "audio", d["component"]["values"]),
         _choices("severity", "audio", d["severity"]["values"]),
         _choices("urgency", "audio", {v: v for v in al["file"]["urgency"]["values"]}),
         _choices("audio_quality", "audio", {v: v for v in al["file"]["audio_quality"]["values"]}),
         "</View>"]
    return "\n".join(p) + "\n"


def cmd_configs(args):
    s = load_schema()
    LS_DIR.mkdir(parents=True, exist_ok=True)
    out = {"text_reports.xml": build_text_config(s), "text_logs.xml": build_log_config(s),
           "images.xml": build_image_config(s), "audio.xml": build_audio_config(s)}
    import xml.dom.minidom
    for name, xml_text in out.items():
        xml.dom.minidom.parseString(xml_text.encode("utf-8"))  # проверка корректности XML
        (LS_DIR / name).write_text(xml_text, encoding="utf-8")
        print(f"записан {LS_DIR.relative_to(ROOT) / name}")


# ---------------------------------------------------------------- tasks
def cmd_tasks(args):
    """Задачи Label Studio с предразметкой из полей трекера (predictions)."""
    LS_DIR.mkdir(parents=True, exist_ok=True)
    tasks = []
    for f in sorted((DATA / "text" / "bug_reports").glob("*/*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        res = [{"from_name": "category", "to_name": "text", "type": "choices", "value": {"choices": [r["category"]]}},
               {"from_name": "component", "to_name": "text", "type": "choices", "value": {"choices": [r["component_hint"]]}}]
        if r.get("severity_prelabel"):
            res.append({"from_name": "severity", "to_name": "text", "type": "choices",
                        "value": {"choices": [r["severity_prelabel"]]}})
        tasks.append({"data": {"text": report_text(r), "report_id": r["report_id"],
                               "source_project": r["source_project"], "tracker": r["tracker"]},
                      "predictions": [{"model_version": "prelabel-v1 (keywords + tracker priority)", "result": res}]})
    (LS_DIR / "tasks_text_reports.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    img = [{"data": {"image": f"/data/local-files/?d=data/images/screenshots/{row['file']}", "file": row["file"]},
            "predictions": [{"model_version": "prelabel-v1 (folder)", "result": [
                {"from_name": "category", "to_name": "image", "type": "choices", "value": {"choices": [row["category"]]}}]}]}
           for row in csv.DictReader(open(DATA / "images" / "images.csv", encoding="utf-8"))]
    (LS_DIR / "tasks_images.json").write_text(json.dumps(img, ensure_ascii=False, indent=1), encoding="utf-8")
    aud = [{"data": {"audio": f"/data/local-files/?d=data/audio/voice_reports/{row['file']}", "file": row["file"]},
            "predictions": [{"model_version": "prelabel-v1 (folder)", "result": [
                {"from_name": "category", "to_name": "audio", "type": "choices", "value": {"choices": [row["category"]]}}]}]}
           for row in csv.DictReader(open(DATA / "audio" / "audio.csv", encoding="utf-8"))]
    (LS_DIR / "tasks_audio.json").write_text(json.dumps(aud, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"задач: тексты {len(tasks)}, изображения {len(img)}, аудио {len(aud)} -> {LS_DIR.relative_to(ROOT)}")


# ---------------------------------------------------------------- validate
class Checker:
    def __init__(self):
        self.errors, self.ok = [], Counter()

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")


def check_entities(ck, where, s, text, entities, base=""):
    ents = s["entity_labels"]
    lim = s["limits"]
    ids = set()
    for e in entities:
        w = f"{where} {base}{e.get('id', '?')}"
        if e.get("id") in ids:
            ck.err(w, "повторяющийся id")
        ids.add(e.get("id"))
        lab = e.get("label")
        if lab not in ents:
            ck.err(w, f"неизвестная метка сущности {lab}")
            continue
        a, b = e.get("start"), e.get("end")
        if not (isinstance(a, int) and isinstance(b, int) and 0 <= a < b <= len(text)):
            ck.err(w, f"неверные смещения {a}..{b}")
            continue
        frag = text[a:b]
        if e.get("text") != frag:
            ck.err(w, f"text {e.get('text')!r} не совпадает с фрагментом {frag!r}")
        if frag != frag.strip():
            ck.err(w, "граница захватывает пробел")
        if lim["entity_single_line"] and "\n" in frag:
            ck.err(w, "сущность переходит на другую строку")
        mx = lim["entity_max_chars"].get(lab, lim["entity_max_chars"]["default"])
        if len(frag) > mx:
            ck.err(w, f"длина {len(frag)} > {mx}")
        attrs = e.get("attributes", {})
        for an, spec in ents[lab]["attributes"].items():
            v = attrs.get(an)
            if v is None:
                if spec.get("required"):
                    ck.err(w, f"нет обязательного атрибута {an}")
            elif spec["type"] == "enum" and v not in spec["values"]:
                ck.err(w, f"{an}={v!r} вне словаря {spec['values']}")
        for an in attrs:
            if an not in ents[lab]["attributes"]:
                ck.err(w, f"лишний атрибут {an}")
        ck.ok["entities"] += 1
    return {e.get("id"): e.get("label") for e in entities}


def check_doc_labels(ck, where, s, labels, keys=("ticket_type", "category", "component", "severity", "confidence")):
    d = s["document_labels"]
    for k in keys:
        v = labels.get(k)
        if k == "severity":
            need = labels.get("ticket_type", "bug") == "bug"
            if need and v not in d[k]["values"]:
                ck.err(where, f"severity={v!r}: для дефекта обязательна одна из {list(d[k]['values'])}")
            if not need and v is not None:
                ck.err(where, "severity должна быть null, если ticket_type != bug")
        elif v not in d[k]["values"]:
            ck.err(where, f"{k}={v!r} вне словаря")


def validate_reports(ck, s):
    files = sorted((ANN / "text" / "reports").glob("*/*.json"))
    for f in files:
        w = str(f.relative_to(ROOT))
        a = json.loads(f.read_text(encoding="utf-8"))
        src = find_report(a.get("report_id", ""))
        if src is None:
            ck.err(w, "нет исходного баг-репорта в data/")
            continue
        text = report_text(src)
        if a.get("text_length") != len(text):
            ck.err(w, f"text_length {a.get('text_length')} != {len(text)} — исходный текст изменился")
            continue
        if f.parent.name != a["labels"].get("category"):
            ck.err(w, f"файл лежит в папке {f.parent.name}, а category={a['labels'].get('category')}")
        if a.get("schema_version") != s["version"]:
            ck.err(w, "версия схемы не совпадает")
        check_doc_labels(ck, w, s, a["labels"])
        # разделы: не пересекаются и покрывают весь непробельный текст
        covered = [False] * len(text)
        secs = sorted(a.get("sections", []), key=lambda x: x["start"])
        prev_end = 0
        for sec in secs:
            if sec["label"] not in s["section_labels"]:
                ck.err(w, f"неизвестный раздел {sec['label']}")
            if sec["start"] < prev_end:
                ck.err(w, f"раздел {sec['label']} {sec['start']} пересекается с предыдущим")
            prev_end = sec["end"]
            for i in range(sec["start"], min(sec["end"], len(text))):
                covered[i] = True
        gaps = [i for i, c in enumerate(text) if not c.isspace() and not covered[i]]
        if gaps:
            ck.err(w, f"{len(gaps)} символов вне разделов, первый в позиции {gaps[0]}: {text[gaps[0]:gaps[0]+30]!r}")
        if not secs or secs[0]["label"] != "TITLE" or secs[0]["end"] != len(src["summary"]):
            ck.err(w, "первый раздел должен быть TITLE и совпадать с заголовком")
        parent = {"STEP": "STEPS", "FRAME": "STACK_TRACE"}
        for it in a.get("items", []):
            inside = [x for x in secs if x["start"] <= it["start"] and it["end"] <= x["end"]]
            if it["label"] not in s["item_labels"]:
                ck.err(w, f"неизвестный элемент {it['label']}")
            elif not inside or inside[0]["label"] != parent[it["label"]]:
                ck.err(w, f"{it['label']} {it['start']}..{it['end']} должен лежать внутри {parent[it['label']]}")
        ids = check_entities(ck, w, s, text, a.get("entities", []))
        for r in a.get("relations", []):
            rt = s["relation_types"].get(r.get("type"))
            if rt is None:
                ck.err(w, f"неизвестная связь {r.get('type')}")
            elif ids.get(r["from"]) not in rt["from"] or ids.get(r["to"]) not in rt["to"]:
                ck.err(w, f"связь {r['type']} {r['from']}->{r['to']} недопустима для этих меток")
        ck.ok["text_reports"] += 1


def validate_logs(ck, s):
    line_spec = s["log_labels"]["line"]
    for f in sorted((ANN / "text" / "logs").glob("*/*.jsonl")):
        w = str(f.relative_to(ROOT))
        src = DATA / "text" / "logs" / f.parent.name / (f.stem + ".log")
        if not src.exists():
            ck.err(w, f"нет исходного лога {src.relative_to(ROOT)}")
            continue
        lines = src.read_text(encoding="utf-8").splitlines()
        seen = set()
        for k, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            r = json.loads(raw)
            wl = f"{w}:{k}"
            n = r.get("line_no")
            if not isinstance(n, int) or not 1 <= n <= len(lines):
                ck.err(wl, f"line_no {n} вне файла")
                continue
            seen.add(n)
            if r.get("text") != lines[n - 1]:
                ck.err(wl, "text не совпадает со строкой лога")
            if r.get("level") not in line_spec["level"]["values"]:
                ck.err(wl, f"level={r.get('level')!r}")
            if r.get("event_type") not in line_spec["event_type"]["values"]:
                ck.err(wl, f"event_type={r.get('event_type')!r}")
            if not isinstance(r.get("is_anomaly"), bool):
                ck.err(wl, "is_anomaly должен быть true/false")
            check_entities(ck, wl, s, lines[n - 1], r.get("entities", []))
            ck.ok["log_lines"] += 1
        if len(seen) != len(lines):
            ck.err(w, f"размечено {len(seen)} строк из {len(lines)}")


def validate_images(ck, s):
    f = ANN / "images" / "screenshots_coco.json"
    if not f.exists():
        return
    from PIL import Image
    w = str(f.relative_to(ROOT))
    coco = json.loads(f.read_text(encoding="utf-8"))
    il = s["image_labels"]
    cats = {c["id"]: c["name"] for c in coco["categories"]}
    if set(cats.values()) != set(il["region_labels"]):
        ck.err(w, "categories в COCO не совпадают с region_labels схемы")
    imgs = {}
    for im in coco["images"]:
        p = DATA / "images" / "screenshots" / im["file_name"]
        if not p.exists():
            ck.err(w, f"нет файла {im['file_name']}")
            continue
        size = Image.open(p).size
        if size != (im["width"], im["height"]):
            ck.err(w, f"{im['file_name']}: размер {size} != {im['width']}x{im['height']}")
        at = im.get("attributes", {})
        check_doc_labels(ck, f"{w} {im['file_name']}", s, at, keys=("category", "component"))
        if at.get("screen_type") not in il["image"]["screen_type"]["values"]:
            ck.err(w, f"{im['file_name']}: screen_type={at.get('screen_type')!r}")
        imgs[im["id"]] = im
        ck.ok["images"] += 1
    mn = s["limits"]["bbox_min_side_px"]
    for an in coco["annotations"]:
        wa = f"{w} ann {an['id']}"
        im = imgs.get(an["image_id"])
        lab = cats.get(an["category_id"])
        if im is None or lab is None:
            ck.err(wa, "неизвестные image_id или category_id")
            continue
        x, y, bw, bh = an["bbox"]
        if x < 0 or y < 0 or x + bw > im["width"] or y + bh > im["height"]:
            ck.err(wa, "рамка выходит за изображение")
        if bw < mn or bh < mn:
            ck.err(wa, f"сторона рамки меньше {mn} px")
        if abs(an.get("area", 0) - bw * bh) > 1:
            ck.err(wa, "area != w*h")
        at = an.get("attributes", {})
        spec = il["region_labels"][lab]
        ocr = at.get("ocr_text")
        if spec["ocr_text"] == "required" and not ocr:
            ck.err(wa, f"{lab}: обязателен ocr_text")
        need = {"DEFECT_AREA": ("defect_type", spec.get("attributes", {}).get("defect_type")),
                "UI_ELEMENT": ("kind", s["entity_labels"]["UI_ELEMENT"]["attributes"]["kind"]["values"]),
                "PII": ("kind", s["entity_labels"]["PII"]["attributes"]["kind"]["values"])}
        if lab in need:
            k, vals = need[lab]
            if at.get(k) not in vals:
                ck.err(wa, f"{lab}: {k}={at.get(k)!r} вне словаря")
        if at.get("entities"):
            if not ocr:
                ck.err(wa, "сущности без ocr_text")
            else:
                check_entities(ck, wa, s, ocr, at["entities"])
        ck.ok["image_regions"] += 1


def parse_textgrid(path):
    """Минимальный разбор TextGrid в длинном формате Praat."""
    txt = path.read_text(encoding="utf-8")
    head = re.search(r"xmin = ([\d.]+)\s*xmax = ([\d.]+)", txt)
    tiers = {}
    for block in re.split(r"\n\s*item \[\d+\]:", txt)[1:]:
        name = re.search(r'name = "(.*?)"', block).group(1)
        ivs = [(float(a), float(b), t.replace('""', '"')) for a, b, t in
               re.findall(r'intervals \[\d+\]:\s*xmin = ([\d.]+)\s*xmax = ([\d.]+)\s*text = "((?:[^"]|"")*)"', block)]
        tiers[name] = ivs
    return float(head.group(1)), float(head.group(2)), tiers


def wer(ref, hyp):
    norm = lambda t: re.findall(r"\w+", t.lower().replace("ё", "е"))
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


def validate_audio(ck, s, stats):
    al = s["audio_labels"]
    lim = s["limits"]
    ref = {row["file"]: row["transcript"] for row in csv.DictReader(open(DATA / "audio" / "audio.csv", encoding="utf-8"))}
    for tg in sorted((ANN / "audio").glob("*/*.TextGrid")):
        w = str(tg.relative_to(ROOT))
        rel = f"{tg.parent.name}/{tg.stem}.wav"
        wav = DATA / "audio" / "voice_reports" / rel
        if not wav.exists():
            ck.err(w, f"нет аудиофайла {rel}")
            continue
        with wave.open(str(wav)) as wf:
            dur = wf.getnframes() / wf.getframerate()
        xmin, xmax, tiers = parse_textgrid(tg)
        if abs(xmax - dur) > 0.01 or xmin != 0:
            ck.err(w, f"границы {xmin}..{xmax} не совпадают с длительностью {dur:.3f}")
        seg, tr = tiers.get("segment"), tiers.get("transcript")
        if seg is None or tr is None:
            ck.err(w, "нужны слои segment и transcript")
            continue
        for name, ivs in (("segment", seg), ("transcript", tr)):
            if ivs[0][0] != 0 or abs(ivs[-1][1] - xmax) > 1e-6 or any(abs(a[1] - b[0]) > 1e-6 for a, b in zip(ivs, ivs[1:])):
                ck.err(w, f"слой {name}: интервалы должны идти встык от 0 до конца")
        if [(a, b) for a, b, _ in seg] != [(a, b) for a, b, _ in tr]:
            ck.err(w, "границы transcript не совпадают с segment")
        for (a, b, lab), (_, _, t) in zip(seg, tr):
            if lab not in al["tiers"]["segment"]["labels"]:
                ck.err(w, f"неизвестная метка сегмента {lab!r}")
            if lab and b - a < lim["audio_min_segment_sec"]:
                ck.err(w, f"сегмент {lab} {a}-{b} короче {lim['audio_min_segment_sec']} с")
            if bool(lab) != bool(t.strip()):
                ck.err(w, f"сегмент {a}-{b}: метка и транскрипция должны быть заполнены вместе")
        side = tg.with_suffix(".json")
        if not side.exists():
            ck.err(w, "нет файла с классами записи (.json)")
            continue
        meta = json.loads(side.read_text(encoding="utf-8"))
        check_doc_labels(ck, str(side.relative_to(ROOT)), s, meta["labels"], keys=("category", "component", "severity"))
        for k in ("urgency", "audio_quality"):
            if meta["labels"].get(k) not in al["file"][k]["values"]:
                ck.err(str(side.relative_to(ROOT)), f"{k}={meta['labels'].get(k)!r}")
        speech = [t for (_, _, lab), (_, _, t) in zip(seg, tr) if lab]
        for e in meta.get("entities", []):
            i = e.get("segment_index")
            if not isinstance(i, int) or not 0 <= i < len(tr) or not seg[i][2]:
                ck.err(str(side.relative_to(ROOT)), f"сущность {e.get('id')}: неверный segment_index {i}")
                continue
            check_entities(ck, f"{side.relative_to(ROOT)} seg{i}", s, tr[i][2], [e])
        full = " ".join(speech)
        if rel in ref:
            stats.setdefault("wer", []).append((rel, wer(ref[rel], full)))
        ck.ok["audio_files"] += 1
        ck.ok["audio_segments"] += len(speech)


def cmd_validate(args):
    s = load_schema()
    ck, stats = Checker(), {}
    validate_reports(ck, s)
    validate_logs(ck, s)
    validate_images(ck, s)
    validate_audio(ck, s, stats)
    lines = ["# Отчёт о проверке аннотаций", "",
             f"Дата проверки: {datetime.now():%Y-%m-%d %H:%M}. Скрипт: `scripts/annotation_tools.py validate`. "
             f"Схема: `annotation_schema/schema.json`, версия {s['version']}.", "",
             "| Что проверено | Количество |", "|---|---:|"]
    names = {"text_reports": "баг-репортов (уровни 1–3)", "entities": "сущностей во всех модальностях",
             "log_lines": "строк логов", "images": "изображений", "image_regions": "областей на изображениях",
             "audio_files": "аудиозаписей", "audio_segments": "речевых сегментов"}
    for k, v in names.items():
        lines.append(f"| {v} | {ck.ok.get(k, 0)} |")
    if stats.get("wer"):
        lines += ["", "Сверка транскрипций с эталонным текстом из `data/audio/audio.csv` (WER, цель ≤ "
                  f"{s['quality_targets']['transcript_wer_max']:.0%}):", "", "| Файл | WER |", "|---|---:|"]
        lines += [f"| `{f}` | {v:.1%} |" for f, v in stats["wer"]]
        for f, v in stats["wer"]:
            if v > s["quality_targets"]["transcript_wer_max"]:
                ck.err(f, f"WER {v:.1%} выше порога")
    lines.append("")
    if ck.errors:
        lines += [f"**Найдено ошибок: {len(ck.errors)}.**", ""] + [f"- {e}" for e in ck.errors]
    else:
        lines.append("**Результат: ошибок не найдено.** Все аннотации соответствуют схеме: метки и атрибуты из "
                     "словарей, смещения совпадают с текстом, разделы покрывают документ, рамки внутри изображений, "
                     "интервалы аудио идут встык и совпадают с длительностью записи.")
    report = "\n".join(lines) + "\n"
    if not args.no_report:
        (ROOT / "docs" / "annotation_validation_report.md").write_text(report, encoding="utf-8")
    print(report)
    return 1 if ck.errors else 0


# ---------------------------------------------------------------- conllu
TOKEN_RE = re.compile(r"\w+|[^\w\s]")


def cmd_conllu(args):
    out_dir = ANN / "text" / "conllu"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted((ANN / "text" / "reports").glob("*/*.json")):
        a = json.loads(f.read_text(encoding="utf-8"))
        text = report_text(find_report(a["report_id"]))
        ents = sorted(a["entities"], key=lambda e: e["start"])
        secs = a["sections"]
        lab = a["labels"]
        out = [f"# newdoc id = {a['report_id']}",
               f"# labels = ticket_type={lab['ticket_type']} category={lab['category']} "
               f"component={lab['component']} severity={lab['severity']}"]
        pos, sent = 0, 0
        for line in text.split("\n"):
            start = pos
            pos += len(line) + 1
            if not line.strip():
                continue
            sent += 1
            sec = next((x["label"] for x in secs if x["start"] <= start + len(line) - len(line.lstrip()) < x["end"]), "_")
            out += [f"# sent_id = {a['report_id']}-{sent}", f"# section = {sec}", f"# text = {line.strip()}"]
            toks = list(TOKEN_RE.finditer(line))
            prev_ent = None
            for i, m in enumerate(toks, 1):
                ts, te = start + m.start(), start + m.end()
                ent = next((e for e in ents if e["start"] < te and ts < e["end"]), None)
                if ent is None:
                    tag = "O"
                else:
                    tag = ("I-" if prev_ent is ent else "B-") + ent["label"]
                prev_ent = ent
                misc = [f"NER={tag}"]
                if m.end() < len(line) and not line[m.end()].isspace():
                    misc.append("SpaceAfter=No")
                form = m.group()
                out.append("\t".join([str(i), form, "_", "_", "_", "_", "_", "_", "_", "|".join(misc)]))
            out.append("")
        (out_dir / f"{a['report_id']}.conllu").write_text("\n".join(out) + "\n", encoding="utf-8")
        n += 1
    print(f"CoNLL-U: {n} файлов -> {out_dir.relative_to(ROOT)}")


# ---------------------------------------------------------------- agree
def kappa(a, b):
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def f1(tp, na, nb):
    p = tp / nb if nb else 0
    r = tp / na if na else 0
    return 2 * p * r / (p + r) if p + r else 0.0


def iou(b1, b2):
    x1, y1 = max(b1[0], b2[0]), max(b1[1], b2[1])
    x2, y2 = min(b1[0] + b1[2], b2[0] + b2[2]), min(b1[1] + b1[3], b2[1] + b2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    return inter / (b1[2] * b1[3] + b2[2] * b2[3] - inter)


def cmd_agree(args):
    s = load_schema()
    A, B = Path(args.a), Path(args.b)
    q = s["quality_targets"]
    rows = []
    load = lambda d: {json.loads(p.read_text(encoding="utf-8"))["report_id"]: json.loads(p.read_text(encoding="utf-8"))
                      for p in (d / "text" / "reports").glob("*/*.json")}
    ra, rb = load(A), load(B)
    common = sorted(set(ra) & set(rb))
    if common:
        for k in ("ticket_type", "category", "component", "severity"):
            v = kappa([str(ra[i]["labels"][k]) for i in common], [str(rb[i]["labels"][k]) for i in common])
            rows.append((f"каппа Коэна: {k} ({len(common)} тикетов)", v, q["cohen_kappa"][k]))
        tp = na = nb = tpp = 0
        for i in common:
            ea = {(e["label"], e["start"], e["end"]) for e in ra[i]["entities"]}
            eb = {(e["label"], e["start"], e["end"]) for e in rb[i]["entities"]}
            tp += len(ea & eb)
            na += len(ea)
            nb += len(eb)
            tpp += sum(any(l == m and s1 < e2 and s2 < e1 for m, s2, e2 in eb) for l, s1, e1 in ea)
        rows.append(("F1 сущностей, точное совпадение", f1(tp, na, nb), q["span_f1_exact"]))
        rows.append(("F1 сущностей, перекрытие", f1(tpp, na, nb), None))
    ca, cb = A / "images" / "screenshots_coco.json", B / "images" / "screenshots_coco.json"
    if ca.exists() and cb.exists():
        ja, jb = json.loads(ca.read_text(encoding="utf-8")), json.loads(cb.read_text(encoding="utf-8"))
        name = lambda j: {c["id"]: c["name"] for c in j["categories"]}
        fn = lambda j: {im["id"]: im["file_name"] for im in j["images"]}
        na_, nb_ = name(ja), name(jb)
        fa, fb = fn(ja), fn(jb)
        boxes = lambda j, nm, ff: [(ff[x["image_id"]], nm[x["category_id"]], x["bbox"]) for x in j["annotations"]]
        ba, bb = boxes(ja, na_, fa), boxes(jb, nb_, fb)
        used, tp = set(), 0
        for f, l, b in ba:
            best = max(((iou(b, b2), k) for k, (f2, l2, b2) in enumerate(bb)
                        if f2 == f and l2 == l and k not in used), default=(0, None))
            if best[0] >= q["bbox_iou_match"]:
                used.add(best[1])
                tp += 1
        rows.append((f"F1 областей на изображениях (IoU ≥ {q['bbox_iou_match']})", f1(tp, len(ba), len(bb)), q["bbox_f1"]))
    tol = s["limits"]["audio_boundary_tolerance_sec"]
    hit = tot = 0
    for tg in (A / "audio").glob("*/*.TextGrid"):
        other = B / "audio" / tg.parent.name / tg.name
        if not other.exists():
            continue
        sa = [x for x in parse_textgrid(tg)[2]["segment"]]
        sb = [x for x in parse_textgrid(other)[2]["segment"]]
        bnd_b = [iv[0] for iv in sb[1:]]
        for iv in sa[1:]:
            tot += 1
            hit += any(abs(iv[0] - t) <= tol for t in bnd_b)
    if tot:
        rows.append((f"совпадение границ аудиосегментов (±{tol} с)", hit / tot, q["audio_segment_boundary_agreement"]))
    print("| Метрика | Значение | Цель |\n|---|---:|---:|")
    for name_, v, t in rows:
        mark = "" if t is None else (" ✓" if v >= t else " ✗")
        print(f"| {name_} | {v:.3f}{mark} | {'' if t is None else t} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("configs")
    sub.add_parser("tasks")
    v = sub.add_parser("validate")
    v.add_argument("--no-report", action="store_true", help="не перезаписывать docs/annotation_validation_report.md")
    sub.add_parser("conllu")
    g = sub.add_parser("agree")
    g.add_argument("--a", required=True, help="папка аннотаций разметчика A (структура как у annotations/)")
    g.add_argument("--b", required=True, help="папка аннотаций разметчика B")
    args = ap.parse_args()
    rc = {"configs": cmd_configs, "tasks": cmd_tasks, "validate": cmd_validate,
          "conllu": cmd_conllu, "agree": cmd_agree}[args.cmd](args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
