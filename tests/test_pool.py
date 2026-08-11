"""Tests for the simulator pool.

Leasing is what stops two episodes sharing one CARLA server, which in
synchronous mode would interleave their world ticks and silently corrupt both
runs. It is worth testing that the lease is actually exclusive.
"""

from __future__ import annotations

import threading
import time

import pytest

from simulator.pool import NoSimulatorAvailable, SimulatorEndpoint, SimulatorPool


def pool_of(count: int) -> SimulatorPool:
    return SimulatorPool([
        SimulatorEndpoint(name=f"carla-{i}", host="127.0.0.1", port=2000 + 10 * i)
        for i in range(count)
    ])


class TestConstruction:
    def test_from_config(self):
        pool = SimulatorPool.from_config({"simulators": [
            {"name": "a", "host": "127.0.0.1", "port": 2000, "gpu": 0},
            {"name": "b", "host": "127.0.0.1", "port": 2010, "gpu": 1},
        ]})
        assert pool.size == 2
        assert [e.port for e in pool.endpoints] == [2000, 2010]
        assert pool.endpoints[1].gpu == 1

    def test_an_empty_pool_is_rejected(self):
        with pytest.raises(ValueError):
            SimulatorPool([])

    def test_the_shipped_config_loads(self):
        import yaml

        from simulator.config import CONFIG_DIR

        with (CONFIG_DIR / "simulator" / "simulators.yaml").open() as fh:
            pool = SimulatorPool.from_config(yaml.safe_load(fh))
        assert pool.size >= 1
        # Ports must be at least 3 apart: CARLA also binds port+1 and port+2.
        ports = sorted(e.port for e in pool.endpoints)
        assert all(b - a >= 3 for a, b in zip(ports, ports[1:]))


class TestLeasing:
    def test_a_lease_returns_an_endpoint(self):
        pool = pool_of(1)
        with pool.lease() as endpoint:
            assert endpoint.port == 2000

    def test_a_lease_is_returned_afterwards(self):
        pool = pool_of(1)
        with pool.lease():
            assert pool.available == 0
        assert pool.available == 1

    def test_a_lease_is_returned_even_when_the_episode_raises(self):
        pool = pool_of(1)
        with pytest.raises(RuntimeError):
            with pool.lease():
                raise RuntimeError("episode blew up")
        assert pool.available == 1

    def test_two_leases_get_different_servers(self):
        pool = pool_of(2)
        with pool.lease() as first, pool.lease() as second:
            assert first.port != second.port

    def test_a_busy_pool_makes_the_next_caller_wait(self):
        """The exclusivity that keeps two episodes off one server."""
        pool = pool_of(1)
        with pool.lease():
            with pytest.raises(NoSimulatorAvailable):
                with pool.lease(timeout_s=0.2):
                    pass

    def test_a_waiter_proceeds_once_a_server_is_freed(self):
        pool = pool_of(1)
        acquired = threading.Event()

        def waiter():
            with pool.lease(timeout_s=5.0):
                acquired.set()

        with pool.lease():
            thread = threading.Thread(target=waiter, daemon=True)
            thread.start()
            time.sleep(0.2)
            assert not acquired.is_set()   # still blocked, as it should be
        thread.join(timeout=5.0)
        assert acquired.is_set()

    def test_status_reports_which_are_busy(self):
        pool = pool_of(2)
        with pool.lease() as leased:
            status = {s["name"]: s["busy"] for s in pool.status()}
        assert status[leased.name] is True
        assert sum(status.values()) == 1

    def test_every_server_is_free_again_at_the_end(self):
        pool = pool_of(2)
        for _ in range(5):
            with pool.lease():
                pass
        assert pool.available == 2
