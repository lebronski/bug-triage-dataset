"""Сбор текстовой модальности: баг-репорты (GitBugs) и фрагменты логов (LogHub).

Запуск (после scripts/download_sources.sh):
    python scripts/collect_text.py --sources sources --out data/text --per-category 10

Что делает:
1. Читает CSV-выгрузки баг-трекеров из GitBugs (Apache Jira, Mozilla Bugzilla,
   GitHub Issues VS Code).
2. Относит каждый репорт к одной из 10 категорий по ключевым словам в заголовке
   (scripts/categories.py), отбрасывает репорты без описания.
3. Для каждой категории отбирает N репортов: не более 3 из одного проекта,
   с разными приоритетами, детерминированно (seed=42).
4. Сохраняет каждый репорт отдельным JSON-файлом + общий bug_reports.csv.
5. Вырезает из логов LogHub строки, относящиеся к категориям, где такие логи есть.
"""
import argparse
import json
import re
import zipfile
from pathlib import Path

import pandas as pd

from categories import CATEGORIES

SEED = 42

# Проект GitBugs -> (путь к CSV внутри репозитория GitBugs, трекер, шаблон ссылки)
PROJECTS = {
    "spark": ("spark/spark_bugs.csv", "Apache Jira", None),
    "hadoop": ("hadoop/hadoop_bugs.csv", "Apache Jira", None),
    "hbase": ("hbase/hbase_bugs.csv", "Apache Jira", None),
    "cassandra": ("cassandra/cassandra_bugs.csv", "Apache Jira", None),
    "firefox": ("firefox/Firefox_bugs.csv.zip", "Mozilla Bugzilla", "https://bugzilla.mozilla.org/show_bug.cgi?id={id}"),
    "thunderbird": ("thunderbird/Thunderbird_bugs.csv", "Mozilla Bugzilla", "https://bugzilla.mozilla.org/show_bug.cgi?id={id}"),
    "seamonkey": ("seamonkey/SeaMonkey_bugs.csv", "Mozilla Bugzilla", "https://bugzilla.mozilla.org/show_bug.cgi?id={id}"),
    "vscode": ("ms_vscode_bugs/vscode_bugs.csv.zip", "GitHub Issues", "https://github.com/microsoft/vscode/issues/{id}"),
}

# Предварительная (автоматическая) привязка исходного приоритета к шкале критичности
# проекта. Это предразметка для полуавтоматической разметки из задания 2,
# а не окончательная метка: её проверяет разметчик.
SEVERITY_MAP = {
    "Blocker": "Blocker", "Urgent": "Blocker", "P1": "Blocker",
    "Critical": "Critical", "High": "Critical", "P2": "Critical",
    "Major": "Major", "Normal": "Major", "P3": "Major",
    "Minor": "Minor", "Trivial": "Minor", "Low": "Minor", "P4": "Minor", "P5": "Minor",
}

# Логи LogHub: категория -> список (система, регулярное выражение, сколько строк взять)
LOG_RULES = {
    "authentication": [("OpenSSH", r"Failed password|Invalid user|authentication failure", 40),
                       ("Linux", r"authentication failure|FAILED LOGIN", 40)],
    "security": [("OpenSSH", r"POSSIBLE BREAK-IN ATTEMPT|Did not receive identification|Bad protocol", 40)],
    "network": [("Thunderbird", r"Connection refused|Connection reset|timed out|No route", 20),
                ("Hadoop", r"Connection refused|timed out|Retrying connect|NoRouteToHost|ConnectException", 40)],
    "memory": [("Spark", r"[Mm]emory|MB|spill", 40),
               ("Hadoop", r"[Mm]emory|OutOfMemory|heap", 40)],
    "crash": [("Zookeeper", r"Exception|ERROR|FATAL", 40),
              ("HDFS", r"[Ee]xception|ERROR|WARN", 40)],
    "performance": [("Hadoop", r"took|[Ss]low|elapsed|ms\b", 40),
                    ("OpenStack", r"took|time: [0-9.]+|len: \d+ time", 40)],
    "ui_layout": [("Android", r"WindowManager|SurfaceFlinger|ViewRootImpl|layout", 40)],
}


def read_project(src: Path, name: str) -> pd.DataFrame:
    rel, tracker, url_tpl = PROJECTS[name]
    path = src / "gitbugs" / rel
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            member = [m for m in z.namelist() if m.endswith(".csv") and not m.startswith("__MACOSX")][0]
            with z.open(member) as f:
                df = pd.read_csv(f, low_memory=False)
    else:
        df = pd.read_csv(path, low_memory=False)
    df["project"] = name
    df["tracker"] = tracker
    df["url"] = df["Issue id"].map(lambda i: url_tpl.format(id=i) if url_tpl else None)
    if "Priority" not in df:
        df["Priority"] = None
    return df


