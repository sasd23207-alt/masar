# -*- coding: utf-8 -*-
"""Smoke-test the local desktop API and persistence across a full backend restart.

Uses only Python's standard library so it also runs on the legacy Python used
by the Windows 32-bit installer workflow.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"


def request_json(base_url, path, method="GET", payload=None, token=None, timeout=5):
    data = None if payload is None else json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {"raw": body}
        return exc.code, parsed


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(env, log_handle):
    backend_exe = env.get("MASAR_SMOKE_BACKEND_EXE")
    command = [backend_exe] if backend_exe else [sys.executable, str(SERVER)]
    proc = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    base = "http://127.0.0.1:" + env["MASAR_PORT"]
    deadline = time.time() + 20
    last_error = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError("Backend exited before becoming healthy; inspect smoke-test log.")
        try:
            code, body = request_json(base, "/api/health", timeout=1)
            if code == 200 and body.get("ok"):
                return proc, base, body
            last_error = "health returned {!r}: {!r}".format(code, body)
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(0.2)
    proc.terminate()
    raise AssertionError("Backend did not become healthy: " + str(last_error))


def stop_server(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    require(SERVER.is_file(), "server.py is missing")
    with tempfile.TemporaryDirectory(prefix="masar-desktop-smoke-") as work:
        root = Path(work)
        db_path = root / "data" / "masar.db"
        backup_path = root / "backups"
        port = str(free_port())
        env = os.environ.copy()
        env.update({
            "MASAR_DB": str(db_path),
            "MASAR_BACKUPS": str(backup_path),
            "MASAR_HOST": "127.0.0.1",
            "MASAR_PORT": port,
            "MASAR_DESKTOP_MODE": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        env.pop("MASAR_ACCESS_CODE", None)
        with (root / "backend.log").open("w+", encoding="utf-8") as log:
            proc = None
            try:
                proc, base, health = start_server(env, log)
                require(health.get("database") == "sqlite", "health did not report SQLite")
                require(health.get("desktopMode") is True, "desktop mode was not enabled")
                require(health.get("managerExists") is False, "fresh database unexpectedly has a manager")

                code, registered = request_json(
                    base, "/api/register", method="POST",
                    payload={"username": "smoke_manager", "password": "Masar-Test-Password-2026",
                             "fullName": "Masar Test Manager"},
                )
                require(code == 200 and registered.get("token"),
                        "first manager registration failed: {!r}".format(registered))
                token = registered["token"]

                code, created = request_json(
                    base, "/api/students", method="POST", token=token,
                    payload={"name": "Masar Persistence Smoke Student", "age": 10,
                             "className": "Test Class A", "guardianName": "Test Guardian",
                             "guardianPhone": ""},
                )
                require(code == 200 and isinstance(created.get("id"), int),
                        "student creation failed: {!r}".format(created))
                student_id = created["id"]

                code, state = request_json(base, "/api/state", token=token)
                require(code == 200, "state API failed before restart")
                before = [s for s in state.get("state", {}).get("students", [])
                          if s.get("id") == student_id and
                          s.get("name") == "Masar Persistence Smoke Student"]
                require(len(before) == 1, "new student missing from state before restart")

                code, duplicate = request_json(
                    base, "/api/register", method="POST",
                    payload={"username": "second_manager", "password": "Masar-Test-Password-2026"},
                )
                require(code == 409 and duplicate.get("error") == "ACCOUNT_EXISTS",
                        "desktop allowed another manager registration: {!r}".format(duplicate))

                stop_server(proc)
                proc = None

                # Relaunch a brand-new server process against the exact same database path.
                proc, base, health = start_server(env, log)
                require(health.get("managerExists") is True,
                        "manager account did not survive backend restart")

                code, me = request_json(base, "/api/me", token=token)
                require(code == 200 and me.get("user", {}).get("username") == "smoke_manager",
                        "saved login session did not survive backend restart: {!r}".format(me))

                code, state = request_json(base, "/api/state", token=token)
                require(code == 200, "state API failed after restart")
                after = [s for s in state.get("state", {}).get("students", [])
                         if s.get("id") == student_id and
                         s.get("name") == "Masar Persistence Smoke Student"]
                require(len(after) == 1, "student record was lost after backend restart")

                code, login = request_json(
                    base, "/api/login", method="POST",
                    payload={"username": "smoke_manager", "password": "Masar-Test-Password-2026"},
                )
                require(code == 200 and login.get("token"),
                        "manager could not log in again after restart")

                print("PASS: fresh SQLite database initialized")
                print("PASS: first desktop manager registered once")
                print("PASS: second manager registration rejected")
                print("PASS: student created and appears in application state")
                print("PASS: manager account and session survived backend restart")
                print("PASS: student record survived backend restart")
                print("PASS: manager can log in again after restart")
            except Exception:
                try:
                    log.flush()
                    log.seek(0)
                    diagnostic = log.read()
                    if diagnostic:
                        print("--- backend log ---")
                        print(diagnostic)
                        print("--- end backend log ---")
                except Exception:
                    pass
                raise
            finally:
                if proc is not None:
                    stop_server(proc)


if __name__ == "__main__":
    run()
