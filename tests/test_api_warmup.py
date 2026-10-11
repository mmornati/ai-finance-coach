"""The background warm-up of the web app: the first page after a sync or an edit is served from a ready snapshot."""
import time

from apihelpers import ctx, world  # noqa: F401


def wait_for(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_warm_builds_what_the_first_page_needs(ctx):
    st = ctx.state
    st.warm()
    snap = st._snap
    assert snap is not None and {"recurring", "memory_check"} <= set(snap.memo)
    st.warm()
    assert st._snap is snap                                        # unchanged data: nothing rebuilt


def test_the_warmer_rebuilds_after_a_write(ctx):
    st = ctx.state
    st.start_warmer(interval=3600)
    assert wait_for(lambda: st._snap is not None and "memory_check" in st._snap.memo)
    old = st._snap
    st.touch()                                                     # what every write of the web app does
    assert wait_for(lambda: st._snap is not old and st._snap is not None and "memory_check" in st._snap.memo)
    assert ctx.get("/health").status_code == 200
