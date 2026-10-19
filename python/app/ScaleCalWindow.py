# -*- coding: utf-8 -*-
"""
ScaleCalWindow.py — Neon/Cyberpunk styled dual-direction curve editor
- Tabs for Raw→Eng and Eng→Raw (paste points or load CSV/XLSX)
- Live neon plot (glow + linear reference)
- Bulk import/export workbook (two sheets per channel: <key>_raw_to_eng and <key>_eng_to_raw)
- Optional background image dimmed over a dark theme (uses neon_style if available)

Author: you 🛠
"""
from project_paths import project_file

import csv, traceback
from typing import List, Tuple, Optional, Dict

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPlainTextEdit,
    QPushButton, QFileDialog, QMessageBox, QTreeWidget, QTreeWidgetItem,
    QTabWidget, QLabel
)

# ---- Matplotlib (PyQt6)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# ---- Optional Excel support
try:
    from openpyxl import load_workbook, Workbook
    _HAVE_XLSX = True
except Exception:
    _HAVE_XLSX = False

# ---- Optional neon background helper (your repo already has neon_style.py)
try:
    from neon_style import NeonBackgroundMixin
except Exception:
    class NeonBackgroundMixin:
        def init_neon_background(self, image_path: str, dimmer: float = 0.10): pass
        def paint_neon_background(self, p, w): pass

# ---- Scaling engine
from scaling import get_scaler, all_keys, label_for, group_for


CYAN = "#19F0FF"
MAGENTA = "#FF4FD8"
BG    = "#0A121E"   # page background
PANEL = "#0F1728"   # cards/panels
INK   = "#E6FAFF"   # text
INK_SOFT = "#A9C6D1"

SFX_R2E = "_raw_to_eng"
SFX_E2R = "_eng_to_raw"


