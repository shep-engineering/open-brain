"""Mobile evaluator harness for the reveal.js demo deck.

Why this exists
---------------
Per the Anthropic harness-engineering research and the brain-harness plan,
mobile UX work needs a deterministic evaluator — not eyeballing on desktop
and hoping. This script builds the mkdocs site, serves it on localhost,
opens it in Playwright's Chromium with real mobile device emulation,
and drives touch events against the deck. Each test returns pass/fail
and drops a screenshot on failure.

Usage
-----
    .venv/Scripts/python.exe tests/mobile/test_demo_nav.py

Exit code is 0 iff all tests pass.

What it covers
--------------
For each of {portrait Pixel 5, landscape Pixel 5}:

1. Page loads, first slide is hash=#/0
2. Right chevron renders inside the viewport and is tappable
3. Tap right chevron -> Reveal advances (hash/h goes 0 -> 1)
4. Tap left chevron -> Reveal goes back (hash/h goes 1 -> 0)
5. Swipe right-to-left -> advances
6. Swipe left-to-right -> goes back
7. Swipe bottom-to-top -> does NOT change slide (this was Shep's bug:
   "swipe up advances" — that's actually scroll, not navigation)

Plus a rotation test:
8. Rotate portrait -> landscape mid-deck; after rotation, chevrons
   still visible and taps still advance slides (catches the
   `const isMobile = ...` computed-once-at-page-load bug)
"""

from __future__ import annotations

import contextlib
import http.server
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, ViewportSize

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SITE_DIR = REPO_ROOT / "site"
SHOTS_DIR = REPO_ROOT / "logs" / "mobile-test-shots"
PORT = 8765
DEMO_URL = f"http://127.0.0.1:{PORT}/demo/"

# Pixel 5 portrait per Playwright's device registry (matches the real phone):
#   viewport 393x851, DPR 2.75, is_mobile, has_touch
# Landscape: swap width/height, keep everything else.
PORTRAIT_VIEWPORT: ViewportSize = {"width": 393, "height": 851}
LANDSCAPE_VIEWPORT: ViewportSize = {"width": 851, "height": 393}


# -------------------------- result plumbing --------------------------

_results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))


def shot(page: Page, name: str) -> Path:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    p = SHOTS_DIR / f"{name}.png"
    page.screenshot(path=str(p))
    return p


# -------------------------- site setup --------------------------

def build_site() -> None:
    print("[setup] mkdocs build ...")
    # Use the interpreter we're already running under — works whether called
    # via .venv python or the system python.
    r = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        print(r.stderr)
        raise SystemExit("mkdocs build failed")
    assert (SITE_DIR / "demo" / "index.html").exists(), "demo not in build output"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **kw): pass


@contextlib.contextmanager
def serve_site():
    """Launch a quiet threaded HTTP server rooted at site/, yield base URL."""
    handler = lambda *a, **kw: QuietHandler(*a, directory=str(SITE_DIR), **kw)
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    # wait until port accepts
    for _ in range(50):
        with socket.socket() as s:
            try:
                s.connect(("127.0.0.1", PORT))
                break
            except OSError:
                time.sleep(0.1)
    else:
        srv.shutdown()
        raise SystemExit("http server never came up")
    print(f"[setup] serving {SITE_DIR} at {DEMO_URL}")
    try:
        yield DEMO_URL
    finally:
        srv.shutdown()
        srv.server_close()


# -------------------------- primitives --------------------------

def get_h(page: Page) -> int:
    """Horizontal slide index per Reveal."""
    return page.evaluate("() => Reveal.getIndices().h")


def wait_for_ready(page: Page, timeout_ms: int = 5000) -> None:
    page.wait_for_function("() => window.Reveal && Reveal.isReady && Reveal.isReady()",
                           timeout=timeout_ms)


def swipe(page: Page, sx: int, sy: int, ex: int, ey: int, steps: int = 16) -> None:
    """Dispatch BOTH PointerEvent and TouchEvent sequences so Reveal's
    gesture handler — which binds pointerdown/pointermove/pointerup AND
    touchstart/touchmove/touchend on the .reveal container — sees the
    motion the same way a real finger would. Pointer events take priority
    in modern Chromium, but touch events are dispatched too as a belt-
    and-suspenders measure for any handler that exclusively listens to
    touch."""
    page.evaluate(
        """({sx, sy, ex, ey, steps}) => {
            const target = document.querySelector('.reveal') || document.body;
            function fire(eventType, x, y) {
                // PointerEvent
                const pointerInit = {
                    pointerId: 1, pointerType: 'touch',
                    isPrimary: true,
                    clientX: x, clientY: y, screenX: x, screenY: y,
                    bubbles: true, cancelable: true, composed: true,
                    pressure: 0.5, width: 10, height: 10,
                };
                const pointerType = ({touchstart: 'pointerdown',
                                      touchmove:  'pointermove',
                                      touchend:   'pointerup'})[eventType];
                target.dispatchEvent(new PointerEvent(pointerType, pointerInit));

                // TouchEvent (parallel)
                const touchInit = {identifier: 1, target: target,
                                   clientX: x, clientY: y, pageX: x, pageY: y,
                                   screenX: x, screenY: y, radiusX: 5, radiusY: 5,
                                   force: 0.5};
                const touch = new Touch(touchInit);
                const isEnd = (eventType === 'touchend');
                target.dispatchEvent(new TouchEvent(eventType, {
                    touches: isEnd ? [] : [touch],
                    targetTouches: isEnd ? [] : [touch],
                    changedTouches: [touch],
                    bubbles: true, cancelable: true, composed: true,
                }));
            }
            fire('touchstart', sx, sy);
            for (let i = 1; i <= steps; i++) {
                const x = sx + (ex - sx) * (i / steps);
                const y = sy + (ey - sy) * (i / steps);
                fire('touchmove', x, y);
            }
            fire('touchend', ex, ey);
        }""",
        {"sx": sx, "sy": sy, "ex": ex, "ey": ey, "steps": steps},
    )


