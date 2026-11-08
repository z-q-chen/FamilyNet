#!/usr/bin/env python3
"""FamilyNet Monitor: local-only system metrics, traffic history and TCP checks."""
import argparse
import base64
import binascii
import calendar
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = Path("/var/lib/familynet-monitor")
CONFIG = Path("/etc/familynet-monitor/config.json")
HTML = Path("/opt/familynet-monitor/index.html")
DB = ROOT / "monitor.db"
DEFAULT_TARGETS = [
    (27, "江苏·联通 IPv4", "59.83.208.215:443"),
    (28, "江苏·移动 IPv4", "36.155.213.76:443"),
    (30, "江苏·联通 IPv6", "[2408:873c:6010:5:8000:0:b00:77]:443"),
    (31, "江苏·移动 IPv6", "[2409:8c20:6ed1:10d:8000:0:b00:79]:443"),
    (32, "河北·联通 IPv4", "110.249.198.54:443"),
    (33, "河北·移动 IPv4", "111.62.113.2:443"),
    (34, "河北·电信 IPv6", "[240e:b1:c802:0:8000:0:b00:99]:443"),
    (35, "河北·联通 IPv6", "[2408:871a:4800:40f::b00:98]:443"),
    (36, "河北·移动 IPv6", "[2409:8c04:1007:a008:8000:0:b00:90]:443"),
    (38, "河北·电信 IPv4", "27.128.147.93:443"),
    (39, "江苏·电信 IPv4", "61.147.236.2:443"),
    (40, "江苏·电信 IPv6", "[240e:978:1503::202]:443"),
]
STOP = threading.Event()


def default_iface():
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 3 and fields[1] == "00000000" and int(fields[3], 16) & 1:
                return fields[0]
    except OSError:
        pass
    names = [x for x in os.listdir("/sys/class/net") if x != "lo"]
    return names[0] if names else "eth0"


def make_config(password):
    import secrets
    salt = secrets.token_hex(16)
    result = {
        "title": "洛杉矶 VPS 监控", "interface": default_iface(),
        "quota_gib": 400, "quota_mode": "sum",
        "reset_day": 0, "price_usd": 11.70, "price_cycle_months": 3,
        "expires_at": "", "refresh_seconds": 5, "sample_seconds": 30,
        "username": "admin", "password_salt": salt,
        "password_hash": hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), 140000
        ).hex(),
        "targets": [{"id": i, "name": n, "target": t, "interval": 1800}
                    for i, n, t in DEFAULT_TARGETS],
    }
    old = Path("/var/lib/komari/komari.db")
    if old.is_file():
        try:
            c = sqlite3.connect("file:" + str(old) + "?mode=ro", uri=True)
            data = c.execute("SELECT id,name,type,target,interval FROM ping_tasks").fetchall()
            if len(data) == 12 and all(row[2] == "tcp" for row in data):
                result["targets"] = [
                    {"id": int(i), "name": n, "target": t, "interval": int(iv)}
                    for i, n, _, t, iv in sorted(data)
                ]
            try:
                c.row_factory = sqlite3.Row
                # Upstream Komari stores monitored node metadata in clients.
                # Keep a legacy fallback for older or customized installations.
                table_names = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                table = "clients" if "clients" in table_names else ("nodes" if "nodes" in table_names else None)
                node = c.execute("SELECT * FROM " + table + " LIMIT 1").fetchone() if table else None
                if node:
                    fields = dict(node)
                    quota = fields.get("traffic_limit")
                    if quota is not None and float(quota) > 0:
                        result["quota_gib"] = round(float(quota) / 1024**3, 4)
                    mode = fields.get("traffic_limit_type")
                    if mode in ("sum", "up", "down", "max", "min"):
                        result["quota_mode"] = mode
                    price = fields.get("price")
                    if price is not None and float(price) > 0:
                        result["price_usd"] = float(price)
                    cycle = fields.get("billing_cycle")
                    if cycle is not None and 27 <= int(cycle) <= 370:
                        result["price_cycle_months"] = max(1, round(int(cycle) / 30))
                    expiry = fields.get("expired_at") or fields.get("expires_at")
                    if expiry:
                        if isinstance(expiry, (int, float)):
                            stamp = float(expiry)
                            if stamp > 1e11:
                                stamp /= 1000
                            result["expires_at"] = datetime.fromtimestamp(stamp).isoformat()
                        else:
                            result["expires_at"] = str(expiry)
                    day = fields.get("traffic_reset_day") or fields.get("traffic_limit_reset_day")
                    if day is not None and 1 <= int(day) <= 31:
                        result["reset_day"] = int(day)
            except (sqlite3.Error, ValueError, TypeError, OverflowError):
                pass
            c.close()
        except (sqlite3.Error, ValueError):
            pass
    return result


def read_iface(iface):
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, _, values = line.partition(":")
            if name.strip() == iface:
                p = values.split()
                return int(p[0]), int(p[8])
    except (OSError, ValueError, IndexError):
        pass
    raise RuntimeError("找不到网络接口 " + iface)


def cpu_ticks():
    values = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    n = list(map(int, values))
    idle = n[3] + (n[4] if len(n) > 4 else 0)
    return sum(n), idle


def memory():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, val = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(val.strip().split()[0]) * 1024
    return values["MemTotal"], values["MemTotal"] - values["MemAvailable"]


def reset_start(day, now=None):
    if not day:
        return None
    now = now or datetime.now()
    y, m = now.year, now.month
    curr = min(day, calendar.monthrange(y, m)[1])
    if now.day < curr:
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
        curr = min(day, calendar.monthrange(y, m)[1])
    return int(datetime(y, m, curr).timestamp())


