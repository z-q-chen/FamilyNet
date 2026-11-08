#!/usr/bin/env python3
"""Offline regression checks for FamilyNet's simple monitor runtime."""
import importlib.util
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path


def main(path):
    spec = importlib.util.spec_from_file_location("familynet_monitor_tested", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory:
        module.ROOT = Path(directory)
        module.DB = Path(directory) / "monitor.db"

        # Simulate an existing database created before explicit round IDs.
        with sqlite3.connect(module.DB) as db:
            db.execute(
                "CREATE TABLE pings (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts INTEGER NOT NULL,task_id INTEGER NOT NULL,latency REAL,"
                " ok INTEGER NOT NULL,loss REAL)"
            )
            for stamp, task in ((1000000, 39), (1000004, 40),
                                (1001800, 39), (1001803, 40)):
                db.execute(
                    "INSERT INTO pings(ts,task_id,latency,ok,loss) "
                    "VALUES(?,?,150,1,0)", (stamp, task)
                )
        db.close()
        store = module.Store()
        assert len(store.execute("SELECT id FROM probe_rounds")) == 2
        assert len(store.execute("SELECT id FROM pings WHERE round_id IS NULL")) == 0
        first_version = store.round_version

        targets = [{"id": i, "name": str(i), "target": "127.0.0.1:443"}
                   for i in range(1, 13)]
        for i in range(50):
            ts = 1100000 + i * 1800
            readings = [(ts, t["id"], 145.0 + i, 1, 0.0) for t in targets]
            store.ping_batch(readings)

        rounds = store.execute("SELECT id,ts FROM probe_rounds ORDER BY id")
        assert len(rounds) == 48, len(rounds)
        assert rounds[-1][0] == store.round_version
        assert store.round_version == first_version + 50
        assert len(store.execute("SELECT id FROM pings")) == 48 * len(targets)
        assert len(store.execute(
            "SELECT id FROM pings WHERE round_id < ?", (rounds[0][0],)
        )) == 0

        # A page read should neither sample the interface nor write to SQLite.
        monitor = object.__new__(module.Monitor)
        monitor.db = store
        monitor.cfg = {"targets": targets}
        monitor.live_lock = threading.Lock()
        monitor.cached_state = {"time": 123, "round_id": store.round_version}
        state_before = monitor.state()
        state_before["time"] = 999
        assert monitor.state()["time"] == 123
        result = monitor.pings()
        assert len(result["rounds"]) == 48
        assert len(result["targets"]) == len(targets)
        assert result["round_id"] == store.round_version
        assert all(len(t["history"]) == 48 for t in result["targets"])
        assert all(
            q["round_id"] == result["rounds"][i]["id"]
            for t in result["targets"] for i, q in enumerate(t["history"])
        )
        assert len(store.execute("SELECT id FROM probe_rounds")) == 48
        store.conn.close()

        # Reinitializing Store must keep persisted batches unchanged.
        reopened = module.Store()
        assert reopened.round_version == result["round_id"]
        assert len(reopened.execute("SELECT id FROM probe_rounds")) == 48
        reopened.conn.close()
    print("PASS: legacy migration, 48-round FIFO, shared alignment, read-only state, restart")


if __name__ == "__main__":
    main(sys.argv[1])
