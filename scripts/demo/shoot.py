"""Screenshots and screencasts of the DEMO app for the documentation (Playwright, headless Chromium).

    scripts/demo/run_demo.sh /tmp/coach-demo &                          # the demo app on 127.0.0.1:8799
    uv run --with playwright --with pillow python scripts/demo/shoot.py /tmp/coach-demo --out docs/assets/screens [--only dashboard,coach] [--video]

Light and dark WebP screenshots of every page (desktop) and a few phone-sized ones; with --video, MP4 screencasts of the main
flows in docs/assets/video/ (scripts/demo/make_film.py then adds the posters and the film). Only ever run it against a demo home: it logs in with a one-time link
of that home.
"""
from __future__ import annotations

import argparse
import io
import base64
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = "http://127.0.0.1:8799"

# name, path, wait-for text (optional), action before the shot (optional), full page
PAGES = [
    ("dashboard", "/", None, None, True),
    ("transactions", "/transactions", None, None, False),
    ("transaction-panel", "/transactions", None, "open_tx", False),
    ("categories", "/categories", None, None, True),
    ("category-detail", "/categories/food.groceries", None, None, True),
    ("review", "/review", None, None, False),
    ("budgets", "/budgets", None, None, True),
    ("subscriptions", "/subscriptions", None, None, True),
    ("subscriptions-detected", "/subscriptions", None, "tab:Detected", True),
    ("wealth", "/wealth", None, None, True),
    ("rental", "/rental", None, None, True),
    ("calendar", "/calendar", None, None, True),
    ("insights", "/insights", None, None, True),
    ("alerts", "/alerts", None, None, True),
    ("memory", "/memory", None, None, True),
    ("setup", "/setup", None, None, True),
    ("household", "/household", None, None, True),
    ("kids", "/kids", None, None, True),
    ("who-pays", "/who-pays", None, None, True),
    ("connections", "/connections", None, None, True),
    ("gold", "/gold", None, None, True),
    ("usage", "/usage", None, None, True),
    ("coach", "/coach", None, None, False),
    ("coach-answer", "/coach", None, "ask:Why was July so expensive?", True),
    ("coach-subscriptions", "/coach", None, "ask:Which subscriptions should we review?", True),
    ("loan-detail", "/wealth", None, "click:Details", True),
    ("memory-proposals", "/memory", None, "click:Proposals", True),
    ("rental-tax", "/rental", None, "click:Tax year", True),
    ("rental-scheme", "/rental", None, "click:Scheme commitment", True),
]
PHONE = ["dashboard", "transactions", "coach", "kids"]


def save(page: Page, target: Path) -> None:
    """A screenshot as WebP (about a tenth of the PNG for the same sharpness): the docs keep dozens of them."""
    from PIL import Image
    png = page.screenshot()
    Image.open(io.BytesIO(png)).convert("RGB").save(target.with_suffix(".webp"), "WEBP", quality=82, method=6)


def login(page: Page, home: str, user: str | None = None) -> None:
    cmd = ["uv", "run", "python", str(HERE / "login_link.py"), home] + (["--user", user] if user else [])
    link = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    page.goto(link)
    page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
    settle(page)


def settle(page: Page) -> None:
    page.wait_for_load_state("load")
    try:
        page.wait_for_function("document.querySelector('main') && !/Loading/.test(document.querySelector('main').innerText) && "
                               "document.querySelectorAll('[aria-busy=true], .skeleton, .animate-pulse').length === 0", timeout=10000)
    except Exception:                                                     # noqa: BLE001
        pass
    page.wait_for_timeout(1500)                                           # data and chart animations


def act(page: Page, action: str | None) -> None:
    if not action:
        return
    if action == "open_tx":
        page.get_by_text("Petroline", exact=True).first.click()
    elif action.startswith(("tab:", "click:")):
        page.get_by_text(action.split(":", 1)[1], exact=False).first.click()
    elif action.startswith("ask:"):
        box = page.get_by_label("Your question")
        box.fill(action[4:])
        box.press("Enter")
        page.get_by_text("AI-generated", exact=False).first.wait_for(timeout=90000)
        page.wait_for_timeout(1200)
    settle(page)


def shoot(home: str, out: Path, only: set[str] | None, schemes: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in schemes:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme=scheme, locale="en-GB")
            page = ctx.new_page()
            login(page, home)
            for name, path, _, action, full in PAGES:
                if only and name not in only:
                    continue
                page.goto(BASE + path)
                settle(page)
                try:
                    act(page, action)
                except Exception as e:                                    # noqa: BLE001
                    print(f"  {name}: action failed ({e})", file=sys.stderr)
                f = out / f"{name}-{scheme}.webp"
                if full:                                                  # grow the window to the content (the sidebar follows)
                    h = page.evaluate("document.documentElement.scrollHeight")
                    page.set_viewport_size({"width": 1440, "height": max(900, min(h, 2600))})
                    page.wait_for_timeout(600)
                save(page, f)
                page.set_viewport_size({"width": 1440, "height": 900})
                print(f"  {f.name}")
            ctx.close()
        # phones (light), then the child's own view
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, color_scheme="light", locale="en-GB",
                                  is_mobile=True, has_touch=True)
        page = ctx.new_page()
        login(page, home)
        for name, path, _, action, _full in PAGES:
            if name in PHONE and (not only or name in only):
                page.goto(BASE + path)
                settle(page)
                save(page, out / f"{name}-phone.webp")
                print(f"  {name}-phone.webp")
        ctx.close()
        if not only or "kid-home" in only:
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, color_scheme="light", locale="en-GB",
                                      is_mobile=True, has_touch=True)
            page = ctx.new_page()
            login(page, home, user="mia")
            settle(page)
            save(page, out / "kid-home-phone.webp")
            print("  kid-home-phone.webp")
            ctx.close()
        browser.close()


