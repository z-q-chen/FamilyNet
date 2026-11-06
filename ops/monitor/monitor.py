#!/usr/bin/env python3
"""FamilyNet Monitor: local-only system metrics, traffic history and TCP checks."""
import argparse
import base64
import binascii
import calendar
import hashlib
import hmac
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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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
                task_id INTEGER NOT NULL, latency REAL, ok INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS pings_task_time ON pings(task_id,ts);
              CREATE TABLE IF NOT EXISTS flows(
                id TEXT PRIMARY KEY, first_ts INTEGER NOT NULL,
                last_ts INTEGER NOT NULL, protocol TEXT NOT NULL,
                src TEXT NOT NULL, sport TEXT NOT NULL,
                dst TEXT NOT NULL, dport TEXT NOT NULL,
                original_bytes INTEGER NOT NULL, reply_bytes INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS flows_time ON flows(last_ts);
            """)

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

    def ping(self, task, latency, ok):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO pings(ts,task_id,latency,ok) VALUES(?,?,?,?)",
                (int(time.time()), task, latency, 1 if ok else 0)
            )

    def cleanup(self):
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM pings WHERE ts<?",
                              (int(time.time()) - 86400,))
            self.conn.execute("DELETE FROM flows WHERE last_ts<?",
                              (int(time.time()) - 86400,))
            # Retain original deltas; discarding intermediate deltas would
            # undercount totals when querying a time range.
            self.conn.execute("DELETE FROM traffic WHERE ts<?",
                              (int(time.time()) - 120 * 86400,))

    def save_flows(self, rows):
        now = int(time.time())
        with self.lock, self.conn:
            self.conn.executemany("""
              INSERT INTO flows VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET
                last_ts=excluded.last_ts,
                original_bytes=MAX(flows.original_bytes,excluded.original_bytes),
                reply_bytes=MAX(flows.reply_bytes,excluded.reply_bytes)
            """, [(x["id"], now, now, x["proto"], x["src"], x["sport"],
                   x["dst"], x["dport"], x["up"], x["down"]) for x in rows])


def ping_target(task):
    raw = task["target"]
    if raw.startswith("["):
        host, port = raw[1:].split("]:", 1)
    else:
        host, port = raw.rsplit(":", 1)
    start = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=3.0):
            pass
        return round((time.monotonic() - start) * 1000, 2), True
    except (OSError, ValueError):
        return None, False


def ping_loop(store, tasks):
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fn-ping")
    try:
        while not STOP.is_set():
            now = int(time.time())
            for task in tasks:
                last = store.execute("SELECT MAX(ts) FROM pings WHERE task_id=?",
                                     (task["id"],))[0][0]
                if last is not None and now - last < max(60, task["interval"]):
                    continue
                def work(t):
                    latency, ok = ping_target(t)
                    store.ping(t["id"], latency, ok)
                pool.submit(work, task)
            STOP.wait(30)
    finally:
        pool.shutdown(wait=False)


CT_TUPLE = re.compile(r"(?:src|dst|sport|dport|bytes)=[^\s]+")
CT_ID = re.compile(r"\bid=(\d+)\b")


def capture_flows():
    if not shutil.which("conntrack"):
        return []
    try:
        out = subprocess.run(
            ["conntrack", "-L", "-o", "extended"], capture_output=True,
            text=True, timeout=8, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.stdout.splitlines()[:6000]:
        if "bytes=" not in line:
            continue
        parts = line.split()
        proto = parts[0] if parts else "?"
        pairs = {}
        for key, value in re.findall(r"\b(src|dst|sport|dport|bytes)=([^\s]+)", line):
            pairs.setdefault(key, []).append(value)
        if any(len(pairs.get(k, [])) < 2 for k in ("src", "dst", "bytes")):
            continue
        src, dst = pairs["src"][0], pairs["dst"][0]
        sport = pairs.get("sport", [""])[0]
        dport = pairs.get("dport", [""])[0]
        try:
            up, down = int(pairs["bytes"][0]), int(pairs["bytes"][1])
        except ValueError:
            continue
        match = CT_ID.search(line)
        raw_id = "|".join([proto, src, sport, dst, dport, match.group(1) if match else ""])
        flow_id = hashlib.sha256(raw_id.encode()).hexdigest()[:28]
        rows.append({"id": flow_id, "proto": proto,
                     "src": src, "sport": sport, "dst": dst, "dport": dport,
                     "up": up, "down": down})
    return rows


def collector_loop(store, cfg):
    last_clean = 0
    while not STOP.is_set():
        started = time.monotonic()
        try:
            store.sample(cfg["interface"])
            store.save_flows(capture_flows())
            if time.time() - last_clean > 3600:
                store.cleanup()
                last_clean = time.time()
        except Exception as exc:
            print("collector:", exc, flush=True)
        STOP.wait(max(1, int(cfg.get("sample_seconds", 30)) - (time.monotonic() - started)))


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db = Store()
        self.last_ticks = None
        self.last_live = None
        self.live_lock = threading.Lock()

    def state(self):
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
        since = reset_start(self.cfg.get("reset_day", 0)) or 0
        values = self.db.execute(
            "SELECT COALESCE(SUM(drx),0),COALESCE(SUM(dtx),0),MIN(ts) "
            "FROM traffic WHERE ts>=?", (since,)
        )[0]
        up_total = int(values[1])
        down_total = int(values[0])
        mode = self.cfg.get("quota_mode", "sum")
        used = {"up": up_total, "down": down_total, "max": max(up_total, down_total),
                "sum": up_total + down_total, "min": min(up_total, down_total)}.get(mode, up_total + down_total)
        return {
            "time": int(now), "hostname": socket.gethostname(),
            "interface": self.cfg["interface"],
            "uptime": uptime, "cpu": cpu,
            "memory_total": mem_total, "memory_used": mem_used,
            "disk_total": disk.total, "disk_used": disk.used,
            "rxps": rxps, "txps": txps, "up_total": up_total,
            "down_total": down_total, "used": used,
            "quota": float(self.cfg["quota_gib"]) * 1024**3,
            "quota_mode": mode, "first_sample": values[2],
            "reset_day": self.cfg.get("reset_day", 0),
            "price_usd": self.cfg.get("price_usd"),
            "price_cycle_months": self.cfg.get("price_cycle_months"),
            "expires_at": self.cfg.get("expires_at", ""),
            "title": self.cfg.get("title", "洛杉矶 VPS 监控"),
            "refresh_seconds": self.cfg.get("refresh_seconds", 5),
            "load": [round(n, 2) for n in os.getloadavg()],
        }

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
        since = int(time.time()) - 86400
        data = self.db.execute(
            "SELECT task_id,ts,latency,ok FROM pings WHERE ts>=? ORDER BY ts",
            (since,)
        )
        by_id = {}
        for task, ts, latency, ok in data:
            by_id.setdefault(str(task), []).append(
                {"ts": ts, "latency": latency, "ok": bool(ok)}
            )
        return [{"id": task["id"], "name": task["name"], "target": task["target"],
                 "history": by_id.get(str(task["id"]), [])}
                for task in self.cfg["targets"]]

    def flows(self, hours):
        since = int(time.time()) - hours * 3600
        data = self.db.execute("""
            SELECT first_ts,last_ts,protocol,src,sport,dst,dport,
                   original_bytes,reply_bytes
            FROM flows WHERE last_ts>=?
            ORDER BY original_bytes+reply_bytes DESC LIMIT 250
        """, (since,))
        return {
            "sampling": "conntrack" if shutil.which("conntrack") else "unavailable",
            "rows": [{"start": a, "end": b, "protocol": p,
                      "src": s, "sport": sp, "dst": d, "dport": dp,
                      "up": u, "down": v}
                     for a, b, p, s, sp, d, dp, u, v in data]
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
                    payload = app.state()
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
    cfg = json.loads(CONFIG.read_text())
    app = Monitor(cfg)
    if opts.check:
        print(json.dumps(app.state(), ensure_ascii=False))
        return
    threading.Thread(target=collector_loop, args=(app.db, cfg),
                     daemon=True, name="fn-collector").start()
    threading.Thread(target=ping_loop, args=(app.db, cfg["targets"]),
                     daemon=True, name="fn-pinger").start()
    server = ThreadingHTTPServer(("127.0.0.1", 25775), handler_factory(app))
    server.daemon_threads = True
    print("FamilyNet Monitor 127.0.0.1:25775", flush=True)
    try:
        server.serve_forever(poll_interval=1)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