class ScaleCalWindow(NeonBackgroundMixin, QWidget):
    def __init__(self, parent: Optional[QWidget] = None, scaler=None,
                 bg_image: Optional[str] = None, bg_dim: float = 0.12, *args, **kwargs):
        """
        scaler: if provided, use that (e.g. shared scaler from main GUI).
                if None, use global get_scaler() so all windows share calibration.
        """
        # Allow legacy call ScaleCalWindow(Scaler())
        if parent is not None and not isinstance(parent, QWidget):
            scaler, parent = parent, None

        super().__init__(parent)
        self.setWindowTitle("Scaling & Calibration")
        self.resize(1100, 720)

        # use shared scaler
        self.scaler = scaler or get_scaler()
        self.init_neon_background(bg_image or "", bg_dim)
        self._apply_neon_styles()
        self._build_ui()
        self._populate_channels()
        self._refresh_plot()

    # ---- paint background (optional image + dim overlay) ----
    def paintEvent(self, ev):
        p = QtWidgets.QStylePainter(self)
        self.paint_neon_background(p, self)

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10,10,10,10)
        root.setSpacing(10)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)
        split.setHandleWidth(3)

        # Left: channel tree
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        split.addWidget(self.tree)

        # Right: editors + plot
        right = QtWidgets.QFrame()
        right.setObjectName("panel")
        split.addWidget(right)
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(14,14,14,14)
        rlay.setSpacing(12)

        # Tabs for R→E and E→R
        self.tabs = QTabWidget()
        self.tabs.setObjectName("tabs")
        self.tabs.currentChanged.connect(lambda _: self._refresh_plot())
        rlay.addWidget(self.tabs, 0)

        # ---- Tab 1: Raw→Eng
        self.tab_r2e = QtWidgets.QFrame(); v1 = QVBoxLayout(self.tab_r2e); self.tab_r2e.setObjectName("panel")
        self.te_r2e = QPlainTextEdit()
        self.te_r2e.setPlaceholderText(
            "Paste one point per line: raw, eng\n"
            "Example:\n0.10, 0.0\n0.50, 0.5\n1.00, 1.1\n2.00, 2.2\n\n"
            "Or Load CSV/XLSX (headers: raw,eng or x,y)."
        )
        self.te_r2e.setFixedHeight(160)
        self.te_r2e.textChanged.connect(self._refresh_plot)

        btns1 = QHBoxLayout()
        self.btn_csv_r2e  = QPushButton("Load CSV…")
        self.btn_xlsx_r2e = QPushButton("Load Excel…")
        self.btn_apply_r2e= QPushButton("Apply Raw→Eng")
        self.btn_apply_r2e.setDefault(True)
        btns1.addWidget(self.btn_csv_r2e); btns1.addWidget(self.btn_xlsx_r2e)
        btns1.addStretch(1); btns1.addWidget(self.btn_apply_r2e)

        v1.addWidget(self.te_r2e)
        v1.addLayout(btns1)
        self.tabs.addTab(self.tab_r2e, "Raw → Eng")

        # ---- Tab 2: Eng→Raw
        self.tab_e2r = QtWidgets.QFrame(); v2 = QVBoxLayout(self.tab_e2r); self.tab_e2r.setObjectName("panel")
        self.te_e2r = QPlainTextEdit()
        self.te_e2r.setPlaceholderText(
            "Paste one point per line: eng, raw\n"
            "Example:\n0.0, 0.10\n0.5, 0.50\n1.1, 1.00\n2.2, 2.00\n\n"
            "Or Load CSV/XLSX (headers: eng,raw or y,x)."
        )
        self.te_e2r.setFixedHeight(160)
        self.te_e2r.textChanged.connect(self._refresh_plot)

        btns2 = QHBoxLayout()
        self.btn_csv_e2r  = QPushButton("Load CSV…")
        self.btn_xlsx_e2r = QPushButton("Load Excel…")
        self.btn_apply_e2r= QPushButton("Apply Eng→Raw")
        btns2.addWidget(self.btn_csv_e2r); btns2.addWidget(self.btn_xlsx_e2r)
        btns2.addStretch(1); btns2.addWidget(self.btn_apply_e2r)

        v2.addWidget(self.te_e2r)
        v2.addLayout(btns2)
        self.tabs.addTab(self.tab_e2r, "Eng → Raw")

        # ---- Plot area (shared)
        plot_row = QHBoxLayout()
        self.plot_label = QLabel("Curve preview")
        self.plot_label.setObjectName("muted")
        plot_row.addWidget(self.plot_label); plot_row.addStretch(1)
        rlay.addLayout(plot_row)

        self.fig = Figure(figsize=(5, 3), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#0C1424")
        self.fig.patch.set_facecolor("none")
        self.canvas = FigureCanvas(self.fig)
        rlay.addWidget(self.canvas, 1)

        # ---- Bulk tools row
        tools_row = QHBoxLayout()
        self.btn_bulk = QPushButton("Load Workbook (all channels)…")
        self.btn_export = QPushButton("Export Workbook (all channels)…")
        tools_row.addStretch(1)
        tools_row.addWidget(self.btn_bulk)
        tools_row.addWidget(self.btn_export)
        rlay.addLayout(tools_row)

        # wiring (load/apply)
        self.btn_csv_r2e.clicked.connect(lambda: self._load_curve_csv(self.te_r2e, expect="raw,eng"))
        self.btn_csv_e2r.clicked.connect(lambda: self._load_curve_csv(self.te_e2r, expect="eng,raw"))
        self.btn_xlsx_r2e.clicked.connect(lambda: self._load_curve_xlsx(self.te_r2e, expect="raw,eng"))
        self.btn_xlsx_e2r.clicked.connect(lambda: self._load_curve_xlsx(self.te_e2r, expect="eng,raw"))
        self.btn_apply_r2e.clicked.connect(self._apply_r2e)
        self.btn_apply_e2r.clicked.connect(self._apply_e2r)
        self.btn_bulk.clicked.connect(self._bulk_load_workbook)
        self.btn_export.clicked.connect(self._export_workbook)

        if not _HAVE_XLSX:
            for b in (self.btn_xlsx_r2e, self.btn_xlsx_e2r, self.btn_bulk, self.btn_export):
                b.setEnabled(False)
                b.setToolTip("Install 'openpyxl' to enable Excel import/export.")

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

    # ---------- Neon stylesheet ----------
    def _apply_neon_styles(self):
        self.setStyleSheet(f"""
            QWidget {{
                color: {INK};
                background: transparent;
                font-size: 13px;
            }}
            QTreeWidget {{
                background: {PANEL}; border: 1px solid #0b1220; border-radius: 12px;
            }}
            QTreeWidget::item {{
                padding: 6px 8px;
            }}
            QTreeWidget::item:selected {{
                background: rgba(25,240,255,0.18);
                color: {INK};
                border-left: 3px solid {CYAN};
            }}
            QTabWidget::pane {{
                border: 1px solid #0b1220; border-radius: 12px; background: {PANEL};
            }}
            QTabBar::tab {{
                background: #0b1322; color: {INK_SOFT};
                padding: 8px 14px; margin: 4px; border-radius: 10px;
            }}
            QTabBar::tab:selected {{
                color: {INK}; border: 1px solid {CYAN}; background: #0e1930;
            }}
            QPlainTextEdit {{
                background: #0A1324; border: 1px solid #0b1220; border-radius: 10px;
                selection-background-color: rgba(255,79,216,0.35);
                font-family: Consolas, "Fira Code", monospace; font-size: 12px;
            }}
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #152039, stop:1 #0f1730);
                border: 1px solid #0b1220; border-radius: 12px; padding: 8px 14px;
                color: {INK};
            }}
            QPushButton:hover {{ border-color: {CYAN}; box-shadow: 0 0 0 0 transparent; }}
            QPushButton:pressed {{ border-color: {MAGENTA}; }}
            QLabel#muted {{ color: {INK_SOFT}; }}
            QFrame#panel {{ background: {PANEL}; border: 1px solid #0b1220; border-radius: 16px; }}
            QSplitter::handle {{
                background: #0b1220;
            }}
            QToolTip {{
                color: {INK}; background: #0d1629; border: 1px solid {CYAN};
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                background:#0b1220; border:none; margin:0px; width:10px; height:10px;
            }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background:{CYAN}; min-height:24px; border-radius:4px;
            }}
        """)

    # ---------------- Channel list ----------------
    def _populate_channels(self):
        self.tree.clear()
        groups: Dict[str, QTreeWidgetItem] = {}
        for k in all_keys():
            grp = group_for(k) or "Other"
            if grp not in groups:
                g = QTreeWidgetItem([grp])
                g.setFlags(g.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.tree.addTopLevelItem(g)
                groups[grp] = g
            it = QTreeWidgetItem([label_for(k)])
            it.setData(0, Qt.ItemDataRole.UserRole, k)
            groups[grp].addChild(it)
        self.tree.expandAll()
        if self.tree.topLevelItemCount() > 0:
            g0 = self.tree.topLevelItem(0)
            if g0 and g0.childCount() > 0:
                self.tree.setCurrentItem(g0.child(0))
        self._load_textboxes_from_scaler()

    def _current_key(self) -> str:
        sel = self.tree.selectedItems()
        if not sel: return ""
        it = sel[0]
        key = it.data(0, Qt.ItemDataRole.UserRole)
        return str(key) if key else ""

    # ---------------- Helpers ----------------
    def _sorted_xy(self, pts: List[List[float]]) -> Tuple[List[float], List[float]]:
        dedup = {}
        for x, y in pts:
            try: dedup[float(x)] = float(y)
            except: pass
        xs = sorted(dedup.keys())
        ys = [dedup[x] for x in xs]
        return xs, ys

    def _parse_text_points(self, txt: str) -> List[List[float]]:
        pts = []
        for line in (txt or "").splitlines():
            s = line.strip()
            if not s or s.startswith("#"): continue
            parts = [p for p in s.replace(",", " ").split() if p]
            if len(parts) < 2: continue
            try: pts.append([float(parts[0]), float(parts[1])])
            except: pass
        xs, ys = self._sorted_xy(pts)
        return [[x, y] for x, y in zip(xs, ys)]

    def _on_selection_changed(self):
        self._load_textboxes_from_scaler()
        self._refresh_plot()

    def _load_textboxes_from_scaler(self):
        key = self._current_key()
        if not key:
            self.te_r2e.clear(); self.te_e2r.clear(); return
        self.scaler.reload_if_changed()
        ent = self.scaler.data.get(key, {}) or {}

        r2e = (ent.get("raw_to_eng") or {})
        self.te_r2e.setPlainText("\n".join(f"{x}, {y}" for x, y in r2e.get("points", []))) if r2e.get("type") == "curve" else self.te_r2e.clear()

        e2r = (ent.get("eng_to_raw") or {})
        self.te_e2r.setPlainText("\n".join(f"{x}, {y}" for x, y in e2r.get("points", []))) if e2r.get("type") == "curve" else self.te_e2r.clear()

    # ---------------- Plot ----------------
    def _refresh_plot(self):
        key = self._current_key()
        self.ax.clear()
        self.ax.set_facecolor("#0C1424")
        self.ax.grid(True, color="#17324e", alpha=0.35)

        if self.tabs.currentIndex() == 0:
            self.ax.set_title(f"{label_for(key)} — Raw → Eng", color=INK)
            self.ax.set_xlabel("raw", color=INK_SOFT)
            self.ax.set_ylabel("eng", color=INK_SOFT)
            pts = self._parse_text_points(self.te_r2e.toPlainText())
        else:
            self.ax.set_title(f"{label_for(key)} — Eng → Raw", color=INK)
            self.ax.set_xlabel("eng", color=INK_SOFT)
            self.ax.set_ylabel("raw", color=INK_SOFT)
            pts = self._parse_text_points(self.te_e2r.toPlainText())

        for spine in self.ax.spines.values():
            spine.set_color("#385a86")

        if len(pts) >= 2:
            xs, ys = self._sorted_xy(pts)
            # Glow underlay
            self.ax.plot(xs, ys, linewidth=8, alpha=0.18, solid_capstyle="round", color=CYAN)
            # Bright line with nodes
            self.ax.plot(xs, ys, marker="o", markersize=5, linewidth=2.2, color=CYAN)
            # Dashed linear reference
            x0, x1 = xs[0], xs[-1]
            if x1 != x0:
                m = (ys[-1] - ys[0]) / (x1 - x0)
                ref = [ys[0] + m*(x - x0) for x in xs]
                self.ax.plot(xs, ref, linestyle="--", linewidth=1.4, color=MAGENTA, alpha=0.8)
        else:
            self.ax.text(0.5, 0.5, "No curve points", ha="center", va="center",
                         transform=self.ax.transAxes, color=INK_SOFT)

        self.canvas.draw_idle()

    # ---------------- Apply handlers ----------------
    def _apply_r2e(self):
        key = self._current_key()
        if not key:
            QMessageBox.warning(self, "Raw→Eng", "Select a channel first."); return
        pts = self._parse_text_points(self.te_r2e.toPlainText())
        if len(pts) < 2:
            QMessageBox.warning(self, "Raw→Eng", "Need at least 2 (raw, eng) points."); return
        try:
            ent = self.scaler.data.setdefault(key, {"enabled": True})
            ent["raw_to_eng"] = {"type": "curve", "points": pts}
            self.scaler.save()
            self._refresh_plot()
            QMessageBox.information(self, "Raw→Eng", f"Saved {len(pts)} points to {label_for(key)}.")
        except Exception as e:
            QMessageBox.critical(self, "Raw→Eng", f"{e}\n\n{traceback.format_exc()}")

    def _apply_e2r(self):
        key = self._current_key()
        if not key:
            QMessageBox.warning(self, "Eng→Raw", "Select a channel first."); return
        pts = self._parse_text_points(self.te_e2r.toPlainText())
        if len(pts) < 2:
            QMessageBox.warning(self, "Eng→Raw", "Need at least 2 (eng, raw) points."); return
        try:
            ent = self.scaler.data.setdefault(key, {"enabled": True})
            ent["eng_to_raw"] = {"type": "curve", "points": pts}
            self.scaler.save()
            self._refresh_plot()
            QMessageBox.information(self, "Eng→Raw", f"Saved {len(pts)} points to {label_for(key)}.")
        except Exception as e:
            QMessageBox.critical(self, "Eng→Raw", f"{e}\n\n{traceback.format_exc()}")

    # ---------------- CSV/XLSX loaders for each tab ----------------
    def _load_curve_csv(self, target_textedit: QPlainTextEdit, expect: str):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV (*.csv)")
        if not path: return
        try:
            pts: List[List[float]] = []
            with open(path, "r", newline="", encoding="utf-8") as f:
                rd = csv.reader(f)
                rows = list(rd)
            if not rows: raise ValueError("Empty CSV.")
            headers = [h.strip().lower() for h in rows[0]]
            data_rows = rows[1:] if len(rows) > 1 and any(h.isalpha() for h in headers) else rows

            if data_rows is rows:
                for r in data_rows:
                    if len(r) < 2: continue
                    pts.append([float(r[0]), float(r[1])])
            else:
                if expect == "raw,eng":
                    cx = self._find_col(headers, ["raw","x"]); cy = self._find_col(headers, ["eng","y"])
                else:
                    cx = self._find_col(headers, ["eng","y"]); cy = self._find_col(headers, ["raw","x"])
                if cx < 0 or cy < 0: raise ValueError(f"Missing expected headers for {expect}.")
                for r in data_rows:
                    try: pts.append([float(r[cx]), float(r[cy])])
                    except: pass

            if len(pts) < 2: raise ValueError("No valid numeric rows found.")
            target_textedit.setPlainText("\n".join(f"{x}, {y}" for x,y in pts))
        except Exception as e:
            QMessageBox.critical(self, "CSV error", f"{e}\n\n{traceback.format_exc()}")

    def _load_curve_xlsx(self, target_textedit: QPlainTextEdit, expect: str):
        if not _HAVE_XLSX:
            QMessageBox.warning(self, "Excel", "openpyxl not installed."); return
        path, _ = QFileDialog.getOpenFileName(self, "Open Excel", "", "Excel (*.xlsx *.xls)")
        if not path: return
        try:
            wb = load_workbook(path, data_only=True); ws = wb.active
            headers = [str(c.value).strip().lower() if c.value is not None else "" for c in ws[1]]
            if expect == "raw,eng":
                cx = self._find_col(headers, ["raw","x"]); cy = self._find_col(headers, ["eng","y"])
            else:
                cx = self._find_col(headers, ["eng","y"]); cy = self._find_col(headers, ["raw","x"])
            if cx < 0 or cy < 0: raise ValueError(f"Missing expected headers for {expect}.")
            pts=[]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row is None or len(row) <= max(cx, cy): continue
                try: pts.append([float(row[cx]), float(row[cy])])
                except: pass
            if len(pts) < 2: raise ValueError("No valid numeric rows found.")
            target_textedit.setPlainText("\n".join(f"{x}, {y}" for x,y in pts))
        except Exception as e:
            QMessageBox.critical(self, "Excel error", f"{e}\n\n{traceback.format_exc()}")

    def _find_col(self, headers: List[str], names: List[str]) -> int:
        for n in names:
            try: return headers.index(n)
            except ValueError: pass
        return -1

    # ---------------- Workbook (bulk) ----------------
    def _bulk_load_workbook(self):
        """
        NEW preferred format:
          - one sheet per direction per channel:
              <key>_raw_to_eng  with headers raw,eng
              <key>_eng_to_raw  with headers eng,raw

        Backward-compat:
          - if a sheet is exactly <key> (or label) and contains BOTH blocks with headers,
            we'll still import it.
        """
        if not _HAVE_XLSX:
            QMessageBox.warning(self, "Excel", "openpyxl not installed."); return
        path, _ = QFileDialog.getOpenFileName(self, "Load Workbook for All Channels", "", "Excel (*.xlsx *.xls)")
        if not path: return

        try:
            wb = load_workbook(path, data_only=True)

            # Build a robust map of possible names -> canonical key
            name_to_key: Dict[str, str] = {}
            for k in all_keys():
                name_to_key[k.strip().lower()] = k
                name_to_key[k.strip().lower().replace(" ", "")] = k
                lbl = label_for(k).strip().lower()
                name_to_key[lbl] = k
                name_to_key[lbl.replace(" ", "")] = k

            def parse_sheet_points(ws, xhdrs, yhdrs) -> List[List[float]]:
                headers = [str(c.value).strip().lower() if c.value is not None else "" for c in ws[1]]
                cx = self._find_col(headers, xhdrs)
                cy = self._find_col(headers, yhdrs)
                if cx < 0 or cy < 0:
                    return []
                pts = []
                for row in ws.iter_rows(min_row=2, values_only=True):
                    if row is None or len(row) <= max(cx, cy): 
                        continue
                    try:
                        pts.append([float(row[cx]), float(row[cy])])
                    except Exception:
                        pass
                xs, ys = self._sorted_xy(pts)
                return [[x, y] for x, y in zip(xs, ys)] if len(xs) >= 2 else []

            def parse_legacy_two_blocks(ws) -> tuple[list[list[float]], list[list[float]]]:
                """
                Legacy format: a single sheet with:
                  row ...: raw,eng header then data
                  blank row
                  row ...: eng,raw header then data
                """
                # Read all rows
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    return [], []
                # Find header rows
                hdr_rows = []
                for i, r in enumerate(rows):
                    if not r:
                        continue
                    c0 = (str(r[0]).strip().lower() if len(r) > 0 and r[0] is not None else "")
                    c1 = (str(r[1]).strip().lower() if len(r) > 1 and r[1] is not None else "")
                    if (c0, c1) in (("raw", "eng"), ("eng", "raw")):
                        hdr_rows.append((i, c0, c1))
                if not hdr_rows:
                    return [], []
                # Take first raw,eng and first eng,raw after it
                raw_idx = next((i for i,c0,c1 in hdr_rows if (c0,c1)==("raw","eng")), None)
                eng_idx = next((i for i,c0,c1 in hdr_rows if (c0,c1)==("eng","raw")), None)
                if raw_idx is None and eng_idx is None:
                    return [], []
                pts_r2e, pts_e2r = [], []

                def read_block(start_i):
                    pts=[]
                    for r in rows[start_i+1:]:
                        if r is None: 
                            break
                        # stop on blank line or next header
                        c0 = (str(r[0]).strip().lower() if len(r) > 0 and r[0] is not None else "")
                        c1 = (str(r[1]).strip().lower() if len(r) > 1 and r[1] is not None else "")
                        if (c0, c1) in (("raw","eng"), ("eng","raw")):
                            break
                        if len(r) < 2:
                            continue
                        if r[0] is None or r[1] is None:
                            # blank-ish row ends block
                            if c0 == "" and c1 == "":
                                break
                            continue
                        try:
                            pts.append([float(r[0]), float(r[1])])
                        except Exception:
                            pass
                    xs, ys = self._sorted_xy(pts)
                    return [[x,y] for x,y in zip(xs,ys)] if len(xs)>=2 else []

                if raw_idx is not None:
                    pts_r2e = read_block(raw_idx)
                if eng_idx is not None:
                    pts_e2r = read_block(eng_idx)
                return pts_r2e, pts_e2r

            changed_channels = set()

            for ws in wb.worksheets:
                sheet_name = (ws.title or "").strip()
                low = sheet_name.lower().replace(" ", "")

                # Preferred format: <key>_raw_to_eng / <key>_eng_to_raw
                if low.endswith(SFX_R2E):
                    base = sheet_name[:-(len(SFX_R2E))].strip()
                    key = name_to_key.get(base.lower()) or name_to_key.get(base.lower().replace(" ",""))
                    if not key:
                        continue
                    pts = parse_sheet_points(ws, ["raw","x"], ["eng","y"])
                    if pts:
                        ent = self.scaler.data.setdefault(key, {"enabled": True})
                        ent["raw_to_eng"] = {"type": "curve", "points": pts}
                        changed_channels.add(key)
                    continue

                if low.endswith(SFX_E2R):
                    base = sheet_name[:-(len(SFX_E2R))].strip()
                    key = name_to_key.get(base.lower()) or name_to_key.get(base.lower().replace(" ",""))
                    if not key:
                        continue
                    pts = parse_sheet_points(ws, ["eng","y"], ["raw","x"])
                    if pts:
                        ent = self.scaler.data.setdefault(key, {"enabled": True})
                        ent["eng_to_raw"] = {"type": "curve", "points": pts}
                        changed_channels.add(key)
                    continue

                # Legacy single-sheet per channel:
                # title might be key or label, and contains both blocks
                key = name_to_key.get(sheet_name.strip().lower()) or name_to_key.get(low)
                if not key:
                    continue
                pts_r2e, pts_e2r = parse_legacy_two_blocks(ws)
                if pts_r2e or pts_e2r:
                    ent = self.scaler.data.setdefault(key, {"enabled": True})
                    if pts_r2e:
                        ent["raw_to_eng"] = {"type": "curve", "points": pts_r2e}
                    if pts_e2r:
                        ent["eng_to_raw"] = {"type": "curve", "points": pts_e2r}
                    changed_channels.add(key)

            if changed_channels:
                self.scaler.save()
                self._load_textboxes_from_scaler()
                self._refresh_plot()

            QMessageBox.information(
                self, "Workbook import",
                f"Updated {len(changed_channels)} channel(s).\n"
                f"(Preferred format: {SFX_R2E} / {SFX_E2R})"
            )

        except Exception as e:
            QMessageBox.critical(self, "Workbook import error", f"{e}\n\n{traceback.format_exc()}")

    def _export_workbook(self):
        """
        Export preferred format:
          - two sheets per channel:
              <key>_raw_to_eng  (raw,eng)
              <key>_eng_to_raw  (eng,raw)
        """
        if not _HAVE_XLSX:
            QMessageBox.warning(self, "Excel", "openpyxl not installed."); return
        path, _ = QFileDialog.getSaveFileName(self, "Export Workbook (all channels)", str(project_file('calibration.xlsx')), "Excel (*.xlsx)")
        if not path: return

        try:
            wb = Workbook()
            # Remove default sheet
            try:
                wb.remove(wb.active)
            except Exception:
                pass

            # Excel sheet name limit is 31 chars
            def safe_sheet_name(s: str) -> str:
                s = (s or "").strip()
                # remove illegal chars: : \ / ? * [ ]
                for ch in (":", "\\", "/", "?", "*", "[", "]"):
                    s = s.replace(ch, "_")
                return s[:31] if len(s) > 31 else s

            made = 0
            for key in all_keys():
                ent = self.scaler.data.get(key, {}) or {}
                r2e = (ent.get("raw_to_eng") or {})
                e2r = (ent.get("eng_to_raw") or {})

                # Raw→Eng sheet
                ws1 = wb.create_sheet(title=safe_sheet_name(f"{key}{SFX_R2E}"))
                ws1.append(["raw", "eng"])
                if r2e.get("type") == "curve":
                    for x, y in r2e.get("points", []):
                        try:
                            ws1.append([float(x), float(y)])
                        except Exception:
                            pass

                # Eng→Raw sheet
                ws2 = wb.create_sheet(title=safe_sheet_name(f"{key}{SFX_E2R}"))
                ws2.append(["eng", "raw"])
                if e2r.get("type") == "curve":
                    for x, y in e2r.get("points", []):
                        try:
                            ws2.append([float(x), float(y)])
                        except Exception:
                            pass

                made += 1

            wb.save(path)
            QMessageBox.information(self, "Export", f"Workbook written:\n{path}\nSheets created: {made*2}")

        except Exception as e:
            QMessageBox.critical(self, "Export error", f"{e}\n\n{traceback.format_exc()}")


# ---- manual test ----
if __name__ == "__main__":
    import sys
    app = QtWidgets.QApplication(sys.argv)
    w = ScaleCalWindow(bg_image="", bg_dim=0.12)  # set bg_image to a path if you want
    w.show()
    sys.exit(app.exec())
