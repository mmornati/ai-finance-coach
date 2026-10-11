"""The per-snapshot cache of the web app: one lock per key, so a slow result never holds up the others."""
import threading
import time
from types import SimpleNamespace

from coach.api.state import Snapshot


def snap():
    return Snapshot(SimpleNamespace(member_view=lambda m: SimpleNamespace(member=m)), ("tok",))


def test_a_slow_key_does_not_block_another_key():
    s = snap()
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "slow"

    t = threading.Thread(target=lambda: s.once("memory_check", slow))
    t.start()
    assert started.wait(5)
    t0 = time.perf_counter()
    assert s.once("insights", lambda: "fast") == "fast"           # answered while the slow key is still computing
    assert time.perf_counter() - t0 < 1
    release.set()
    t.join(5)
    assert s.once("memory_check", lambda: "again") == "slow"


def test_concurrent_requests_for_one_key_compute_it_once():
    s = snap()
    calls = []

    def compute():
        calls.append(1)
        time.sleep(0.1)
        return 42

    out = []
    threads = [threading.Thread(target=lambda: out.append(s.once("k", compute))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert out == [42] * 8 and len(calls) == 1


def test_nested_keys_and_member_views():
    s = snap()
    assert s.once("outer", lambda: s.once("inner", lambda: 1) + 1) == 2
    assert s.memo["inner"] == 1
    v = s.view("adult-1")
    assert v is s.view("adult-1") and v.ds.member == "adult-1" and v.token == ("tok", "adult-1")


def test_a_failed_computation_is_not_cached():
    s = snap()

    def boom():
        raise RuntimeError("x")

    try:
        s.once("k", boom)
    except RuntimeError:
        pass
    assert s.once("k", lambda: "ok") == "ok"
