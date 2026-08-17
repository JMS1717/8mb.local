from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(os.name == "nt", reason="fork is not available on Windows")
def test_hw_cache_lock_is_recreated_in_forked_child():
    """A child must be able to read the cache after a parent probe lock."""
    import worker.app.hw_detect as hw_detect

    hw_detect._HW_CACHE = {"probe_generation": "parent"}
    hw_detect._HW_CACHE_LOCK.acquire()
    try:
        pid = os.fork()
    except AttributeError:
        pytest.skip("fork is not available")

    if pid == 0:
        try:
            with hw_detect._HW_CACHE_LOCK:
                assert hw_detect._HW_CACHE["probe_generation"] == "parent"
            os._exit(0)
        except BaseException:
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    hw_detect._HW_CACHE_LOCK.release()
    assert os.waitstatus_to_exitcode(status) == 0
