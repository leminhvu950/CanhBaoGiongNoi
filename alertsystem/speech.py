"""Lắng nghe micro và phát hiện từ khóa cứu hộ bằng Vosk (chạy offline)."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

from .config import SpeechConfig


def normalize(text: str) -> str:
    """Bỏ dấu tiếng Việt, viết thường, bỏ ký tự đặc biệt.

    Vosk có thể trả về 'cứu' hoặc 'cuu' tùy model, nên chuẩn hóa cả hai phía
    để việc so khớp từ khóa luôn ổn định.
    """
    text = text.lower().translate(str.maketrans("đĐ", "dD"))
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class KeywordListener:
    """Đọc âm thanh từ micro theo luồng, gọi callback khi nghe được từ khóa."""

    def __init__(
        self,
        config: SpeechConfig,
        on_trigger: Callable[[str, str], None],
        model_path: str | Path,
    ) -> None:
        self.config = config
        self.on_trigger = on_trigger
        self.model_path = Path(model_path)
        self.sample_rate = config.sample_rate
        self.keywords: list[tuple[str, str]] = [
            (kw, normalize(kw)) for kw in config.keywords if normalize(kw)
        ]
        self.keywords.sort(key=lambda item: len(item[1]), reverse=True)

        self._audio_queue: queue.Queue[bytes] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream: sd.RawInputStream | None = None
        self._cooldown_until = 0.0

    # ---------- vòng đời ----------

    def start(self) -> None:
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Chưa có model Vosk tại {self.model_path}. "
                "Tải model tiếng Việt và giải nén vào thư mục models/."
            )

        SetLogLevel(-1)  # tắt log rác của Kaldi
        model = Model(str(self.model_path))
        recognizer = KaldiRecognizer(model, self.sample_rate)

        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=8000,
            dtype="int16",
            channels=1,
            device=self.config.input_device,
            callback=self._audio_callback,
        )
        self._stream.start()

        self._thread = threading.Thread(
            target=self._recognize_loop, args=(recognizer,), name="speech", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        if self._thread is not None:
            self._thread.join(timeout=3)

    # ---------- nội bộ ----------

    def _audio_callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        self._audio_queue.put(bytes(indata))

    def _recognize_loop(self, recognizer: KaldiRecognizer) -> None:
        while not self._stop_event.is_set():
            try:
                chunk = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if recognizer.AcceptWaveform(chunk):
                text = json.loads(recognizer.Result()).get("text", "")
            else:
                text = json.loads(recognizer.PartialResult()).get("partial", "")

            if text:
                self._check(text)

    def _check(self, text: str) -> None:
        normalized = normalize(text)
        if not normalized:
            return

        for keyword, normalized_keyword in self.keywords:
            if normalized_keyword in normalized:
                now = time.monotonic()
                if now < self._cooldown_until:
                    return
                self._cooldown_until = now + self.config.cooldown_seconds
                self.on_trigger(keyword, text)
                return
