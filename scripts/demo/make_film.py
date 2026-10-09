"""Posters for the screencasts and the short film of the landing page, from the clips shoot.py --video recorded.

    uv run --with playwright --with pillow python scripts/demo/make_film.py            # docs/assets/video/{*.webp, film.mp4, film.webp}

The title cards are HTML rendered by headless Chromium (same fonts and colours as the site), each clip is normalised to
1920x1200 at 30 fps with short fades, and everything is joined into one H.264 MP4. Needs ffmpeg.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VID = ROOT / "docs" / "assets" / "video"
W, H, FPS = 1920, 1200, 30

POSTER_AT = {"tour": 4, "ask-coach": 12, "transaction": 6, "subscriptions": 4}

# (kind, value, seconds): a card is (kicker, title, subtitle)
FILM = [
    ("card", ("AI FINANCE COACH", "Your household's money,<br>explained with your own numbers.", "Private. Self-hosted. Open source."), 3.6),
    ("card", ("01 / THE PICTURE", "Everything in one place.", "Balances, a usual month, cash flow, a 90-day forecast, budgets and what is coming."), 2.6),
    ("clip", "tour", None),
    ("card", ("02 / ASK", "Ask in plain words.", "The coach calls read-only tools on redacted data. Every figure comes from code, every claim cites its evidence."), 3.0),
    ("clip", "ask-coach", None),
    ("card", ("03 / EXPLAIN", "Every label explained.", "Why this category, who it belongs to, and a preview before anything changes."), 2.6),
    ("clip", "transaction", None),
    ("card", ("04 / SAVE", "Subscriptions under control.", "Price rises, overlaps, contracts, notice periods and cheaper offers, dated and sourced."), 2.8),
    ("clip", "subscriptions", None),
    ("card", ("LOCAL FIRST", "Nothing leaves your machine<br>without a typed yes.", "github.com/mmornati/ai-finance-coach"), 3.6),
]

CARD = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;background:#0b1220;overflow:hidden}}
body{{display:flex;flex-direction:column;justify-content:center;padding:0 190px;box-sizing:border-box;color:#e9eefb;
 font-family:ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif;
 background:radial-gradient(1400px 600px at 85% -10%,rgba(110,147,255,.32),transparent 60%),
            radial-gradient(800px 500px at -5% 115%,rgba(245,184,61,.16),transparent 60%),#0b1220}}
.k{{font-family:ui-monospace,"SF Mono",Menlo,monospace;letter-spacing:.22em;font-size:26px;color:#f5b83d;margin-bottom:34px}}
h1{{font-family:ui-serif,"New York",Georgia,serif;font-weight:600;font-size:96px;line-height:1.05;margin:0 0 36px;letter-spacing:-.015em}}
p{{font-size:36px;line-height:1.4;color:rgba(233,238,251,.72);max-width:1300px;margin:0}}
.rule{{width:120px;height:6px;border-radius:3px;background:linear-gradient(90deg,#f5b83d,#6e93ff);margin-bottom:40px}}
.mark{{position:absolute;right:120px;bottom:100px;width:84px;height:84px}}
</style></head><body><div class="k">{k}</div><div class="rule"></div><h1>{t}</h1><p>{s}</p>{mark}</body></html>"""


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def still(src: Path, t: float, dst: Path) -> None:
    """One frame of a video as a 1280 px WebP (ffmpeg extracts, Pillow encodes: not every ffmpeg has libwebp)."""
    from PIL import Image
    with tempfile.TemporaryDirectory() as d:
        png = Path(d) / "f.png"
        run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(src), "-frames:v", "1", "-vf", "scale=1280:-2", str(png)])
        Image.open(png).convert("RGB").save(dst, "WEBP", quality=80, method=6)


def posters() -> None:
    for name, t in POSTER_AT.items():
        src = VID / f"{name}.mp4"
        if src.exists():
            still(src, t, VID / f"{name}.webp")
            print(f"  {name}.webp")


def render_cards(tmp: Path) -> list[Path]:
    mark = (ROOT / "docs" / "assets" / "brand" / "mark.svg").read_text().replace("<svg ", '<svg class="mark" ', 1)
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": W, "height": H})
        for i, (kind, val, _) in enumerate(FILM):
            if kind != "card":
                continue
            k, t, s = val
            page.set_content(CARD.format(w=W, h=H, k=k, t=t, s=s, mark=mark))
            f = tmp / f"card{i}.png"
            page.screenshot(path=str(f))
            out.append(f)
        b.close()
    return out


def film() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        render_cards(tmp)
        parts = []
        for i, (kind, val, secs) in enumerate(FILM):
            seg = tmp / f"seg{i:02d}.mp4"
            if kind == "card":
                fade = f"fade=t=in:st=0:d=0.35,fade=t=out:st={secs - 0.35}:d=0.35"
                run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", str(secs), "-i", str(tmp / f"card{i}.png"), "-vf",
                     f"scale={W}:{H},{fade},format=yuv420p", "-r", str(FPS), "-c:v", "libx264", "-preset", "slow", "-crf", "20", str(seg)])
            else:
                src = VID / f"{val}.mp4"
                dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                                           capture_output=True, text=True, check=True).stdout)
                fade = f"fade=t=in:st=0:d=0.3,fade=t=out:st={dur - 0.35:.2f}:d=0.35"
                run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                     f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0b1220,fps={FPS},{fade},format=yuv420p",
                     "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "24", str(seg)])
            parts.append(seg)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart",
             str(VID / "film.mp4")])
    still(VID / "film.mp4", 1.6, VID / "film.webp")
    print("  film.mp4, film.webp")


if __name__ == "__main__":
    posters()
    if "--posters-only" not in sys.argv:
        film()
