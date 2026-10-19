"""Viewer-only marker readouts and PDF reports; no Qt, SQL or live controls.

The snapshots contain exactly the displayed samples, after the existing viewer
calibration. Values are never interpolated across a missing sample. PDF export
uses new Matplotlib figures, so it cannot move markers or change the live plots.

Matplotlib API references:
  https://matplotlib.org/stable/api/backend_pdf_api.html
  https://matplotlib.org/stable/api/_as_gen/matplotlib.axes.Axes.annotate.html
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import os
import tempfile
import textwrap

import numpy as np
from matplotlib import rc_context
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.image import imread
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator, ScalarFormatter, LogFormatterSciNotation
from matplotlib.transforms import Bbox


@dataclass
class TraceSnapshot:
    name: str
    label: str
    unit: str
    x: np.ndarray
    y: np.ndarray
    color: str
    warning: str = ""

    def value_at(self, x: float) -> float:
        """Return the Y value at this displayed sample, NOT the nearest valid Y."""
        if not np.isfinite(x) or not len(self.x) or not len(self.y):
            return float("nan")
        # The viewer uses integer displayed-sample indices. Requiring an exact
        # match avoids silently borrowing a different row when a value is NaN.
        indices = np.flatnonzero(np.isfinite(self.x) & (self.x == x))
        if not len(indices) or indices[0] >= len(self.y):
            return float("nan")
        return float(self.y[indices[0]])

    def readout(self, x: float) -> str:
        caption = self.label
        suffix = f" [{self.unit}]" if self.unit else ""
        if suffix and caption.endswith(suffix):
            caption = caption[:-len(suffix)]
        value = format_value(self.value_at(x))
        return f"{caption}: {value}{' ' + self.unit if self.unit else ''}"


@dataclass(frozen=True)
class MarkerSnapshot:
    x: float
    marker_id: int
    color: str
    time_text: str


@dataclass
class PlotSnapshot:
    title: str
    traces: list[TraceSnapshot]
    markers: list[MarkerSnapshot]
    xlim: tuple[float, float]
    ylim: tuple[float, float]
    ylabel: str = ""
    yscale: str = "linear"
    scientific: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class ReportInfo:
    database: str
    start_time: str
    end_time: str
    displayed_count: int
    loaded_count: int
    scaling_mode: str
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    notes: list[str] = field(default_factory=list)


def snap_sample(x: float, count: int) -> int | None:
    """Snap all panes to the same displayed row; clamp the small plot margins."""
    if count <= 0 or x is None or not np.isfinite(x):
        return None
    return max(0, min(count - 1, int(np.floor(float(x) + 0.5))))


def format_value(value: float) -> str:
    return f"{value:.7g}" if np.isfinite(value) else "N/A"


def marker_text(marker: MarkerSnapshot, traces: list[TraceSnapshot]) -> str:
    rows = [f"M{marker.marker_id}", marker.time_text]
    rows.extend(trace.readout(marker.x) for trace in traces)
    if not traces:
        rows.append("No plotted values")
    return "\n".join(rows)


def add_marker_artists(ax, marker, text, *, background="#181A1E", color=None):
    """Y uses axes coordinates, so the readout stays visible after Y zoom/log."""
    ink = color or marker.color
    line = ax.axvline(marker.x, color=ink, alpha=0.9, lw=1.25,
                     linestyle="--", zorder=7)
    label = ax.text(0.02, 0.97, text, transform=ax.transAxes,
                    color=ink, fontsize=8.5, ha="left", va="top", zorder=12,
                    linespacing=1.15,
                    bbox=dict(boxstyle="round,pad=0.30", facecolor=background,
                              edgecolor=ink, alpha=0.97, linewidth=0.7))
    label.set_in_layout(False)
    line.set_in_layout(False)
    return line, label


def position_marker_labels(ax, pairs, renderer) -> bool:
    """Keep readouts inside the axes and avoid the legend / earlier readouts.

    The marker remains at its data X coordinate. Only its text box is moved.
    Dense selections can exceed the screen space; the PDF value table always
    preserves the complete readouts, even when compact graph labels are used.
    """
    bounds = ax.get_window_extent(renderer)
    if bounds.width < 30 or bounds.height < 30:
        return False
    pad = 6.0
    blocked = []
    legend = ax.get_legend()
    if legend is not None and legend.get_visible():
        blocked.append(legend.get_window_extent(renderer).padded(pad))
    changed = False
    lo, hi = sorted(ax.get_xlim())
    inverse = ax.transAxes.inverted()
    for line, label in pairs:
        x = float(line.get_xdata()[0])
        visible = bool(np.isfinite(x) and lo <= x <= hi)
        if label.get_visible() != visible:
            label.set_visible(visible)
            changed = True
        if not visible:
            continue
        box = label.get_window_extent(renderer)
        width, height = box.width + 2 * pad, box.height + 2 * pad
        anchor = ax.get_xaxis_transform().transform((x, 0.98))[0]
        left, right = bounds.x0, max(bounds.x0, bounds.x1 - width)
        preferred = float(np.clip(anchor - width / 2, left, right))
        xs = [preferred, left, right]
        for obstacle in blocked:
            xs += [float(np.clip(obstacle.x0 - width - 2, left, right)),
                   float(np.clip(obstacle.x1 + 2, left, right))]
        xs = sorted(set(xs), key=lambda v: abs(v - preferred))
        ys = [bounds.y1]
        ys += sorted({b.y0 - 2 for b in blocked}, reverse=True)
        chosen = None
        for top in ys:
            if top - height < bounds.y0:
                continue
            for xx in xs:
                candidate = Bbox.from_extents(xx, top - height, xx + width, top)
                if not any(candidate.overlaps(b) for b in blocked):
                    chosen = candidate
                    break
            if chosen is not None:
                break
        if chosen is None:
            chosen = Bbox.from_extents(preferred, bounds.y1 - height,
                                       preferred + width, bounds.y1)
        pos = tuple(inverse.transform((chosen.x0 + pad, chosen.y1 - pad)))
        if not np.allclose(label.get_position(), pos, atol=1e-5, rtol=0):
            label.set_position(pos)
            changed = True
        blocked.append(chosen.padded(2))
    return changed


_PAPER = dict(bg="#FFFFFF", panel="#FFFFFF", text="#202733", dim="#566170", grid="#DCE1E7",
              colors=["#B52F3E", "#34495E", "#32799D", "#8B6A35", "#367D68", "#765785"])
_SCREEN = dict(bg="#090A0C", panel="#181A1E", text="#ECEFF3", dim="#A4ADB9", grid="#30343B")


def _page(info, theme, logo_path=None, subtitle="Displayed plots"):
    fig = Figure(figsize=(8.2677, 11.6929), dpi=120, facecolor=theme["bg"])
    FigureCanvasAgg(fig)
    title_x = 0.07
    if logo_path and Path(logo_path).is_file():
        try:
            image = imread(logo_path)
            logo_ax = fig.add_axes([0.07, 0.919, 0.082, 0.062])
            logo_ax.imshow(image)
            logo_ax.set_axis_off()
            title_x = 0.172
        except (OSError, ValueError):
            pass  # Missing/unreadable logo must not prevent saving real data.
    fig.text(title_x, 0.958, "CNL  |  Record Viewer", fontsize=17, weight="bold", color=theme["text"])
    fig.text(title_x, 0.937, subtitle, fontsize=10, color=theme["dim"])
    fig.text(0.07, 0.902, "Database: " + textwrap.shorten(Path(info.database).name, width=100, placeholder="..."),
             fontsize=8, color=theme["text"])
    fig.text(0.07, 0.883, f"Displayed time span: {info.start_time}  to  {info.end_time}",
             fontsize=7.6, color=theme["dim"])
    fig.text(0.07, 0.864, f"{info.scaling_mode}  |  Displayed: {info.displayed_count:,} / Loaded: {info.loaded_count:,} rows",
             fontsize=8, color=theme["dim"])
    return fig


def _footer(fig, info, page, theme, text):
    fig.text(0.07, 0.045, text, fontsize=7.1, color=theme["dim"], va="top")
    fig.text(0.07, 0.020, f"Crocker Nuclear Laboratory  |  Exported {info.created_at}", fontsize=7, color=theme["dim"])
    fig.text(0.94, 0.020, f"{page}", fontsize=7, color=theme["dim"], ha="right")


def _visible_markers(plot):
    lo, hi = sorted(plot.xlim)
    return [m for m in plot.markers if np.isfinite(m.x) and lo <= m.x <= hi]


def _draw_plot(ax, plot, theme, include_markers, paper):
    ax.set_facecolor(theme["panel"])
    ax.set_axisbelow(True)
    ax.grid(True, color=theme["grid"], linewidth=0.5)
    ax.tick_params(colors=theme["dim"], labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(theme["grid"])
    ax.set_title(plot.title, loc="left", fontsize=11, weight="bold", color=theme["text"], pad=10)
    ax.set_xlabel("Sample (displayed index)", fontsize=8, color=theme["dim"])
    ax.set_ylabel(plot.ylabel or "Recorded value", fontsize=8, color=theme["dim"])
    ax.xaxis.set_major_locator(MaxNLocator(nbins=8, integer=True))
    ax.set_yscale(plot.yscale)
    if plot.yscale == "log":
        ax.yaxis.set_major_formatter(LogFormatterSciNotation())
    else:
        formatter = ScalarFormatter(useMathText=plot.scientific)
        if plot.scientific:
            formatter.set_scientific(True)
            formatter.set_powerlimits((-3, 4))
        ax.yaxis.set_major_formatter(formatter)
    ax.yaxis.get_offset_text().set_color(theme["dim"])
    ax.yaxis.get_offset_text().set_fontsize(7)
    drew = False
    for i, trace in enumerate(plot.traces):
        finite = np.isfinite(trace.x) & np.isfinite(trace.y)
        if not finite.any():
            continue
        color = theme["colors"][i % len(theme["colors"])] if paper else trace.color
        ax.plot(trace.x, trace.y, color=color, lw=1.1, label=trace.label,
                marker="o" if finite.sum() == 1 else None, markersize=3)
        drew = True
    if drew:
        legend = ax.legend(loc="upper right", fontsize=7.5, framealpha=0.97,
                           facecolor=theme["panel"], edgecolor=theme["grid"])
        for label in legend.get_texts():
            label.set_color(theme["text"])
    else:
        ax.text(0.5, 0.5, "No finite plotted values", transform=ax.transAxes,
                ha="center", color=theme["dim"], fontsize=9)
    # No autoscaling from markers or text; preserve the user's current zoom.
    ax.set_xlim(plot.xlim)
    ax.set_ylim(plot.ylim)
    artists = []
    if include_markers:
        markers = _visible_markers(plot)
        # Large selections use IDs on the graph, full values in the following
        # table. Never truncate a value or falsely imply there were no markers.
        compact = len(markers) > 3 or len(plot.traces) > 3
        for marker in markers:
            color = theme["colors"][(marker.marker_id - 1) % len(theme["colors"])] if paper else marker.color
            label = f"M{marker.marker_id}" if compact else marker_text(marker, plot.traces)
            artists.append(add_marker_artists(ax, marker, label, background=theme["panel"], color=color))
    return artists


def _marker_rows(plots):
    rows = []
    for plot in plots:
        for marker in _visible_markers(plot):
            for trace in plot.traces:
                # Keep the legend's alias / conversion-status qualifiers.
                name = trace.label
                if trace.unit and name.endswith(f" [{trace.unit}]"):
                    name = name[:-(len(trace.unit) + 3)]
                rows.append((f"M{marker.marker_id}", marker.time_text,
                             f"{plot.title} / {name}", format_value(trace.value_at(marker.x)), trace.unit or "--"))
    return rows


def _detail_pages(rows, info, theme, logo_path):
    """Paginated complete marker values, including multiple curves and N/A."""
    fig = None
    top = 0.0
    columns = [0.075, 0.16, 0.425, 0.785, 0.87]
    widths = [8, 26, 34, 13, 10]
    for row in rows:
        cells = [textwrap.wrap(str(s), w, break_long_words=True) or [""] for s, w in zip(row, widths)]
        height = max(len(c) for c in cells) * 0.014 + 0.017
        if fig is None or top - height < 0.085:
            if fig is not None:
                yield fig
            fig = _page(info, theme, logo_path, "Marker values / current display units")
            for xx, caption in zip(columns, ["Marker", "Time (viewer local)", "Plot / signal", "Value", "Unit"]):
                fig.text(xx, 0.815, caption, fontsize=8, weight="bold", color=theme["text"])
            top = 0.782
        for xx, cell in zip(columns, cells):
            fig.text(xx, top, "\n".join(cell), fontsize=7.4, color=theme["text"], va="top", linespacing=1.25)
        top -= height
        fig.add_artist(Line2D(
            [0.07, 0.94], [top + 0.006, top + 0.006], transform=fig.transFigure,
            color=theme["grid"], linewidth=0.45))
    if fig is not None:
        yield fig


def _note_pages(notes, info, theme, logo_path):
    """Do not hide calibration warnings or provenance when there are many."""
    lines = []
    for note in notes:
        lines.extend(textwrap.wrap(str(note), width=108) or [""])
        lines.append("")
    for start in range(0, len(lines), 44):
        fig = _page(info, theme, logo_path, "Display / calibration notes")
        fig.text(0.07, 0.813, "\n".join(lines[start:start + 44]), color=theme["text"],
                 fontsize=8, va="top", linespacing=1.4)
        yield fig


def export_pdf(path, plots, info, *, include_markers=True, print_friendly=True,
               one_plot_per_page=False, logo_path=None) -> int:
    """Write an A4 vector PDF atomically, using ONLY the supplied snapshots.

    Existing output survives any rendering/write failure (including a PDF open
    in another program on Windows). No database, GUI artist or calibration is
    changed. Returns the number of pages written.
    """
    if not plots:
        raise ValueError("Select at least one plot to export.")
    destination = Path(path).expanduser().absolute()
    if destination.resolve() == Path(info.database).expanduser().resolve():
        raise ValueError("The PDF destination cannot replace the source database.")
    if destination.suffix.lower() != ".pdf":
        raise ValueError("The output filename must end with .pdf")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Output folder does not exist: {destination.parent}")
    theme = _PAPER if print_friendly else _SCREEN
    groups = [[plot] for plot in plots] if one_plot_per_page else [plots]
    rows = _marker_rows(plots) if include_markers else []
    notes = list(info.notes)
    for plot in plots:
        notes.extend(f"{plot.title}: {warning}" for warning in plot.warnings)
    notes = list(dict.fromkeys(notes))
    fd, temporary = tempfile.mkstemp(prefix=".cnl-report-", suffix=".pdf", dir=destination.parent)
    os.close(fd)  # PdfPages must be able to reopen this on Windows.
    pages = 0
    try:
        # Local rc_context never changes the controller's Matplotlib defaults.
        with rc_context({"font.family": "DejaVu Sans", "text.usetex": False,
                         "pdf.fonttype": 42, "savefig.bbox": None}), PdfPages(temporary) as pdf:
            meta = pdf.infodict()
            meta.update(Title="CNL Record Viewer - displayed plots", Author="Crocker Nuclear Laboratory",
                        Subject=f"{Path(info.database).name} | {info.scaling_mode}")
            for group in groups:
                fig = _page(info, theme, logo_path)
                grid = fig.add_gridspec(len(group), 1, left=0.115, right=0.945,
                                       top=0.80, bottom=0.105, hspace=0.57)
                layouts = []
                for index, plot in enumerate(group):
                    ax = fig.add_subplot(grid[index, 0])
                    layouts.append((ax, _draw_plot(ax, plot, theme, include_markers, print_friendly)))
                pages += 1
                note = "Displayed samples and current zoom; no re-query or interpolation. "
                note += "Marker values follow." if rows else "Markers omitted." if not include_markers else "No visible markers."
                if any(p.warnings for p in group):
                    note += "\nConversion warnings: see display / calibration notes."
                _footer(fig, info, pages, theme, note)
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
                for ax, pairs in layouts:
                    position_marker_labels(ax, pairs, renderer)
                pdf.savefig(fig, facecolor=theme["bg"], bbox_inches=None)
                fig.clear()
            for fig in _detail_pages(rows, info, theme, logo_path):
                pages += 1
                _footer(fig, info, pages, theme,
                        "Values at the nearest displayed sample. N/A = missing / unavailable; -- = unit not specified.\n"
                        "Only markers within the exported X range are listed.")
                pdf.savefig(fig, facecolor=theme["bg"], bbox_inches=None)
                fig.clear()
            for fig in _note_pages(notes, info, theme, logo_path):
                pages += 1
                _footer(fig, info, pages, theme, "Display only. SQLite records and calibration files are unchanged.")
                pdf.savefig(fig, facecolor=theme["bg"], bbox_inches=None)
                fig.clear()
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return pages
