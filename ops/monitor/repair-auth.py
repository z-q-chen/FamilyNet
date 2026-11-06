#!/usr/bin/env python3
"""Repair only FamilyNet Monitor traffic-audit credentials and verify both routes."""
import base64
import hashlib
import http.client
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone

CONFIG = Path("/etc/familynet-monitor/config.json")
CREDS = Path("/root/familynet-monitor-credentials.txt")
BACKUP_ROOT = Path("/var/backups/familynet-monitor")
MONITOR_PORT = 25775
PUBLIC_HOST = "207.57.74.12"


class LocalTLS(http.client.HTTPSConnection):
    """Test the actual Caddy listener without relying on public hairpin routing."""
    def connect(self):
        sock = socket.create_connection(("127.0.0.1", 9443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=PUBLIC_HOST)


def get_status(password, public=False, path="/api/auth"):
    auth = base64.b64encode(("admin:" + password).encode("utf-8")).decode("ascii")
    headers = {"Authorization": "Basic " + auth, "Accept": "application/json"}
    if public:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = LocalTLS(PUBLIC_HOST, 9443, timeout=6, context=ctx)
        headers["Host"] = PUBLIC_HOST + ":9443"
    else:
        conn = http.client.HTTPConnection("127.0.0.1", MONITOR_PORT, timeout=6)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        status = response.status
        response.read(5000)
        return status
    finally:
        conn.close()


def restart():
    subprocess.run(["systemctl", "restart", "familynet-monitor.service"], check=True)


def wait_local(password):
    result = None
    for _ in range(15):
        try:
            result = get_status(password)
            if result == 200:
                return result
        except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException):
            pass
        time.sleep(1)
    return result


def main():
    if os.geteuid() != 0:
        sys.exit("请用 sudo python3 运行。")
    if not CONFIG.is_file():
        sys.exit("没有找到新监控配置。")
    if subprocess.run(["systemctl", "is-active", "--quiet", "familynet-monitor.service"]).returncode != 0:
        sys.exit("监控服务未运行，未修改密码。")
    current = json.loads(CONFIG.read_text())
    for key in ("username", "password_salt", "password_hash"):
        if key not in current:
            sys.exit("配置缺少认证字段 " + key + "；未修改。")
    if current.get("username") != "admin":
        sys.exit("当前账号不是 admin；拒绝自动重置。")

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    folder = BACKUP_ROOT / ("auth-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir(mode=0o700)
    shutil.copy2(CONFIG, folder / "config.json")
    if CREDS.is_file():
        shutil.copy2(CREDS, folder / "old-credentials.txt")

    password = secrets.token_urlsafe(20)
    salt = secrets.token_hex(16)
    changed = dict(current)
    changed["password_salt"] = salt
    changed["password_hash"] = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 140000
    ).hex()

    temp = CONFIG.with_suffix(".auth-tmp")
    try:
        temp.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n")
        temp.chmod(0o600)
        os.replace(temp, CONFIG)
        restart()
        local = wait_local(password)
        if local != 200:
            raise RuntimeError("本机验证接口状态：" + str(local))
        external = get_status(password, public=True)
        if external != 200:
            raise RuntimeError("9443 反向代理验证接口状态：" + str(external))
        # Confirm that the private data route also accepts the same credentials.
        if get_status(password, public=True, path="/api/flows") != 200:
            raise RuntimeError("9443 流量明细接口仍无法通过身份验证")
        CREDS.write_text(
            "地址: https://" + PUBLIC_HOST + ":9443\n"
            "账号: admin\n"
            "流量分析密码: " + password + "\n"
        )
        CREDS.chmod(0o600)
    except Exception as exc:
        shutil.copy2(folder / "config.json", CONFIG)
        CONFIG.chmod(0o600)
        try:
            restart()
        except Exception:
            print("原配置已恢复，但服务重新启动失败。", file=sys.stderr)
        print("密码未更改；原配置已恢复。", file=sys.stderr)
        print("错误：" + str(exc), file=sys.stderr)
        print("配置备份：" + str(folder), file=sys.stderr)
        sys.exit(1)
    finally:
        temp.unlink(missing_ok=True)
    print("验证通过：本机 25775、HTTPS 9443 和流量明细接口均正常")
    print("账号：admin")
    print("新密码：" + password)
    print("密码文件：" + str(CREDS))


if __name__ == "__main__":
    main()