# ------------------------------------------------------------------------------------------------------------ screencasts

def slow_scroll(page: Page, px: int, steps: int = 30, pause: float = 0.03) -> None:
    page.mouse.move(800, 450)
    for _ in range(steps):
        page.mouse.wheel(0, px / steps)
        page.wait_for_timeout(int(pause * 1000) + 15)


def record(home: str, out: Path, only: set[str] | None) -> None:
    """Each flow is captured with Chrome's own screencast (DevTools frames with their timestamps), then assembled by ffmpeg into an
    MP4 with the real timing. (Playwright's built-in video recorder gives blank frames with recent headless Chromium.)"""
    out.mkdir(parents=True, exist_ok=True)
    flows = {"tour": flow_tour, "ask-coach": flow_coach, "transaction": flow_transaction, "subscriptions": flow_subscriptions}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, fn in flows.items():
            if only and name not in only:
                continue
            ctx = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=1.5, color_scheme="dark", locale="en-GB")
            page = ctx.new_page()
            login(page, home)
            page.goto(BASE + "/")
            settle(page)
            frames: list[tuple[float, bytes]] = []
            cdp = ctx.new_cdp_session(page)

            def on_frame(params, cdp=cdp, frames=frames):
                frames.append((params["metadata"]["timestamp"], base64.b64decode(params["data"])))
                cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})

            cdp.on("Page.screencastFrame", on_frame)
            cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 88, "maxWidth": 1920, "maxHeight": 1200})
            fn(page)
            page.wait_for_timeout(1500)
            cdp.send("Page.stopScreencast")
            ctx.close()
            assemble(frames, out / f"{name}.mp4")
            print(f"  {name}.mp4 ({len(frames)} frames)")
        browser.close()


def assemble(frames: list[tuple[float, bytes]], target: Path) -> None:
    """Frames with timestamps -> a constant 30 fps H.264 MP4 (ffconcat with per-frame durations)."""
    tmp = target.with_suffix(".frames")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    lines = ["ffconcat version 1.0"]
    for i, (ts, data) in enumerate(frames):
        f = tmp / f"{i:05d}.jpg"
        f.write_bytes(data)
        dur = (frames[i + 1][0] - ts) if i + 1 < len(frames) else 1.0
        lines += [f"file '{f.name}'", f"duration {max(dur, 0.001):.4f}"]
    lines.append(f"file '{len(frames) - 1:05d}.jpg'")
    (tmp / "list.txt").write_text("\n".join(lines) + "\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-vf",
                    "fps=30,scale=1920:-2:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "24",
                    "-movflags", "+faststart", str(target)], check=True)
    shutil.rmtree(tmp)


def flow_tour(page: Page) -> None:
    page.wait_for_timeout(1000 * 1.5)
    slow_scroll(page, 900, 40)
    page.wait_for_timeout(1000 * 1)
    for path in ("/subscriptions", "/wealth", "/insights", "/kids"):
        page.goto(BASE + path)
        settle(page)
        page.wait_for_timeout(1000 * 1.2)
        slow_scroll(page, 600, 30)
        page.wait_for_timeout(1000 * 0.8)


def flow_coach(page: Page) -> None:
    page.goto(BASE + "/coach")
    settle(page)
    page.wait_for_timeout(800)
    box = page.get_by_label("Your question")
    box.click()
    box.type("Why was July so expensive?", delay=60)
    page.wait_for_timeout(400)
    page.keyboard.press("Enter")
    page.get_by_text("AI-generated", exact=False).first.wait_for(timeout=90000)
    page.wait_for_timeout(2500)
    try:
        page.get_by_text("How this was answered", exact=False).first.click()
        page.wait_for_timeout(2500)
    except Exception:                                                     # noqa: BLE001
        pass
    slow_scroll(page, 400, 25)
    page.wait_for_timeout(1500)


def flow_transaction(page: Page) -> None:
    page.goto(BASE + "/transactions")
    settle(page)
    page.wait_for_timeout(1000)
    search = page.get_by_placeholder("Search", exact=False).first
    search.click()
    search.type("trattoria", delay=90)
    page.wait_for_timeout(1800)
    page.get_by_text("Trattoria Bella", exact=True).first.click()
    page.wait_for_timeout(1800)
    for label in ("Why this category?", "Why this person?"):
        try:
            page.get_by_text(label, exact=False).first.click()
            page.wait_for_timeout(2200)
        except Exception:                                                 # noqa: BLE001
            pass
    page.wait_for_timeout(1500)


def flow_subscriptions(page: Page) -> None:
    page.goto(BASE + "/subscriptions")
    settle(page)
    page.wait_for_timeout(1500)
    slow_scroll(page, 1400, 70, 0.04)
    page.wait_for_timeout(1200)
    page.mouse.wheel(0, -3000)
    page.wait_for_timeout(600)
    page.get_by_text("Detected payments", exact=False).first.click()
    settle(page)
    slow_scroll(page, 900, 45, 0.04)
    page.wait_for_timeout(1500)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("home")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "assets" / "screens")
    ap.add_argument("--only", default="")
    ap.add_argument("--scheme", default="light,dark")
    ap.add_argument("--video", action="store_true")
    a = ap.parse_args()
    if not (Path(a.home) / ".coach-demo-home").exists():
        sys.exit("refusing: not a demo home")
    only = set(filter(None, a.only.split(","))) or None
    if a.video:
        record(a.home, a.out.parent / "video", only)
    else:
        shoot(a.home, a.out, only, a.scheme.split(","))
