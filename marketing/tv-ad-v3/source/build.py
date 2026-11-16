import pathlib
D = pathlib.Path(__file__).parent
QR = (D/"theirqr.svg").read_text().replace('<svg version="1.0"','<svg style="width:100%;height:100%" version="1.0"',1)

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Inter+Tight:wght@500;600;700;800&family=Inter:wght@500;600&display=swap');
:root{--paper:#F4F1EB;--ink:#0E2145;--blue:#1B7CFF;--gray:#E3E4EA;}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1920px;height:1080px;overflow:hidden;background:var(--paper)}
body{position:relative;color:var(--ink);font-family:'Inter',sans-serif}
.brand{position:absolute;left:60px;top:46px;display:flex;align-items:center;gap:16px;font:800 56px/1 'Inter Tight';letter-spacing:-.04em}
.brand i{width:62px;height:62px;border-radius:20px;background:var(--blue);display:grid;place-items:center;color:#fff;font:800 38px/1 'Inter Tight';position:relative;font-style:normal;letter-spacing:0}
.brand i:after{content:'';position:absolute;left:-3px;bottom:-3px;width:20px;height:20px;background:var(--blue);clip-path:polygon(0 0,100% 100%,0 100%)}
.brand b{color:var(--blue);font-weight:800}
.head{position:absolute;left:58px;top:175px;font:800 158px/.88 'Inter Tight';letter-spacing:-.05em;white-space:nowrap}
.head .b{color:var(--blue)}
.sub{position:absolute;left:66px;top:660px;width:800px;font:600 34px/1.38 'Inter';color:rgba(14,33,69,.62);letter-spacing:-.01em}
.qr{position:absolute;width:540px;height:540px;background:#fff;border-radius:46px;padding:26px;box-shadow:0 14px 0 rgba(14,33,69,.09),0 0 0 4px var(--ink)}
.msg{position:absolute;width:fit-content;padding:22px 42px 24px;border-radius:52px;font:600 50px/1.12 'Inter Tight';letter-spacing:-.02em}
.out{background:var(--blue);color:#fff}
.in{background:var(--gray);color:#16161a;border-radius:52px}
.out:before{content:'';position:absolute;bottom:0;right:-9px;width:30px;height:34px;background:var(--blue);border-bottom-left-radius:26px}
.out:after{content:'';position:absolute;bottom:0;right:-24px;width:24px;height:34px;background:var(--paper);border-bottom-left-radius:20px}
.in:before{content:'';position:absolute;bottom:0;left:-9px;width:30px;height:34px;background:var(--gray);border-bottom-right-radius:26px}
.in:after{content:'';position:absolute;bottom:0;left:-24px;width:24px;height:34px;background:var(--paper);border-bottom-right-radius:20px}
.url{position:absolute;left:0;width:1920px;text-align:center;top:852px;font:800 148px/1 'Inter Tight';letter-spacing:-.05em;color:var(--blue)}
.url span{border-bottom:12px solid var(--blue);padding-bottom:0;display:inline-block;line-height:.92}
.foot{position:absolute;left:60px;bottom:26px;font:500 25px/1 'Inter';color:rgba(14,33,69,.55)}
.sticker{position:absolute;display:grid;place-items:center;width:210px;height:210px;border-radius:50%;background:var(--ink);box-shadow:0 10px 0 rgba(14,33,69,.12)}
.sticker svg{width:112px;height:112px;stroke:#fff;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.tag{position:absolute;font:700 30px/1 'Inter Tight';letter-spacing:.01em;color:var(--ink);background:#fff;border:4px solid var(--ink);padding:14px 26px;border-radius:999px}
"""

ICONS = {
 'cal':"<rect x='3' y='5' width='18' height='16' rx='2.5'/><path d='M3 10h18M8 3v4M16 3v4M8.5 15l2.2 2.2L15.5 13'/>",
 'trophy':"<path d='M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3'/>",
 'club':"<circle cx='9' cy='8' r='3'/><path d='M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6'/><circle cx='17' cy='9' r='2.5'/><path d='M16 14.2c2.9.3 5 2.6 5 5.8'/>",
 'book':"<path d='M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5zM8 7h7M8 11h5'/>",
}

def slide(head, sub, q, a, icon, tag, qr_rot=5, qr_left=800, flip=False):
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>
<div class='brand'><i>N</i><span>Nueva<b>AI</b></span></div>
<div class='head'>{head}</div>
<div class='sub'>{sub}</div>
<div class='qr' style='left:{qr_left}px;top:46px;transform:rotate({qr_rot}deg)'>{QR}</div>
<div class='sticker' style='left:1560px;top:70px;transform:rotate(-8deg)'><svg viewBox='0 0 24 24'>{ICONS[icon]}</svg></div>
<div class='tag' style='left:1560px;top:310px;transform:rotate(-3deg)'>{tag}</div>
<div class='msg out' style='right:90px;top:575px'>{q}</div>
<div class='msg in' style='right:70px;top:700px;max-width:830px'>{a}</div>
<div class='url'><span>nuev-ai.vercel.app</span></div>
<div class='foot'>Student-made. Not officially affiliated with or endorsed by The Nueva School.</div>
</body></html>"""

S = [
 slide("Skip<br>the group<br><span class='b'>chat.</span>", "Is there school Monday? Dates, no-school days and bell schedules, straight from the school calendar.", "is there school monday", "Here's what the calendar says for Monday.", 'cal', "Calendar", 5, 930),
 slide("Never miss<br>a home<br><span class='b'>game.</span>", "Game times, places and opponents for every team, pulled from the athletics schedule.", "when's the next home game", "Here's the next game, with time and place.", 'trophy', "Games", -5, 930),
 slide("Find<br>your<br><span class='b'>club.</span>", "Which clubs meet which day, what they do, and where to show up.", "what clubs meet on thursday", "Here are Thursday's clubs and where they meet.", 'club', "Clubs", 6, 930),
 slide("Plan your<br>classes<br><span class='b'>faster.</span>", "Courses, teachers and handbook answers, with the source linked every time.", "who teaches chemistry", "Here's who teaches it, plus course details.", 'book', "Classes", -4, 930),
]
for i,h in enumerate(S,1): (D/f"slide{i}.html").write_text(h)
print('ok')