def next_month_date(source, day):
    year = source.year + (1 if source.month == 12 else 0)
    month = 1 if source.month == 12 else source.month + 1
    return source.replace(
        year=year, month=month,
        day=min(day, calendar.monthrange(year, month)[1])
    )


def monthly_start_utc(now, reset_day, first_reset_at):
    """Return current UTC billing boundary, or None before the first reset."""
    first = datetime.fromtimestamp(first_reset_at, timezone.utc)
    if now < first_reset_at:
        return None, first_reset_at
    day = max(1, min(31, int(reset_day)))
    cursor = first
    while next_month_date(cursor, day).timestamp() <= now:
        cursor = next_month_date(cursor, day)
    return int(cursor.timestamp()), int(next_month_date(cursor, day).timestamp())


def ensure_billing_schedule(cfg):
    """Persist a monthly first reset based on the current boot date.

    This VPS's first billing month uses its live network-interface counters.
    Later months use the continuously sampled persistent monthly counter.
    """
    if int(cfg.get("first_reset_at") or 0) > 0 and int(cfg.get("reset_day") or 0) > 0:
        return cfg
    boot_seconds = float(Path("/proc/uptime").read_text().split()[0])
    boot_time = datetime.fromtimestamp(time.time() - boot_seconds, timezone.utc)
    day = int(cfg.get("reset_day") or 0)
    if not 1 <= day <= 31:
        day = boot_time.day
    first = next_month_date(boot_time, day)
    current = time.time()
    while first.timestamp() <= current:
        first = next_month_date(first, day)
    new_cfg = dict(cfg)
    new_cfg["reset_day"] = day
    new_cfg["first_reset_at"] = int(first.timestamp())
    target = CONFIG.with_suffix(".tmp")
    target.write_text(json.dumps(new_cfg, ensure_ascii=False, indent=2) + "\n")
    os.chmod(target, 0o600)
    os.replace(target, CONFIG)
    return new_cfg


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


