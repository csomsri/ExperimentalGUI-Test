# ScaleCalWindow.py — all-signal linear calibration editor (PyQt6) with selector + Excel/CSV
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QHBoxLayout, QMessageBox, QCheckBox, QDoubleSpinBox, QLabel,
    QFileDialog, QComboBox, QGroupBox, QFormLayout, QAbstractItemView
)
from PyQt6.QtCore import Qt
from datetime import datetime
import os, csv

from scaling import Scaler, all_keys, label_for, group_for  # uses your uploaded scaling.py

# Try Excel (openpyxl); fall back to CSV
try:
    from openpyxl import Workbook, load_workbook
    _HAVE_XLSX = True
except Exception:
    _HAVE_XLSX = False


class ScaleCalWindow(QWidget):
    def __init__(self, scaler: Scaler):
        super().__init__()
        self.setWindowTitle("Scaling & Calibration")
        self.resize(1000, 720)
        self.scaler = scaler

        # Keys and bookkeeping
        self._keys = all_keys()
        self._excel_path = os.path.abspath("calibration_records.xlsx")
        self._csv_path   = os.path.abspath("calibration_records.csv")
        self._row_for_key = {}  # key -> row index

        root = QVBoxLayout(self)

        # =========================
        # Quick Edit (per-signal)
        # =========================
        quick = QGroupBox("Quick Edit (pick a signal to focus)")
        ql = QVBoxLayout(quick)

        # Top row: selector + show-only toggle
        top = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.setEditable(False)

        # Build “Group • Label” list sorted by group, label
        items = []
        for k in self._keys:
            grp = group_for(k) or "Other"
            lbl = label_for(k)
            items.append((grp, lbl, k))
        items.sort(key=lambda t: (t[0], t[1]))
        for grp, lbl, key in items:
            self.selector.addItem(f"{grp} • {lbl}", userData=key)

        self.only_selected = QCheckBox("Show only selected")

        top.addWidget(QLabel("Signal:"))
        top.addWidget(self.selector, stretch=1)
        top.addSpacing(16)
        top.addWidget(self.only_selected)
        top.addStretch(1)
        ql.addLayout(top)

        # Form: Enabled + four fields
        form = QFormLayout()
        self.sel_enabled = QCheckBox("Enabled")

        def prep_spin(sp: QDoubleSpinBox, val: float):
            sp.setDecimals(6)
            sp.setRange(-1e12, 1e12)
            sp.setSingleStep(0.1)
            sp.setValue(float(val))

        self.sel_r2e_gain = QDoubleSpinBox(); prep_spin(self.sel_r2e_gain, 1.0)
        self.sel_r2e_offs = QDoubleSpinBox(); prep_spin(self.sel_r2e_offs, 0.0)
        self.sel_e2r_gain = QDoubleSpinBox(); prep_spin(self.sel_e2r_gain, 1.0)
        self.sel_e2r_offs = QDoubleSpinBox(); prep_spin(self.sel_e2r_offs, 0.0)

        form.addRow(self.sel_enabled)
        form.addRow("Raw→Eng gain",   self.sel_r2e_gain)
        form.addRow("Raw→Eng offset", self.sel_r2e_offs)
        form.addRow("Eng→Raw gain",   self.sel_e2r_gain)
        form.addRow("Eng→Raw offset", self.sel_e2r_offs)
        ql.addLayout(form)

        quick_btns = QHBoxLayout()
        self.btn_apply_to_table = QPushButton("Apply to table")
        self.btn_reload_from_store = QPushButton("Reload from store")
        quick_btns.addWidget(self.btn_apply_to_table)
        quick_btns.addWidget(self.btn_reload_from_store)
        quick_btns.addStretch(1)
        ql.addLayout(quick_btns)

        root.addWidget(quick)

        # =========================
        # Full table
        # =========================
        self.table = QTableWidget(len(self._keys), 6, self)
        self.table.setHorizontalHeaderLabels([
            "Group", "Signal", "Enabled", "Raw→Eng gain", "Raw→Eng offset", "Eng→Raw gain / offset"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        for r, key in enumerate(self._keys):
            self._row_for_key[key] = r
            entry = self.scaler.data.get(key, {})

            # Group (read-only)
            grp_item = QTableWidgetItem(group_for(key))
            grp_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r, 0, grp_item)

            # Signal (human label; tooltip shows canonical key)
            lbl_item = QTableWidgetItem(label_for(key))
            lbl_item.setToolTip(key)
            lbl_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r, 1, lbl_item)

            # Enabled
            cb = QCheckBox()
            cb.setChecked(entry.get("enabled", True))
            self.table.setCellWidget(r, 2, cb)

            # Raw→Eng
            rt = entry.get("raw_to_eng", {})
            gain = QDoubleSpinBox(); gain.setDecimals(6); gain.setRange(-1e12, 1e12); gain.setValue(rt.get("gain", 1.0))
            offs = QDoubleSpinBox(); offs.setDecimals(6); offs.setRange(-1e12, 1e12); offs.setValue(rt.get("offset", 0.0))
            self.table.setCellWidget(r, 3, gain)
            self.table.setCellWidget(r, 4, offs)

            # Eng→Raw (gain, offset) stacked
            rt2 = entry.get("eng_to_raw", {})
            cell = QWidget(); lay = QHBoxLayout(cell); lay.setContentsMargins(0, 0, 0, 0)
            g2 = QDoubleSpinBox(); g2.setDecimals(6); g2.setRange(-1e12, 1e12); g2.setValue(rt2.get("gain", 1.0))
            o2 = QDoubleSpinBox(); o2.setDecimals(6); o2.setRange(-1e12, 1e12); o2.setValue(rt2.get("offset", 0.0))
            lay.addWidget(QLabel("g:")); lay.addWidget(g2); lay.addWidget(QLabel("b:")); lay.addWidget(o2)
            self.table.setCellWidget(r, 5, cell)

            # stash refs for save()/reload() and quick-panel sync
            cb._key  = key
            cb._gain = gain; cb._offs = offs; cb._g2 = g2; cb._o2 = o2
            self.table.setRowHeight(r, 28)

        root.addWidget(self.table)

        # Buttons row
        btns = QHBoxLayout()
        save = QPushButton("Save"); save.clicked.connect(self.save)
        reloadb = QPushButton("Reload"); reloadb.clicked.connect(self.reload)
        identity = QPushButton("Set identity for all"); identity.clicked.connect(self.set_identity)
        export_btn = QPushButton("Export Excel…"); export_btn.clicked.connect(self.export_excel)
        import_btn = QPushButton("Load from Excel…"); import_btn.clicked.connect(self.import_excel)
        btns.addWidget(save); btns.addWidget(reloadb); btns.addStretch(1)
        btns.addWidget(identity); btns.addSpacing(12)
        btns.addWidget(export_btn); btns.addWidget(import_btn)
        root.addLayout(btns)

        # Wire up quick panel actions
        self.selector.currentIndexChanged.connect(self._on_select_changed)
        self.only_selected.toggled.connect(self._apply_row_visibility)
        self.btn_apply_to_table.clicked.connect(self._apply_panel_to_table_current)
        self.btn_reload_from_store.clicked.connect(self._reload_panel_from_store_current)

        # Initialize panel with current selection
        self._on_select_changed(self.selector.currentIndex())

    # ---------- helpers ----------
    def _current_key(self) -> str:
        i = self.selector.currentIndex()
        return self.selector.itemData(i)

    def _load_key_to_panel(self, key: str):
        """Populate quick panel from scaler store for given key."""
        self.scaler.reload_if_changed()
        entry = self.scaler.data.get(key, {})
        rt = entry.get("raw_to_eng", {})
        rt2 = entry.get("eng_to_raw", {})
        self.sel_enabled.setChecked(entry.get("enabled", True))
        self.sel_r2e_gain.setValue(float(rt.get("gain", 1.0)))
        self.sel_r2e_offs.setValue(float(rt.get("offset", 0.0)))
        self.sel_e2r_gain.setValue(float(rt2.get("gain", 1.0)))
        self.sel_e2r_offs.setValue(float(rt2.get("offset", 0.0)))

    def _panel_to_table(self, key: str):
        """Copy panel values into the corresponding table row (widgets)."""
        r = self._row_for_key.get(key, None)
        if r is None:
            return
        cb = self.table.cellWidget(r, 2)
        cb.setChecked(self.sel_enabled.isChecked())
        cb._gain.setValue(float(self.sel_r2e_gain.value()))
        cb._offs.setValue(float(self.sel_r2e_offs.value()))
        cb._g2.setValue(float(self.sel_e2r_gain.value()))
        cb._o2.setValue(float(self.sel_e2r_offs.value()))

    def _scroll_to_key(self, key: str):
        r = self._row_for_key.get(key, None)
        if r is None:
            return
        item = self.table.item(r, 1)
        if item is None:
            return
        self.table.selectRow(r)
        self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _apply_row_visibility(self):
        """Show only the selected row if toggled; else show all."""
        only = self.only_selected.isChecked()
        sel_key = self._current_key()
        for r, key in enumerate(self._keys):
            self.table.setRowHidden(r, only and (key != sel_key))

    # ---------- event handlers ----------
    def _on_select_changed(self, idx: int):
        key = self._current_key()
        if not key:
            return
        self._load_key_to_panel(key)
        self._panel_to_table(key)
        self._apply_row_visibility()
        self._scroll_to_key(key)

    def _apply_panel_to_table_current(self):
        key = self._current_key()
        if key:
            self._panel_to_table(key)

    def _reload_panel_from_store_current(self):
        key = self._current_key()
        if key:
            self._load_key_to_panel(key)

    # ---------- table-wide ops ----------
    def set_identity(self):
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 2)
            cb.setChecked(True)
            cb._gain.setValue(1.0); cb._offs.setValue(0.0)
            cb._g2.setValue(1.0);   cb._o2.setValue(0.0)

    def reload(self):
        self.scaler.load()
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 2)
            key = cb._key
            entry = self.scaler.data.get(key, {})
            cb.setChecked(entry.get("enabled", True))

            rt = entry.get("raw_to_eng", {})
            cb._gain.setValue(rt.get("gain", 1.0))
            cb._offs.setValue(rt.get("offset", 0.0))

            rt2 = entry.get("eng_to_raw", {})
            cb._g2.setValue(rt2.get("gain", 1.0))
            cb._o2.setValue(rt2.get("offset", 0.0))

        # keep the quick panel consistent
        self._on_select_changed(self.selector.currentIndex())

    def save(self):
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 2)
            key = cb._key
            enabled = cb.isChecked()
            g  = float(cb._gain.value()); b  = float(cb._offs.value())
            g2 = float(cb._g2.value());   b2 = float(cb._o2.value())
            self.scaler.data[key] = {
                "enabled": enabled,
                "raw_to_eng": {"type": "linear", "gain": g,  "offset": b},
                "eng_to_raw": {"type": "linear", "gain": g2, "offset": b2},
            }
        self.scaler.save()
        QMessageBox.information(self, "Saved", "Calibration saved to: " + self.scaler.path)

    # ---------- Excel/CSV export/import ----------
    def export_excel(self):
        if _HAVE_XLSX:
            path, _ = QFileDialog.getSaveFileName(self, "Export calibration to Excel",
                                                  self._excel_path, "Excel (*.xlsx)")
            if not path:
                return
            self._excel_path = path
            self._export_xlsx(path)
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Export calibration to CSV",
                                                  self._csv_path, "CSV (*.csv)")
            if not path:
                return
            self._csv_path = path
            self._export_csv(path)

    def import_excel(self):
        if _HAVE_XLSX:
            path, _ = QFileDialog.getOpenFileName(self, "Load calibration from Excel",
                                                  self._excel_path, "Excel (*.xlsx)")
            if not path:
                return
            self._excel_path = path
            self._import_xlsx_latest(path)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Load calibration from CSV",
                                                  self._csv_path, "CSV (*.csv)")
            if not path:
                return
            self._csv_path = path
            self._import_csv_latest(path)

        QMessageBox.information(self, "Loaded", "Calibration loaded from:\n" + path)

    # ----- XLSX helpers -----
    def _export_xlsx(self, path: str):
        header = ["saved_at","key","group","label","enabled",
                  "r2e_gain","r2e_offset","e2r_gain","e2r_offset"]

        if os.path.exists(path):
            wb = load_workbook(path)
            ws = wb.active
            if ws.max_row == 1 and all((ws.cell(1, c+1).value is None) for c in range(len(header))):
                ws.append(header)
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "records"
            ws.append(header)

        saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 2)
            key = cb._key
            grp = self.table.item(r, 0).text()
            lbl = self.table.item(r, 1).text()
            enabled = bool(cb.isChecked())
            r2e_g = float(cb._gain.value())
            r2e_b = float(cb._offs.value())
            e2r_g = float(cb._g2.value())
            e2r_b = float(cb._o2.value())
            ws.append([saved_at, key, grp, lbl, enabled, r2e_g, r2e_b, e2r_g, e2r_b])

        wb.save(path)

    def _import_xlsx_latest(self, path: str):
        wb = load_workbook(path, read_only=True)
        ws = wb.active

        col_key = 2
        col_enabled = 5
        col_r2e_g = 6; col_r2e_b = 7
        col_e2r_g = 8; col_e2r_b = 9

        latest = {}
        first = True
        for row in ws.iter_rows(values_only=True):
            if first:
                first = False
                if row and str(row[0]).lower().startswith("saved_at"):
                    continue
            if not row or len(row) < 9:
                continue
            k = row[col_key-1]
            if not k:
                continue
            latest[k] = (row[col_enabled-1], row[col_r2e_g-1], row[col_r2e_b-1],
                         row[col_e2r_g-1], row[col_e2r_b-1])

        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 2)
            key = cb._key
            if key in latest:
                en, g, b, g2, b2 = latest[key]
                try:
                    cb.setChecked(bool(en))
                    cb._gain.setValue(float(g))
                    cb._offs.setValue(float(b))
                    cb._g2.setValue(float(g2))
                    cb._o2.setValue(float(b2))
                except Exception:
                    pass

        self._on_select_changed(self.selector.currentIndex())

    # ----- CSV helpers -----
    def _export_csv(self, path: str):
        header = ["saved_at","key","group","label","enabled",
                  "r2e_gain","r2e_offset","e2r_gain","e2r_offset"]
        new_file = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(header)
            saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for r in range(self.table.rowCount()):
                cb = self.table.cellWidget(r, 2)
                key = cb._key
                grp = self.table.item(r, 0).text()
                lbl = self.table.item(r, 1).text()
                enabled = bool(cb.isChecked())
                r2e_g = float(cb._gain.value())
                r2e_b = float(cb._offs.value())
                e2r_g = float(cb._g2.value())
                e2r_b = float(cb._o2.value())
                w.writerow([saved_at, key, grp, lbl, enabled, r2e_g, r2e_b, e2r_g, e2r_b])

    def _import_csv_latest(self, path: str):
        latest = {}
        with open(path, "r", newline="") as f:
            rd = csv.reader(f)
            header = next(rd, None)
            for row in rd:
                if len(row) < 9:
                    continue
                k = row[1]
                latest[k] = (row[4], row[5], row[6], row[7], row[8])
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 2)
            key = cb._key
            if key in latest:
                en, g, b, g2, b2 = latest[key]
                try:
                    cb.setChecked(en in ("True","true","1",1,True))
                    cb._gain.setValue(float(g))
                    cb._offs.setValue(float(b))
                    cb._g2.setValue(float(g2))
                    cb._o2.setValue(float(b2))
                except Exception:
                    pass

        self._on_select_changed(self.selector.currentIndex())


# Optional: run window standalone for testing
if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    import sys
    app = QApplication(sys.argv)
    w = ScaleCalWindow(Scaler())
    w.show()
    sys.exit(app.exec())
