"""Cảnh báo nguy hiểm: nghe micro -> phát hiện từ khóa cứu hộ -> còi ESP32 -> Telegram."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time

try:
    from alertsystem.camera import CameraRecorder
    from alertsystem.config import AppConfig
    from alertsystem.esp32 import Esp32Alarm
    from alertsystem.notifier import TelegramNotifier
    from alertsystem.speech import KeywordListener, normalize
except ImportError as exc:  # thiếu thư viện
    print(f"Thiếu thư viện: {exc}\nCài đặt bằng: pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(1) from exc

logger = logging.getLogger("canhbaonguyhiem")


class AlertSystem:
    """Ghép micro, webcam, ESP32 và Telegram thành một luồng cảnh báo."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.camera = CameraRecorder(config.camera, config.output_dir)
        self.alarm = Esp32Alarm(config.esp32)
        self.notifier = TelegramNotifier(config.telegram)
        self.listener = KeywordListener(config.speech, self._on_keyword, config.model_path)

        self._alert_lock = threading.Lock()
        self._alert_done = threading.Event()

    # ---------- vòng đời ----------

    def start(self) -> None:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self._log_warnings()

        self.camera.start()
        self.listener.start()
        logger.info("Sẵn sàng. Đang nghe từ khóa: %s", ", ".join(self.config.speech.keywords))

    def stop(self) -> None:
        self.listener.stop()
        self.camera.stop()
        logger.info("Đã dừng hệ thống")

    def run(self, should_stop) -> None:
        """Vòng lặp chính: hiện cửa sổ camera (nếu bật) cho tới khi should_stop() trả True."""
        if self.config.camera.show_preview:
            logger.info("Cửa sổ camera: nhấn q hoặc ESC để thoát")
            if self.camera.preview_loop(self._preview_status, should_stop):
                return

        # Không có cửa sổ camera thì chạy nền
        while not should_stop():
            time.sleep(0.2)

    def trigger_test(self) -> None:
        """Kích hoạt một cảnh báo giả để kiểm tra ESP32 + Telegram + camera."""
        logger.info("=== KÍCH HOẠT THỬ ===")
        self._alert_done.clear()
        self._on_keyword("kích hoạt thử", "kiểm tra hệ thống")

    def test_finished(self) -> bool:
        return self._alert_done.is_set()

    def _preview_status(self) -> str:
        if self._alert_lock.locked():
            return "DANG XU LY CANH BAO..."
        keywords = ", ".join(normalize(kw) for kw in self.config.speech.keywords)
        return f"San sang - dang nghe: {keywords}"

    # ---------- xử lý cảnh báo ----------

    def _on_keyword(self, keyword: str, transcript: str) -> None:
        logger.warning("PHÁT HIỆN TỪ KHÓA '%s' trong: \"%s\"", keyword, transcript)
        threading.Thread(
            target=self._handle_alert,
            args=(keyword, transcript),
            name="alert",
            daemon=True,
        ).start()

    def _handle_alert(self, keyword: str, transcript: str) -> None:
        if not self._alert_lock.acquire(blocking=False):
            logger.info("Bỏ qua: cảnh báo trước đang được xử lý")
            return

        try:
            if self.config.esp32.enabled:
                logger.info("Bật còi cảnh báo trên ESP32...")
                esp32_ok = self.alarm.trigger_on()
            else:
                logger.warning("Chưa cấu hình ESP32, bỏ qua bước bật còi")
                esp32_ok = False

            logger.info("Trích xuất ảnh/video từ webcam...")
            snapshot, clip = self.camera.capture_event()

            if self.config.telegram.enabled:
                logger.info("Gửi cảnh báo qua Telegram...")
                self.notifier.send_alert(keyword, transcript, snapshot, clip, esp32_ok)
            else:
                logger.warning(
                    "Chưa cấu hình Telegram, bỏ qua bước gửi. Dữ liệu lưu tại: %s",
                    snapshot.parent if snapshot else self.config.output_dir,
                )
        except Exception:  # noqa: BLE001 - log lại để hệ thống không chết
            logger.exception("Lỗi khi xử lý cảnh báo")
        finally:
            self._alert_lock.release()
            self._alert_done.set()

    def _log_warnings(self) -> None:
        if not self.config.telegram.enabled:
            logger.warning("Telegram chưa cấu hình (bot_token/chat_ids) - sẽ không gửi được tin")
        if not self.config.esp32.enabled:
            logger.warning("ESP32 chưa cấu hình (ip) - sẽ không bật được còi")


def list_input_devices() -> None:
    import sounddevice as sd

    print("Thiết bị âm thanh đầu vào khả dụng (dùng index làm 'input_device' trong config.json):")
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            print(f"  [{index}] {device['name']} ({device['max_input_channels']} kênh)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cảnh báo nguy hiểm: nghe từ khóa 'cứu' -> còi ESP32 -> gửi ảnh/video qua Telegram"
    )
    parser.add_argument("--config", default="config.json", help="Đường dẫn file cấu hình JSON")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Kích hoạt cảnh báo thử rồi thoát (kiểm tra ESP32 + Telegram + camera)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Liệt kê thiết bị âm thanh để chọn input_device",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="Không hiện cửa sổ camera (chạy nền)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_devices:
        list_input_devices()
        return 0

    try:
        config = AppConfig.load(args.config)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Không đọc được cấu hình: %s", exc)
        return 1

    if args.no_preview:
        config.camera.show_preview = False

    system = AlertSystem(config)
    try:
        system.start()
    except Exception as exc:  # noqa: BLE001 - lỗi phần cứng/model
        logger.error("Không khởi động được hệ thống: %s", exc)
        system.stop()
        return 1

    try:
        if args.test:
            system.trigger_test()
            system.run(should_stop=system.test_finished)
        else:
            system.run(should_stop=lambda: False)
    except KeyboardInterrupt:
        logger.info("Nhận Ctrl+C, đang dừng...")
    finally:
        system.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