class Store:
    def __init__(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(DB), check_same_thread=False, timeout=15)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        with self.lock, self.conn:
            self.conn.executescript("""
              CREATE TABLE IF NOT EXISTS traffic(
                ts INTEGER PRIMARY KEY, iface TEXT NOT NULL,
                rx INTEGER NOT NULL, tx INTEGER NOT NULL,
                drx INTEGER NOT NULL, dtx INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS pings(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
                task_id INTEGER NOT NULL, latency REAL, ok INTEGER NOT NULL,
                loss REAL);
              CREATE INDEX IF NOT EXISTS pings_task_time ON pings(task_id,ts);
              CREATE TABLE IF NOT EXISTS flows(
                id TEXT PRIMARY KEY, first_ts INTEGER NOT NULL,
                last_ts INTEGER NOT NULL, protocol TEXT NOT NULL,
                src TEXT NOT NULL, sport TEXT NOT NULL,
                dst TEXT NOT NULL, dport TEXT NOT NULL,
                original_bytes INTEGER NOT NULL, reply_bytes INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS flows_time ON flows(last_ts);
              CREATE TABLE IF NOT EXISTS flow_usage(
                bucket INTEGER NOT NULL, dst TEXT NOT NULL,
                dport TEXT NOT NULL, protocol TEXT NOT NULL,
                up INTEGER NOT NULL, down INTEGER NOT NULL,
                PRIMARY KEY(bucket,dst,dport,protocol)
              );
              CREATE INDEX IF NOT EXISTS flow_usage_time ON flow_usage(bucket);
              CREATE TABLE IF NOT EXISTS dest_names(
                ip TEXT PRIMARY KEY, name TEXT NOT NULL,
                resolved_at INTEGER NOT NULL
              );
              CREATE TABLE IF NOT EXISTS monthly_counter (
                id INTEGER PRIMARY KEY CHECK (id=1),
                cycle_start INTEGER NOT NULL,
                boot_id TEXT NOT NULL,
                last_rx INTEGER NOT NULL,
                last_tx INTEGER NOT NULL,
                sum_rx INTEGER NOT NULL,
                sum_tx INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
              );
            """)
            ping_cols = {col[1] for col in self.conn.execute("PRAGMA table_info(pings)")}
            if "loss" not in ping_cols:
                self.conn.execute("ALTER TABLE pings ADD COLUMN loss REAL")
            if "round_id" not in ping_cols:
                self.conn.execute("ALTER TABLE pings ADD COLUMN round_id INTEGER")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS pings_round_task ON pings(round_id,task_id)"
            )
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS probe_rounds("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL)"
            )
            # One-time migration of old samples, preserving the original history.
            # Only legacy records need timestamp grouping; normal operation uses IDs.
            legacy = self.conn.execute(
                "SELECT id,ts FROM pings WHERE round_id IS NULL ORDER BY ts,id"
            ).fetchall()
            previous = None
            current_round = None
            updates = []
            for sample_id, ts in legacy:
                if previous is None or ts - previous > 600:
                    current_round = self.conn.execute(
                        "INSERT INTO probe_rounds(ts) VALUES(?)", (ts,)
                    ).lastrowid
                    previous = ts
                updates.append((current_round, sample_id))
            if updates:
                self.conn.executemany(
                    "UPDATE pings SET round_id=? WHERE id=?", updates
                )
            self._trim_probe_rounds()
            last_round = self.conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM probe_rounds"
            ).fetchone()
            self.round_version = last_round[0]

    def _trim_probe_rounds(self):
        """Expire by FIFO position, not the wall clock."""
        row = self.conn.execute(
            "SELECT id FROM probe_rounds ORDER BY id DESC LIMIT 1 OFFSET 47"
        ).fetchone()
        if row:
            keep_from = row[0]
            self.conn.execute(
                "DELETE FROM pings WHERE round_id < ?", (keep_from,)
            )
            self.conn.execute(
                "DELETE FROM probe_rounds WHERE id < ?", (keep_from,)
            )

    def monthly_count(self, cycle_start, rx, tx, current_boot, now):
        """Accumulate post-reset bytes, surviving monitor restarts and reboots."""
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT cycle_start,boot_id,last_rx,last_tx,sum_rx,sum_tx "
                "FROM monthly_counter WHERE id=1"
            ).fetchone()
            if row is None or row[0] != cycle_start:
                self.conn.execute(
                    "INSERT OR REPLACE INTO monthly_counter VALUES(1,?,?,?,?,?,?,?)",
                    (cycle_start, current_boot, rx, tx, 0, 0, int(now))
                )
                return 0, 0
            _, prev_boot, last_rx, last_tx, sum_rx, sum_tx = row
            if prev_boot != current_boot:
                drx, dtx = rx, tx
            else:
                # Ignore out-of-order reads instead of introducing a jump.
                drx, dtx = max(0, rx - last_rx), max(0, tx - last_tx)
                rx, tx = max(last_rx, rx), max(last_tx, tx)
            total_rx, total_tx = sum_rx + drx, sum_tx + dtx
            if drx or dtx or prev_boot != current_boot:
                self.conn.execute(
                    "UPDATE monthly_counter SET boot_id=?,last_rx=?,last_tx=?,"
                    "sum_rx=?,sum_tx=?,updated_at=? WHERE id=1",
                    (current_boot, rx, tx, total_rx, total_tx, int(now))
                )
            return total_rx, total_tx

    def execute(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def sample(self, iface, now=None):
        now = int(now or time.time())
        rx, tx = read_iface(iface)
        with self.lock, self.conn:
            old = self.conn.execute(
                "SELECT ts,iface,rx,tx FROM traffic ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            drx = max(0, rx - old[2]) if old and old[1] == iface else 0
            dtx = max(0, tx - old[3]) if old and old[1] == iface else 0
            self.conn.execute(
                "INSERT OR REPLACE INTO traffic VALUES(?,?,?,?,?,?)",
                (now, iface, rx, tx, drx, dtx)
            )

    def ping(self, task, latency, ok, loss=None):
        """Legacy helper; all new monitor samples use complete batches."""
        self.ping_batch([(int(time.time()), task, latency, 1 if ok else 0, loss)])

    def ping_batch(self, readings):
        """Atomically append one probe round and keep only the latest 48."""
        if not readings:
            return
        round_ts = int(readings[0][0])
        with self.lock, self.conn:
            round_id = self.conn.execute(
                "INSERT INTO probe_rounds(ts) VALUES(?)", (round_ts,)
            ).lastrowid
            self.conn.executemany(
                "INSERT INTO pings(ts,task_id,latency,ok,loss,round_id) "
                "VALUES(?,?,?,?,?,?)",
                [(*row, round_id) for row in readings]
            )
            self._trim_probe_rounds()
            self.round_version = round_id

    def cleanup(self):
        with self.lock, self.conn:
            # Probe samples are a persistent 48-round queue, maintained
            # atomically in ping_batch(). No time-based cleanup here.
            self.conn.execute("DELETE FROM flows WHERE last_ts<?",
                              (int(time.time()) - 86400,))
            self.conn.execute("DELETE FROM flow_usage WHERE bucket<?",
                              (int(time.time()) - 31 * 86400,))
            # Retain original deltas; discarding intermediate deltas would
            # undercount totals when querying a time range.
            self.conn.execute("DELETE FROM traffic WHERE ts<?",
                              (int(time.time()) - 120 * 86400,))

    def save_flows(self, rows, source):
        now = int(time.time())
        bucket = (now // 60) * 60
        aggregated = {}
        with self.lock, self.conn:
            for x in rows:
                fid = x["id"]
                prior = self.conn.execute(
                    "SELECT original_bytes,reply_bytes FROM flows WHERE id=?",
                    (fid,)
                ).fetchone()
                new_up, new_down = max(0, int(x["up"])), max(0, int(x["down"]))
                # New sessions begin at zero; retained snapshots survive restarts.
                du = max(0, new_up - prior[0]) if prior else new_up
                dd = max(0, new_down - prior[1]) if prior else new_down
                # A reused connection tuple has reset its counters.
                if prior and new_up < prior[0]:
                    du = new_up
                if prior and new_down < prior[1]:
                    dd = new_down
                self.conn.execute("""
                    INSERT INTO flows VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                      last_ts=excluded.last_ts,
                      original_bytes=excluded.original_bytes,
                      reply_bytes=excluded.reply_bytes
                """, (fid, now, now, x["proto"], x["src"], x["sport"],
                      x["dst"], x["dport"], new_up, new_down))
                if not du and not dd:
                    continue
                key = (bucket, x["dst"], x["dport"], x["proto"])
                current = aggregated.setdefault(key, [0, 0])
                current[0] += du
                current[1] += dd
            if aggregated:
                self.conn.executemany("""
                    INSERT INTO flow_usage(bucket,dst,dport,protocol,up,down)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(bucket,dst,dport,protocol) DO UPDATE SET
                      up=flow_usage.up+excluded.up,
                      down=flow_usage.down+excluded.down
                """, [(*k, v[0], v[1]) for k, v in aggregated.items()])
        self.last_flow_source = source
        self.last_flow_count = len(rows)
        self.last_flow_at = now



def ping_target(task):
    """Five TCP connect probes; failures approximate loss, not ICMP loss."""
    raw = task["target"]
    if raw.startswith("["):
        host, port = raw[1:].split("]:", 1)
    else:
        host, port = raw.rsplit(":", 1)
    latencies = []
    failures = 0
    try:
        destination_port = int(port)
    except ValueError:
        return None, False, 100.0
    for _ in range(5):
        start = time.monotonic()
        try:
            with socket.create_connection((host, destination_port), timeout=2.0):
                pass
            latencies.append((time.monotonic() - start) * 1000)
        except OSError:
            failures += 1
        if STOP.is_set():
            break
    attempts = len(latencies) + failures
    loss = round(100 * failures / attempts, 1) if attempts else 100.0
    latency = round(sum(latencies) / len(latencies), 2) if latencies else None
    return latency, bool(latencies), loss


def ping_loop(store, tasks):
    """Run all targets in one 30-minute batch, storing the same timestamp."""
    if not tasks:
        return
    interval = max(max(60, int(t["interval"])) for t in tasks)
    pool = ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="fn-ping")
    try:
        while not STOP.is_set():
            now = int(time.time())
            latest = store.execute(
                "SELECT MAX(ts) FROM pings WHERE task_id IN (" +
                ",".join("?" for _ in tasks) + ")",
                tuple(t["id"] for t in tasks)
            )[0][0]
            if latest is not None and now < latest + interval:
                STOP.wait(min(30, latest + interval - now))
                continue
            round_ts = now
            futures = [pool.submit(ping_target, task) for task in tasks]
            readings = []
            for task, future in zip(tasks, futures):
                try:
                    latency, ok, loss = future.result()
                except Exception:
                    latency, ok, loss = None, False, 100.0
                readings.append((round_ts, task["id"], latency,
                                 1 if ok else 0, loss))
            if STOP.is_set():
                break
            store.ping_batch(readings)
            STOP.wait(30)
    finally:
        pool.shutdown(wait=False)


CT_TUPLE = re.compile(r"(?:src|dst|sport|dport|bytes)=[^\s]+")
CT_ID = re.compile(r"\bid=(\d+)\b")


def known_local_ips():
    """Get local host interface addresses for distinguishing egress flows."""
    try:
        proc = subprocess.run(["ip", "-j", "address"], capture_output=True,
                              text=True, timeout=3, check=False)
        if proc.returncode == 0:
            info = json.loads(proc.stdout)
            return {
                entry["local"] for nic in info for entry in nic.get("addr_info", [])
                if entry.get("local")
            }
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return set()


def public_ip(value):
    try:
        return ipaddress.ip_address(value.split("%", 1)[0]).is_global
    except ValueError:
        return False


def capture_conntrack(local_ips):
    if not shutil.which("conntrack"):
        return []
    try:
        proc = subprocess.run(
            ["conntrack", "-L", "-o", "extended"],
            capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode not in (0, 1):
        return []
    rows = []
    for line in proc.stdout.splitlines()[:10000]:
        if "bytes=" not in line:
            continue
        fields = re.findall(r"\b(src|dst|sport|dport|bytes)=([^\s]+)", line)
        values = {}
        for key, value in fields:
            values.setdefault(key, []).append(value)
        if any(len(values.get(key, [])) < 2 for key in ("src", "dst", "bytes")):
            continue
        src, dst = values["src"][0], values["dst"][0]
        if src not in local_ips or not public_ip(dst):
            continue
        sport = values.get("sport", [""])[0]
        dport = values.get("dport", [""])[0]
        proto = line.split()[2]
        try:
            sent, received = int(values["bytes"][0]), int(values["bytes"][1])
        except (ValueError, OverflowError):
            continue
        match = CT_ID.search(line)
        token = "|".join(("ct", proto, src, sport, dst, dport,
                          match.group(1) if match else ""))
        rows.append({
            "id": hashlib.sha256(token.encode()).hexdigest()[:30],
            "proto": proto, "src": src, "sport": sport,
            "dst": dst, "dport": dport,
            "up": sent, "down": received
        })
    return rows


def split_endpoint(value):
    text = value.strip().strip("[]")
    if text.startswith("[") and "]:" in text:
        host, port = text[1:].rsplit("]:", 1)
    elif ":" in text:
        host, port = text.rsplit(":", 1)
    else:
        return "", ""
    return host.strip("[]"), port


def capture_socket_bytes(local_ips):
    """Fallback: Linux TCP_INFO bytes from ss, only when conntrack is empty."""
    if not shutil.which("ss"):
        return []
    try:
        proc = subprocess.run(["ss", "-Htin"], capture_output=True,
                              text=True, timeout=7, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    header = None
    info = []
    def flush():
        if not header:
            return
        parts = header.split()
        if len(parts) < 5:
            return
        src, sport = split_endpoint(parts[3])
        dst, dport = split_endpoint(parts[4])
        if not dst or src not in local_ips or not public_ip(dst):
            return
        # Outbound TCP sockets normally use ephemeral source ports.
        try:
            if int(sport) < 10000:
                return
        except ValueError:
            return
        details = " ".join(info)
        sent = re.search(r"\bbytes_sent:(\d+)", details)
        received = re.search(r"\bbytes_received:(\d+)", details)
        if not sent and not received:
            return
        token = "|".join(("ss", "tcp", src, sport, dst, dport))
        rows.append({
            "id": hashlib.sha256(token.encode()).hexdigest()[:30],
            "proto": "tcp", "src": src, "sport": sport,
            "dst": dst, "dport": dport,
            "up": int(sent.group(1)) if sent else 0,
            "down": int(received.group(1)) if received else 0
        })
    for line in proc.stdout.splitlines():
        if line[:1].isspace():
            info.append(line)
        else:
            flush()
            header, info = line, []
    flush()
    return rows


def capture_flows():
    local_ips = known_local_ips()
    if not local_ips:
        return [], "no-local-ip"
    rows = capture_conntrack(local_ips)
    if rows:
        return rows, "conntrack"
    rows = capture_socket_bytes(local_ips)
    if rows:
        return rows, "tcp-info"
    return [], "no-byte-counters"



def collector_loop(store, cfg):
    """Slow disk/connection capture; independent from dashboard refresh."""
    last_clean = 0
    while not STOP.is_set():
        started = time.monotonic()
        try:
            store.sample(cfg["interface"])
            records, source = capture_flows()
            store.save_flows(records, source)
            if time.time() - last_clean > 3600:
                store.cleanup()
                last_clean = time.time()
        except Exception as exc:
            print("collector:", exc, flush=True)
        STOP.wait(max(1, int(cfg.get("sample_seconds", 30)) - (time.monotonic() - started)))


def state_loop(app):
    """One shared live sample every 3 seconds; HTTP requests only read it."""
    while not STOP.is_set():
        started = time.monotonic()
        try:
            app.sample_state()
        except Exception as exc:
            print("state:", type(exc).__name__, str(exc), flush=True)
        STOP.wait(max(0.25, 3 - (time.monotonic() - started)))


# PTR names and well-established provider ranges are only identification hints.
SERVICE_HINTS = (
    ("googlevideo.com", "YouTube"),
    ("youtube.com", "YouTube"),
    ("ytimg.com", "YouTube"),
    ("ggpht.com", "Google"),
    ("1e100.net", "Google 系列"),
    ("google.com", "Google"),
    ("googleusercontent.com", "Google"),
    ("gstatic.com", "Google"),
    ("chatgpt.com", "ChatGPT / OpenAI"),
    ("openai.com", "ChatGPT / OpenAI"),
    ("oaistatic.com", "ChatGPT / OpenAI"),
    ("oaiusercontent.com", "ChatGPT / OpenAI"),
    ("github.com", "GitHub"),
    ("githubusercontent.com", "GitHub"),
    ("githubassets.com", "GitHub"),
    ("huggingface.co", "Hugging Face"),
    ("hf.co", "Hugging Face"),
    ("discord.com", "Discord"),
    ("discordapp.net", "Discord"),
    ("telegram.org", "Telegram"),
    ("t.me", "Telegram"),
    ("whatsapp.net", "WhatsApp"),
    ("facebook.com", "Meta"),
    ("fbcdn.net", "Meta"),
    ("instagram.com", "Instagram"),
    ("tiktokcdn.com", "TikTok"),
    ("tiktok.com", "TikTok"),
    ("byteoversea.com", "字节系"),
    ("microsoft.com", "Microsoft"),
    ("microsoftonline.com", "Microsoft"),
    ("office.com", "Microsoft 365"),
    ("azureedge.net", "Azure / CDN"),
    ("bing.com", "Bing"),
    ("apple.com", "Apple"),
    ("mzstatic.com", "Apple"),
    ("icloud.com", "iCloud"),
    ("nflxvideo.net", "Netflix"),
    ("netflix.com", "Netflix"),
    ("twitch.tv", "Twitch"),
    ("steamcontent.com", "Steam"),
    ("steampowered.com", "Steam"),
    ("cloudflare.com", "Cloudflare / CDN"),
    ("cloudflare.net", "Cloudflare / CDN"),
    ("cloudfront.net", "AWS CloudFront"),
    ("amazonaws.com", "AWS"),
    ("amazon.com", "Amazon"),
    ("fastly.net", "Fastly CDN"),
    ("akamaiedge.net", "Akamai CDN"),
    ("akamai.net", "Akamai CDN"),
    ("edgekey.net", "Akamai CDN"),
)
IP_HINTS = (
    ("142.250.0.0/15", "Google 系列"),
    ("172.217.0.0/16", "Google 系列"),
    ("216.58.0.0/16", "Google 系列"),
    ("74.125.0.0/16", "Google 系列"),
    ("140.82.112.0/20", "GitHub"),
    ("185.199.108.0/22", "GitHub"),
    ("17.0.0.0/8", "Apple"),
    ("104.16.0.0/13", "Cloudflare / CDN"),
    ("172.64.0.0/13", "Cloudflare / CDN"),
)
IP_NETWORKS = tuple((ipaddress.ip_network(a), b) for a, b in IP_HINTS)


def reverse_name(ip):
    if not shutil.which("getent"):
        return ""
    try:
        proc = subprocess.run(
            ["getent", "hosts", ip], capture_output=True,
            text=True, timeout=1.0, check=False
        )
        for line in proc.stdout.splitlines():
            fields = line.split()
            if len(fields) > 1 and fields[0] == ip:
                return fields[1].rstrip(".").lower()[:160]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def guess_service(ip, ptr):
    host = (ptr or "").lower().strip(".")
    for suffix, name in SERVICE_HINTS:
        if host == suffix or host.endswith("." + suffix):
            return name, "推测"
    try:
        addr = ipaddress.ip_address(ip)
        for network, name in IP_NETWORKS:
            if addr in network:
                return name, "推测"
    except ValueError:
        pass
    if host and host != ip:
        return host[:54], "反向域名"
    return "未识别 · " + ip, "未知"



def komari_rpc(method, params=None):
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "common:" + method, "params": params or {}
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:25774/api/rpc2", data=payload,
        method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=7) as response:
        obj = json.load(response)
    if obj.get("error"):
        raise RuntimeError(str(obj["error"].get("message", "Komari RPC error")))
    return obj.get("result")


def agent_month_reset_day():
    for item in Path("/proc").iterdir():
        if not item.name.isdecimal():
            continue
        try:
            args = [x.decode(errors="replace") for x in
                    (item / "cmdline").read_bytes().split(b"\x00") if x]
        except (OSError, PermissionError):
            continue
        if not args or "komari-agent" not in Path(args[0]).name:
            continue
        for i, arg in enumerate(args):
            raw = None
            if arg == "--month-rotate" and i + 1 < len(args):
                raw = args[i + 1]
            elif arg.startswith("--month-rotate="):
                raw = arg.split("=", 1)[1]
            if raw is not None:
                try:
                    value = int(raw)
                    if 1 <= value <= 31:
                        return value
                except ValueError:
                    pass
    return 0


def migrate_komari(app):
    """Carry forward probe traffic and existing 24h ping history once.

    Cumulative probe totals may be reset-aware/monthly as configured in the
    old agent; retain its observed value and add only newly sampled deltas.
    """
    cfg = app.cfg
    if cfg.get("baseline_at"):
        print("已有探针累计流量基线，不重复导入")
        return
    try:
        result = komari_rpc("getNodesLatestStatus")
        if not isinstance(result, dict) or len(result) != 1:
            raise ValueError("Komari 必须恰好有一台在线服务器供自动迁移")
        status = next(iter(result.values()))
        if not isinstance(status, dict):
            raise ValueError("Komari 状态结构不匹配")
        base_up = int(status.get("net_total_up", -1))
        base_down = int(status.get("net_total_down", -1))
        if base_up < 0 or base_down < 0 or base_up + base_down == 0:
            raise ValueError("没有有效的 Komari 累计流量")
        source = "Komari Agent"
    except Exception as e:
        # The old RPC can be inaccessible or stale. The current physical
        # interface counters from Linux are still better than resetting to 0.
        base_down, base_up = read_iface(cfg["interface"])
        if base_up + base_down == 0:
            raise RuntimeError("没有可迁移的流量基线") from e
        source = "Linux interface / since boot"
        print("Komari 读取失败，采用 Linux 网卡已有流量:", str(e)[:160])
    now = int(time.time())
    cfg = dict(cfg)
    cfg["baseline_at"] = now
    cfg["baseline_up"] = base_up
    cfg["baseline_down"] = base_down
    cfg["baseline_source"] = source
    if source == "Komari Agent" and not int(cfg.get("reset_day") or 0):
        rotate = agent_month_reset_day()
        if rotate:
            cfg["reset_day"] = rotate
    # Atomically persist before disabling the old probe.
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG)
    app.cfg.update(cfg)
    print("流量基线: 上传", base_up, "下载", base_down, "来源", source)

    # Optional: preserve the colored, per-task historical ping bars.
    try:
        records = komari_rpc("getRecords", {
            "type": "ping", "hours": 24, "task_id": -1, "maxCount": -1
        })
        if not isinstance(records, dict):
            return
        allowed = {int(task["id"]) for task in cfg["targets"]}
        rows = []
        for x in records.get("records", [])[:5000]:
            task = int(x["task_id"])
            if task not in allowed:
                continue
            raw = x.get("time")
            stamp = int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp())
            if stamp < now - 86400 or stamp > now + 60:
                continue
            value = float(x["value"])
            rows.append((stamp, task, value if value >= 0 else None, 1 if value >= 0 else 0))
        with app.db.lock, app.db.conn:
            for stamp, task, latency, ok in rows:
                found = app.db.conn.execute(
                    "SELECT 1 FROM pings WHERE task_id=? AND ts=? LIMIT 1", (task, stamp)
                ).fetchone()
                if not found:
                    app.db.conn.execute(
                        "INSERT INTO pings(ts,task_id,latency,ok) VALUES(?,?,?,?)",
                        (stamp, task, latency, ok)
                    )
        print("已导入 Ping 历史:", len(rows), "条")
    except Exception as e:
        print("旧 Ping 历史暂未导入:", str(e)[:150])


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db = Store()
        self.last_ticks = None
        self.last_live = None
        self.live_lock = threading.Lock()
        self.cached_state = {"time": 0, "round_id": self.db.round_version}
        self.db.last_flow_source = "starting"
        self.db.last_flow_count = 0
        self.db.last_flow_at = None

    def sample_state(self):
        now = time.time()
        total, idle = cpu_ticks()
        rx, tx = read_iface(self.cfg["interface"])
        with self.live_lock:
            cpu = None
            if self.last_ticks:
                dtotal = total - self.last_ticks[0]
                didle = idle - self.last_ticks[1]
                if dtotal > 0:
                    cpu = round(100 * (dtotal - didle) / dtotal, 2)
            self.last_ticks = (total, idle)
            rxps = txps = 0
            if self.last_live:
                elapsed = now - self.last_live[0]
                if elapsed > 0.2:
                    rxps = max(0, rx - self.last_live[1]) / elapsed
                    txps = max(0, tx - self.last_live[2]) / elapsed
            self.last_live = (now, rx, tx)
        mem_total, mem_used = memory()
        disk = shutil.disk_usage("/")
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        # The interface counters seed the first billing period. They are not
        # exposed as standalone lifetime totals on the public dashboard.
        first_sample_row = self.db.execute("SELECT MIN(ts) FROM traffic")[0]
        # First month: the kernel's since-boot bytes. Later months: a
        # persisted billing counter starting at each configured reset time.
        first_reset = int(self.cfg.get("first_reset_at") or 0)
        reset_day = int(self.cfg.get("reset_day") or 0)
        if first_reset > 0 and reset_day > 0:
            cycle_start, next_reset = monthly_start_utc(now, reset_day, first_reset)
        else:
            cycle_start, next_reset = None, None
        if cycle_start is None:
            cycle_down, cycle_up = int(rx), int(tx)
        else:
            cycle_down, cycle_up = self.db.monthly_count(
                cycle_start, int(rx), int(tx), boot_id(), now
            )
        mode = self.cfg.get("quota_mode", "sum")
        used = {"up": cycle_up, "down": cycle_down,
                "max": max(cycle_up, cycle_down),
                "sum": cycle_up + cycle_down,
                "min": min(cycle_up, cycle_down)}.get(mode, cycle_up + cycle_down)
        snapshot = {
            "time": int(now), "hostname": socket.gethostname(),
            "interface": self.cfg["interface"],
            "uptime": uptime, "cpu": cpu, "cpu_cores": os.cpu_count() or 0,
            "memory_total": mem_total, "memory_used": mem_used,
            "disk_total": disk.total, "disk_used": disk.used,
            "rxps": rxps, "txps": txps, "used": used,
            "quota": float(self.cfg["quota_gib"]) * 1024**3,
            "quota_mode": mode, "first_sample": first_sample_row[0],
            "billing_source": "first_boot_then_monthly",
            "cycle_up": cycle_up, "cycle_down": cycle_down,
            "cycle_start": cycle_start, "next_reset_at": next_reset,
            "reset_day": self.cfg.get("reset_day", 0),
            "price_usd": self.cfg.get("price_usd"),
            "price_cycle_months": self.cfg.get("price_cycle_months"),
            "expires_at": self.cfg.get("expires_at", ""),
            "title": self.cfg.get("title", "洛杉矶 VPS 监控"),
            "refresh_seconds": self.cfg.get("refresh_seconds", 5),
            "load": [round(n, 2) for n in os.getloadavg()],
            "round_id": self.db.round_version,
        }
        with self.live_lock:
            self.cached_state = snapshot
        return snapshot

    def state(self):
        with self.live_lock:
            return dict(self.cached_state)

    def history(self, hours):
        start = int(time.time()) - hours * 3600
        step = 300 if hours <= 24 else 3600
        if hours > 24 * 14:
            step = 86400
        data = self.db.execute("""
            SELECT (ts/?)*? AS bucket,
                   SUM(dtx),SUM(drx)
            FROM traffic WHERE ts>=?
            GROUP BY bucket ORDER BY bucket
        """, (step, step, start))
        return [{"ts": ts, "up": up, "down": down} for ts, up, down in data]

    def pings(self):
        """Return explicit batch IDs, never reconstruct rounds from timestamps."""
        rounds = self.db.execute(
            "SELECT id,ts FROM probe_rounds ORDER BY id DESC LIMIT 48"
        )[::-1]
        rows = self.db.execute(
            "SELECT round_id,task_id,ts,latency,ok,loss FROM pings "
            "WHERE round_id >= ? ORDER BY round_id,id",
            (rounds[0][0],) if rounds else (0,)
        ) if rounds else []
        by_id = {}
        for rid, task, ts, latency, ok, loss in rows:
            by_id.setdefault(str(task), []).append(
                {"round_id": rid, "ts": ts, "latency": latency,
                 "ok": bool(ok),
                 "loss": round(float(loss), 1) if loss is not None
                         else (0.0 if ok else 100.0)}
            )
        return {
            "round_id": rounds[-1][0] if rounds else 0,
            "rounds": [{"id": rid, "ts": ts} for rid, ts in rounds],
            "targets": [
                {"id": task["id"], "name": task["name"],
                 "target": task["target"],
                 "history": by_id.get(str(task["id"]), [])}
                for task in self.cfg["targets"]
            ],
        }

    def resolved_names(self, ips):
        now = int(time.time())
        ips = list(dict.fromkeys(ips))[:30]
        known = {}
        todo = []
        for ip in ips:
            row = self.db.execute(
                "SELECT name,resolved_at FROM dest_names WHERE ip=?", (ip,)
            )
            if row and now - row[0][1] < (86400 * (7 if row[0][0] else 1)):
                known[ip] = row[0][0]
            else:
                todo.append(ip)
        if todo:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(reverse_name, todo))
            with self.db.lock, self.db.conn:
                self.db.conn.executemany(
                    "INSERT INTO dest_names(ip,name,resolved_at) VALUES(?,?,?) "
                    "ON CONFLICT(ip) DO UPDATE SET name=excluded.name,"
                    "resolved_at=excluded.resolved_at",
                    [(ip, name, now) for ip, name in zip(todo, results)]
                )
            known.update(zip(todo, results))
        return known

    def flows(self, hours=24, start=None, end=None):
        now = int(time.time())
        if start is None or end is None:
            end = now
            start = end - hours * 3600
        start = max(now - 31 * 86400, int(start))
        end = min(now + 60, int(end))
        if end <= start or end - start > 31 * 86400:
            raise ValueError("invalid analysis range")
        # Only aggregate persisted samples when the user opens a time bucket.
        rows = self.db.execute("""
            SELECT dst,dport,protocol,SUM(up),SUM(down),COUNT(*)
            FROM flow_usage
            WHERE bucket>=? AND bucket<?
            GROUP BY dst,dport,protocol
            ORDER BY SUM(up)+SUM(down) DESC LIMIT 200
        """, (start, end))
        names = self.resolved_names([r[0] for r in rows[:30]])
        service_groups = {}
        connections = []
        captured = 0
        for ip, port, proto, sent, received, samples in rows:
            sent, received = int(sent), int(received)
            captured += sent + received
            name, confidence = guess_service(ip, names.get(ip, ""))
            entry = {
                "ip": ip, "port": port, "proto": proto, "name": name,
                "confidence": confidence, "hostname": names.get(ip, ""),
                "up": sent, "down": received,
                "total": sent + received, "samples": samples
            }
            connections.append(entry)
            if name not in service_groups:
                service_groups[name] = {
                    "name": name, "confidence": confidence, "up": 0,
                    "down": 0, "total": 0, "connections": 0
                }
            group = service_groups[name]
            group["up"] += sent
            group["down"] += received
            group["total"] += sent + received
            group["connections"] += 1
        groups = sorted(service_groups.values(), key=lambda item: item["total"], reverse=True)
        for item in groups:
            item["share"] = round(item["total"] * 100 / captured, 1) if captured else 0
        return {
            "source": getattr(self.db, "last_flow_source", "starting"),
            "observed_connections": getattr(self.db, "last_flow_count", 0),
            "start": start, "end": end, "captured": captured,
            "services": groups[:40], "connections": connections[:80]
        }


