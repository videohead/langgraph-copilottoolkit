#!/usr/bin/env python3
import json
import os
import socket
import sys
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _check_socket_from_url(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return False, f"invalid URL: {url}"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{host}:{port} ({exc})"


def _http_get_json(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
            if status < 200 or status >= 300:
                return False, f"HTTP {status}"
            return True, payload
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _wait_for_ok(check_name: str, fn, timeout_seconds: int) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last_msg = ""
    while time.time() < deadline:
        ok, msg = fn()
        if ok:
            return True, msg
        last_msg = msg
        time.sleep(1)
    return False, last_msg


def main() -> int:
    timeout_seconds = int(os.environ.get("STARTUP_SMOKE_TIMEOUT_SECONDS", "45"))

    appserver_url = os.environ.get("APPSERVER_HEALTH_URL", "http://127.0.0.1:8000/")
    django_candidates = []
    django_from_env = os.environ.get("DJANGO_HEALTH_URL", "").strip()
    if django_from_env:
        django_candidates.append(django_from_env)
    django_candidates.extend(
        [
            "http://django:8080/api/health/",
            "http://django.langgraph.internal:8080/api/health/",
        ]
    )

    postgres_uri = os.environ.get("POSTGRES_URI", "").strip()
    redis_url = os.environ.get("REDIS_URL", "").strip()

    print("[startup-smoke] running checks...")

    app_ok, app_msg = _wait_for_ok(
        "appserver",
        lambda: _http_get_json(appserver_url),
        timeout_seconds,
    )
    if app_ok:
        print(f"[startup-smoke] appserver OK: {appserver_url}")
    else:
        print(f"[startup-smoke] appserver FAIL: {appserver_url} ({app_msg})")

    django_ok = False
    django_msg = "no candidates checked"
    for candidate in django_candidates:
        ok, msg = _wait_for_ok(
            "django",
            lambda c=candidate: _http_get_json(c),
            timeout_seconds,
        )
        if ok:
            django_ok = True
            django_msg = candidate
            break
        django_msg = f"{candidate} ({msg})"

    if django_ok:
        print(f"[startup-smoke] django health OK: {django_msg}")
    else:
        print(f"[startup-smoke] django health FAIL: {django_msg}")

    pg_ok = False
    pg_msg = "POSTGRES_URI is not set"
    if postgres_uri:
        pg_ok, pg_msg = _check_socket_from_url(postgres_uri)
    print(f"[startup-smoke] postgres {'OK' if pg_ok else 'FAIL'}: {pg_msg}")

    redis_ok = False
    redis_msg = "REDIS_URL is not set"
    if redis_url:
        redis_ok, redis_msg = _check_socket_from_url(redis_url)
    print(f"[startup-smoke] redis {'OK' if redis_ok else 'FAIL'}: {redis_msg}")

    all_ok = app_ok and django_ok and pg_ok and redis_ok
    summary = {
        "appserver": app_ok,
        "django": django_ok,
        "postgres": pg_ok,
        "redis": redis_ok,
    }
    print(f"[startup-smoke] summary: {json.dumps(summary)}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
