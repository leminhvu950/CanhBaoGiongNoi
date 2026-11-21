"""Gửi cảnh báo kèm ảnh/video qua Telegram Bot API."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import requests

from .config import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, config: TelegramConfig, timeout: float = 60.0) -> None:
        self.config = config
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{config.bot_token}"

    # ---------- API công khai ----------

    def send_message(self, text: str) -> bool:
        """Gửi tin nhắn cho tất cả người nhận, trả True nếu gửi được ít nhất 1 nơi."""
        return self._send_to_all("sendMessage", {"text": text})

    def send_alert(
        self,
        keyword: str,
        transcript: str,
        snapshot: Path | None,
        clip: Path | None,
        esp32_ok: bool,
    ) -> bool:
        caption = (
            "🚨 CẢNH BÁO NGUY HIỂM\n"
            f"Từ khóa: {keyword}\n"
            f"Nhận được: \"{transcript}\"\n"
            f"Thời gian: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"Còi ESP32: {'đã bật' if esp32_ok else 'LỖI - không kết nối được'}"
        )

        delivered = False
        for chat_id in self.config.chat_ids:
            if clip is not None and self._send_video(chat_id, clip, caption):
                delivered = True
                continue
            if snapshot is not None and self._send_photo(chat_id, snapshot, caption):
                delivered = True
            if clip is not None:
                self._send_document(chat_id, clip, "Video cảnh báo (bản gốc)")
        return delivered

    # ---------- nội bộ ----------

    def _send_to_all(self, method: str, data: dict) -> bool:
        delivered = False
        for chat_id in self.config.chat_ids:
            if self._call(method, {**data, "chat_id": chat_id}):
                delivered = True
        return delivered

    def _send_video(self, chat_id: str, path: Path, caption: str) -> bool:
        with path.open("rb") as handle:
            return self._call(
                "sendVideo",
                {"chat_id": chat_id, "caption": caption},
                files={"video": (path.name, handle, "video/mp4")},
            )

    def _send_photo(self, chat_id: str, path: Path, caption: str) -> bool:
        with path.open("rb") as handle:
            return self._call(
                "sendPhoto",
                {"chat_id": chat_id, "caption": caption},
                files={"photo": (path.name, handle, "image/jpeg")},
            )

    def _send_document(self, chat_id: str, path: Path, caption: str) -> bool:
        with path.open("rb") as handle:
            return self._call(
                "sendDocument",
                {"chat_id": chat_id, "caption": caption},
                files={"document": (path.name, handle, "application/octet-stream")},
            )

    def _call(self, method: str, data: dict, files: dict | None = None) -> bool:
        try:
            response = requests.post(
                f"{self.base_url}/{method}",
                data=data,
                files=files,
                timeout=self.timeout,
            )
            payload = response.json()
        except requests.RequestException as exc:
            logger.error("Telegram %s lỗi kết nối: %s", method, exc)
            return False
        except ValueError:
            logger.error("Telegram %s trả về dữ liệu không hợp lệ", method)
            return False

        if not payload.get("ok"):
            logger.error("Telegram %s thất bại: %s", method, payload.get("description"))
            return False
        return True