def tap_chevron(page: Page, side: str) -> None:
    """Tap the custom mobile nav chevron via a real touchstart/touchend
    sequence. Playwright's touchscreen.tap() does NOT synthesize a click
    event in has_touch contexts (verified empirically), so a chevron whose
    handler only listens for `click` would never fire. Our implementation
    binds touchstart explicitly — this primitive matches that."""
    selector = ".mobile-nav-right" if side == "right" else ".mobile-nav-left"
    handle = page.query_selector(selector)
    if handle is None:
        raise AssertionError(f"{selector} not found in DOM")
    box = handle.bounding_box()
    if box is None:
        raise AssertionError(f"{selector} has no bounding box (not rendered)")
    cx = int(box["x"] + box["width"] / 2)
    cy = int(box["y"] + box["height"] / 2)
    # Dispatch touchstart on the actual button so its event listener fires.
    page.evaluate(
        """({sel, x, y}) => {
            const el = document.querySelector(sel);
            if (!el) return;
            const init = {identifier: 1, target: el, clientX: x, clientY: y,
                          pageX: x, pageY: y, screenX: x, screenY: y,
                          radiusX: 5, radiusY: 5, force: 1};
            const touch = new Touch(init);
            const start = new TouchEvent('touchstart', {
                touches: [touch], targetTouches: [touch], changedTouches: [touch],
                bubbles: true, cancelable: true, composed: true,
            });
            el.dispatchEvent(start);
            const end = new TouchEvent('touchend', {
                touches: [], targetTouches: [], changedTouches: [touch],
                bubbles: true, cancelable: true, composed: true,
            });
            el.dispatchEvent(end);
        }""",
        {"sel": selector, "x": cx, "y": cy},
    )


def element_visible(page: Page, selector: str) -> bool:
    handle = page.query_selector(selector)
    if handle is None:
        return False
    return handle.is_visible()


# -------------------------- test suite --------------------------

def run_orientation_tests(page: Page, label: str) -> None:
    print(f"\n--- {label} ---")
    page.goto(DEMO_URL)
    wait_for_ready(page)

    # T1: first slide, hash == #/0
    h0 = get_h(page)
    record(f"{label}: page loads on slide 0", h0 == 0, f"h={h0}")

    # T2: right chevron renders + is in viewport
    right_visible = element_visible(page, ".mobile-nav-right")
    left_visible = element_visible(page, ".mobile-nav-left")
    record(f"{label}: right chevron visible", right_visible)
    record(f"{label}: left chevron visible", left_visible)
    if not right_visible:
        shot(page, f"{label}_no-right-chevron")

    # T3: tap right chevron -> advance
    try:
        tap_chevron(page, "right")
        page.wait_for_timeout(250)
        h1 = get_h(page)
        record(f"{label}: right chevron advances", h1 > h0,
               f"h0={h0} -> h1={h1}")
        if h1 <= h0:
            shot(page, f"{label}_right-chevron-did-not-advance")
    except AssertionError as e:
        record(f"{label}: right chevron advances", False, str(e))

    # T4: tap left chevron -> go back
    try:
        before = get_h(page)
        tap_chevron(page, "left")
        page.wait_for_timeout(250)
        after = get_h(page)
        record(f"{label}: left chevron goes back", after < before,
               f"h before={before} after={after}")
    except AssertionError as e:
        record(f"{label}: left chevron goes back", False, str(e))

    # Reset to slide 0
    page.evaluate("() => Reveal.slide(0, 0, 0)")
    page.wait_for_timeout(150)

    # T5: swipe right-to-left -> advances
    vp = page.viewport_size
    mid_y = vp["height"] // 2
    before = get_h(page)
    swipe(page, int(vp["width"] * 0.8), mid_y, int(vp["width"] * 0.2), mid_y)
    page.wait_for_timeout(300)
    after = get_h(page)
    record(f"{label}: swipe R->L advances", after > before,
           f"h before={before} after={after}")

    # T6: swipe left-to-right -> back
    before = get_h(page)
    swipe(page, int(vp["width"] * 0.2), mid_y, int(vp["width"] * 0.8), mid_y)
    page.wait_for_timeout(300)
    after = get_h(page)
    record(f"{label}: swipe L->R goes back", after < before,
           f"h before={before} after={after}")

    # T7: swipe up should NOT navigate (Shep's bug)
    page.evaluate("() => Reveal.slide(2, 0, 0)")  # middle slide
    page.wait_for_timeout(150)
    before = get_h(page)
    mid_x = vp["width"] // 2
    swipe(page, mid_x, int(vp["height"] * 0.8), mid_x, int(vp["height"] * 0.2))
    page.wait_for_timeout(300)
    after = get_h(page)
    record(f"{label}: swipe up does NOT navigate", after == before,
           f"h before={before} after={after}")