def categorize(summary: str):
    s = str(summary).lower()
    for c in CATEGORIES:
        if re.search(c["summary_regex"], s, flags=re.I):
            return c["id"]
    return None


def clean(text) -> str:
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def collect_reports(src: Path, out: Path, per_category: int):
    frames = [read_project(src, p) for p in PROJECTS]
    df = pd.concat(frames, ignore_index=True)
    df["Description"] = df["Description"].map(clean)
    df["Summary"] = df["Summary"].map(clean)
    df = df[(df["Description"].str.len() >= 200) & (df["Description"].str.len() <= 4000)]
    # отбрасываем автоматические отчёты CI о нестабильных тестах — это не пользовательские баг-репорты
    df = df[~df["Summary"].str.contains(r"^(?:Intermittent|Perma|\[meta\])|\[Intermittent\]|TEST-UNEXPECTED|CLONE-TEST|DISREGARD|\bperma\b", case=False, regex=True)]
    df["category"] = df["Summary"].map(categorize)
    df = df[df["category"].notna()]
    print("Кандидатов по категориям:\n", df["category"].value_counts().to_string())

    rows = []
    for cat in CATEGORIES:
        pool = df[df["category"] == cat["id"]].sample(frac=1, random_state=SEED)
        chosen, per_project, seen_prio = [], {}, set()
        # 1-й проход: разнообразие проектов и приоритетов
        for _, r in pool.iterrows():
            if len(chosen) >= per_category:
                break
            if per_project.get(r.project, 0) >= 3:
                continue
            if r.Priority in seen_prio and len(seen_prio) < 3 and len(chosen) < 4:
                continue
            chosen.append(r)
            per_project[r.project] = per_project.get(r.project, 0) + 1
            seen_prio.add(r.Priority)
        # 2-й проход: добрать до нужного числа, если проектов мало
        if len(chosen) < per_category:
            ids = {(c.project, c["Issue id"]) for c in chosen}
            for _, r in pool.iterrows():
                if len(chosen) >= per_category:
                    break
                if (r.project, r["Issue id"]) not in ids:
                    chosen.append(r)
        cat_dir = out / "bug_reports" / cat["id"]
        cat_dir.mkdir(parents=True, exist_ok=True)
        for r in chosen:
            prio = None if pd.isna(r.Priority) or r.Priority == "--" else str(r.Priority)
            rec = {
                "report_id": f"{r.project}-{r['Issue id']}",
                "category": cat["id"],
                "component_hint": cat["component"],
                "source_project": r.project,
                "tracker": r.tracker,
                "issue_id": str(r["Issue id"]),
                "url": r.url,
                "summary": r.Summary,
                "description": r.Description,
                "priority_original": prio,
                "severity_prelabel": SEVERITY_MAP.get(prio),
                "status": None if pd.isna(r.get("Status")) else r.get("Status"),
                "resolution": None if pd.isna(r.get("Resolution")) else r.get("Resolution"),
                "created": None if pd.isna(r.get("Created")) else str(r.get("Created")),
                "affects_versions": None if pd.isna(r.get("Affects Version/s")) else str(r.get("Affects Version/s")),
                "license": "CC BY 4.0 (GitBugs, Patil 2025)",
            }
            (cat_dir / f"{rec['report_id']}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append(rec)
    pd.DataFrame(rows).to_csv(out / "bug_reports.csv", index=False, encoding="utf-8")
    print(f"Сохранено баг-репортов: {len(rows)}")
    return rows


def collect_logs(src: Path, out: Path):
    rows = []
    for cat, rules in LOG_RULES.items():
        for system, regex, limit in rules:
            path = src / "loghub" / system / f"{system}_2k.log"
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            hits = [(i + 1, ln) for i, ln in enumerate(lines) if re.search(regex, ln)][:limit]
            if not hits:
                continue
            d = out / "logs" / cat
            d.mkdir(parents=True, exist_ok=True)
            fn = d / f"{system.lower()}_{cat}.log"
            fn.write_text("\n".join(ln for _, ln in hits) + "\n", encoding="utf-8")
            rows.append({"log_file": str(fn.relative_to(out)), "category": cat, "system": system,
                         "source_file": f"loghub/{system}/{system}_2k.log", "regex": regex,
                         "lines": len(hits), "first_line_no": hits[0][0], "last_line_no": hits[-1][0]})
    pd.DataFrame(rows).to_csv(out / "logs.csv", index=False, encoding="utf-8")
    print(f"Сохранено лог-файлов: {len(rows)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="sources", type=Path)
    ap.add_argument("--out", default="data/text", type=Path)
    ap.add_argument("--per-category", default=10, type=int)
    a = ap.parse_args()
    collect_reports(a.sources, a.out, a.per_category)
    collect_logs(a.sources, a.out)
