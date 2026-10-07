import os
import json
import shutil
from pathlib import Path

POSSIBLE_SOURCES = [
    Path("../nexus-scraper/data/pages"),
    Path("nexus-scraper/data/pages"),
    Path("./pages"),
    Path("../pages"),
]

source_dir = None
for p in POSSIBLE_SOURCES:
    if p.exists() and p.is_dir():
        source_dir = p
        break

if not source_dir:
    print("Error: Could not locate 'pages' folder inside 'nexus-scraper'.")
    exit(1)

target_base = Path("data/raw")
categories = ["nexus", "courses", "faculty", "calendar", "news", "handbook", "faq", "sheets", "docs"]

for cat in categories:
    (target_base / cat).mkdir(parents=True, exist_ok=True)

json_files = list(source_dir.glob("*.json"))
print(f"Found source directory at: {source_dir.resolve()}")
print(f"Sorting {len(json_files)} JSON files...")

stats = {cat: 0 for cat in categories}

for file_path in json_files:
    filename = file_path.name
    lower_name = filename.lower()
    
    title_str, url_str, content_str = "", "", ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
            if isinstance(data, dict):
                title_str = str(data.get("title", "")).lower()
                url_str = str(data.get("url", "")).lower()
                content_str = str(data.get("content", "") or data.get("html", "") or data.get("text", ""))[:500].lower()
    except Exception:
        pass

    search_text = f"{lower_name} {title_str} {url_str} {content_str}"

    if any(k in search_text for k in ["sheet", "csv", "table", "spreadsheet", "google sheets"]):
        dest = "sheets"
    elif any(k in search_text for k in ["faculty", "teacher", "staff", "bio", "instructor", "department", "directory"]):
        dest = "faculty"
    elif any(k in search_text for k in ["course", "syllabus", "class", "curriculum", "catalog"]):
        dest = "courses"
    elif any(k in search_text for k in ["calendar", "schedule", "events", "dates", "athletics", "game"]):
        dest = "calendar"
    elif any(k in search_text for k in ["handbook", "policy", "rules", "code-of-conduct", "guidelines"]):
        dest = "handbook"
    elif any(k in search_text for k in ["faq", "questions", "answers", "help", "support"]):
        dest = "faq"
    elif any(k in search_text for k in ["news", "announcement", "newsletter", "update", "article"]):
        dest = "news"
    elif any(k in search_text for k in ["gdoc", "docx", "pdf", "drive", "google doc"]):
        dest = "docs"
    else:
        dest = "nexus"

    shutil.copy2(file_path, target_base / dest / filename)
    stats[dest] += 1

print("\nSorting complete. Dataset distribution:")
for cat, count in stats.items():
    print(f"  - {cat}: {count} files")
