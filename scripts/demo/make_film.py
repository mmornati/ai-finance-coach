"""Posters for the screencasts and the narrated film of the landing page, from the clips shoot.py --video recorded.

    uv run --with playwright --with pillow --with kokoro-onnx --with phonemizer-fork --with soundfile \
        python scripts/demo/make_film.py --kokoro-dir DIR [--lang en,fr] [--tts kokoro|edge] [--posters-only]

Output in docs/assets/video/: <clip>.webp posters, and per language film.<lang>.mp4 (H.264 + AAC), film.<lang>.vtt (subtitles)
and film.<lang>.webp (poster). The title cards are HTML rendered by headless Chromium (the site's fonts and colours). Each scene
lasts as long as its picture or its narration, whichever is longer (a clip holds its last frame); the voice is loudness-normalised.

The voice-over, two free engines:
* `kokoro` (default): Kokoro-82M, an open-weight neural text-to-speech model (Apache-2.0), run locally with `kokoro-onnx`. Download
  kokoro-v1.0.onnx and voices-v1.0.bin from https://github.com/thewh1teagle/kokoro-onnx/releases (model-files-v1.0) into --kokoro-dir.
  It needs the espeak-ng library for the phonemes (macOS: `brew install espeak-ng`; Debian: `apt install espeak-ng`).
* `edge`: Microsoft Edge's online neural voices through the open-source `edge-tts` client (no account). The service refuses
  unofficial clients from time to time; only the narration text below (public copy, never data) would be sent.
Needs ffmpeg.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VID = ROOT / "docs" / "assets" / "video"
W, H, FPS = 1920, 1200, 30
LEAD_IN, TAIL = 0.35, 0.75                     # seconds of silence before / after the voice inside a scene

POSTER_AT = {"tour": 4, "ask-coach": 12, "transaction": 6, "subscriptions": 4}
VOICES = {                                      # (kokoro voice, kokoro lang, edge voice)
    "en": ("af_heart", "en-us", "en-US-AvaNeural"),
    "fr": ("ff_siwis", "fr-fr", "fr-FR-VivienneMultilingualNeural"),
}

# Each scene: a title card (kicker, title, subtitle) or a clip, a minimum length, and the narration per language.
SCENES = [
    {"card": {"en": ("AI FINANCE COACH", "Your household's money,<br>explained with your own numbers.", "Private. Self-hosted. Open source."),
              "fr": ("AI FINANCE COACH", "L'argent de votre foyer,<br>expliqué avec vos propres chiffres.", "Privé. Auto-hébergé. Open source.")},
     "min": 3.6,
     "say": {"en": "This is AI Finance Coach: a private coach for your household's money, running on your own machine.",
             "fr": "Voici AI Finance Coach : un coach privé pour l'argent de votre foyer, qui tourne sur votre propre machine."}},
    {"card": {"en": ("01 / THE PICTURE", "Everything in one place.", "Balances, a usual month, cash flow, a 90-day forecast, budgets and what is coming."),
              "fr": ("01 / LA VUE D'ENSEMBLE", "Tout au même endroit.", "Soldes, un mois habituel, flux, prévision à 90 jours, budgets et échéances.")},
     "min": 2.4,
     "say": {"en": "First, the big picture.", "fr": "D'abord, la vue d'ensemble."}},
    {"clip": "tour",
     "say": {"en": "Your bank accounts sync through Europe's open-banking rules, and every payment is sorted by your rules and by what you "
                   "taught it. The dashboard shows a usual month, your cash flow, a ninety-day forecast and your budgets. Subscriptions, "
                   "loans, net worth and the kids' pocket money each have their own page.",
             "fr": "Vos comptes se synchronisent grâce aux règles européennes d'open banking, et chaque paiement est classé selon vos règles "
                   "et ce que vous lui avez appris. Le tableau de bord montre un mois habituel, vos flux, une prévision à quatre-vingt-dix "
                   "jours et vos budgets. Abonnements, prêts, patrimoine et argent de poche des enfants ont chacun leur page."}},
    {"card": {"en": ("02 / ASK", "Ask in plain words.", "The coach calls read-only tools on redacted data. Every figure comes from code, every claim cites its evidence."),
              "fr": ("02 / DEMANDEZ", "Posez vos questions simplement.", "Le coach appelle des outils en lecture seule sur des données anonymisées. Chaque chiffre vient du code.")},
     "min": 2.4,
     "say": {"en": "Then, ask in plain words.", "fr": "Ensuite, posez vos questions simplement."}},
    {"clip": "ask-coach",
     "say": {"en": "The coach never reads your raw transactions. It calls read-only tools that compute the figures on redacted data, "
                   "and every claim links back to the payments behind it.",
             "fr": "Le coach ne lit jamais vos transactions brutes. Il appelle des outils en lecture seule qui calculent les chiffres "
                   "sur des données anonymisées, et chaque affirmation renvoie aux paiements concernés."}},
    {"card": {"en": ("03 / EXPLAIN", "Every label explained.", "Why this category, who it belongs to, and a preview before anything changes."),
              "fr": ("03 / EXPLIQUEZ", "Chaque catégorie expliquée.", "Pourquoi cette catégorie, à qui appartient le paiement, et un aperçu avant tout changement.")},
     "min": 2.4,
     "say": {"en": "Every label is explained.", "fr": "Chaque catégorie est expliquée."}},
    {"clip": "transaction",
     "say": {"en": "Open any payment to see why it has its category and who it belongs to. Change it, and you see a preview first.",
             "fr": "Ouvrez n'importe quel paiement pour voir pourquoi il a sa catégorie et à qui il appartient. Vous le modifiez ? "
                   "Un aperçu s'affiche d'abord."}},
    {"card": {"en": ("04 / SAVE", "Subscriptions under control.", "Price rises, overlaps, contracts, notice periods and cheaper offers, dated and sourced."),
              "fr": ("04 / ÉCONOMISEZ", "Abonnements sous contrôle.", "Hausses de prix, doublons, contrats, préavis et offres moins chères, datées et sourcées.")},
     "min": 2.4,
     "say": {"en": "Keep your subscriptions under control.", "fr": "Gardez vos abonnements sous contrôle."}},
    {"clip": "subscriptions",
     "say": {"en": "Price rises, overlapping services, notice periods and contracts, with cheaper offers that are dated and sourced. "
                   "The savings are computed by code.",
             "fr": "Hausses de prix, services en double, préavis et contrats, avec des offres moins chères, datées et sourcées. "
                   "Les économies sont calculées par le code."}},
    {"card": {"en": ("LOCAL FIRST", "Nothing leaves your machine<br>without a typed yes.", "github.com/mmornati/ai-finance-coach"),
              "fr": ("LOCAL D'ABORD", "Rien ne quitte votre machine<br>sans votre accord tapé.", "github.com/mmornati/ai-finance-coach")},
     "min": 3.6,
     "say": {"en": "Your data stays yours: nothing leaves your machine without a typed yes. AI Finance Coach is open source. Find it on GitHub.",
             "fr": "Vos données restent les vôtres : rien ne quitte votre machine sans votre accord. AI Finance Coach est open source. "
                   "Retrouvez-le sur GitHub."}},
]

CARD = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;background:#0b1220;overflow:hidden}}
body{{display:flex;flex-direction:column;justify-content:center;padding:0 190px;box-sizing:border-box;color:#e9eefb;
 font-family:ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif;
 background:radial-gradient(1400px 600px at 85% -10%,rgba(110,147,255,.32),transparent 60%),
            radial-gradient(800px 500px at -5% 115%,rgba(245,184,61,.16),transparent 60%),#0b1220}}
.k{{font-family:ui-monospace,"SF Mono",Menlo,monospace;letter-spacing:.22em;font-size:26px;color:#f5b83d;margin-bottom:34px}}
h1{{font-family:ui-serif,"New York",Georgia,serif;font-weight:600;font-size:88px;line-height:1.06;margin:0 0 36px;letter-spacing:-.015em}}
p{{font-size:36px;line-height:1.4;color:rgba(233,238,251,.76);max-width:1360px;margin:0}}
.rule{{width:120px;height:6px;border-radius:3px;background:linear-gradient(90deg,#f5b83d,#6e93ff);margin-bottom:40px}}
.mark{{position:absolute;right:120px;bottom:100px;width:84px;height:84px}}
</style></head><body><div class="k">{k}</div><div class="rule"></div><h1>{t}</h1><p>{s}</p>{mark}</body></html>"""


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


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


