# main_control_gui.py — Transparent buttons over video background (all buttons wired) + Title PNG overlay
# -*- coding: utf-8 -*-
import sys, os, math, json, sqlite3, csv, queue as _q
from datetime import datetime
from typing import Dict, Optional, Any, List, Tuple

from PyQt6.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel, QLineEdit, QMessageBox, QFileDialog,
    QDialog, QVBoxLayout, QHBoxLayout, QGraphicsDropShadowEffect
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QRadialGradient, QPainterPath,
    QImage, QPixmap, QFont, QGuiApplication, QIcon
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QUrl, QSize, QPoint, QRect, QEvent
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink

# ======= App windows =======
from MagneticFieldMonitoringWindow import MagneticFieldMonitoringWindow
from MagneticFieldControllerWindow import MagneticFieldControllerWindow
from sqlite_viewer_qt import SQLiteLogViewer
from snapshot_recall_qt import SnapshotRecallDialog
from SourceExtractionMonitoringWindow import SourceExtractionMonitoringWindow
from VacuumBeamMonitoringWindow import VacuumBeamMonitoringWindow
from BeamTransportMonitoringWindow import BeamTransportMonitoringWindow
from scaling import Scaler
from ScaleCalWindow import ScaleCalWindow
from SettingsDialog import SettingsDialog
from alarm import alarm_manager
from app_settings import app_settings
from beam_range import BeamRangePad, RangeCodec
from SmallScreenMagneticBeam import SmallScreenMagneticBeamWindow
import beam_trace; beam_trace.install()


# =================== switches ===================
HIDE_TEXT          = True     # hide title label + bubble labels + note box
DISABLE_PNG_BG     = True     # True => pure dark bg (no white PNG arcs)
DRAW_SPIRAL        = True     # center multi-electron system
DRAW_ELECTRONS     = True     # master for center electrons AND button halos
SHOW_CORE_VIDEO    = True     # play your MP4 in the core
BACKGROUND_DIM     = 0.10

DRAW_BUTTON_ELECTRONS = DRAW_ELECTRONS

# --- title image overlay (drawn on top, centered at the very end) ---
SHOW_TITLE_IMAGE       = True
TITLE_IMAGE_PATH       = "cnl_title.png"   # put the PNG next to this file, or use a full path
TITLE_TOP_MARGIN_PX    = 0                # distance from top edge
TITLE_MAX_WIDTH_FRAC   = 0.6              # up to 50% of window width
TITLE_MAX_HEIGHT_FRAC  = 0.26              # up to 16% of window height

# =================== Assets / Style ===================
BACKGROUND_IMAGE     = r"C:\Users\clasa\Downloads\background_thinned.png"
ANIM_BG_VIDEO        = "gui_bg.mp4"  # <— your video file here (or use a full path)
VIDEO_SMOOTH         = True
CORE_RADIUS_FRAC     = 0.125
VIDEO_FILL           = 0.92
VIDEO_CONTENT_SCALE  = 0.72

HERE     = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")

DEFAULT_DB_PATH = "cyclotron_data.db"
TABLE_NAME      = "channel_data"
TIME_COL        = "timestamp"
EXPECTED_COLS   = [
    "ch1","ch2","ch3","ch4","ch5","ch6","ch7","ch8","ch9","ch10","ch11","ch12",
    "main_magnet","centering_beam",
    "arc_voltage","arc_current","filament","esd_kv","esd_ma","outside_iron","inside_iron"
]
SNAPSHOT_XLSX   = "trimcoil_snapshots.xlsx"
SNAPSHOT_CSV    = "trimcoil_snapshots.csv"
LV_EPOCH_OFFSET = 2082844800

# =================== Center-orbit electrons ===================
SPIRAL = {
    "r0":   0.02,
    "rmax": 0.36,
    "turns": 5.1,
    "growth": 1.08,
    "count": 12,        # how many electrons on the center orbits
    "speed": 0.75,
    "front_gain": 1.00,
    "back_gain": 0.60,
    "guide_alpha": 58
}
COORDINATED_SPIRAL = True
COORD_SPACING      = 1.0 / max(1, SPIRAL["count"])
ELLIPSE = {"ax": 1.85, "by": 0.38, "angle_deg": 0.0, "dx": 0.00, "dy": 0.00}
SPIRAL_TRAIL = {"enabled": True, "back_alpha": 28, "front_alpha": 46, "width": 1.6, "length": 0.09}

def qmix(a: QColor, b: QColor, t: float, alpha: Optional[int]=None) -> QColor:
    t = max(0.0, min(1.0, t))
    q = QColor(
        int(a.red()*(1-t) + b.red()*t),
        int(a.green()*(1-t) + b.green()*t),
        int(a.blue()*(1-t) + b.blue()*t),
    )
    if alpha is not None: q.setAlpha(alpha)
    return q

PALETTES = [
    {"name":"cyan",   "rim":QColor(72,205,255),  "rim2":QColor(140,235,255), "tint":QColor(30,110,150), "label":QColor(210,240,255)},
    {"name":"indigo", "rim":QColor(120,150,255), "rim2":QColor(170,200,255), "tint":QColor(35,45,110),  "label":QColor(220,230,255)},
    {"name":"teal",   "rim":QColor(0,230,200),   "rim2":QColor(140,255,230), "tint":QColor(10,90,80),   "label":QColor(200,255,245)},
    {"name":"violet", "rim":QColor(200,140,255), "rim2":QColor(230,190,255), "tint":QColor(65,40,100),  "label":QColor(235,225,255)},
]
BUBBLE_ALPHA   = 0.80
MIN_BUBBLE_PX  = 64

