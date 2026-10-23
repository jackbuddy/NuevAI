"""Builds the manual-download checklist from the files the crawler could not read.

    python scripts/make_download_checklist.py          # writes data/pipeline/DOWNLOAD_CHECKLIST.html (+ .json)
    python scripts/check_manual_docs.py                # compares the checklist with what is in the folder

The HTML page is self-contained (no internet needed): checkboxes remember progress, every row shows the exact filename to
save as, and the page can export your progress or scan the folder to tick off what is already there.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "pipeline"
SCRAPER = ROOT.parent / "nexus-scraper" / "data" / "google_links_deduped.json"
OUT_HTML, OUT_JSON = DATA / "DOWNLOAD_CHECKLIST.html", DATA / "download_checklist.json"
FOLDER = DATA / "manual_docs"

SKIP = re.compile(r"salary|payroll|compensation|'s email|\bemail\b.*\bfrom\b|ssn|medical record|diploma|rsvp|roster", re.I)
REVIEW = re.compile(r"\bLOA\b|leave of absence|physician|authorization|reimbursement|evaluation|incident|narcan", re.I)
ATHLETICS = re.compile(r"tennis|swim|basketball|volleyball|soccer|golf|track|cross country|baseball|softball|lacrosse|badminton|"
                       r"water polo|athlet|sports?|team|schedule|handbook", re.I)
GOOD = lambda t: bool(t) and len(t.strip()) > 3 and not t.strip().lower().startswith(("http", "here", "link", "click", "see ", "pd f"))


def file_id(url: str):
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", url) or re.search(r"[?&]id=([A-Za-z0-9_-]{20,})", url)
    return m.group(1) if m else None


def kind_of(url: str) -> str:
    return ("doc" if "/document/" in url else "slides" if "/presentation/" in url else "sheet" if "/spreadsheets/" in url
            else "file" if "drive.google.com" in url else "other")


FORMAT = {  # kind -> (how to export, extension)
    "doc": ("File > Download > Microsoft Word (.docx)", "docx"),
    "slides": ("File > Download > PDF Document (.pdf)", "pdf"),
    "sheet": ("File > Download > Microsoft Excel (.xlsx)", "xlsx"),
    "file": ("Download as is (usually a PDF). Skip it if it is just an image.", "pdf"),
    "other": ("Download as a PDF or Word file", "pdf"),
}


def safe_name(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s&'().,+-]", "", title)).strip()[:80] or "Untitled"


def board_label(found_on: list[str]) -> str:
    for u in found_on or []:
        m = re.search(r"resourceboarddetail/(\d+)", u)
        if m:
            return f"Nexus resource board {m.group(1)}"
        if "nuevaschool.org" in u:
            return "school website"
    return "Nexus"


def build() -> list[dict]:
    needs = json.loads((DATA / "needs_access.json").read_text(encoding="utf-8"))
    meta = {}
    if SCRAPER.exists():
        for x in json.loads(SCRAPER.read_text(encoding="utf-8")):
            fid = file_id(x.get("url", ""))
            if fid:
                meta[fid] = x
    rows, used = [], set()
    for n in needs:
        fid = file_id(n["url"])
        m = meta.get(fid, {})
        title = next((t for t in (n.get("anchor_text"), m.get("text")) if GOOD(t or "")), None)
        untitled = title is None
        kind = kind_of(n["url"])
        how, ext = FORMAT[kind]
        title = title or f"Untitled {kind} {(fid or '')[:6]}"
        base = safe_name(re.sub(r"\.(docx?|pdf|xlsx?)$", "", title, flags=re.I))
        name, i = f"{base}.{ext}", 2
        while name.lower() in used:
            name, i = f"{base} ({i}).{ext}", i + 1
        used.add(name.lower())
        found = board_label(m.get("found_on", []))
        status = "skip" if SKIP.search(title) else "review" if REVIEW.search(title) else "ok"
        priority = 1 if (ATHLETICS.search(title) and status == "ok") else 2 if status == "ok" else 3
        rows.append({"id": len(rows) + 1, "title": title, "untitled": untitled, "url": n["url"], "kind": kind, "how": how,
                     "filename": name, "found_on": found, "status": status, "priority": priority,
                     "why_unreadable": "private (needs your school sign-in)" if "HTTP" in n["reason"] else "a Drive file, not a Google Doc"})
    rows.sort(key=lambda r: (r["priority"], r["title"].lower()))
    for i, r in enumerate(rows, 1):
        r["id"] = i
    return rows


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NuevAI download checklist</title>
<style>
:root{--ink:#0b2145;--ink2:#5b6b85;--line:#e2e8f1;--soft:#f3f6fb;--navy:#0b2145;--blue:#2c5792;--ok:#1b4177}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:#fff}
.wrap{max-width:900px;margin:0 auto;padding:28px 18px 80px}h1{font-size:28px;margin:0 0 4px}.sub{color:var(--ink2);margin:0 0 18px}
.box{border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:14px 0;background:#fff}
.steps{counter-reset:s;margin:0;padding:0;list-style:none}.steps li{counter-increment:s;padding:6px 0 6px 34px;position:relative}
.steps li:before{content:counter(s);position:absolute;left:0;top:6px;width:22px;height:22px;border-radius:50%;background:var(--navy);color:#fff;font-size:12px;display:grid;place-items:center}
code,.path{background:var(--soft);border-radius:6px;padding:2px 6px;font:13px ui-monospace,Menlo,monospace;word-break:break-all}
.bar{height:10px;border-radius:99px;background:var(--soft);overflow:hidden}.bar i{display:block;height:100%;background:var(--navy);width:0;transition:width .3s}
.top{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin:10px 0}
button,.btn{font:inherit;font-size:13px;font-weight:600;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:99px;padding:6px 13px;cursor:pointer}
button:hover{background:var(--soft)}button.on{background:var(--navy);color:#fff;border-color:var(--navy)}
h2{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--blue);margin:26px 0 8px}
.row{display:grid;grid-template-columns:26px 1fr auto;gap:10px;align-items:start;border:1px solid var(--line);border-radius:12px;padding:11px 13px;margin:8px 0}
.row.done{background:var(--soft)}.row.done .t{text-decoration:line-through;color:var(--ink2)}.row.skip{opacity:.6}
.row input{width:18px;height:18px;margin-top:2px;accent-color:var(--navy)}.t{font-weight:700}.meta{color:var(--ink2);font-size:12.5px;margin-top:2px}
.tag{display:inline-block;font-size:11px;font-weight:700;border-radius:99px;padding:1px 8px;margin-left:6px;background:var(--soft);color:var(--blue);vertical-align:1px}
.tag.skip{background:#fff3d6;color:#8a5a00}.tag.review{background:#e8eff8;color:#1b4177}
.fn{margin-top:6px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}.act{display:flex;gap:6px;flex-direction:column;align-items:stretch}
a.btn{text-decoration:none;text-align:center;background:var(--navy);color:#fff;border-color:var(--navy)}
.hide{display:none}.note{font-size:12.5px;color:var(--ink2)}
</style></head><body><div class="wrap">
<h1>Documents to download</h1><p class="sub">__N__ files NuevAI couldn't read on its own. Download them while signed in to your school Google account.</p>

<div class="box"><ol class="steps">
<li><b>Open</b> a file with the button on its row (signed in to your school account).</li>
<li><b>Download</b> it using the format shown, and <b>save it with the exact filename shown</b> (use "Copy name") into this folder:<br><span class="path" id="path">__FOLDER__</span> <button onclick="copy(document.getElementById('path').textContent,this)">Copy path</button></li>
<li>Tick the row. When you are done, tell me <b>"downloaded"</b>, or run <code>python run_pipeline.py --stages manual,chunk,index</code>.</li>
</ol><p class="note">Rows marked <b>Skip</b> look private (salary, personal email), so they are hidden from the to-do count. Rows marked <b>Review</b> are probably fine but check the contents first: anything in these files becomes answerable to everyone who uses the chatbot.</p></div>

<div class="box"><div class="top"><b id="count"></b><span><button onclick="scan()">Scan folder…</button> <button onclick="exportCsv()">Export progress (CSV)</button> <button onclick="copyAll()">Copy all filenames</button></span></div>
<div class="bar"><i id="bar"></i></div>
<div class="top" style="margin-bottom:0"><span id="filters"></span><span class="note" id="scanmsg"></span></div></div>
<div id="list"></div>

<script>
const DATA=__DATA__;const KEY='nuevai-download-checklist-v1';
let state={};try{state=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
let filter='todo';
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(state))}catch(e){}};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function copy(t,btn){navigator.clipboard.writeText(t).then(()=>{const o=btn.textContent;btn.textContent='Copied';setTimeout(()=>btn.textContent=o,1200)})}
const groups=[[1,'Athletics & handbooks (most useful first)'],[2,'Other documents'],[3,'Looks private (skip) or needs a review']];
function render(){
  const todo=DATA.filter(r=>r.status!=='skip'),done=todo.filter(r=>state[r.id]).length;
  document.getElementById('count').textContent=done+' of '+todo.length+' downloaded';
  document.getElementById('bar').style.width=(todo.length?100*done/todo.length:0)+'%';
  const fs=[['todo','To do'],['done','Done'],['all','All']];
  document.getElementById('filters').innerHTML=fs.map(([k,l])=>`<button class="${filter===k?'on':''}" onclick="filter='${k}';render()">${l}</button>`).join(' ');
  let h='';
  for(const [p,label] of groups){
    const rows=DATA.filter(r=>r.priority===p&&(filter==='all'||(filter==='done'?state[r.id]:!state[r.id]&&r.status!=='skip')));
    if(!rows.length)continue;h+=`<h2>${label} (${rows.length})</h2>`;
    for(const r of rows){
      h+=`<div class="row ${state[r.id]?'done':''} ${r.status==='skip'?'skip':''}"><input type="checkbox" ${state[r.id]?'checked':''} onchange="state[${r.id}]=this.checked;save();render()" aria-label="Done">
      <div><div class="t">${esc(r.title)}<span class="tag">${r.kind}</span>${r.status!=='ok'?`<span class="tag ${r.status}">${r.status==='skip'?'Skip':'Review'}</span>`:''}</div>
      <div class="meta">${esc(r.how)} · found on ${esc(r.found_on)} · ${esc(r.why_unreadable)}</div>
      <div class="fn"><span class="path">${esc(r.filename)}</span><button data-fn="${esc(r.filename)}" onclick="copy(this.dataset.fn,this)">Copy name</button></div></div>
      <div class="act"><a class="btn" href="${esc(r.url)}" target="_blank" rel="noopener">Open</a></div></div>`;
    }
  }
  document.getElementById('list').innerHTML=h||'<p class="note">Nothing here.</p>';
}
function exportCsv(){
  const q=s=>'"'+String(s).replace(/"/g,'""')+'"';
  const csv=['id,done,status,kind,filename,title,url'].concat(DATA.map(r=>[r.id,state[r.id]?'yes':'no',r.status,r.kind,q(r.filename),q(r.title),q(r.url)].join(','))).join('\n');
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='download-progress.csv';a.click();
}
function copyAll(){copy(DATA.filter(r=>r.status!=='skip').map(r=>r.filename).join('\n'),event.target)}
async function scan(){
  const msg=document.getElementById('scanmsg');
  if(!window.showDirectoryPicker){msg.textContent='Your browser cannot scan folders. Use Chrome, or tick rows by hand.';return}
  try{
    const dir=await showDirectoryPicker();const names=[];for await(const [n] of dir.entries())names.push(n.toLowerCase().replace(/\.[a-z0-9]+$/,''));
    let hit=0;for(const r of DATA){const stem=r.filename.toLowerCase().replace(/\.[a-z0-9]+$/,'');if(names.includes(stem)){if(!state[r.id])hit++;state[r.id]=true}}
    save();render();msg.textContent='Found '+hit+' new file(s) in the folder.';
  }catch(e){msg.textContent='Scan cancelled.'}
}
render();
</script></div></body></html>"""


def main() -> int:
    rows = build()
    FOLDER.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    html = HTML.replace("__DATA__", json.dumps(rows, ensure_ascii=False)).replace("__N__", str(len(rows))).replace("__FOLDER__", str(FOLDER))
    OUT_HTML.write_text(html, encoding="utf-8")
    todo = sum(1 for r in rows if r["status"] != "skip")
    print(f"{len(rows)} files ({todo} to download, {len(rows) - todo} marked skip) -> {OUT_HTML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
