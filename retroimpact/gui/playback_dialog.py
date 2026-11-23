"""View > Print-head playback...: watch the current sheet being printed, with the head sweeping and the paper feeding."""
from __future__ import annotations

import os
import tempfile
import time

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QProgressDialog, QPushButton, QSlider, QVBoxLayout)

from ..playback import (PagePlayer, auto_speed, export_gif, export_mp4, ffmpeg_path, synth_audio, write_wav)

SPEEDS = [1, 2, 5, 10, 25, 50, 100]
RATE = 22050


class PlaybackDialog(QDialog):
    def __init__(self, parent, doc, page_index: int, settings):
        super().__init__(parent)
        self.setWindowTitle(f"Print-head playback - page {page_index + 1}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(900, 560)
        self.t = 0.0
        self.playing = False
        self.last_clock = 0.0
        self._last_arr = None
        self._effect = None
        self._wav_path = None

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.player = PagePlayer(doc, doc.pages[page_index], settings, dpi=96)
        finally:
            QApplication.restoreOverrideCursor()
        self.auto = auto_speed(self.player.duration)

        lay = QVBoxLayout(self)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setMinimumSize(320, 160)
        self.view.setStyleSheet("background: #2b2d31;")
        lay.addWidget(self.view, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._seek)
        lay.addWidget(self.slider)

        row = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.toggle)
        row.addWidget(self.play_btn)
        rewind = QPushButton("Rewind")
        rewind.clicked.connect(lambda: self._seek_time(0.0))
        row.addWidget(rewind)
        self.time_lbl = QLabel("")
        row.addWidget(self.time_lbl)
        row.addStretch(1)
        row.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        for sp in SPEEDS:
            self.speed.addItem(f"{sp}x", sp)
        self.speed.setCurrentIndex(min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - self.auto)))
        self.speed.currentIndexChanged.connect(self._speed_changed)
        row.addWidget(self.speed)
        self.follow = QCheckBox("Follow the head")
        self.follow.setChecked(True)
        self.follow.toggled.connect(lambda _on: self._show())
        row.addWidget(self.follow)
        self.sound = QCheckBox("Sound")
        self.sound.setChecked(False)
        self.sound.setEnabled(self._have_audio())
        self.sound.setToolTip("Synthesised printer noise" if self.sound.isEnabled() else "Qt Multimedia is not available")
        self.sound.toggled.connect(lambda on: self._restart_sound() if self.playing else None)
        row.addWidget(self.sound)
        lay.addLayout(row)

        row = QHBoxLayout()
        gif = QPushButton("Export GIF…")
        gif.clicked.connect(lambda: self._export("gif"))
        row.addWidget(gif)
        mp4 = QPushButton("Export MP4 with sound…")
        mp4.clicked.connect(lambda: self._export("mp4"))
        mp4.setEnabled(ffmpeg_path() is not None)
        mp4.setToolTip("" if ffmpeg_path() else "MP4 export needs ffmpeg on the PATH")
        row.addWidget(mp4)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)

        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self._show()

    # -------------------------------------------------------------------------------------------- playing
    def _speed_value(self) -> float:
        return float(self.speed.currentData())

    def toggle(self):
        self.playing = not self.playing
        if self.playing:
            if self.t >= self.player.duration:
                self.t = 0.0
            self.last_clock = time.monotonic()
            self.timer.start()
            self._restart_sound()
        else:
            self.timer.stop()
            self._stop_sound()
        self.play_btn.setText("Pause" if self.playing else "Play")

    def _tick(self):
        now = time.monotonic()
        self.t = min(self.player.duration, self.t + (now - self.last_clock) * self._speed_value())
        self.last_clock = now
        self._show()
        if self.t >= self.player.duration:
            self.toggle()

    def _seek(self, v: int):
        self._seek_time(self.player.duration * v / 1000.0)

    def _seek_time(self, t: float):
        self.t = min(max(0.0, t), self.player.duration)
        self.last_clock = time.monotonic()
        self._show()
        if self.playing:
            self._restart_sound()

    def _speed_changed(self, _i):
        if self.playing:
            self._restart_sound()

    def _show(self):
        arr = self.player.frame(self.t, follow=self.follow.isChecked())
        self._last_arr = arr
        self._paint(arr)
        d = self.player.duration
        self.slider.blockSignals(True)
        self.slider.setValue(int(1000 * self.t / d) if d else 0)
        self.slider.blockSignals(False)
        self.time_lbl.setText(f"  {self.t:5.1f} s / {d:.0f} s (real printer time)")

    def _paint(self, arr):
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        pm = QPixmap.fromImage(img).scaled(self.view.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
        self.view.setPixmap(pm)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._last_arr is not None:
            self._paint(self._last_arr)

    def closeEvent(self, ev):
        self.timer.stop()
        self._stop_sound()
        if self._wav_path:
            try:
                os.unlink(self._wav_path)
            except OSError:
                pass
        super().closeEvent(ev)

    # -------------------------------------------------------------------------------------------- sound (optional)
    @staticmethod
    def _have_audio() -> bool:
        try:
            from PySide6.QtMultimedia import QSoundEffect  # noqa: F401
            return True
        except Exception:
            return False

    def _stop_sound(self):
        if self._effect is not None:
            try:
                self._effect.stop()
            except Exception:
                pass

    def _restart_sound(self):
        self._stop_sound()
        if not (self.sound.isChecked() and self.sound.isEnabled() and self.playing):
            return
        try:
            from PySide6.QtMultimedia import QSoundEffect

            speed = self._speed_value()
            samples = synth_audio(self.player.timeline, speed, RATE)
            samples = samples[int(self.t / speed * RATE):]  # start where the picture is
            if len(samples) < 100:
                return
            if self._wav_path is None:
                fd, self._wav_path = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
            write_wav(self._wav_path, samples, RATE)
            if self._effect is None:
                self._effect = QSoundEffect(self)
            self._effect.setSource(QUrl.fromLocalFile(self._wav_path))
            self._effect.setVolume(0.7)
            self._effect.play()
        except Exception:
            self.sound.setChecked(False)
            self.sound.setEnabled(False)

    # -------------------------------------------------------------------------------------------- export
    def _export(self, kind: str):
        was = self.playing
        if was:
            self.toggle()
        speed = self._speed_value()
        ext = ".gif" if kind == "gif" else ".mp4"
        out, _ = QFileDialog.getSaveFileName(self, "Export playback", f"printing{ext}",
                                             "GIF animation (*.gif)" if kind == "gif" else "MP4 video (*.mp4)")
        if not out:
            return
        n_frames = int(self.player.duration / speed * (15 if kind == "gif" else 30)) + 1
        dlg = QProgressDialog("Making the animation…", "Cancel", 0, n_frames, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)

        def prog(i, n):
            dlg.setMaximum(n)
            dlg.setValue(i)
            QApplication.processEvents()
            return not dlg.wasCanceled()

        try:
            follow = self.follow.isChecked()
            if kind == "gif":
                export_gif(self.player, out, speed, 15, follow, prog)
            else:
                export_mp4(self.player, out, speed, 30, follow, True, prog)
            dlg.setValue(dlg.maximum())
        except InterruptedError:
            try:
                os.unlink(out)
            except OSError:
                pass
        except Exception as e:
            dlg.close()
            QMessageBox.warning(self, "Export playback", f"Could not make the animation:\n{e}")


__all__ = ["PlaybackDialog"]