LABEL_FONT = QFont("Orbitron, Exo 2, Rajdhani, Segoe UI Semibold, Arial", 12, QFont.Weight.DemiBold)
try:
    LABEL_FONT.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 102)
except Exception:
    pass

TITLE = {
    "enabled": False if HIDE_TEXT else True,
    "text": "Crocker Nuclear Laboratory",
    "family": "Orbitron, Exo 2, Rajdhani, Segoe UI Semibold, Arial",
    "size_pt": 28,
    "weight": int(QFont.Weight.Bold),
    "color": QColor(180, 235, 255, 255),
    "glow":  QColor(80, 210, 255, 140),
    "shadow_blur": 20,
    "top_margin_px": 10
}

def _parse_db_time(val) -> datetime:
    try:
        fv = float(val)
        if fv > 3_000_000_000: fv -= LV_EPOCH_OFFSET
        return datetime.fromtimestamp(fv)
    except Exception:
        pass
    s = str(val).replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y/%m/%d %H:%M:%S"):
        try: return datetime.strptime(s, fmt)
        except Exception: continue
    return datetime.fromisoformat(s)

# ---------- Simple windows kept (unchanged) ----------
class ControllerWindow(QWidget):
    def __init__(self, title):
        super().__init__()
        self.setWindowTitle(title)
        self.setGeometry(300, 300, 400, 200)
        self.setStyleSheet("background-color: #222; color: white;")
        label = QLabel(f"{title} - Controller Window", self)
        label.setFont(QFont("Arial", 14))
        label.setGeometry(50, 80, 300, 40)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.show()

