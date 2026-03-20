from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ndi_core.ndi_wrapper import NDIlib_video_frame_v2_t, NDIWrapper
from ndi_core.network_check import check_network_report, list_interfaces, pick_auto_interface


def _ndi_frame_to_qimage(frame: NDIlib_video_frame_v2_t) -> QImage | None:
    # FourCC for BGRX/BGRA (NDI uses little-endian BGRA layout)
    fourcc_bgrx = ord("B") | (ord("G") << 8) | (ord("R") << 16) | (ord("X") << 24)
    fourcc_bgra = ord("B") | (ord("G") << 8) | (ord("R") << 16) | (ord("A") << 24)
    if int(frame.FourCC) not in (fourcc_bgrx, fourcc_bgra):
        return None

    w = int(frame.xres)
    h = int(frame.yres)
    if w <= 0 or h <= 0:
        return None
    if not bool(frame.p_data):
        return None

    stride = int(frame.line_stride_in_bytes) if int(frame.line_stride_in_bytes) else w * 4
    size = max(stride * h, 0)
    if size == 0:
        return None

    # Copie mémoire (on ne peut pas garder frame.p_data après free)
    buf = ctypes.string_at(frame.p_data, size)

    # Format_ARGB32 correspond au layout BGRA en little-endian sur Windows.
    img = QImage(buf, w, h, stride, QImage.Format_ARGB32)
    return img.copy()


class _RefreshWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, ndi: NDIWrapper):
        super().__init__()
        self._ndi = ndi

    def run(self):
        try:
            sources = self._ndi.list_sources()
            self.finished.emit(sources)
        except Exception as e:
            self.failed.emit(str(e))


class _NetworkWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, ndi_iface: str | None, profile: str, duration_s: float):
        super().__init__()
        self._iface = ndi_iface
        self._profile = profile
        self._duration_s = duration_s

    def run(self):
        try:
            ok, report = check_network_report(self._duration_s, self._profile, self._iface)
            # On renvoie le report (inclut OK/KO).
            self.finished.emit(report)
        except Exception as e:
            self.failed.emit(str(e))


