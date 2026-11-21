"""Gửi lệnh bật/tắt còi cảnh báo tới ESP32 qua HTTP (WiFi nội bộ)."""

from __future__ import annotations

import logging

import requests

from .config import Esp32Config

logger = logging.getLogger(__name__)


class Esp32Alarm:
    def __init__(self, config: Esp32Config, timeout: float = 5.0, retries: int = 3) -> None:
        self.config = config
        self.timeout = timeout
        self.retries = retries
        self.base_url = f"http://{config.ip}:{config.port}"

    def trigger_on(self, seconds: int | None = None) -> bool:
        """Bật còi + đèn trên ESP32 trong `seconds` giây."""
        duration = seconds if seconds is not None else self.config.alarm_seconds
        ok = self._request("/alert", {"state": "on", "duration": duration})
        if ok:
            logger.info("ESP32: đã bật cảnh báo trong %ss", duration)
        else:
            logger.error("ESP32: KHÔNG bật được cảnh báo (%s)", self.base_url)
        return ok

    def trigger_off(self) -> bool:
        ok = self._request("/alert", {"state": "off"})
        if ok:
            logger.info("ESP32: đã tắt cảnh báo")
        return ok

    def status(self) -> dict | None:
        """Đọc trạng thái board, dùng để kiểm tra kết nối trước khi chạy."""
        try:
            response = requests.get(f"{self.base_url}/status", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("ESP32: không đọc được trạng thái từ %s: %s", self.base_url, exc)
            return None

    def _request(self, path: str, params: dict) -> bool:
        for attempt in range(1, self.retries + 1):
            try:
                response = requests.get(
                    f"{self.base_url}{path}", params=params, timeout=self.timeout
                )
                if response.status_code == 200:
                    return True
                logger.warning(
                    "ESP32 trả về HTTP %s (lần %s/%s)",
                    response.status_code,
                    attempt,
                    self.retries,
                )
            except requests.RequestException as exc:
                logger.warning("ESP32 lỗi kết nối (lần %s/%s): %s", attempt, self.retries, exc)
        return False