class BeamRangeDialog(QDialog):
    def __init__(self, parent, base_bitmask_u64: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Beam Range")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet("background:#0d1321; color:#e6faff;")
        self.pad = BeamRangePad(base_bitmask_u64=base_bitmask_u64, use_onehot_also=False, mirror_nibble=True)
        lay = QVBoxLayout(self); lay.addWidget(self.pad)
        row = QHBoxLayout(); row.addStretch(1)
        btnClose = QPushButton("Close")
        btnClose.setStyleSheet(
            "QPushButton{background:#0f2230;border:1px solid #66d6ff;border-radius:8px;"
            "padding:6px 14px;color:#bfefff;} QPushButton:hover{background:#114b5f;color:#e6ffff;}"
        )
        btnClose.clicked.connect(self.accept); row.addWidget(btnClose); lay.addLayout(row)

# ---------- Transparent Bubbles (icons only) & Halos ----------
class BubbleButton(QPushButton):
    def __init__(self, index: int, conf: Dict[str,Any], parent: "MainControlWindow"):
        super().__init__("", parent)
        self.index = index
        self.parent_win = parent
        self.title = conf.get("title","")
        self._icon_pm = QPixmap(conf.get("icon") or "") if conf.get("icon") else QPixmap()
        self.group = int(conf.get("group", 0)) % 4
        self.palette = PALETTES[self.group]

        # transparent overlay button (no rims/fills)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setMinimumSize(40, 40)

        self._hover = False
        self._pulse = 0.0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(33)

    def _tick(self):
        target = 1.0 if self._hover else 0.0
        self._pulse += (target - self._pulse) * 0.2
        if abs(target - self._pulse) > 0.01: self.update()

    def sizeHint(self) -> QSize: return QSize(96, 96)

    def paintEvent(self, ev):
        # draw ONLY the icon (transparent button)
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing |
                         QPainter.RenderHint.SmoothPixmapTransform |
                         QPainter.RenderHint.TextAntialiasing)
        if not self._icon_pm.isNull():
            w, h = self.width(), self.height()
            r = int(min(w, h) * 0.90)
            pm = self._icon_pm.scaled(r, r,
                                      Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
            p.setOpacity(1.0)
            p.drawPixmap((w - pm.width())//2, (h - pm.height())//2, pm)

class ElectronHalo(QWidget):
    def __init__(self, target_btn: QPushButton, parent: QWidget,
                 electron_count: int = 2, orbits: int = 1, electrons_per_orbit: int | None = None,
                 tilt_deg: float = 28.0, orbit_scales: List[float] | None = None,
                 base_scale: float = 1.0, shrink_step: float = 0.12,
                 speed: float = 0.035, trail_len: float = 0.13, trail_w: float = 1.4):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._btn = target_btn
        self._orbits = max(1, int(orbits))
        if electrons_per_orbit is None:
            per = max(1, int(round(electron_count / self._orbits)))
            self._e_per = [per] * self._orbits
        else:
            self._e_per = [max(1, int(electrons_per_orbit))] * self._orbits

        self._t = 0.0
        self._speed = float(speed)
        self._pad = 10
        self._tilt = math.radians(float(tilt_deg))
        self._depth_gain = 0.22
        self._trail_len = float(trail_len)
        self._trail_w = float(trail_w)
        self._orbit_scales = list(orbit_scales) if orbit_scales else None
        self._base_scale = float(base_scale)
        self._shrink_step = float(shrink_step)

        self._btn.installEventFilter(self)
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(16)
        self._sync(); self.show()

    def eventFilter(self, watched, ev):
        if watched is self._btn and ev.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
            self._sync()
        return False

    def _sync(self):
        if not self._btn.isVisible():
            self.hide(); return
        r = self._btn.geometry()
        self.setGeometry(r.adjusted(-self._pad, -self._pad, +self._pad, +self._pad))
        self.show(); self.update()

    def _tick(self):
        self._t += self._speed
        if self.isVisible(): self.update()

    def paintEvent(self, _):
        if not DRAW_BUTTON_ELECTRONS:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w/2.0, h/2.0

        a0 = max(24.0, (w-10)/2.0)
        b0 = max(18.0, (h-10)/2.0)
        b0_tilt = b0 * math.cos(self._tilt)

        def draw_pass(front: bool):
            for ring, ecount in enumerate(self._e_per):
                scale = max(0.05, 1.0 - self._shrink_step * ring)
                a = a0 * scale
                b = b0_tilt * scale

                p.setPen(QPen(QColor(0, 210, 255, 70), 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), a, b)

                for k in range(ecount):
                    ang = self._t + (k * (2*math.pi / max(1, ecount))) + ring*0.35
                    z = math.sin(ang)
                    is_front = (z >= 0.0)
                    if is_front != front:
                        continue

                    a_i = a * 0.98
                    b_i = b * 0.98
                    x = cx + a_i * math.cos(ang)
                    y = cy + b_i * math.sin(ang)

                    depth = 1.0 + z * 0.22
                    core_r = 3.4 * depth
                    glow_r = 6.2 * depth

                    steps = 36
                    tmax = int(steps * 0.13)
                    if tmax > 0:
                        path = QPainterPath()
                        first = True
                        for s in range(tmax, -1, -1):
                            ta = ang - (s * (2*math.pi / steps))
                            tz = math.sin(ta)
                            if (tz >= 0.0) != front:
                                continue
                            tx = cx + a_i * math.cos(ta)
                            ty = cy + b_i * math.sin(ta)
                            path.moveTo(tx, ty) if first else path.lineTo(tx, ty)
                            first = False
                        if not first:
                            p.setPen(QPen(QColor(120, 230, 255, 70 if front else 40), 1.4))
                            p.setBrush(Qt.BrushStyle.NoBrush)
                            p.drawPath(path)

                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(0, 255, 240, 70 if front else 40))
                    p.drawEllipse(QPointF(x, y), glow_r, glow_r)
                    p.setBrush(QColor(140, 232, 255, 150 if front else 90))
                    p.drawEllipse(QPointF(x, y), core_r, core_r)

        draw_pass(front=False)
        draw_pass(front=True)

# =================== Main window ===================
class MainControlWindow(QWidget):
    def __init__(self, data_queue=None, target_values=None, control_queue=None, plot_config=None):
        super().__init__()
        self.data_queue    = data_queue
        self.target_values = target_values
        self.control_queue = control_queue
        self.plot_config   = plot_config

        self.mag_monitor = None; self._src_monitor = None; self._vac_monitor = None
        self._transport_monitor = None; self.field_controller = None
        self._sqlite_viewer = None; self._scale_window = None
        self._generic_windows = []

        self.db_path = DEFAULT_DB_PATH
        self.scaler = Scaler()

        self._range_dialog: Optional[BeamRangeDialog] = None
        self._bitmask_u64: int = 0

        self.setWindowTitle("Cyclotron Control System — Neon")
        self.setGeometry(100, 100, 1280, 768)
        self.setStyleSheet("background:black; color:white;")

        # background image (disabled to avoid white arcs)
        self.bg_pixmap = QPixmap() if DISABLE_PNG_BG else (QPixmap(BACKGROUND_IMAGE) if os.path.exists(BACKGROUND_IMAGE) else QPixmap())

        # center video (your MP4)
        self._video_player = QMediaPlayer(self)
        self._video_audio  = QAudioOutput(self); self._video_audio.setMuted(True)
        self._video_player.setAudioOutput(self._video_audio)
        this_sink = QVideoSink(self); self._video_sink = this_sink
        self._video_player.setVideoSink(this_sink)
        this_sink.videoFrameChanged.connect(self._on_frame)
        self._current_frame: Optional[QImage] = None
        if SHOW_CORE_VIDEO and os.path.exists(ANIM_BG_VIDEO):
            self._video_player.setSource(QUrl.fromLocalFile(os.path.abspath(ANIM_BG_VIDEO)))
            try:
                self._video_player.setLoops(QMediaPlayer.Loops.Infinite)
            except Exception:
                self._video_player.mediaStatusChanged.connect(
                    lambda s: self._video_player.play() if s == QMediaPlayer.MediaStatus.EndOfMedia else None
                )
            self._video_player.play()

        # Title PNG (optional)
        self._title_pm = QPixmap(TITLE_IMAGE_PATH) if (SHOW_TITLE_IMAGE and os.path.exists(TITLE_IMAGE_PATH)) else QPixmap()

        # FX timers
        self._fx_phase = 0.0
        self._fx_timer = QTimer(self); self._fx_timer.timeout.connect(self._tick_fx); self._fx_timer.start(33)

        # fan-out queues
        self.mag_queue = _q.Queue(); self.src_queue = _q.Queue(); self.vac_queue = _q.Queue()
        self._pump_timer = QTimer(self); self._pump_timer.timeout.connect(self._pump_data); self._pump_timer.start(50)

        # (title and note box omitted in textless mode)
        self.note_edit: Optional[QLineEdit] = None
        self.title_label: Optional[QLabel] = None
        if TITLE["enabled"]:
            self._build_title()

        # bubble icons (transparent buttons)
        self.bubble_confs: List[Dict[str, Any]] = [
            {"title":"Magnetic\nField", "icon": os.path.join(ICON_DIR, "icon_magnetic_field_3d.png"), "x":0.075, "y":0.18, "size":0.13, "group":0},
            {"title":"Beam \n Source & Extraction","icon": os.path.join(ICON_DIR, "icon_beam_extractor_3d.png"), "x":0.1964, "y":0.18, "size":0.12, "group":0},
            {"title":"Beam Transportation","icon": os.path.join(ICON_DIR, "icon_beam_transportation_3d.png"),"x":0.075,"y":0.36,"size":0.12,"group":0},
            {"title":"Vac/Beam",        "icon": os.path.join(ICON_DIR, "icon_vacuum_pumps_3d.png"), "x":0.8036,"y":0.18,"size":0.13,"group":1},
            {"title":"RF Power",        "icon": os.path.join(ICON_DIR, "icon_rf_system_3d.png"),    "x":0.9250,"y":0.18,"size":0.13,"group":1},
            {"title":"SQLite",          "icon": os.path.join(ICON_DIR, "icon_SQLite_3d.png"),       "x":0.9250,"y":0.36,"size":0.13,"group":1},
            {"title":"Field Ctrl",      "icon": os.path.join(ICON_DIR, "icon_controllers_3d.png"),  "x":0.5,"y":0.82,"size":0.16,"group":1},
            {"title":"Snapshot",        "icon": os.path.join(ICON_DIR, "icon_snapshot_3d.png"),     "x":0.9250,"y":0.82,"size":0.13,"group":2},
            {"title":"Recall",          "icon": os.path.join(ICON_DIR, "icon_recall_3d.png"),       "x":0.9250,"y":0.66,"size":0.13,"group":2},
            {"title":"Scaling",         "icon": os.path.join(ICON_DIR, "icon_scaling_3d.png"),      "x":0.8036,"y":0.82,"size":0.13,"group":2},
            {"title":"Beam Range",      "icon": os.path.join(ICON_DIR, "icon_beam_range_3d.png"),   "x":0.0750,"y":0.82,"size":0.13,"group":3},
            {"title":"Alarm",           "icon": os.path.join(ICON_DIR, "icon_alarm_3d.png"),        "x":0.1964,"y":0.82,"size":0.13,"group":3},
            {"title":"Settings",        "icon": os.path.join(ICON_DIR, "icon_settings_3d.png"),     "x":0.0750,"y":0.66,"size":0.13,"group":3},
        ]
        self.bubbles: List[Tuple[QPushButton, QLabel]] = []
        self._build_bubbles()
        self._layout_all_bubbles()

        # hide all text (title/labels/note)
        if HIDE_TEXT:
            if self.title_label: self.title_label.hide()
            if hasattr(self, "note_edit") and self.note_edit: self.note_edit.hide()
            for btn, lab in self.bubbles:
                if lab: lab.hide()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---------- Title (not used in textless mode) ----------
    def _build_title(self):
        self.title_label = QLabel(TITLE["text"], self)
        f = QFont(TITLE["family"], TITLE["size_pt"], TITLE["weight"])
        self.title_label.setFont(f)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        c = TITLE["color"]
        self.title_label.setStyleSheet(f"background: transparent; color: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()});")
        fx = QGraphicsDropShadowEffect(self); fx.setBlurRadius(TITLE["shadow_blur"]); fx.setOffset(0, 0); fx.setColor(TITLE["glow"])
        self.title_label.setGraphicsEffect(fx)

    def _layout_title(self):
        if not self.title_label: return
        W, _H = self.width(), self.height()
        fm = self.title_label.fontMetrics(); h = fm.height() + 6
        y = int(TITLE["top_margin_px"])
        self.title_label.setGeometry(0, y, W, h); self.title_label.raise_()

    # ---------- Data pump ----------
    def _pump_data(self):
        if self.data_queue is None: return
        while not self.data_queue.empty():
            sample = self.data_queue.get()
            self.mag_queue.put(sample if not isinstance(sample, dict) else dict(sample))
            self.src_queue.put(sample if not isinstance(sample, dict) else dict(sample))
            self.vac_queue.put(sample if not isinstance(sample, dict) else dict(sample))
            self._maybe_follow_range(sample)

    def _maybe_follow_range(self, sample):
        """Auto-select beam mode and update range highlight based on incoming LabVIEW bitmask."""
        try:
            if not isinstance(sample, dict):
                return

            maybe_idx = None
            for k in ("beam_range_idx", "range_idx"):
                if k in sample:
                    maybe_idx = int(sample[k])
                    break

            bm_val = None
            for k in ("bitmask_u64", "bitmask_double", "bitmask"):
                if k in sample:
                    bm_val = sample[k]
                    break

            if bm_val is None and maybe_idx is None:
                return

            if bm_val is not None:
                self._bitmask_u64 = RangeCodec.double_to_u64(float(bm_val))

        # --------------------------
        # AUTO MODE SWITCHING LOGIC
        # --------------------------
            low4 = self._bitmask_u64 & 0xF
            from beam_cal import beam_cal, save_cfg as save_beam_cfg

            if low4 == 11:
                beam_cal.cfg.select_mode = "digital"
                save_beam_cfg(beam_cal.cfg)
                print("[BeamCal] AUTO → DIGITAL mode (U64 nibble=11)")
            elif 1 <= low4 <= 10:
                beam_cal.cfg.select_mode = "manual"
                beam_cal.cfg.manual_index = low4 - 1
                save_beam_cfg(beam_cal.cfg)
                print(f"[BeamCal] AUTO → MANUAL idx={low4-1} (U64 nibble={low4})")

        # ---- Follow updates in UI ----
            if self._range_dialog is not None:
                pad = self._range_dialog.pad
                pad.set_base_bitmask(self._bitmask_u64)
                pad.follow_from_u64(self._bitmask_u64, explicit_idx=maybe_idx)

            if self.field_controller and hasattr(self.field_controller, "request_beam_range_change"):
                idx_follow = maybe_idx if maybe_idx is not None else RangeCodec.parse(self._bitmask_u64, None)
                self.field_controller.request_beam_range_change(int(idx_follow), int(self._bitmask_u64))

        except Exception as e:
            print("[BeamCal] follow_range error:", e)


    # ---------- Window openers (all wired) ----------
    def _safe_show_window(self, attr_name: str, factory):
        w = getattr(self, attr_name, None)
        alive = False
        if w is not None:
            try: alive = w.isVisible()
            except RuntimeError: alive = False
        if not alive:
            w = factory()
            try: w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            except Exception: pass
            try: w.destroyed.connect(lambda _obj=None, name=attr_name: setattr(self, name, None))
            except Exception: pass
            setattr(self, attr_name, w)
        try:
            w.show(); w.raise_(); w.activateWindow()
        except RuntimeError:
            setattr(self, attr_name, None)

    def show_magnetic_field_monitoring(self):
        self._safe_show_window(
            "mag_monitor",
            lambda: MagneticFieldMonitoringWindow(self.mag_queue, self.target_values, self.plot_config)
        )

    def show_magnetic_field_controller(self):
        self._safe_show_window(
            "field_controller",
            lambda: SmallScreenMagneticBeamWindow(self.control_queue, self.target_values, self.plot_config)
        )

    def show_vacuum_beam_monitoring(self):
        self._safe_show_window(
            "_vac_monitor",
            lambda: VacuumBeamMonitoringWindow(self.vac_queue, plot_rate_hz=4, seconds=120, bg_image=None)
        )

    def show_source_extraction_monitoring(self):
        self._safe_show_window(
            "_src_monitor",
            lambda: SourceExtractionMonitoringWindow(self.src_queue, plot_rate_hz=4, seconds=60)
        )

    def show_beam_transport_monitoring(self):
        self._safe_show_window(
            "_transport_monitor",
            lambda: BeamTransportMonitoringWindow(self.vac_queue, plot_rate_hz=4, seconds=120, bg_image=None)
        )

    def show_sqlite_viewer(self):
        self._safe_show_window("_sqlite_viewer", lambda: SQLiteLogViewer())

    def show_scaling_window(self):
        self._safe_show_window("_scale_window", lambda: ScaleCalWindow(Scaler()))

    def show_settings_dialog(self):
        try:
            dlg = SettingsDialog(self)
            dlg.exec()
        except Exception as e:
            print("[UI] Settings dialog error:", e)

    def show_beam_range_dialog(self):
        try:
            dlg = BeamRangeDialog(self, base_bitmask_u64=getattr(self, "_bitmask_u64", 0))
            dlg.show(); dlg.raise_(); dlg.activateWindow()
        except Exception as e:
            print("[UI] Beam Range dialog error:", e)

    def toggle_alarm(self):
        try:
            active = getattr(alarm_manager, "is_active", lambda: False)()
            alarm_manager.set_active( not active)  # if your alarm_manager has a setter; otherwise implement accordingly
        except Exception:
            # simple feedback in console if alarm_manager is not available
            print("[Alarm] toggled.")

    # ---------- Snapshot / Recall ----------
    def _ensure_db_path(self) -> str | None:
        if hasattr(self, "db_path") and self.db_path and os.path.exists(self.db_path):
            return self.db_path
        path, _ = QFileDialog.getOpenFileName(self, "Locate cyclotron SQLite DB", "", "SQLite DB (*.db *.sqlite *.sqlite3);;All Files (*)")
        if path:
            self.db_path = path
            return path
        return None

    def _get_latest_row(self, con: sqlite3.Connection):
        cur = con.cursor()
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()]
        if TIME_COL not in cols: raise RuntimeError(f"Time column '{TIME_COL}' not found in '{TABLE_NAME}'.")
        present = [c for c in EXPECTED_COLS if c in cols]
        if not present: raise RuntimeError("No expected channel columns found.")
        fields = [TIME_COL] + present
        row = cur.execute(f"SELECT {', '.join(fields)} FROM {TABLE_NAME} ORDER BY ROWID DESC LIMIT 1").fetchone()
        if not row: raise RuntimeError("No rows found in the table.")
        return fields, row

    try:
        from openpyxl import Workbook, load_workbook
        _HAVE_OPENPYXL = True
    except Exception:
        _HAVE_OPENPYXL = False

    def _append_to_xlsx(self, fields, row_values, note):
        from openpyxl import Workbook, load_workbook
        path = SNAPSHOT_XLSX
        if os.path.exists(path):
            wb = load_workbook(path); ws = wb.active
        else:
            wb = Workbook(); ws = wb.active; ws.title = "Snapshots"; ws.append(fields + ["note"])
        ws.append(row_values + [note])
        r = ws.max_row
        ws.cell(row=r, column=1).number_format = "yyyy-mm-dd hh:mm:ss"
        wb.save(path)
        return os.path.abspath(path)

    def _append_to_csv(self, fields, row_values, note):
        path = SNAPSHOT_CSV
        new_file = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new_file: w.writerow(fields + ["note"])
            w.writerow(row_values + [note])
        return os.path.abspath(path)

    def snapshot_to_excel(self):
        db = self._ensure_db_path()
        if not db:
            QMessageBox.warning(self, "DB not set", "Please locate your SQLite database to take a snapshot."); return
        try:
            con = sqlite3.connect(db)
            fields, row = self._get_latest_row(con)
        except Exception as e:
            QMessageBox.critical(self, "Snapshot error", str(e)); return
        finally:
            try: con.close()
            except Exception: pass
        dt = _parse_db_time(row[0])
        human_time = dt.strftime("%Y-%m-%d %H:%M:%S")
        row_for_excel = [dt] + list(row[1:])
        row_for_csv   = [human_time] + list(row[1:])
        try:
            if self._HAVE_OPENPYXL:
                out = self._append_to_xlsx(fields, row_for_excel, "")
            else:
                out = self._append_to_csv(fields, row_for_csv, "")
        except Exception as e:
            QMessageBox.critical(self, "Write error", f"Failed to write snapshot:\n{e}"); return
        QMessageBox.information(self, "Snapshot saved", f"Appended 1 row to:\n{out}")

    def show_recall_dialog(self):
        def _apply(payload: Dict[str, float], note: str = "", timestamp: str = ""):
            QMessageBox.information(self, "Recall", "Snapshot loaded (preview only in this build).")
        dlg = SnapshotRecallDialog(self, apply_callback=_apply, default_path=SNAPSHOT_XLSX if os.path.exists(SNAPSHOT_XLSX) else SNAPSHOT_CSV)
        dlg.exec()

    # ---------- Bubbles ----------
    def _make_label(self, text: str, group: int) -> QLabel:
        lab = QLabel(text, self)
        lab.setFont(LABEL_FONT)
        lab.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setContentsMargins(0, 2, 0, 2)
        lab.setStyleSheet("background: transparent; color: rgba(0,0,0,0);")
        lab.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        return lab

    def _build_bubbles(self):
        self.bubbles.clear()
        if not hasattr(self, "_halos"): self._halos = []
        for i, conf in enumerate(self.bubble_confs):
            icon_path = conf.get("icon")
            if icon_path and not os.path.exists(icon_path): icon_path = None
            conf["icon"] = icon_path

            btn = BubbleButton(i, conf, self)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_bubble_clicked(idx))
            lab = self._make_label(conf.get("title",""), conf.get("group",0))
            self.bubbles.append((btn, lab))

            # button electron halos
            try:
                DOUBLE_ORBIT_TITLES = {"Field Ctrl", "Magnetic\nField"}
                if conf.get("title","") in DOUBLE_ORBIT_TITLES:
                    ElectronHalo(btn, self, orbits=2, electrons_per_orbit=2,
                                 orbit_scales=[0.78, 0.55], base_scale=1.0)
                else:
                    ElectronHalo(btn, self, electron_count=2)
            except Exception as e:
                print("halo create error:", e)

    def _on_bubble_clicked(self, idx: int):
        raw = self.bubble_confs[idx].get("title", "")
        key = "".join(ch for ch in raw.lower() if ch.isalnum())

        actions = {
            "magneticfield":            self.show_magnetic_field_monitoring,
            "fieldctrl":                self.show_magnetic_field_controller,
            "vacbeam":                  self.show_vacuum_beam_monitoring,
            "beamsourceextraction":     self.show_source_extraction_monitoring,
            "beamtransportation":       self.show_beam_transport_monitoring,
            "sqlite":                   self.show_sqlite_viewer,
            "scaling":                  self.show_scaling_window,
            "settings":                 self.show_settings_dialog,
            "snapshot":                 self.snapshot_to_excel,
            "recall":                   self.show_recall_dialog,
            "beamrange":                self.show_beam_range_dialog,
            "alarm":                    self.toggle_alarm,
            "rfpower":                  self.show_magnetic_field_controller,
        }

        if key in actions:
            actions[key]()
        else:
            if "magnetic" in key and "field" in key:
                self.show_magnetic_field_monitoring()
            elif "field" in key and "ctrl" in key:
                self.show_magnetic_field_controller()
            elif "vac" in key and "beam" in key:
                self.show_vacuum_beam_monitoring()
            elif "transport" in key:
                self.show_beam_transport_monitoring()
            elif "source" in key or "extraction" in key:
                self.show_source_extraction_monitoring()
            elif "sqlite" in key:
                self.show_sqlite_viewer()
            elif "scale" in key:
                self.show_scaling_window()
            elif "setting" in key:
                self.show_settings_dialog()
            elif "range" in key:
                self.show_beam_range_dialog()
            elif "alarm" in key:
                self.toggle_alarm()
            elif "snapshot" in key:
                self.snapshot_to_excel()
            elif "recall" in key:
                self.show_recall_dialog()
            else:
                print(f"[UI] No action wired for: '{raw}'  (normalized: '{key}')")

    def _geom_from_conf(self, conf: Dict[str,Any]) -> Tuple[int,int,int]:
        W, H = self.width(), self.height(); S = min(W, H)
        d = int(max(MIN_BUBBLE_PX, float(conf.get("size", 0.18)) * S))
        x = int(float(conf.get("x", 0.5)) * W - d/2); y = int(float(conf.get("y", 0.5)) * H - d/2)
        return x, y, d

    def _layout_one_bubble(self, idx: int):
        btn, lab = self.bubbles[idx]
        x, y, d = self._geom_from_conf(self.bubble_confs[idx])
        btn.setGeometry(x, y, d, d); btn.raise_()
        lab.setGeometry(0, 0, 1, 1); lab.hide()
        self.update()

    def _layout_all_bubbles(self):
        for i in range(len(self.bubbles)):
            self._layout_one_bubble(i)

    # ---------- Keys ----------
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_B:
            new_vis = not (self.title_label and self.title_label.isVisible()) if self.title_label else True
            if self.title_label: self.title_label.setVisible(new_vis)
            for btn, lab in self.bubbles:
                if lab: lab.setVisible(new_vis)
            return
        super().keyPressEvent(e)

    # ---------- FX / Paint ----------
    def _tick_fx(self):
        self._fx_phase = (self._fx_phase + 0.0125) % 1.0
        self.update()

    def _on_frame(self, frame):
        img = frame.toImage()
        self._current_frame = img if not img.isNull() else None
        self.update()

    def _energy_color(self, t: float) -> QColor:
        t = max(0.0, min(1.0, t))
        return QColor(int(255*(1-t)), int(40*(1-t)+20*t), int(255*(0.15+0.85*t)))

    def _paint_orbit_electrons_spiral(self, p: QPainter, cx: float, cy: float, S: float,
                                      params: Dict[str, Any], draw_front: bool):
        if not DRAW_ELECTRONS:
            return
        r0   = float(params["r0"]) * S
        rmax = float(params["rmax"]) * S
        turns  = float(params["turns"])
        growth = float(params["growth"])
        angle  = math.radians(float(ELLIPSE["angle_deg"]))
        dx = float(ELLIPSE["dx"]) * S; dy = float(ELLIPSE["dy"]) * S
        ax = float(ELLIPSE["ax"]); by = float(ELLIPSE["by"])
        count = int(params["count"]); speed = float(params["speed"])
        fg = float(params["front_gain"]); bg = float(params["back_gain"])
        cosA, sinA = math.cos(angle), math.sin(angle)

        base_size = max(6, int(0.016 * S))
        phase = (getattr(self, "_fx_phase", 0.0) * speed) % 1.0

        trail_on = bool(SPIRAL_TRAIL.get("enabled", False))
        trail_len  = float(SPIRAL_TRAIL.get("length", 0.08))
        trail_w    = float(SPIRAL_TRAIL.get("width", 1.6))
        trail_a_f  = int(SPIRAL_TRAIL.get("front_alpha", 40))
        trail_a_b  = int(SPIRAL_TRAIL.get("back_alpha", 28))

        for k in range(count):
            theta_phase  = phase if COORDINATED_SPIRAL else (phase + k / count)
            radius_phase = (phase + k * COORD_SPACING) if COORDINATED_SPIRAL else theta_phase
            rp = max(0.0, min(1.0, radius_phase))
            r  = r0 + (rmax - r0) * (rp ** growth)
            th = 2.0 * math.pi * turns * (theta_phase % 1.0)
            ex, ey = ax * r * math.cos(th), by * r * math.sin(th)
            rx, ry = ex*cosA - ey*sinA, ex*sinA + ey*cosA
            x,  y  = cx + dx + rx, cy + dy + ry
            is_front = (ry > 0)
            if (is_front != draw_front): 
                continue

            dot_color = self._energy_color((r - r0) / max(1e-9, (rmax - r0)))

            if trail_on and trail_len > 0.0:
                path = QPainterPath(); Nseg = 80
                prev_x = prev_y = None
                for i in range(Nseg + 1):
                    u = trail_len * (i / Nseg)
                    th_u_phase = (theta_phase - u)
                    r_u_phase  = (radius_phase - u) if COORDINATED_SPIRAL else (theta_phase - u)
                    if r_u_phase <= 0.0:
                        if i == 0:
                            r_u = r0; th_u = 2.0*math.pi*turns*th_u_phase
                            ex_u, ey_u = ax*r_u*math.cos(th_u), by*r_u*math.sin(th_u)
                            rx_u, ry_u = ex_u*cosA - ey_u*sinA, ex_u*sinA + ey_u*cosA
                            x_u, y_u   = cx + dx + rx_u, cy + dy + ry_u
                            path.moveTo(x_u, y_u)
                        break
                    rp_u = max(0.0, min(1.0, r_u_phase))
                    r_u  = r0 + (rmax - r0) * (rp_u ** growth)
                    th_u = 2.0*math.pi*turns*th_u_phase
                    ex_u, ey_u = ax*r_u*math.cos(th_u), by*r_u*math.sin(th_u)
                    rx_u, ry_u = ex_u*cosA - ey_u*sinA, ex_u*sinA + ey_u*cosA
                    x_u, y_u   = cx + dx + rx_u, cy + dy + ry_u
                    if i == 0: 
                        path.moveTo(x_u, y_u)
                    else:
                        if prev_x is not None:
                            dxj, dyj = x_u-prev_x, y_u-prev_y
                            if (dxj*dxj + dyj*dyj) > (S*0.08)**2:
                                path.moveTo(x_u, y_u); prev_x, prev_y = x_u, y_u; continue
                        path.lineTo(x_u, y_u)
                    prev_x, prev_y = x_u, y_u
                p.setPen(QPen(QColor(dot_color.red(), dot_color.green(), dot_color.blue(),
                                     int(trail_a_f if is_front else trail_a_b)), trail_w))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)

            ry_norm = 0.0 if rmax <= 1e-9 else max(-1.0, min(1.0, ry / rmax))
            depth = (0.65 + 0.35 * ((ry_norm + 1.0) * 0.5)) * (fg if is_front else bg)
            size = int(base_size * (0.85 + 0.30 * depth))
            rdot = size / 2.0
            g = QRadialGradient(QPointF(x, y), rdot)
            alpha_peak = int(210 * depth) if is_front else int(130 * depth)
            bright = QColor(255, 255, 255, min(255, alpha_peak+30))
            g.setColorAt(0.00, bright)
            g.setColorAt(0.35, QColor(dot_color.red(), dot_color.green(), dot_color.blue(), alpha_peak))
            g.setColorAt(1.00, QColor(dot_color.red(), dot_color.green(), dot_color.blue(), 0))
            p.save(); p.setPen(Qt.PenStyle.NoPen); p.setBrush(g)
            p.drawEllipse(QPointF(x, y), rdot, rdot); p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing |
                         QPainter.RenderHint.SmoothPixmapTransform |
                         QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        S = min(w, h); cx, cy = w/2, h/2

        # background
        if not self.bg_pixmap.isNull():
            bg = self.bg_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(self.rect(), bg)
        else:
            p.fillRect(self.rect(), Qt.GlobalColor.black)
        if BACKGROUND_DIM > 0.0:
            p.fillRect(self.rect(), QColor(0,0,0, int(255*BACKGROUND_DIM)))

        # center orbits — back pass (electrons behind)
        self._paint_orbit_electrons_spiral(p, cx, cy, S, SPIRAL, draw_front=False)

        # core glow + video
        r_core = CORE_RADIUS_FRAC * S
        core_path = QPainterPath(); core_path.addEllipse(QPointF(cx, cy), r_core-1, r_core-1)
        halo = QRadialGradient(QPointF(cx, cy), r_core*1.20)
        halo.setColorAt(0.70, QColor(70,210,255,32))
        halo.setColorAt(0.92, QColor(70,210,255,12))
        halo.setColorAt(1.00, QColor(70,210,255,0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(halo)
        p.drawEllipse(QPointF(cx, cy), r_core*1.16, r_core*1.16)
        g = QRadialGradient(QPointF(cx, cy), r_core*0.55)
        g.setColorAt(0.0, QColor(255,255,255,180))
        g.setColorAt(0.5, QColor(120,220,255,90))
        g.setColorAt(1.0, QColor(0,220,255,0))
        p.setBrush(g); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx,cy), r_core*0.32, r_core*0.32)

        if SHOW_CORE_VIDEO and self._current_frame is not None:
            p.save(); p.setClipPath(core_path)
            vw, vh = self._current_frame.width(), self._current_frame.height()
            if vw > 0 and vh > 0:
                scale = (2*(r_core*VIDEO_FILL*VIDEO_CONTENT_SCALE)) / max(1, min(vw, vh))
                tw, th = int(vw*scale), int(vh*scale)
                img_scaled = self._current_frame.scaled(
                    tw, th,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation if True else Qt.TransformationMode.FastTransformation
                )
                top_left = QPointF(cx - img_scaled.width()/2, cy - img_scaled.height()/2)
                p.drawImage(top_left, img_scaled)
            p.restore()

        # center orbits — front pass (electrons in front)
        self._paint_orbit_electrons_spiral(p, cx, cy, S, SPIRAL, draw_front=True)

        # --- Title PNG overlay (draw last so it’s on top) ---
        if SHOW_TITLE_IMAGE and not self._title_pm.isNull():
            W, H = self.width(), self.height()
            max_w = int(W * float(TITLE_MAX_WIDTH_FRAC))
            max_h = int(H * float(TITLE_MAX_HEIGHT_FRAC))
            pm = self._title_pm
            scaled = pm.scaled(max_w, max_h,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            x = (W - scaled.width()) // 2
            y = int(TITLE_TOP_MARGIN_PX)
            p.drawPixmap(x, y, scaled)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._layout_all_bubbles()
        if TITLE["enabled"]:
            self._layout_title()

# ---------- launcher ----------
def run_gui(data_queue=None, target_values=None, control_queue=None, plot_config=None):
    app = QApplication(sys.argv)
    win = MainControlWindow(data_queue, target_values, control_queue, plot_config)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    run_gui()