class PreviewDialog(QDialog):
    def __init__(self, parent: QWidget, ndi: NDIWrapper, source: dict[str, str], recv, initial_img: QImage | None):
        super().__init__(parent)
        self.setWindowTitle(f"Preview — {source.get('name', '')}")
        self.setMinimumSize(960, 540)

        self._ndi = ndi
        self._source = source
        self._recv = recv
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._image_label = QLabel(self)
        self._image_label.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.addWidget(self._image_label)

        if initial_img is not None:
            self._set_image(initial_img)

        self._timer.start(int(1000 / 15))

    def _set_image(self, img: QImage):
        if img is None or img.isNull():
            return
        pix = QPixmap.fromImage(img)
        # Scale doux pour limiter les artefacts.
        pix = pix.scaled(self._image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_label.setPixmap(pix)

    def resizeEvent(self, event):
        # Re-scale sur resize.
        super().resizeEvent(event)
        pix = self._image_label.pixmap()
        if pix is None:
            return
        # Ne pas scaler à chaque frame, seulement lors du resize.

    def _tick(self):
        if not self._recv:
            return
        frame, frame_type = self._ndi.capture_video_frame(self._recv, timeout_ms=50)
        if frame is None:
            return
        try:
            if frame_type == 1:
                img = _ndi_frame_to_qimage(frame)
                if img is not None:
                    self._set_image(img)
        finally:
            self._ndi.free_video_frame(self._recv, frame)

    def closeEvent(self, event):
        try:
            if self._timer:
                self._timer.stop()
        except Exception:
            pass
        try:
            if self._recv:
                self._ndi.destroy_receiver(self._recv)
        except Exception:
            pass
        self._recv = None
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NDI Manager (Windows / PySide6)")
        self.resize(1100, 600)

        self._ndi = NDIWrapper()
        self._sources: list[dict[str, str]] = []

        self._list = QListWidget(self)

        self._refresh_btn = QPushButton("Refresh NDI", self)
        self._preview_btn = QPushButton("Preview", self)
        self._network_btn = QPushButton("Check Network", self)

        self._profile_combo = QComboBox(self)
        self._profile_combo.addItems(["NDI 720p", "NDI 1080p", "NDI 1080p (safe)", "NDI 4K"])

        self._iface_combo = QComboBox(self)
        self._iface_combo.addItems(["Auto interface", "Total machine"])
        for iface in list_interfaces():
            self._iface_combo.addItem(iface)

        self._network_result = QLabel(self)
        self._network_result.setWordWrap(True)
        self._network_result.setText("")

        left = QVBoxLayout()
        left.addWidget(QLabel("Sources NDI"))
        left.addWidget(self._list)
        left_widget = QWidget()
        left_widget.setLayout(left)

        right = QVBoxLayout()
        right.addWidget(QLabel("Network test"))
        right.addWidget(QLabel("Interface"))
        right.addWidget(self._iface_combo)
        right.addWidget(QLabel("Profile"))
        right.addWidget(self._profile_combo)
        right.addWidget(self._network_btn)
        right.addWidget(self._network_result)
        right.addSpacing(16)
        right.addWidget(self._refresh_btn)
        right.addWidget(self._preview_btn)

        right_widget = QWidget()
        right_widget.setLayout(right)

        main = QHBoxLayout()
        main.addWidget(left_widget, 1)
        main.addWidget(right_widget, 0)

        root = QWidget()
        root.setLayout(main)
        self.setCentralWidget(root)

        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        self._preview_btn.clicked.connect(self._on_preview_clicked)
        self._network_btn.clicked.connect(self._on_network_clicked)

        self._on_refresh_clicked()

    def _selected_source(self) -> dict[str, str] | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._sources):
            return None
        return self._sources[row]

    def _populate_sources(self, sources: list[dict[str, str]]):
        self._sources = sources
        self._list.clear()
        for s in sources:
            name = s.get("name", "")
            ip = s.get("ip", "")
            text = f"{name} ({ip})" if ip else name
            it = QListWidgetItem(text)
            self._list.addItem(it)

    def _on_refresh_clicked(self):
        self._refresh_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)

        self._thread = QThread(self)
        self._worker = _RefreshWorker(self._ndi)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_sources_ready)
        self._worker.failed.connect(self._on_sources_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_sources_ready(self, sources: list[dict[str, str]]):
        self._populate_sources(sources)
        self._refresh_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)

    def _on_sources_error(self, err: str):
        QMessageBox.critical(self, "NDI error", err)
        self._refresh_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)

    def _resolve_iface_for_test(self) -> str | None:
        choice = self._iface_combo.currentText()
        if choice == "Total machine":
            return None
        if choice == "Auto interface":
            return pick_auto_interface()
        return choice

    def _on_network_clicked(self):
        self._network_btn.setEnabled(False)
        self._network_result.setText("Running network test...")

        iface = self._resolve_iface_for_test()
        profile = self._profile_combo.currentText()

        # Durée courte par défaut, comme dans la version mac.
        duration_s = 3.0

        self._net_thread = QThread(self)
        self._net_worker = _NetworkWorker(iface, profile, duration_s)
        self._net_worker.moveToThread(self._net_thread)
        self._net_thread.started.connect(self._net_worker.run)
        self._net_worker.finished.connect(self._on_network_done)
        self._net_worker.failed.connect(self._on_network_failed)
        self._net_worker.finished.connect(self._net_thread.quit)
        self._net_worker.failed.connect(self._net_thread.quit)
        self._net_thread.finished.connect(self._net_thread.deleteLater)
        self._net_thread.start()

    def _on_network_done(self, report: str):
        self._network_result.setText(report)
        self._network_btn.setEnabled(True)

    def _on_network_failed(self, err: str):
        QMessageBox.critical(self, "Network test error", err)
        self._network_btn.setEnabled(True)

    def _on_preview_clicked(self):
        source = self._selected_source()
        if not source:
            QMessageBox.information(self, "Preview", "Sélectionne une source NDI dans la liste.")
            return

        recv = None
        try:
            # Warmup : on évite de lancer un receive "live" si pas de video.
            recv = self._ndi.create_receiver(source)
            initial_img = None
            for _ in range(10):
                frame, frame_type = self._ndi.capture_video_frame(recv, timeout_ms=400)
                if frame is None:
                    continue
                try:
                    if frame_type == 1:
                        initial_img = _ndi_frame_to_qimage(frame)
                        if initial_img is not None:
                            break
                finally:
                    self._ndi.free_video_frame(recv, frame)

            if initial_img is None:
                self._ndi.destroy_receiver(recv)
                recv = None
                QMessageBox.information(
                    self,
                    "Preview",
                    "Aucune image NDI capturée (timeout ou format non supporté).",
                )
                return

            dlg = PreviewDialog(self, self._ndi, source, recv, initial_img)
            dlg.exec()
        except Exception as e:
            if recv:
                try:
                    self._ndi.destroy_receiver(recv)
                except Exception:
                    pass
            QMessageBox.critical(self, "Preview error", str(e))


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

