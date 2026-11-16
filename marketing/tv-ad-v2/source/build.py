import pathlib
D = pathlib.Path(__file__).parent
QR = (D/"theirqr.svg").read_text()

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;700&family=Caveat+Brush&display=swap');
:root{--paper:#F5F1E8;--ink:#0B2145;--blue:#1B7CFF;--gray:#E4E4E9;--hi:#FFE14D;}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1920px;height:1080px;overflow:hidden;background:var(--paper)}
body{position:relative;color:var(--ink);font-family:'Inter',sans-serif}
.h{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;letter-spacing:-.035em;line-height:.95;isolation:isolate}
.mono{font-family:'JetBrains Mono',monospace;font-weight:500;letter-spacing:-.01em}
.brand{position:absolute;left:96px;top:70px;display:flex;align-items:center;gap:18px;font:800 52px/1 'Bricolage Grotesque';letter-spacing:-.04em}
.brand i{width:60px;height:60px;border-radius:19px;background:var(--blue);display:grid;place-items:center;color:#fff;font:800 36px/1 'Bricolage Grotesque';position:relative;font-style:normal}
.brand i:after{content:'';position:absolute;left:-3px;bottom:-3px;width:18px;height:18px;background:var(--blue);border-bottom-right-radius:18px;clip-path:polygon(0 0,100% 100%,0 100%)}
.brand b{color:var(--blue);font-weight:800}
.foot{position:absolute;left:96px;bottom:46px;font:500 25px/1 'JetBrains Mono';color:rgba(11,33,69,.62);letter-spacing:-.01em}
.thread{position:absolute;display:flex;flex-direction:column;gap:14px}
.sender{font:600 25px/1 'Inter';color:rgba(11,33,69,.5);margin:18px 0 -4px 30px}
.msg{position:relative;padding:22px 40px 24px;border-radius:48px;font:500 46px/1.15 'Inter';letter-spacing:-.02em;max-width:760px;width:fit-content}
.in{background:var(--gray);color:#16161a;align-self:flex-start}
.out{background:var(--blue);color:#fff;align-self:flex-end}
.in.t:before,.out.t:before{content:'';position:absolute;bottom:0;width:30px;height:34px}
.in.t:before{left:-9px;background:var(--gray);border-bottom-right-radius:26px}
.in.t:after{content:'';position:absolute;bottom:0;left:-24px;width:24px;height:34px;background:var(--paper);border-bottom-right-radius:20px}
.out.t:before{right:-9px;background:var(--blue);border-bottom-left-radius:26px}
.out.t:after{content:'';position:absolute;bottom:0;right:-24px;width:24px;height:34px;background:var(--paper);border-bottom-left-radius:20px}
.status{align-self:flex-end;font:600 24px/1 'Inter';color:rgba(11,33,69,.5);margin:-4px 14px 0 0}
.react{position:absolute;top:-22px;right:-26px;width:60px;height:60px;border-radius:50%;background:#fff;border:5px solid var(--paper);display:grid;place-items:center;font:800 30px/1 'Bricolage Grotesque';color:var(--blue);box-shadow:0 2px 0 rgba(0,0,0,.06)}
.typing{background:var(--gray);align-self:flex-start;border-radius:48px;padding:26px 36px;display:flex;gap:11px;position:relative}
.typing s{width:18px;height:18px;border-radius:50%;background:#9a9aa3;display:block}
mark{background:none;position:relative;white-space:nowrap}
mark:before{content:'';position:absolute;left:-.08em;right:-.08em;top:.18em;bottom:.02em;background:var(--hi);z-index:-1;transform:rotate(-1.2deg);border-radius:.08em}
.hl{position:relative;z-index:0;display:inline-block}
.pill{display:inline-block;padding:12px 26px;border-radius:999px;border:3px solid var(--ink);font:700 28px/1 'JetBrains Mono';letter-spacing:-.02em}
.file{position:absolute;width:330px;height:360px;background:#fff;border-radius:18px;box-shadow:0 14px 0 rgba(11,33,69,.08),0 0 0 3px var(--ink);padding:26px;clip-path:polygon(0 0,calc(100% - 70px) 0,100% 70px,100% 100%,0 100%)}
.file:after{content:'';position:absolute;right:0;top:0;width:70px;height:70px;background:var(--gray);border-left:3px solid var(--ink);border-bottom:3px solid var(--ink)}
.file .pdf{display:inline-block;background:#E5484D;color:#fff;font:700 24px/1 'JetBrains Mono';padding:8px 12px;border-radius:8px}
.file .nm{font:700 23px/1.3 'JetBrains Mono';margin-top:22px;overflow-wrap:break-word;letter-spacing:-.05em;padding-right:10px}
.lines span{display:block;height:14px;border-radius:7px;background:#e6e6ec;margin-top:16px}
"""

def page(body, n):
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"

BRAND = "<div class='brand'><i>N</i><span>Nueva<b>AI</b></span></div>"
DISC = "<div class='foot'>Student-made. Not officially affiliated with or endorsed by The Nueva School.</div>"

# 1 -- hook
s1 = BRAND + """
<h1 class='h' style='position:absolute;left:96px;top:290px;font-size:150px;width:1000px'>Your group chat is <mark>not</mark> a school database.</h1>
<div class='mono' style='position:absolute;left:100px;top:850px;font-size:30px;color:rgba(11,33,69,.7)'>11:47 PM. Every group chat. Every Sunday.</div>
<div class='thread' style='left:1130px;top:150px;width:720px;gap:12px'>
 <div class='sender' style='margin-top:0'>maya</div><div class='msg in t' style='font-size:42px'>wait is there school monday</div>
 <div class='sender'>sam</div><div class='msg in t' style='font-size:42px'>i thought we had monday off<span class='react'>?</span></div>
 <div class='sender'>jo</div><div class='msg in t' style='font-size:42px'>no that was last year</div>
 <div class='sender'>maya</div><div class='msg in t' style='font-size:42px'>does anyone have the calendar</div>
 <div class='sender'>sam</div><div class='msg in t' style='font-size:42px'>it's on nexus somewhere</div>
</div>""" + DISC

# 2 -- reply
s2 = BRAND + """
<h1 class='h' style='position:absolute;left:96px;top:250px;font-size:176px;width:1000px'>Text <span style='color:var(--blue)'>NuevAI</span> instead.</h1>
<div class='mono' style='position:absolute;left:100px;top:760px;font-size:31px;width:880px;line-height:1.35;color:rgba(11,33,69,.75)'>Answers from the school's own calendar, handbook, menus and schedules. With the sources right there.</div>
<div class='thread' style='left:1090px;top:200px;width:760px'>
 <div class='msg out t' style='align-self:flex-end'>is there school monday</div>
 <div class='status'>Delivered</div>
 <div class='msg in' style='margin-top:10px'>Checking the school calendar…</div>
 <div class='msg in t'>Yes. Sorry.</div>
 <div class='msg out t' style='margin-top:14px'>ugh. when's the next home game?</div>
 <div class='typing'><s></s><s></s><s></s></div>
</div>""" + DISC

# 3 -- PDF
files = [("menu_oct_FINAL.pdf",-6,96,560),("menu_oct_FINAL_v2.pdf",3,410,590),("menu_oct_FINAL_v3_use_this_one.pdf",-3,720,545)]
fh = "".join(f"<div class='file' style='left:{x}px;top:{y}px;transform:rotate({r}deg)'><span class='pdf'>PDF</span><div class='nm'>{n.replace('_','_<wbr>')}</div><div class='lines'><span style='width:90%'></span><span style='width:70%'></span><span style='width:80%'></span></div></div>" for n,r,x,y in files)
s3 = BRAND + """
<h1 class='h' style='position:absolute;left:96px;top:215px;font-size:158px;width:1740px;white-space:nowrap'>The lunch menu is a <mark>PDF.</mark></h1>
""" + fh + """
<div class='mono' style='position:absolute;left:100px;top:440px;font-size:34px;color:rgba(11,33,69,.75)'>We read them so you don't have to.</div>
<div class='thread' style='left:1130px;top:600px;width:690px'>
 <div class='msg out t' style='align-self:flex-end'>what's for lunch</div>
 <div class='msg in t'>Here's today's menu for your campus.</div>
</div>
""" + DISC

# 4 -- anaphora
s4 = BRAND + """
<div class='h' style='position:absolute;left:96px;top:300px;font-size:112px;line-height:1.06;width:1700px;white-space:nowrap'>
 It knows when the game is.<br>It knows what's for lunch.<br>It knows which clubs meet Thursday.<br><span class='hl'><mark style='color:var(--ink)'>It does not know your grades.</mark></span>
</div>
<div class='mono' style='position:absolute;left:100px;top:905px;font-size:32px;color:rgba(11,33,69,.7)'>No account. No logins. On purpose.</div>
<svg style='position:absolute;right:90px;top:70px;width:230px;height:230px;transform:rotate(14deg)' viewBox='0 0 200 200'><defs><path id='c' d='M100,100 m-72,0 a72,72 0 1,1 144,0 a72,72 0 1,1 -144,0'/></defs><circle cx='100' cy='100' r='96' fill='var(--blue)'/><text font-family='JetBrains Mono' font-weight='700' font-size='21' fill='#fff'><textPath href='#c' textLength='440' lengthAdjust='spacing'>STUDENT-MADE ✦ STUDENT-MADE ✦</textPath></text><text x='100' y='122' text-anchor='middle' font-family='Bricolage Grotesque' font-weight='800' font-size='72' fill='#fff'>N</text></svg>
""" + DISC

# 5 -- CTA
s5 = BRAND + """
<h1 class='h' style='position:absolute;left:96px;top:215px;font-size:176px;width:1020px'>Go on. Ask it something.</h1>
<div class='h' style='position:absolute;left:100px;top:700px;font-size:84px;letter-spacing:-.04em;color:var(--blue)'>nuev-ai.vercel.app</div>
<div style='position:absolute;left:100px;top:835px;display:flex;gap:18px'><span class='pill'>Free</span><span class='pill'>No account</span><span class='pill'>Made by students</span></div>
<div style='position:absolute;left:1190px;top:130px;width:620px;height:620px;background:#fff;border-radius:44px;padding:34px;transform:rotate(3deg);box-shadow:0 18px 0 rgba(11,33,69,.1),0 0 0 4px var(--ink)'><div style='width:100%;height:100%'>""" + QR.replace('<svg version="1.0"','<svg style="width:100%;height:100%" version="1.0"',1) + """</div></div>
<div style='position:absolute;left:1170px;top:820px;font:400 54px/1 "Caveat Brush";color:var(--ink);transform:rotate(-4deg)'>yes, this one. camera up.</div>
<svg style='position:absolute;left:1090px;top:660px;width:200px;height:170px' viewBox='0 0 200 170' fill='none' stroke='#0B2145' stroke-width='6' stroke-linecap='round'><path d='M150 150 C 60 150, 20 100, 40 30'/><path d='M14 56 L40 28 L66 54'/></svg>
""" + DISC.replace("left:96px","left:96px")

for i,b in enumerate([s1,s2,s3,s4,s5],1):
    (D/f"slide{i}.html").write_text(page(b,i))
print("ok")