def render_cards(tmp: Path, lang: str) -> None:
    mark = (ROOT / "docs" / "assets" / "brand" / "mark.svg").read_text().replace("<svg ", '<svg class="mark" ', 1)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": W, "height": H})
        for i, sc in enumerate(SCENES):
            if "card" in sc:
                k, t, s = sc["card"][lang]
                page.set_content(CARD.format(lang=lang, w=W, h=H, k=k, t=t, s=s, mark=mark))
                page.screenshot(path=str(tmp / f"card{i}.png"))
        b.close()


async def _edge(text: str, voice: str, out: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice).save(str(out))


def _kokoro(kokoro_dir: Path):
    import ctypes.util

    from kokoro_onnx import EspeakConfig, Kokoro
    lib = ctypes.util.find_library("espeak-ng") or "/opt/homebrew/lib/libespeak-ng.dylib"
    data = next((str(p) for p in (Path(lib).resolve().parents[1] / "share" / "espeak-ng-data", Path("/usr/share/espeak-ng-data"),
                                  Path("/usr/lib/x86_64-linux-gnu/espeak-ng-data")) if p.exists()), None)
    return Kokoro(str(kokoro_dir / "kokoro-v1.0.onnx"), str(kokoro_dir / "voices-v1.0.bin"),
                  espeak_config=EspeakConfig(lib_path=lib, data_path=data))


