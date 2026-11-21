"""Webcam USB: giữ sẵn buffer frame và trích xuất ảnh + video khi có cảnh báo."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import cv2

from .config import CameraConfig

logger = logging.getLogger(__name__)

WINDOW_NAME = "Camera canh bao"


class CameraRecorder:
    """Luồng đọc webcam liên tục, cho phép lấy lại `pre_seconds` giây trước đó."""

    def __init__(self, config: CameraConfig, output_dir: Path) -> None:
        self.config = config
        self.output_dir = Path(output_dir)

        self._capture: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._buffer_lock = threading.Lock()
        self._clip_lock = threading.Lock()

        pre_frames = max(1, int(config.fps * config.pre_seconds))
        post_frames = max(1, int(config.fps * config.post_seconds))
        self._pre_buffer: deque = deque(maxlen=pre_frames)
        self._live_queue: queue.Queue = queue.Queue(maxsize=post_frames * 2)

    # ---------- vòng đời ----------

    def start(self) -> None:
        capture = self._open_capture(self.config.index)
        if capture is None:
            raise RuntimeError(
                f"Không mở được webcam index={self.config.index}. "
                "Thử đổi 'camera.index' sang 1, 2... (Windows thường đánh số webcam ngoài là 1), "
                "hoặc kiểm tra camera không bị ứng dụng khác chiếm."
            )

        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        capture.set(cv2.CAP_PROP_FPS, self.config.fps)
        self._capture = capture

        self._thread = threading.Thread(target=self._capture_loop, name="camera", daemon=True)
        self._thread.start()
        logger.info(
            "Webcam index=%s đang chạy (%sx%s @ %sfps)",
            self.config.index,
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            self.config.fps,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    # ---------- nội bộ ----------

    @staticmethod
    def _open_capture(index: int) -> cv2.VideoCapture | None:
        """Thử lần lượt các backend vì mỗi webcam chỉ hợp với một loại."""
        for name in ("CAP_DSHOW", "CAP_MSMF", "CAP_ANY"):
            backend = getattr(cv2, name, None)
            if backend is None:
                continue
            capture = cv2.VideoCapture(index, backend)
            if capture.isOpened():
                logger.info("Mở webcam index=%s với backend %s", index, name)
                return capture
            capture.release()
        return None

    def _draw_overlay(self, frame, status_text: str):
        """Vẽ trạng thái lên khung hình xem trực tiếp (OpenCV không vẽ được dấu tiếng Việt)."""
        height, width = frame.shape[:2]
        cv2.putText(
            frame, status_text, (10, height - 12),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA,
        )
        cv2.putText(
            frame, status_text, (10, height - 12),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA,
        )

        if self._clip_lock.locked():  # đang ghi clip cảnh báo
            cv2.rectangle(frame, (0, 0), (width - 1, height - 1), (0, 0, 255), 3)
            cv2.putText(
                frame, "REC", (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2, cv2.LINE_AA,
            )
        return frame

    def _capture_loop(self) -> None:
        interval = 1.0 / max(1, self.config.fps)
        while not self._stop_event.is_set():
            started = time.monotonic()
            ok, frame = self._capture.read()
            if not ok:
                logger.warning("Không đọc được frame từ webcam")
                time.sleep(0.2)
                continue

            with self._buffer_lock:
                self._pre_buffer.append(frame)

            try:
                self._live_queue.put_nowait(frame)
            except queue.Full:
                pass  # không có ai đang ghi clip thì bỏ frame, tránh dồn bộ nhớ

            elapsed = time.monotonic() - started
            if elapsed < interval:
                time.sleep(interval - elapsed)

    # ---------- API công khai ----------

    def latest_frame(self):
        """Frame mới nhất đang có trong buffer."""
        with self._buffer_lock:
            return self._pre_buffer[-1] if self._pre_buffer else None

    def preview_loop(
        self,
        status_text: Callable[[], str],
        should_stop: Callable[[], bool],
    ) -> bool:
        """Hiện cửa sổ xem camera trực tiếp. Nhấn q hoặc ESC để thoát.

        Trả về False nếu không mở được cửa sổ (ví dụ máy không có màn hình).
        """
        try:
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        except cv2.error as exc:
            logger.error("Không mở được cửa sổ camera: %s", exc)
            return False

        try:
            while not should_stop():
                frame = self.latest_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue
                cv2.imshow(WINDOW_NAME, self._draw_overlay(frame.copy(), status_text()))
                key = cv2.waitKey(30) & 0xFF
                if key in (ord("q"), 27):
                    logger.info("Đã đóng cửa sổ camera, dừng hệ thống")
                    return True
        except cv2.error as exc:
            logger.error("Lỗi khi hiện cửa sổ camera: %s", exc)
            return False
        finally:
            cv2.destroyWindow(WINDOW_NAME)
            cv2.waitKey(1)
        return True

    def capture_event(self) -> tuple[Path | None, Path | None]:
        """Lưu 1 ảnh + 1 clip cho sự kiện cảnh báo.

        Trả về (đường_dẫn_ảnh, đường_dẫn_video); phần nào lỗi thì trả None.
        """
        if not self._clip_lock.acquire(blocking=False):
            logger.warning("Đang ghi clip cho cảnh báo trước đó, bỏ qua sự kiện này")
            return None, None

        try:
            with self._buffer_lock:
                pre_frames = list(self._pre_buffer)

            self._drain_live_queue()

            if not pre_frames:
                logger.warning("Buffer webcam rỗng, chưa kịp lấy frame nào")
                return None, None

            event_dir = self.output_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
            event_dir.mkdir(parents=True, exist_ok=True)

            snapshot_path = self._write_snapshot(event_dir, pre_frames[-1])
            video_path = self._write_clip(event_dir, pre_frames)
            return snapshot_path, video_path
        finally:
            self._clip_lock.release()

    def _drain_live_queue(self) -> None:
        while True:
            try:
                self._live_queue.get_nowait()
            except queue.Empty:
                return

    def _write_snapshot(self, event_dir: Path, frame) -> Path | None:
        path = event_dir / "snapshot.jpg"
        if cv2.imwrite(str(path), frame):
            logger.info("Đã lưu ảnh: %s", path)
            return path
        logger.error("Không ghi được ảnh %s", path)
        return None

    def _write_clip(self, event_dir: Path, pre_frames: list) -> Path | None:
        height, width = pre_frames[0].shape[:2]
        fps = max(1, self.config.fps)

        writer, path = self._open_writer(event_dir, width, height, fps)
        if writer is None:
            return None

        written = 0
        try:
            for frame in pre_frames:
                writer.write(frame)
                written += 1

            deadline = time.monotonic() + self.config.post_seconds
            while time.monotonic() < deadline and not self._stop_event.is_set():
                try:
                    frame = self._live_queue.get(timeout=0.5)
                except queue.Empty:
                    break
                writer.write(frame)
                written += 1
        finally:
            writer.release()

        logger.info("Đã lưu video: %s (%s frame)", path, written)
        return path

    def _open_writer(self, event_dir: Path, width: int, height: int, fps: int):
        for codec, extension in (("mp4v", ".mp4"), ("XVID", ".avi")):
            path = event_dir / f"clip{extension}"
            writer = cv2.VideoWriter(
                str(path), cv2.VideoWriter_fourcc(*codec), fps, (width, height)
            )
            if writer.isOpened():
                return writer, path
            writer.release()
        logger.error("Không tạo được VideoWriter cho clip cảnh báo")
        return None, None