def handler_factory(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FamilyNet"
        sys_version = ""

        def log_message(self, fmt, *args):
            return

        def respond(self, status, payload, mime="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                value = base64.b64decode(header[6:], validate=True).decode()
                name, password = value.split(":", 1)
                salt = bytes.fromhex(app.cfg["password_salt"])
                computed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 140000)
                return hmac.compare_digest(name, app.cfg["username"]) and \
                    hmac.compare_digest(computed.hex(), app.cfg["password_hash"])
            except (ValueError, UnicodeError, binascii.Error):
                return False

        def do_GET(self):
            path = urlparse(self.path)
            if path.path == "/healthz":
                return self.respond(200, '{"status":"ok"}')
            # Overview and its metrics are public. Audit data is protected
            # at the server endpoint, not merely hidden in the interface.
            if path.path in ("/api/auth", "/api/history", "/api/flows") and not self.authorized():
                return self.respond(401, '{"error":"unauthorized"}')
            if path.path == "/api/auth":
                return self.respond(200, '{"ok":true}')
            if path.path in ("/", "/index.html"):
                try:
                    return self.respond(200, HTML.read_bytes(), "text/html; charset=utf-8")
                except OSError:
                    return self.respond(500, '{"error":"missing html"}')
            query = parse_qs(path.query)
            try:
                hours = max(1, min(720, int(query.get("hours", ["24"])[0])))
                if path.path == "/api/state":
                    # Audit is tunneled through the already working state route.
                    # Every private mode is authenticated independently.
                    audit = query.get("audit", [None])[0]
                    if audit is None:
                        payload = app.state()
                    elif audit in ("auth", "history", "flows", "rank"):
                        if not self.authorized():
                            return self.respond(401, '{"error":"unauthorized"}')
                        if audit == "auth":
                            payload = {"ok": True}
                        elif audit == "history":
                            payload = app.history(hours)
                        elif audit in ("flows", "rank"):
                            def optional_time(name):
                                values = query.get(name, [])
                                return int(values[0]) if values else None
                            payload = app.flows(
                                hours, optional_time("start"), optional_time("end")
                            )
                    else:
                        return self.respond(404, '{"error":"not found"}')
                elif path.path == "/api/history":
                    payload = app.history(hours)
                elif path.path == "/api/pings":
                    payload = app.pings()
                elif path.path == "/api/flows":
                    payload = app.flows(hours)
                else:
                    return self.respond(404, '{"error":"not found"}')
                return self.respond(200, json.dumps(payload, ensure_ascii=False))
            except Exception as exc:
                print("request:", type(exc).__name__, str(exc), flush=True)
                return self.respond(500, '{"error":"unavailable"}')
    return Handler


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--init-password", metavar="PASSWORD")
    args.add_argument("--check", action="store_true")
    args.add_argument("--import-komari", action="store_true")
    opts = args.parse_args()
    if opts.init_password:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if CONFIG.exists():
            print("配置已存在，不覆盖")
            return
        config = make_config(opts.init_password)
        CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        os.chmod(CONFIG, 0o600)
        print("初始化完成，监控接口:", config["interface"])
        return
    cfg = ensure_billing_schedule(json.loads(CONFIG.read_text()))
    app = Monitor(cfg)
    app.sample_state()
    if opts.import_komari:
        migrate_komari(app)
        return
    if opts.check:
        print(json.dumps(app.state(), ensure_ascii=False))
        return
    threading.Thread(target=state_loop, args=(app,),
                     daemon=True, name="fn-state").start()
    threading.Thread(target=collector_loop, args=(app.db, cfg),
                     daemon=True, name="fn-collector").start()
    threading.Thread(target=ping_loop, args=(app.db, cfg["targets"]),
                     daemon=True, name="fn-pinger").start()
    port = int(os.environ.get("FAMILYNET_MONITOR_PORT", "25775"))
    if port not in (25774, 25775):
        raise ValueError("unsupported monitor port")
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_factory(app))
    server.daemon_threads = True
    print("FamilyNet Monitor", port, flush=True)
    try:
        server.serve_forever(poll_interval=1)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
