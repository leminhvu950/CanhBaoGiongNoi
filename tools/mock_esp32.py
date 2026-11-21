"""ESP32 giả lập - dùng để test khi chưa có board thật.

Mô phỏng đúng các endpoint của firmware esp32_alert.ino:
    GET /status
    GET /alert?state=on&duration=60
    GET /alert?state=off

Cách dùng:
    python tools/mock_esp32.py                # chạy ở cổng 8080
    python tools/mock_esp32.py --port 80      # giống board thật (Windows cần quyền admin)

Rồi trỏ config.json vào mock:  "esp32": { "ip": "127.0.0.1", "port": 8080 }
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BUZZER_HZ = 2200
DEFAULT_ALARM_MS = 60_000

_lock = threading.Lock()
_alarm = False
_alarm_end_ms = 0.0
_started = time.time()


def set_alarm(active: bool, duration_ms: int = 0) -> None:
    global _alarm, _alarm_end_ms
    with _lock:
        _alarm = active
        _alarm_end_ms = time.monotonic() * 1000 + duration_ms if active else 0.0
    if active:
        print(f"\n*** CÒI BẬT *** bật lúc {datetime.now():%H:%M:%S} trong {duration_ms // 1000}s")
    else:
        print(f"\n--- CÒI TẮT --- lúc {datetime.now():%H:%M:%S}")


def status_payload() -> dict:
    with _lock:
        remaining = int(max(0, _alarm_end_ms - time.monotonic() * 1000)) if _alarm else 0
        return {
            "alarm": _alarm,
            "remaining_ms": remaining,
            "rssi": -50,
            "uptime_s": int(time.time() - _started),
            "ip": "127.0.0.1 (mock)",
        }


class MockEsp32Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - tên do BaseHTTPRequestHandler quy định
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/status":
            self._send(status_payload())
            return

        if parsed.path == "/alert":
            state = params.get("state", [""])[0]
            if state == "on":
                seconds = int(params.get("duration", ["60"])[0] or 60)
                set_alarm(True, seconds * 1000)
            elif state == "off":
                set_alarm(False)
            else:
                self._send({"error": "state phải là on hoặc off"}, 400)
                return
            self._send(status_payload())
            return

        self._send({"error": "not found"}, 404)

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        print(f"[HTTP] {self.command} {self.path} -> {code} {payload}")

    def log_message(self, fmt: str, *args) -> None:
        pass  # đã tự in log ở _send


def beep_loop() -> None:
    """Giả lập còi bằng loa máy tính (chỉ chạy được trên Windows)."""
    try:
        import winsound
    except ImportError:
        return

    while True:
        with _lock:
            active = _alarm
        if active:
            winsound.Beep(BUZZER_HZ, 400)
        else:
            time.sleep(0.2)


def auto_off_loop() -> None:
    """Tự tắt còi khi hết thời lượng, giống firmware."""
    while True:
        time.sleep(0.2)
        with _lock:
            expired = _alarm and time.monotonic() * 1000 >= _alarm_end_ms
        if expired:
            set_alarm(False)


def main() -> int:
    parser = argparse.ArgumentParser(description="ESP32 giả lập để test không cần phần cứng")
    parser.add_argument("--port", type=int, default=8080, help="Cổng HTTP (mặc định 8080)")
    parser.add_argument("--no-beep", action="store_true", help="Tắt tiếng bíp mô phỏng còi")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # hiện log ngay, không bị buffer

    if not args.no_beep:
        threading.Thread(target=beep_loop, daemon=True).start()
    threading.Thread(target=auto_off_loop, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), MockEsp32Handler)
    print(f"ESP32 giả lập đang chạy tại http://127.0.0.1:{args.port}")
    print("Đặt trong config.json:  \"esp32\": { \"ip\": \"127.0.0.1\", \"port\": %d }\n" % args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng mock ESP32")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
