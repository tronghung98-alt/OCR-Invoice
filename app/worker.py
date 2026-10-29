"""
Vòng lặp AI chạy liên tục trong nền (một luồng riêng của server):
  có hóa đơn chờ  -> nhận, đọc, tự kiểm tra, xuất Excel, báo cáo (services.process_request)
  không có        -> chờ WORKER_POLL_SECONDS giây, hoặc tới khi có người gửi hóa đơn mới
Mỗi lần chỉ đọc MỘT hóa đơn để không vượt giới hạn gọi Gemini.
"""
import logging
import threading

from . import config, services
from .db import SessionLocal

log = logging.getLogger("invoice.worker")


class AIWorker:
    def __init__(self):
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> bool:
        """Xử lý một phiếu nếu có. Trả về True nếu đã làm việc."""
        with SessionLocal() as db:
            rid = services.claim_next(db)
        if rid is None:
            return False
        log.info("AI nhận phiếu #%s", rid)
        services.process_request(rid)
        return True

    def _loop(self):
        log.info("Vòng lặp AI đã chạy (kiểm tra hàng đợi mỗi %ss).", config.WORKER_POLL_SECONDS)
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # noqa: BLE001 - vòng lặp không được chết
                log.exception("Lỗi trong vòng lặp AI")
                worked = False
            if not worked:
                self._wake.wait(config.WORKER_POLL_SECONDS)
                self._wake.clear()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ai-worker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def notify(self):
        """Có hóa đơn mới / yêu cầu đọc lại -> đánh thức ngay, không chờ hết chu kỳ."""
        self._wake.set()

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


worker = AIWorker()
