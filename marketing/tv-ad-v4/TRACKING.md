# Nueva AI TV ads: QR tracking

Canva (editable): https://www.canva.com/d/H15ryz5rGxGQk7G  (design DAHbM2dZLyM, "Nueva AI TV Ads (nuevaai.org)")

Each slide has its own QR code, so the Insights dashboard (First visit tab, "Where visitors came from") shows which ad people scanned.

| Slide | Ad | QR points to | `src` tag |
|---|---|---|---|
| 1 | Skip the group chat (calendar) | https://nuevaai.org/?src=tv-calendar | tv-calendar |
| 2 | Never miss a home game (games) | https://nuevaai.org/?src=tv-games | tv-games |
| 3 | Text NuevaAI instead (lunch) | https://nuevaai.org/?src=tv-lunch | tv-lunch |
| 4 | Find your club | https://nuevaai.org/?src=tv-clubs | tv-clubs |
| 5 | Plan your classes faster | https://nuevaai.org/?src=tv-classes | tv-classes |

Notes
- The QR images in `qr/` are exact-module PNGs (48 px per module, 4-module quiet zone, error correction M) and SVGs, all 29x29 modules. Use the SVG for print.
- To change a link, regenerate the QR (see how in the repo history: qrcode library, same settings) and replace the QR image on that Canva page.
- The tag is read by lib/analytics.ts (`?src=` or `utm_source`), lowercased, [a-z0-9_-], max 24 characters.