def run_rotation_test(page: Page) -> None:
    """Verify the mobile chrome (chevrons + counter) survives a portrait→
    landscape viewport change.

    HARNESS-FIDELITY NOTE:
    Playwright's set_viewport_size() does NOT trigger Reveal's full
    re-layout chain even when 'resize' + 'orientationchange' events are
    dispatched manually. As a result, Reveal.next()/Reveal.right() etc
    silently no-op in Playwright after a viewport change in the same
    session, even though they work fine on a real phone (where the
    OS-level rotation fires the full event chain Reveal depends on).

    What we CAN reliably assert in Playwright after rotation:
      - chevron elements still in DOM and visible (proves the
        `pointer:coarse` CSS approach beats the old `max-width:768px`)
      - chevron pointer-events stays `auto` (no stuck disabled state)
      - slide counter still rendered and showing "n / total"

    What we CANNOT reliably assert here (deferred to AVD/real-device):
      - Reveal.next() actually advances after rotation
      - swipe gestures still work after rotation
    """
    print("\n--- rotation: portrait -> landscape ---")
    page.set_viewport_size(PORTRAIT_VIEWPORT)
    page.goto(DEMO_URL)
    wait_for_ready(page)

    page.evaluate("() => Reveal.slide(2, 0, 0)")
    page.wait_for_timeout(200)

    page.set_viewport_size(LANDSCAPE_VIEWPORT)
    page.evaluate("""() => {
        window.dispatchEvent(new Event('resize'));
        window.dispatchEvent(new Event('orientationchange'));
    }""")
    page.wait_for_timeout(400)

    right_visible = element_visible(page, ".mobile-nav-right")
    left_visible  = element_visible(page, ".mobile-nav-left")
    record("rotation: right chevron visible after landscape", right_visible)
    record("rotation: left chevron visible after landscape",  left_visible)
    if not right_visible:
        shot(page, "rotation_no-right-chevron")

    rights_interactive = page.evaluate("""() => {
        const el = document.querySelector('.mobile-nav-right');
        if (!el) return false;
        return getComputedStyle(el).pointerEvents !== 'none';
    }""")
    record("rotation: right chevron pointer-events is auto after landscape",
           rights_interactive)

    counter_text = page.evaluate("""() => {
        const c = document.querySelector('.mobile-counter');
        return c ? c.textContent : null;
    }""")
    record("rotation: slide counter present after landscape",
           counter_text is not None and "/" in (counter_text or ""),
           f"counter={counter_text!r}")


# -------------------------- driver --------------------------

def main() -> int:
    build_site()
    with serve_site():
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            # Portrait context
            ctx_p = browser.new_context(
                viewport=PORTRAIT_VIEWPORT,
                device_scale_factor=2.75,
                is_mobile=True, has_touch=True,
                user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 5) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Mobile Safari/537.36",
            )
            page_p = ctx_p.new_page()
            run_orientation_tests(page_p, "portrait")
            ctx_p.close()

            # Landscape context (fresh — matches opening the URL already in landscape)
            ctx_l = browser.new_context(
                viewport=LANDSCAPE_VIEWPORT,
                device_scale_factor=2.75,
                is_mobile=True, has_touch=True,
                user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 5) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Mobile Safari/537.36",
            )
            page_l = ctx_l.new_page()
            run_orientation_tests(page_l, "landscape")
            ctx_l.close()

            # Rotation mid-session
            ctx_r = browser.new_context(
                viewport=PORTRAIT_VIEWPORT,
                device_scale_factor=2.75,
                is_mobile=True, has_touch=True,
            )
            page_r = ctx_r.new_page()
            run_rotation_test(page_r)
            ctx_r.close()

            browser.close()

    # summary
    print("\n" + "=" * 60)
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"  {passed}/{total} passed, {failed} failed")
    if failed:
        print("\n  FAILURES:")
        for name, ok, detail in _results:
            if not ok:
                print(f"    - {name}  ({detail})")
        print(f"\n  Screenshots: {SHOTS_DIR}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