def speak(lang: str, tmp: Path, engine: str, kokoro_dir: Path | None) -> list[Path]:
    """The narration of every scene, as WAV files (mono 48 kHz)."""
    k_voice, k_lang, e_voice = VOICES[lang]
    tts = _kokoro(kokoro_dir) if engine == "kokoro" else None
    out = []
    for i, sc in enumerate(SCENES):
        raw, wav = tmp / f"say{i}.raw.wav", tmp / f"say{i}.wav"
        if tts:
            import soundfile as sf
            audio, sr = tts.create(sc["say"][lang], voice=k_voice, speed=0.97, lang=k_lang)
            sf.write(str(raw), audio, sr)
        else:
            raw = raw.with_suffix(".mp3")
            asyncio.run(_edge(sc["say"][lang], e_voice, raw))
        run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", "48000", str(wav)])
        out.append(wav)
    return out


def vtt_time(t: float) -> str:
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def cues(text: str, start: float, length: float) -> list[tuple[float, float, str]]:
    """Split a narration into sentences and share its spoken time between them by length (subtitles)."""
    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", text) if p.strip()]
    total = sum(len(p) for p in parts)
    out, t = [], start
    for p in parts:
        d = length * len(p) / total
        out.append((t, t + d, p))
        t += d
    return out


def film(lang: str, engine: str, kokoro_dir: Path | None) -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        render_cards(tmp, lang)
        voices = speak(lang, tmp, engine, kokoro_dir)
        parts, subs, clock = [], [], 0.0
        for i, sc in enumerate(SCENES):
            said = duration(voices[i])
            seg = tmp / f"seg{i:02d}.mp4"
            if "card" in sc:
                secs = max(sc["min"], LEAD_IN + said + TAIL)
                video_in = ["-loop", "1", "-t", f"{secs:.3f}", "-i", str(tmp / f"card{i}.png")]
                vf = f"scale={W}:{H},fps={FPS}"
            else:
                src = VID / f"{sc['clip']}.mp4"
                clip = duration(src)
                secs = max(clip, LEAD_IN + said + TAIL)
                video_in = ["-i", str(src)]
                vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0b1220,fps={FPS},"
                      f"tpad=stop_mode=clone:stop_duration={max(0.0, secs - clip):.3f}")
            vf += f",fade=t=in:st=0:d=0.3,fade=t=out:st={secs - 0.35:.3f}:d=0.35,format=yuv420p"
            delay = int(LEAD_IN * 1000)
            run(["ffmpeg", "-v", "error", "-y", *video_in, "-i", str(voices[i]),
                 "-filter_complex", f"[0:v]{vf}[v];[1:a]adelay={delay}|{delay},apad,atrim=0:{secs:.3f},aformat=sample_rates=48000:channel_layouts=stereo[a]",
                 "-map", "[v]", "-map", "[a]", "-t", f"{secs:.3f}", "-c:v", "libx264", "-preset", "slow", "-crf", "22",
                 "-c:a", "pcm_s16le", str(seg)])
            subs += cues(sc["say"][lang], clock + LEAD_IN, said)
            clock += secs
            parts.append(seg)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        joined = tmp / "joined.mkv"
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
        target = VID / f"film.{lang}.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(joined), "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             "-ar", "48000", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target)])
    (VID / f"film.{lang}.vtt").write_text("WEBVTT\n\n" + "\n".join(
        f"{n}\n{vtt_time(a)} --> {vtt_time(b)}\n{t}\n" for n, (a, b, t) in enumerate(subs, 1)))
    still(VID / f"film.{lang}.mp4", 1.6, VID / f"film.{lang}.webp")
    meta = {"duration": round(duration(VID / f"film.{lang}.mp4"), 1), "engine": engine,
            "voice": VOICES[lang][0] if engine == "kokoro" else VOICES[lang][2]}
    print(f"  film.{lang}.mp4 / .vtt / .webp {json.dumps(meta)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en,fr")
    ap.add_argument("--tts", choices=("kokoro", "edge"), default="kokoro")
    ap.add_argument("--kokoro-dir", type=Path, help="folder holding kokoro-v1.0.onnx and voices-v1.0.bin")
    ap.add_argument("--posters-only", action="store_true")
    a = ap.parse_args()
    if a.tts == "kokoro" and not a.posters_only and not (a.kokoro_dir and (a.kokoro_dir / "kokoro-v1.0.onnx").exists()):
        ap.error("--kokoro-dir must hold kokoro-v1.0.onnx and voices-v1.0.bin (see the docstring)")
    posters()
    if not a.posters_only:
        for lang in a.lang.split(","):
            film(lang, a.tts, a.kokoro_dir)
