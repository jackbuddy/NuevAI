import os
import json
import re
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter
from tqdm import tqdm

RAW_DIR = "data/raw"
OUTPUT_CHUNKS = "data/cleaned_chunks.json"
OUTPUT_LINKS = "data/links_registry.json"

CATEGORIES = ["nexus", "courses", "faculty", "calendar", "news", "handbook", "faq", "sheets", "docs"]

chunks_output = []
links_registry = []

URL_REGEX = r'https?://[^\s<>"]+|www\.[^\s<>"]+'

def extract_text_from_json(data):
    """Recursively extract all text content from arbitrary JSON structures."""
    if isinstance(data, str):
        return data
    elif isinstance(data, list):
        return " ".join([extract_text_from_json(item) for item in data])
    elif isinstance(data, dict):
        # Priority text fields
        priority_keys = ["content", "text", "markdown", "body", "html", "page_text", "raw_text", "description"]
        extracted = []
        for key in priority_keys:
            if key in data and data[key]:
                extracted.append(extract_text_from_json(data[key]))
        
        if extracted:
            return " ".join(extracted)
        
        # Fallback: concatenate all string values, skipping system metadata
        fallback = []
        for k, v in data.items():
            if k not in ["url", "title", "id", "file_name", "timestamp"] and v:
                fallback.append(extract_text_from_json(v))
        return " ".join(fallback)
    return ""

def clean_html_if_needed(text):
    if "<html" in text.lower() or "<div" in text.lower() or "<p>" in text.lower():
        return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)
    return text.strip()

for cat in CATEGORIES:
    cat_dir = os.path.join(RAW_DIR, cat)
    if not os.path.exists(cat_dir):
        continue

    files = [f for f in os.listdir(cat_dir) if f.endswith(".json")]
    
    if cat == "handbook":
        splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=60)
    elif cat == "courses":
        splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=40)
    else:
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

    for filename in tqdm(files, desc=f"Parsing {cat}"):
        file_path = os.path.join(cat_dir, filename)
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            
            title = ""
            url = ""
            if isinstance(data, dict):
                title = data.get("title") or data.get("page_title") or filename
                url = data.get("url") or data.get("link") or ""
            
            raw_content = extract_text_from_json(data)
            clean_content = clean_html_if_needed(raw_content)

            # Extract direct resource links
            found_urls = re.findall(URL_REGEX, raw_content + " " + str(url))
            for found_url in found_urls:
                if any(x in found_url.lower() for x in ["forms.gle", "docs.google", "drive.google", "slides", "pdf", "sheet"]):
                    links_registry.append({
                        "title": title,
                        "url": found_url,
                        "category": cat,
                        "source_file": filename
                    })

            if not clean_content or len(clean_content) < 10:
                continue

            if cat in ["faculty", "faq"]:
                chunks_output.append({
                    "chunk_id": f"{cat}_{filename}_0",
                    "source_category": cat,
                    "source_file": filename,
                    "content": f"Title: {title}\nURL: {url}\nContent: {clean_content}"
                })
            else:
                splits = splitter.split_text(clean_content)
                for idx, split in enumerate(splits):
                    chunks_output.append({
                        "chunk_id": f"{cat}_{filename}_{idx}",
                        "source_category": cat,
                        "source_file": filename,
                        "content": f"Title: {title}\nURL: {url}\nContent: {split.strip()}"
                    })
        except Exception:
            pass

os.makedirs("data", exist_ok=True)

# Deduplicate direct links
unique_links = {l["url"]: l for l in links_registry}.values()

with open(OUTPUT_CHUNKS, "w", encoding="utf-8") as f:
    json.dump(chunks_output, f, indent=2)

with open(OUTPUT_LINKS, "w", encoding="utf-8") as f:
    json.dump(list(unique_links), f, indent=2)

print(f"\nParsing complete!\n  - Cleaned Chunks: {len(chunks_output)} -> {OUTPUT_CHUNKS}\n  - Extracted Direct Links: {len(list(unique_links))} -> {OUTPUT_LINKS}")
