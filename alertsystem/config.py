"""Đọc và kiểm tra cấu hình từ file JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_KEYWORDS = ["cứu", "cứu tôi", "cứu với", "giúp tôi", "nguy hiểm", "help"]


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_ids: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token.strip()) and bool(self.chat_ids)


@dataclass
class Esp32Config:
    ip: str = ""
    port: int = 80
    alarm_seconds: int = 60

    @property
    def enabled(self) -> bool:
        return bool(self.ip.strip())


@dataclass
class SpeechConfig:
    model_path: str = "models/vosk-model-small-vn-0.4"
    sample_rate: int = 16000
    input_device: int | None = None
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    cooldown_seconds: float = 8.0


@dataclass
class CameraConfig:
    index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 20
    pre_seconds: float = 5.0
    post_seconds: float = 10.0
    show_preview: bool = True


@dataclass
class StorageConfig:
    output_dir: str = "data"


@dataclass
class AppConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    esp32: Esp32Config = field(default_factory=Esp32Config)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    root: Path = field(default_factory=Path)

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path).resolve()
        if not config_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file cấu hình: {config_path}")

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return cls(
            telegram=TelegramConfig(**raw.get("telegram", {})),
            esp32=Esp32Config(**raw.get("esp32", {})),
            speech=SpeechConfig(**raw.get("speech", {})),
            camera=CameraConfig(**raw.get("camera", {})),
            storage=StorageConfig(**raw.get("storage", {})),
            root=config_path.parent,
        )

    @property
    def model_path(self) -> Path:
        """Đường dẫn model Vosk, tính tương đối theo thư mục chứa config."""
        path = Path(self.speech.model_path)
        return path if path.is_absolute() else (self.root / path).resolve()

    @property
    def output_dir(self) -> Path:
        """Thư mục lưu ảnh/video cảnh báo."""
        path = Path(self.storage.output_dir)
        return path if path.is_absolute() else (self.root / path).resolve()
