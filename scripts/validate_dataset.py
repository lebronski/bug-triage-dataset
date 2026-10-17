"""Проверка корректности загруженных данных (шаг 4.3 лабораторной работы).

Запуск:
    python scripts/validate_dataset.py            # проверка + отчёт docs/validation_report.md
    python scripts/validate_dataset.py --update-checksums   # пересчитать data/checksums.sha256

Проверяется:
- для каждой из 10 категорий есть данные всех трёх модальностей и их не меньше минимума;
- каждый JSON баг-репорта читается и содержит обязательные поля, категория совпадает с папкой;
- лог-файлы непустые и в UTF-8;
- каждый PNG открывается и не повреждён, размер не меньше 300x300;
- каждый WAV читается, моно, 16 кГц, длительность больше 2 с;
- файлы метаданных (CSV) ссылаются только на существующие файлы и описывают все файлы;
- контрольные суммы SHA-256 совпадают с data/checksums.sha256.
"""
import argparse
import csv
import hashlib
import json
import sys
import wave
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from categories import CATEGORIES, CATEGORY_IDS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MIN = {"bug_reports": 10, "images": 3, "audio": 2}
REQUIRED = ["report_id", "category", "source_project", "tracker", "issue_id", "summary", "description", "license"]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def data_files():
    return sorted(p for p in DATA.rglob("*") if p.is_file() and p.name != "checksums.sha256")


def main(update: bool):
    errors, stats = [], {c: {"bug_reports": 0, "logs": 0, "images": 0, "audio": 0} for c in CATEGORY_IDS}

    # --- текст: баг-репорты
    for f in sorted((DATA / "text" / "bug_reports").glob("*/*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"{f}: JSON не читается ({e})")
            continue
        miss = [k for k in REQUIRED if not r.get(k)]
        if miss:
            errors.append(f"{f.name}: нет полей {miss}")
        if r.get("category") != f.parent.name:
            errors.append(f"{f.name}: категория {r.get('category')} не совпадает с папкой {f.parent.name}")
        if len(r.get("description", "")) < 200:
            errors.append(f"{f.name}: слишком короткое описание")
        stats.setdefault(f.parent.name, {}).setdefault("bug_reports", 0)
        stats[f.parent.name]["bug_reports"] += 1

    # --- текст: логи
    for f in sorted((DATA / "text" / "logs").glob("*/*.log")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
            if not lines:
                errors.append(f"{f.name}: пустой лог")
        except UnicodeDecodeError:
            errors.append(f"{f.name}: не UTF-8")
        stats[f.parent.name]["logs"] += 1

    # --- изображения
    for f in sorted((DATA / "images" / "screenshots").glob("*/*.png")):
        try:
            with Image.open(f) as im:
                im.verify()
            with Image.open(f) as im:
                w, h = im.size
            if w < 300 or h < 300:
                errors.append(f"{f.name}: слишком маленькое изображение {w}x{h}")
        except Exception as e:
            errors.append(f"{f.name}: повреждённый PNG ({e})")
        stats[f.parent.name]["images"] += 1

    # --- аудио
    for f in sorted((DATA / "audio" / "voice_reports").glob("*/*.wav")):
        try:
            with wave.open(str(f)) as w:
                dur = w.getnframes() / w.getframerate()
                if w.getnchannels() != 1 or w.getframerate() != 16000:
                    errors.append(f"{f.name}: ожидается моно 16 кГц, получено {w.getnchannels()} кан., {w.getframerate()} Гц")
                if dur < 2:
                    errors.append(f"{f.name}: длительность {dur:.1f} с < 2 с")
        except Exception as e:
            errors.append(f"{f.name}: WAV не читается ({e})")
        stats[f.parent.name]["audio"] += 1

    # --- полнота по категориям
    for c in CATEGORY_IDS:
        for mod, m in MIN.items():
            if stats[c][mod] < m:
                errors.append(f"категория {c}: {mod} = {stats[c][mod]} < {m}")

    # --- согласованность метаданных и файлов
    def check_csv(csv_path: Path, col: str, base: Path, pattern: str):
        with open(csv_path, encoding="utf-8") as fh:
            listed = {row[col] for row in csv.DictReader(fh)}
        actual = {str(p.relative_to(base)) for p in base.glob(pattern)}
        for x in sorted(listed - actual):
            errors.append(f"{csv_path.name}: файл {x} указан, но отсутствует")
        for x in sorted(actual - listed):
            errors.append(f"{csv_path.name}: файл {x} не описан в метаданных")
        return len(listed)

    n_rep_csv = 0
    with open(DATA / "text" / "bug_reports.csv", encoding="utf-8") as fh:
        ids = {row["report_id"] + ".json" for row in csv.DictReader(fh)}
        n_rep_csv = len(ids)
    files = {p.name for p in (DATA / "text" / "bug_reports").glob("*/*.json")}
    if ids != files:
        errors.append(f"bug_reports.csv и JSON-файлы расходятся: {len(ids ^ files)} шт.")
    check_csv(DATA / "text" / "logs.csv", "log_file", DATA / "text", "logs/*/*.log")
    check_csv(DATA / "images" / "images.csv", "file", DATA / "images" / "screenshots", "*/*.png")
    check_csv(DATA / "audio" / "audio.csv", "file", DATA / "audio" / "voice_reports", "*/*.wav")

    # --- контрольные суммы
    ck = DATA / "checksums.sha256"
    files_all = data_files()
    if update or not ck.exists():
        ck.write_text("".join(f"{sha256(p)}  {p.relative_to(ROOT).as_posix()}\n" for p in files_all), encoding="utf-8")
        print(f"Контрольные суммы записаны: {len(files_all)} файлов")
    stored = dict(reversed(line.split("  ", 1)) for line in ck.read_text(encoding="utf-8").split("\n") if line)
    for p in files_all:
        rel = p.relative_to(ROOT).as_posix()
        if rel not in stored:
            errors.append(f"{rel}: нет в checksums.sha256")
        elif stored[rel] != sha256(p):
            errors.append(f"{rel}: контрольная сумма не совпадает")

    # --- отчёт
    names = {c["id"]: c["name_ru"] for c in CATEGORIES}
    tot = {k: sum(s[k] for s in stats.values()) for k in ["bug_reports", "logs", "images", "audio"]}
    lines = [
        "# Отчёт о проверке данных",
        "",
        f"Дата проверки: {datetime.now():%Y-%m-%d %H:%M}. Скрипт: `scripts/validate_dataset.py`.",
        "",
        "| Категория (объект) | Баг-репорты | Логи | Скриншоты | Аудио |",
        "|---|---:|---:|---:|---:|",
    ]
    for c in CATEGORY_IDS:
        s = stats[c]
        lines.append(f"| `{c}` — {names[c]} | {s['bug_reports']} | {s['logs']} | {s['images']} | {s['audio']} |")
    lines += [f"| **Итого** | **{tot['bug_reports']}** | **{tot['logs']}** | **{tot['images']}** | **{tot['audio']}** |", "",
              f"Файлов данных под контролем SHA-256: {len(files_all)}. Записей в bug_reports.csv: {n_rep_csv}.", ""]
    if errors:
        lines += ["## Найденные ошибки", ""] + [f"- {e}" for e in errors]
    else:
        lines += ["**Результат: ошибок не найдено.** Все файлы читаются, форматы соответствуют описанию, "
                  "метаданные согласованы с файлами, контрольные суммы совпадают, "
                  "для каждой из 10 категорий есть данные всех трёх модальностей."]
    (ROOT / "docs" / "validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-checksums", action="store_true")
    sys.exit(main(ap.parse_args().update_checksums))
