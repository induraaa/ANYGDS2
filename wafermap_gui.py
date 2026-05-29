#!/usr/bin/env python3
"""
GDS2 Wafer Map Converter
Classic Windows desktop application style.
Run:  python wafermap_gui.py
Requires: Python 3.8+  (tkinter included in all standard Python installs)
"""

import re
import math
import threading
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import List, Tuple, Optional, Set



# ─────────────────────────────────────────────────────────────────────────────
#  CORE CONVERTER
# ─────────────────────────────────────────────────────────────────────────────
def parse_gds2(text, die_structure_names):
    coords = []
    re_sref = re.compile(r'^SREF\s*$')
    re_sname = re.compile(r'^SNAME\s+(.+)$')
    re_xy = re.compile(r'^XY\s+(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)')
    re_endel = re.compile(r'^ENDEL\s*$')
    in_sref, cur_sname = False, None
    for line in text.splitlines():
        line = line.strip()
        if re_sref.match(line):
            in_sref, cur_sname = True, None
            continue
        if not in_sref:
            continue
        m = re_sname.match(line)
        if m:
            cur_sname = m.group(1)
            continue
        m = re_xy.match(line)
        if m:
            if cur_sname and (not die_structure_names or cur_sname in die_structure_names):
                coords.append((float(m.group(1)) / 1e6, float(m.group(2)) / 1e6, cur_sname))
            continue
        if re_endel.match(line):
            in_sref = False
    return coords


def _extract_xy_pairs(line):
    pairs = []
    for m in re.finditer(r'(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)', line):
        pairs.append((float(m.group(1)) / 1e6, float(m.group(2)) / 1e6))
    return pairs


def parse_layout_shapes(text, include_snames=None):
    include_set = set(include_snames or [])
    element_types = {"BOUNDARY", "PATH", "BOX", "SREF", "AREF"}
    shapes = []
    current_points = []
    in_element = False
    current_sname = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line in element_types:
            in_element = True
            current_points = []
            current_sname = None
            continue
        if not in_element:
            continue
        m = re.match(r'^SNAME\s+(.+)$', line)
        if m:
            current_sname = m.group(1).strip()
            continue
        if line.startswith("XY"):
            current_points.extend(_extract_xy_pairs(line))
            continue
        if line == "ENDEL":
            if current_points:
                if not include_set or (current_sname in include_set):
                    shapes.append(current_points[:])
            in_element = False
            current_points = []
            current_sname = None
    return shapes


def detect_pitch(coords):
    from collections import Counter
    xs = sorted(set(x for x, y, _ in coords))
    ys = sorted(set(y for x, y, _ in coords))
    def most_common_diff(vals):
        if len(vals) < 2:
            return None
        diffs = [abs(vals[i+1] - vals[i]) for i in range(len(vals)-1)]
        diffs = [d for d in diffs if d > 0]
        if not diffs:
            return None
        return Counter(diffs).most_common(1)[0][0]
    return most_common_diff(xs), most_common_diff(ys)


def auto_detect_die_structures(text):
    """Pick the SREF cell name that forms the largest regular die array."""
    from collections import defaultdict
    by_name = defaultdict(list)
    in_sref = False
    sname = None
    for line in text.splitlines():
        line = line.strip()
        if line == "SREF":
            in_sref, sname = True, None
            continue
        if not in_sref:
            continue
        m = re.match(r"^SNAME\s+(.+)$", line)
        if m:
            sname = m.group(1).strip()
            continue
        m = re.match(r"^XY\s+(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)", line)
        if m and sname:
            by_name[sname].append(
                (float(m.group(1)) / 1e6, float(m.group(2)) / 1e6, sname))
            continue
        if line == "ENDEL":
            in_sref = False

    best_name = None
    best_sites = 0
    for name, pts in by_name.items():
        if len(pts) < 4:
            continue
        px, py = detect_pitch(pts)
        if not px or not py:
            continue
        sites = cluster_die_sites(pts, px, py)
        if len(sites) > best_sites:
            best_sites = len(sites)
            best_name = name
    return [best_name] if best_name else []


def cluster_die_sites(coords, pitch_x, pitch_y):
    """Merge multiple placements (e.g. sub-cells) into one die per pitch site."""
    sites = set()
    for x, y, *_ in coords:
        sites.add((round(x / pitch_x), round(y / pitch_y)))
    return sites


def build_full_map_grid(coords, pitch_x, pitch_y, diameter, cols, rows,
                        street_px=3, die_fill=0.88):
    """
    SCR08-style ASCII map: '.' outside wafer, '1' die area, 'X' scribe/street.
    One merged die footprint per pitch site (multiple SREF sub-cells collapse).
    """
    if not coords or not pitch_x or not pitch_y:
        return [], 0

    sites = cluster_die_sites(coords, pitch_x, pitch_y)
    die_count = len(sites)
    if not sites:
        return [], 0

    min_gx = min(g for g, _ in sites)
    max_gx = max(g for g, _ in sites)
    min_gy = min(g for _, g in sites)
    max_gy = max(g for _, g in sites)
    n_cols_die = max_gx - min_gx + 1
    n_rows_die = max_gy - min_gy + 1

    pad = street_px * 4
    usable_w = max(cols - 2 * pad, n_cols_die + 1)
    usable_h = max(rows - 2 * pad, n_rows_die + 1)
    slot_x = max(4, usable_w // n_cols_die)
    slot_y = max(4, usable_h // n_rows_die)
    die_px_w = max(3, slot_x - street_px)
    die_px_h = max(3, slot_y - street_px)
    map_w = pad * 2 + n_cols_die * slot_x
    map_h = pad * 2 + n_rows_die * slot_y

    grid = [["." for _ in range(map_w)] for _ in range(map_h)]

    for gx, gy in sites:
        rel_x = gx - min_gx
        rel_y = max_gy - gy
        x0 = pad + rel_x * slot_x
        y0 = pad + rel_y * slot_y
        for py in range(y0, min(map_h, y0 + die_px_h)):
            for px in range(x0, min(map_w, x0 + die_px_w)):
                grid[py][px] = "1"

    centers = [(gx * pitch_x, gy * pitch_y) for gx, gy in sites]
    max_r = max(math.hypot(x, y) for x, y in centers) + max(pitch_x, pitch_y)
    wafer_r = max(diameter / 2.0, max_r * 1.08)
    span_mm = 2.0 * wafer_r

    def cell_center_mm(px, py):
        x_mm = (px + 0.5) / map_w * span_mm - wafer_r
        y_mm = wafer_r - (py + 0.5) / map_h * span_mm
        return x_mm, y_mm

    for py in range(map_h):
        for px in range(map_w):
            if grid[py][px] == "1":
                continue
            x_mm, y_mm = cell_center_mm(px, py)
            if math.hypot(x_mm, y_mm) <= wafer_r:
                grid[py][px] = "X"

    # Fit / center into requested output size (e.g. SCR08 340 x 567)
    if map_w != cols or map_h != rows:
        out = [["." for _ in range(cols)] for _ in range(rows)]
        off_x = max(0, (cols - map_w) // 2)
        off_y = max(0, (rows - map_h) // 2)
        for y in range(map_h):
            oy = y + off_y
            if oy >= rows:
                break
            for x in range(map_w):
                ox = x + off_x
                if ox >= cols:
                    break
                out[oy][ox] = grid[y][x]
        grid = out

    return grid, die_count


def build_grid(coords, diameter, die_x, die_y, show_edge):
    half = diameter / 2.0
    die_set = set()
    for x, y, _ in coords:
        gx, gy = round(x / die_x), round(y / die_y)
        if math.sqrt((gx * die_x)**2 + (gy * die_y)**2) <= half:
            die_set.add((gx, gy))
    if die_set:
        min_gx = min(g[0] for g in die_set) - 1
        max_gx = max(g[0] for g in die_set) + 1
        min_gy = min(g[1] for g in die_set) - 1
        max_gy = max(g[1] for g in die_set) + 1
    else:
        r = math.ceil(half / min(die_x, die_y)) + 1
        min_gx, max_gx, min_gy, max_gy = -r, r, -r, r
    grid = []
    for gy in range(max_gy, min_gy - 1, -1):
        row = []
        for gx in range(min_gx, max_gx + 1):
            dist = math.sqrt((gx * die_x)**2 + (gy * die_y)**2)
            if dist > half:
                row.append('.')
            elif (gx, gy) in die_set:
                row.append('?')
            else:
                edge = show_edge and any(
                    (gx + dx, gy + dy) in die_set
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
                row.append('*' if edge else '.')
        grid.append(row)
    return grid


def format_output(grid, wafer_id, diameter, die_count, line_mode, bin_rows=None, die_x=None, die_y=None):
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0
    diam_str = str(diameter)
    header = [
        '"' + wafer_id + '",6,"METRIC","BOTTOM","' + str(die_x if die_x is not None else diam_str) + '","' + str(die_y if die_y is not None else diam_str) + '",' +
        str(n_rows) + ',' + str(n_cols) + ',"0","0"',
        '"44","4"', '"0"', '"1","4"', '"POST"', '0', '0', '0',
        '"FALSE"', '0', '0', '"FALSE"', '"0"', '"0"',
        '"' + str(die_count) + '"',
        '"RVD","RVD","RVD"', '"FALSE"', '""', '"0"', '"100:6"',
        '"RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD"',
        '""', '""',
        '"RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD","RVD"',
    ]
    if bin_rows:
        header.append('"' + str(len(bin_rows)) + '"')
        for b in bin_rows:
            header.append(b)
    out = bytearray()
    for h in header:
        out += (h + '\r\n').encode('latin-1')
    for row in grid:
        cells = ','.join('"' + c + '"' for c in row)
        if line_mode == 'sinf':
            out += (cells + ',"\r"').encode('latin-1') + b'\r\n'
        elif line_mode == 'crlf':
            out += (cells + '\r\n').encode('latin-1')
        else:
            out += (cells + '\n').encode('latin-1')
    return bytes(out)


def format_ascii_layout_output(grid, line_mode):
    if not grid:
        return b""
    sep = "\n"
    if line_mode == "crlf":
        sep = "\r\n"
    elif line_mode == "sinf":
        sep = "\r\n"
    text = sep.join("".join(row) for row in grid) + sep
    return text.encode("latin-1", errors="replace")


def fmt_size(b):
    if b < 1024:    return str(b) + ' B'
    if b < 1024 ** 2: return str(round(b / 1024, 1)) + ' KB'
    return str(round(b / 1024 ** 2, 1)) + ' MB'


# ─────────────────────────────────────────────────────────────────────────────
#  CLASSIC WINDOWS STYLE CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
WIN_BG = "#f0f0f0"
WIN_WHITE = "#ffffff"
WIN_BORDER = "#adadad"
WIN_DARK = "#808080"
WIN_BLUE = "#0066cc"
WIN_BTNFACE = "#e1e1e1"
WIN_TEXT = "#000000"
WIN_TEXT2 = "#555555"

DIE_COL = "#26ff00"
EDGE_COL = "#2e302d"
EMPTY_COL = "#cce4f0"
OUT_COL = "#f0f0f0"

FONT = ("MS Sans Serif", 8)
FONT_BOLD = ("MS Sans Serif", 8, "bold")
FONT_MONO = ("Courier New", 8)
FONT_TITLE = ("MS Sans Serif", 9, "bold")


def label(parent, text, **kw):
    return tk.Label(parent, text=text, bg=WIN_BG, fg=WIN_TEXT, font=FONT, **kw)


def mk_entry(parent, textvariable, width=18, **kw):
    return tk.Entry(parent, textvariable=textvariable,
                    bg=WIN_WHITE, fg=WIN_TEXT, relief=tk.SUNKEN, bd=1,
                    font=FONT_MONO, width=width, **kw)


def mk_btn(parent, text, command, width=12, state=tk.NORMAL, **kw):
    return tk.Button(parent, text=text, command=command,
                     bg=WIN_BTNFACE, fg=WIN_TEXT, relief=tk.RAISED, bd=2,
                     font=FONT, width=width, activebackground="#d0d0d0",
                     cursor="arrow", state=state, **kw)


def group_box(parent, text, **pack_kw):
    f = tk.LabelFrame(parent, text=text, bg=WIN_BG, fg=WIN_TEXT,
                      font=FONT_BOLD, relief=tk.GROOVE, bd=2, padx=6, pady=4)
    f.pack(**pack_kw)
    return f


def hsep(parent):
    tk.Frame(parent, bg=WIN_BORDER, height=1).pack(fill=tk.X)


# ─────────────────────────────────────────────────────────────────────────────
#  TOOLBAR BUTTON  (icon + label, hover/press behaviour)
# ─────────────────────────────────────────────────────────────────────────────
class ToolButton(tk.Frame):
    def __init__(self, parent, icon, caption, command, **kw):
        super().__init__(parent, bg=WIN_BG, cursor="arrow", relief=tk.FLAT, bd=1, **kw)
        self._cmd = command
        self._enabled = True
        self._icon_lbl = tk.Label(self, text=icon, bg=WIN_BG,
                                  font=("Segoe UI Symbol", 16), fg=WIN_TEXT, width=3)
        self._icon_lbl.pack()
        self._cap_lbl = tk.Label(self, text=caption, bg=WIN_BG,
                                 font=("MS Sans Serif", 7), fg=WIN_TEXT)
        self._cap_lbl.pack()
        self._bind_all()

    def _bind_all(self):
        for w in (self, self._icon_lbl, self._cap_lbl):
            w.bind("<Button-1>", self._press)
            w.bind("<ButtonRelease-1>", self._release)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _unbind_all(self):
        for w in (self, self._icon_lbl, self._cap_lbl):
            for ev in ("<Button-1>", "<ButtonRelease-1>", "<Enter>", "<Leave>"):
                w.unbind(ev)
        self.config(relief=tk.FLAT)

    def _press(self, _):
        if self._enabled: self.config(relief=tk.SUNKEN)

    def _release(self, _):
        if self._enabled:
            self.config(relief=tk.FLAT)
            self._cmd()

    def _enter(self, _):
        if self._enabled: self.config(relief=tk.RAISED)

    def _leave(self, _):
        if self._enabled: self.config(relief=tk.FLAT)

    def set_state(self, state):
        self._enabled = (state == tk.NORMAL)
        fg = WIN_TEXT if self._enabled else WIN_DARK
        self._icon_lbl.config(fg=fg)
        self._cap_lbl.config(fg=fg)
        if self._enabled:
            self._bind_all()
        else:
            self._unbind_all()


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN APPLICATION
# ─────────────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GDS2 Wafer Map Converter")
        self.geometry("1100x700")
        self.minsize(860, 560)
        self.configure(bg=WIN_BG)
        self.option_add("*Font", FONT)
        self.option_add("*Background", WIN_BG)

        # Set window icon
        try:
            icon_path = Path(__file__).parent / "cat.ico"
            if icon_path.exists():
                self.iconbitmap(str(icon_path))
        except Exception:
            pass

        self._gds_text = None
        self._grid = None
        self._out_bytes = None
        self._cell_px = 5
        self._input_path = None
        self._canvas_img = None
        self._update_timer = None
        self._text_font_size = 8
        self._fit_pending = False

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._set_status("Ready")

    # ── Menu ──────────────────────────────────────────────────────────────────
    def _build_menu(self):
        mb = tk.Menu(self, font=FONT, bg=WIN_BG, fg=WIN_TEXT, relief=tk.FLAT)
        self.config(menu=mb)

        fm = tk.Menu(mb, tearoff=0, font=FONT, bg=WIN_BG)
        fm.add_command(label="Open GDS2 File...     Ctrl+O", command=self._browse)
        # 👉 Add this:
        fm.add_command(label="Open ASCII Wafer Map...   Ctrl+Shift+O", command=self._open_ascii_map)
        fm.add_separator()
        fm.add_command(label="Export Wafer Map...   Ctrl+S", command=self._export)
        fm.add_separator()
        fm.add_command(label="Reset", command=self._reset)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.quit)
        mb.add_cascade(label="File", menu=fm)

        vm = tk.Menu(mb, tearoff=0, font=FONT, bg=WIN_BG)
        vm.add_command(label="Zoom In", command=lambda: self._zoom(1.4))
        vm.add_command(label="Zoom Out", command=lambda: self._zoom(0.71))
        vm.add_command(label="Fit to Window", command=self._fit)
        mb.add_cascade(label="View", menu=vm)

        hm = tk.Menu(mb, tearoff=0, font=FONT, bg=WIN_BG)
        hm.add_command(label="About...", command=self._about)
        mb.add_cascade(label="Help", menu=hm)

        self.bind("<Control-o>", lambda e: self._browse())
        self.bind("<Control-s>", lambda e: self._export())
        # 👉 Add this:
        self.bind("<Control-Shift-O>", lambda e: self._open_ascii_map())
    # ── Toolbar ───────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        tb = tk.Frame(self, bg=WIN_BG, relief=tk.RAISED, bd=1)
        tb.pack(fill=tk.X)

        self._tbb_open = ToolButton(tb, "📂", "Open", self._browse)
        self._tbb_open.pack(side=tk.LEFT, padx=2, pady=2)

        self._tbb_open_map = ToolButton(tb, "📄", "Open Map", self._open_ascii_map)
        self._tbb_open_map.pack(side=tk.LEFT, padx=2, pady=2)

        self._tbb_convert = ToolButton(tb, "⚙", "Convert", self._run_convert)
        self._tbb_convert.pack(side=tk.LEFT, padx=2, pady=2)
        self._tbb_convert.set_state(tk.DISABLED)

        self._tbb_export = ToolButton(tb, "💾", "Export", self._export)
        self._tbb_export.pack(side=tk.LEFT, padx=2, pady=2)
        self._tbb_export.set_state(tk.DISABLED)

        tk.Frame(tb, bg=WIN_BORDER, width=1, relief=tk.SUNKEN).pack(
            side=tk.LEFT, fill=tk.Y, padx=5, pady=4)

        self._tbb_zin = ToolButton(tb, "🔍", "Zoom In", lambda: self._zoom(1.4))
        self._tbb_zin.pack(side=tk.LEFT, padx=2, pady=2)
        self._tbb_zin.set_state(tk.DISABLED)

        self._tbb_zout = ToolButton(tb, "🔎", "Zoom Out", lambda: self._zoom(0.71))
        self._tbb_zout.pack(side=tk.LEFT, padx=2, pady=2)
        self._tbb_zout.set_state(tk.DISABLED)

        self._tbb_fit = ToolButton(tb, "⊡", "Fit", self._fit)
        self._tbb_fit.pack(side=tk.LEFT, padx=2, pady=2)
        self._tbb_fit.set_state(tk.DISABLED)

        tk.Frame(tb, bg=WIN_BORDER, width=1, relief=tk.SUNKEN).pack(
            side=tk.LEFT, fill=tk.Y, padx=5, pady=4)

        ToolButton(tb, "↺", "Reset", self._reset).pack(
            side=tk.LEFT, padx=2, pady=2)

        self._zoom_lbl = tk.Label(tb, text="Zoom: 100%",
                                  bg=WIN_BG, fg=WIN_TEXT2,
                                  font=FONT, relief=tk.SUNKEN, bd=1, padx=6)
        self._zoom_lbl.pack(side=tk.RIGHT, padx=8, pady=5)

    # ── Body ──────────────────────────────────────────────────────────────────
    def _build_body(self):
        body = tk.Frame(self, bg=WIN_BG)
        body.pack(fill=tk.BOTH, expand=True)

        # Left sidebar
        left = tk.Frame(body, bg=WIN_BG, width=275, relief=tk.SUNKEN, bd=1)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 0), pady=4)
        left.pack_propagate(False)
        self._build_sidebar(left)

        # Divider
        tk.Frame(body, bg=WIN_BORDER, width=1).pack(
            side=tk.LEFT, fill=tk.Y, pady=4)

        # Right map area
        right = tk.Frame(body, bg=WIN_BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._build_maparea(right)

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        # Scrollable inner frame
        c = tk.Canvas(parent, bg=WIN_BG, highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(parent, orient=tk.VERTICAL, command=c.yview)
        inner = tk.Frame(c, bg=WIN_BG)
        inner.bind("<Configure>",
                   lambda e: c.configure(scrollregion=c.bbox("all")))
        c.create_window((0, 0), window=inner, anchor="nw")
        c.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        c.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── File group ──
        grp = group_box(inner, "Input File", fill=tk.X, padx=6, pady=(6, 4))
        self._file_lbl = tk.Label(grp, text="No file selected",
                                  bg=WIN_WHITE, fg=WIN_TEXT2,
                                  relief=tk.SUNKEN, bd=1,
                                  font=FONT_MONO, anchor="w",
                                  padx=4, width=28)
        self._file_lbl.pack(fill=tk.X, pady=(0, 5))
        mk_btn(grp, "Browse...", self._browse, width=12).pack(anchor="w")

        # ── Wafer settings ──
        grp = group_box(inner, "Wafer Settings", fill=tk.X, padx=6, pady=4)
        grp.columnconfigure(1, weight=1)

        label(grp, "Wafer ID:").grid(row=0, column=0, sticky="w", pady=2)
        self._v_wafer_id = tk.StringVar(value="GDS2_WAFER_MAP")
        mk_entry(grp, self._v_wafer_id, width=18).grid(
            row=0, column=1, sticky="ew", padx=(4, 0), pady=2)

        label(grp, "Diameter (mm):").grid(row=1, column=0, sticky="w", pady=2)
        self._v_diameter = tk.StringVar(value="147.3")
        mk_entry(grp, self._v_diameter, width=10).grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=2)

        label(grp, "Die Size X (mm):").grid(row=2, column=0, sticky="w", pady=2)
        self._v_die_x = tk.StringVar(value="1.473")
        mk_entry(grp, self._v_die_x, width=10).grid(
            row=2, column=1, sticky="w", padx=(4, 0), pady=2)

        label(grp, "Die Size Y (mm):").grid(row=3, column=0, sticky="w", pady=2)
        self._v_die_y = tk.StringVar(value="1.473")
        mk_entry(grp, self._v_die_y, width=10).grid(
            row=3, column=1, sticky="w", padx=(4, 0), pady=2)

        mk_btn(grp, "Auto-detect", self._auto_detect_pitch, width=10).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=(0,0), pady=(2,2))

        label(grp, "Die Structures:").grid(row=5, column=0, sticky="nw", pady=2)
        self._v_structs = tk.StringVar(value="")
        tk.Entry(grp, textvariable=self._v_structs,
                 bg=WIN_WHITE, fg=WIN_TEXT, relief=tk.SUNKEN,
                 bd=1, font=FONT_MONO, width=18).grid(
            row=5, column=1, sticky="ew", padx=(4, 0), pady=2)

        tk.Label(grp, text="(SNAME filter; empty = auto-detect die cell)",
                 bg=WIN_BG, fg=WIN_TEXT2,
                 font=("MS Sans Serif", 7)).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=2)

        self._v_edge = tk.BooleanVar(value=True)
        tk.Checkbutton(grp, text="Show edge dies  (*)",
                       variable=self._v_edge,
                       bg=WIN_BG, fg=WIN_TEXT, font=FONT,
                       activebackground=WIN_BG,
                       selectcolor=WIN_WHITE).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(6, 2))

        self._v_mode = tk.StringVar(value="layout")
        tk.Label(grp, text="Conversion Mode:", bg=WIN_BG, fg=WIN_TEXT, font=FONT).grid(
            row=8, column=0, sticky="w", pady=(8, 2))
        tk.Frame(grp, bg=WIN_BG).grid(row=9, column=0, columnspan=2, sticky="ew")
        tk.Radiobutton(grp, text="Wafer map (die-based)", variable=self._v_mode,
                       value="wafer", bg=WIN_BG, fg=WIN_TEXT,
                       font=FONT, activebackground=WIN_BG,
                       selectcolor=WIN_WHITE).grid(row=9, column=0, columnspan=2, sticky="w")
        tk.Radiobutton(grp, text="Full ASCII map (SCR08-style)", variable=self._v_mode,
                       value="layout", bg=WIN_BG, fg=WIN_TEXT,
                       font=FONT, activebackground=WIN_BG,
                       selectcolor=WIN_WHITE).grid(row=10, column=0, columnspan=2, sticky="w")

        label(grp, "ASCII Cols:").grid(row=11, column=0, sticky="w", pady=2)
        self._v_layout_cols = tk.StringVar(value="340")
        mk_entry(grp, self._v_layout_cols, width=10).grid(
            row=11, column=1, sticky="w", padx=(4, 0), pady=2)

        label(grp, "ASCII Rows:").grid(row=12, column=0, sticky="w", pady=2)
        self._v_layout_rows = tk.StringVar(value="567")
        mk_entry(grp, self._v_layout_rows, width=10).grid(
            row=12, column=1, sticky="w", padx=(4, 0), pady=2)

        # ── Output settings ──
        grp = group_box(inner, "Output Settings", fill=tk.X, padx=6, pady=4)
        grp.columnconfigure(1, weight=1)

        label(grp, "Filename:").grid(row=0, column=0, sticky="w", pady=2)
        self._v_out_name = tk.StringVar(value="wafermap.txt")
        mk_entry(grp, self._v_out_name, width=18).grid(
            row=0, column=1, sticky="ew", padx=(4, 0), pady=2)

        label(grp, "Line Ending:").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 2))

        self._v_line_end = tk.StringVar(value="sinf")
        for txt, val in [
            ("SINF format  (required for product reader)", "sinf"),
            ("CRLF — Windows", "crlf"),
            ("LF — Unix", "lf"),
        ]:
            tk.Radiobutton(grp, text=txt, variable=self._v_line_end,
                           value=val, bg=WIN_BG, fg=WIN_TEXT,
                           font=FONT, activebackground=WIN_BG,
                           selectcolor=WIN_WHITE).grid(
                columnspan=2, sticky="w", padx=2, pady=1)

        # ── Actions ──
        grp = group_box(inner, "Actions", fill=tk.X, padx=6, pady=4)
        self._btn_convert = mk_btn(grp, "Convert", self._run_convert,
                                   width=16, state=tk.DISABLED)
        self._btn_convert.pack(fill=tk.X, pady=(0, 4))

        self._btn_export = mk_btn(grp, "Export .txt...", self._export,
                                  width=16, state=tk.DISABLED)
        self._btn_export.pack(fill=tk.X, pady=(0, 4))

        mk_btn(grp, "Reset", self._reset, width=16).pack(fill=tk.X)

        # ── Statistics ──
        grp = group_box(inner, "Statistics", fill=tk.X, padx=6, pady=4)
        grp.columnconfigure(1, weight=1)
        for i, (txt, attr) in enumerate([
            ("Dies Found:", "_s_dies"),
            ("Map Rows:", "_s_rows"),
            ("Map Cols:", "_s_cols"),
            ("File Size:", "_s_size"),
            ("Parse Time:", "_s_time"),
        ]):
            label(grp, txt).grid(row=i, column=0, sticky="w", pady=1)
            v = tk.Label(grp, text="—", bg=WIN_BG,
                         fg=WIN_BLUE, font=FONT_BOLD, anchor="w")
            v.grid(row=i, column=1, sticky="w", padx=(6, 0))
            setattr(self, attr, v)

        # ── Legend ──
        grp = group_box(inner, "Legend", fill=tk.X, padx=6, pady=(4, 4))
        for sym, col, desc in [
            ("?", DIE_COL, "Active die (wafer mode)"),
            ("*", EDGE_COL, "Edge / scribe (wafer mode)"),
            ("1", DIE_COL, "Die (full map)"),
            ("X", EDGE_COL, "Scribe / street (full map)"),
            (".", OUT_COL, "Outside wafer"),
        ]:
            row = tk.Frame(grp, bg=WIN_BG)
            row.pack(anchor="w", pady=2)
            tk.Label(row, text="   ", bg=col,
                     relief=tk.RAISED, bd=1).pack(side=tk.LEFT, padx=(0, 6))
            tk.Label(row, text='"' + sym + '"  ' + desc,
                     bg=WIN_BG, fg=WIN_TEXT, font=FONT).pack(side=tk.LEFT)

        # ── Bin Definitions ──
        grp = group_box(inner, "Bin Definitions", fill=tk.X, padx=6, pady=(4, 8))
        tk.Label(grp, text="One bin per line. Format:", bg=WIN_BG, fg=WIN_TEXT2,
                 font=("MS Sans Serif", 7)).pack(anchor="w")
        tk.Label(grp, text='"ID","NAME","","lim","0","P/F",color,"tol","","Bool"',
                 bg=WIN_BG, fg=WIN_TEXT2, font=("Courier New", 6),
                 wraplength=220, justify="left").pack(anchor="w", pady=(0, 4))

        btn_row = tk.Frame(grp, bg=WIN_BG)
        btn_row.pack(fill=tk.X, pady=(0, 4))
        mk_btn(btn_row, "Load .txt...", self._load_bin_template, width=10).pack(side=tk.LEFT, padx=(0, 4))
        mk_btn(btn_row, "Save .txt...", self._save_bin_template, width=10).pack(side=tk.LEFT)

        self._bin_txt = tk.Text(grp, bg=WIN_WHITE, fg=WIN_TEXT,
                                font=("Courier New", 7), relief=tk.SUNKEN, bd=1,
                                wrap=tk.NONE, height=12, width=28)
        bin_vsb = tk.Scrollbar(grp, orient=tk.VERTICAL, command=self._bin_txt.yview)
        self._bin_txt.configure(yscrollcommand=bin_vsb.set)
        bin_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._bin_txt.pack(fill=tk.BOTH, expand=True)

        # Default bin table
        default_bins = [
            '"1","PASS","","0","0","PASS",65280,"0","0","False"',
            '"2","PASS","","0","0","PASS",65280,"0","0","False"',
            '"3","PASS","","0","0","PASS",65280,"0","0","False"',
            '"4","PASS","","0","0","PASS",65280,"0","0","False"',
            '"5","LEAKAGE 1","","","","FAIL",16777088,"","","False"',
            '"6","LEAKAGE 2","","","","FAIL",16711680,"","","False"',
            '"7","LEAKAGE 3","","","","FAIL",8404992,"","","False"',
            '"8","BREAKDOWN 1","","","","FAIL",22446,"","","False"',
            '"9","BREAKDOWN 2","","","","FAIL",232147,"","","False"',
            '"10","BREAKDOWN 3","","","","FAIL",128,"","","False"',
            '"11","SATURATION 1","","100","0","FAIL",65535,"10","","True"',
            '"12","HFE","","","","FAIL",4227327,"","","False"',
            '"13","OTHER VOLTAGE","","20","0","FAIL",8388736,"5","","True"',
            '"14","JUNCTION RES","","","","FAIL",16744703,"","","False"',
            '"15","OTHER CURRENT","","","","FAIL",7715583,"","","False"',
            '"16","SPARE","","","","FAIL",10551106,"","","False"',
            '"17","SPARE","","","","FAIL",16593349,"","","False"',
            '"18","SPARE","","","","FAIL",3831306,"","","False"',
            '"19","SPARE","","","","FAIL",8388863,"","","False"',
            '"20","SPARE","","","","FAIL",32896,"","","False"',
            '"21","KELVIN","","20","0","FAIL",2631835,"5","0","True"',
            '"22","CONTINUITY","","50","0","FAIL",255,"5","","True"',
            '"40","OPTICAL DEFECT","","","","FAIL",1052688,"","","False"',
            '"98","DPAT","","","","FAIL",14401252,"","","False"',
            '"99","GDBN","","","","FAIL",13145877,"","","False"',
        ]
        self._bin_txt.insert(tk.END, "\n".join(default_bins))

    # ── Map Area ──────────────────────────────────────────────────────────────
    def _build_maparea(self, parent):
        # Top bar with coord display
        topbar = tk.Frame(parent, bg=WIN_BG)
        topbar.pack(fill=tk.X)

        tk.Label(topbar, text="Map View", bg=WIN_BG, fg=WIN_TEXT,
                 font=FONT_BOLD).pack(side=tk.LEFT, padx=8, pady=2)

        self._coord_lbl = tk.Label(
            topbar, text="", bg=WIN_BG, fg=WIN_TEXT2,
            font=FONT_MONO, relief=tk.SUNKEN, bd=1, padx=6)
        self._coord_lbl.pack(side=tk.RIGHT, padx=4, pady=2)

        # Grid line toggle
        self._v_grid = tk.BooleanVar(value=True)
        tk.Checkbutton(topbar, text="Grid Lines",
                       variable=self._v_grid,
                       bg=WIN_BG, fg=WIN_TEXT, font=FONT,
                       activebackground=WIN_BG,
                       selectcolor=WIN_WHITE,
                       command=self._draw).pack(side=tk.RIGHT, padx=4)

        hsep(parent)

        # Split pane: map on left, editable text on right
        split = tk.PanedWindow(parent, orient=tk.HORIZONTAL,
                               bg=WIN_BG, sashwidth=5,
                               sashrelief=tk.RAISED, bd=1)
        split.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        # ── Left: Map Canvas ──
        self._map_frame = tk.Frame(split, bg=WIN_BG)
        split.add(self._map_frame, minsize=300)

        hsc = tk.Scrollbar(self._map_frame, orient=tk.HORIZONTAL)
        vsc = tk.Scrollbar(self._map_frame, orient=tk.VERTICAL)
        self._canvas = tk.Canvas(
            self._map_frame,
            bg=WIN_BG, relief=tk.SUNKEN, bd=2,
            cursor="crosshair",
            xscrollcommand=hsc.set,
            yscrollcommand=vsc.set)
        hsc.config(command=self._canvas.xview)
        vsc.config(command=self._canvas.yview)
        vsc.pack(side=tk.RIGHT, fill=tk.Y)
        hsc.pack(side=tk.BOTTOM, fill=tk.X)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._empty_lbl = tk.Label(
            self._canvas,
            text="Open a GDS2 .txt file and click  Convert",
            bg=WIN_BG, fg=WIN_TEXT2,
            font=("MS Sans Serif", 10))
        self._empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

        self._canvas.bind("<Motion>", self._mouse_move)
        self._canvas.bind("<Leave>", lambda e: self._coord_lbl.config(text=""))
        self._canvas.bind("<MouseWheel>", self._scroll)
        self._canvas.bind("<Control-MouseWheel>", self._ctrl_scroll)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<Button-1>", self._cell_click)
        # ── Right: Editable Raw Text ──
        self._raw_frame = tk.Frame(split, bg=WIN_BG)
        split.add(self._raw_frame, minsize=300)

        # Header
        raw_hdr = tk.Frame(self._raw_frame, bg=WIN_BG)
        raw_hdr.pack(fill=tk.X)
        tk.Label(raw_hdr, text="Raw Output  (editable — updates map live)",
                 bg=WIN_BG, fg=WIN_TEXT, font=FONT_BOLD).pack(
            side=tk.LEFT, padx=8, pady=2)

        hsep(self._raw_frame)

        # Text area
        rhs = tk.Scrollbar(self._raw_frame, orient=tk.HORIZONTAL)
        rvs = tk.Scrollbar(self._raw_frame, orient=tk.VERTICAL)
        self._raw_txt = tk.Text(
            self._raw_frame,
            bg=WIN_WHITE, fg=WIN_TEXT, font=FONT_MONO,
            relief=tk.SUNKEN, bd=2, wrap=tk.NONE,
            xscrollcommand=rhs.set, yscrollcommand=rvs.set)
        rhs.config(command=self._raw_txt.xview)
        rvs.config(command=self._raw_txt.yview)
        rvs.pack(side=tk.RIGHT, fill=tk.Y)
        rhs.pack(side=tk.BOTTOM, fill=tk.X)
        self._raw_txt.pack(fill=tk.BOTH, expand=True)

        # Bind text changes to auto-update map (with debouncing)
        self._raw_txt.bind("<<Modified>>", self._on_text_modified)

        # Bind Ctrl+MouseWheel for text zoom
        self._raw_txt.bind("<Control-MouseWheel>", self._on_text_zoom)

    # ── Status Bar ────────────────────────────────────────────────────────────
    def _build_statusbar(self):
        hsep(self)
        sb = tk.Frame(self, bg=WIN_BG, height=22)
        sb.pack(fill=tk.X, side=tk.BOTTOM)
        sb.pack_propagate(False)
        self._status_lbl = tk.Label(
            sb, text="Ready", bg=WIN_BG, fg=WIN_TEXT,
            font=FONT, relief=tk.SUNKEN, bd=1, anchor="w", padx=6)
        self._status_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=1)
        self._status_right = tk.Label(
            sb, text="", bg=WIN_BG, fg=WIN_TEXT,
            font=FONT, relief=tk.SUNKEN, bd=1, padx=6, width=16)
        self._status_right.pack(side=tk.RIGHT, padx=2, pady=1)

    def _set_status(self, msg, right=""):
        self._status_lbl.config(text=msg)
        self._status_right.config(text=right)

    # ── Browse ────────────────────────────────────────────────────────────────
    def _browse(self):
        path = filedialog.askopenfilename(
            title="Open GDS2 Text File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        p = Path(path)
        try:
            self._gds_text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            messagebox.showerror("File Error", str(e))
            return
        self._input_path = p
        sz = fmt_size(p.stat().st_size)
        nm = p.name if len(p.name) <= 30 else "..." + p.name[-27:]
        self._file_lbl.config(text=nm, fg=WIN_TEXT)
        suffix = "_fullmap.txt" if self._v_mode.get() == "layout" else "_wafermap.txt"
        self._v_out_name.set(p.stem + suffix)
        self._btn_convert.config(state=tk.NORMAL)
        self._tbb_convert.set_state(tk.NORMAL)
        self._set_status("Loaded: " + p.name + "  (" + sz + ")")

    # ── Convert ───────────────────────────────────────────────────────────────
    def _run_convert(self):
        if not self._gds_text:
            messagebox.showwarning("No File", "Please open a GDS2 file first.")
            return
        self._btn_convert.config(state=tk.DISABLED)
        self._btn_export.config(state=tk.DISABLED)
        self._tbb_convert.set_state(tk.DISABLED)
        self._tbb_export.set_state(tk.DISABLED)
        self._set_status("Converting — please wait...")
        threading.Thread(target=self._convert_worker, daemon=True).start()

    def _convert_worker(self):
        import time
        t0 = time.perf_counter()
        try:
            wafer_id = self._v_wafer_id.get() or "GDS2_WAFER_MAP"
            diameter = float(self._v_diameter.get())
            die_x = float(self._v_die_x.get())
            die_y = float(self._v_die_y.get())
            structs = [s.strip() for s in self._v_structs.get().split(",") if s.strip()]
            show_edge = self._v_edge.get()
            line_mode = self._v_line_end.get()
            mode = self._v_mode.get()

            if mode == "layout":
                cols = max(20, int(self._v_layout_cols.get()))
                rows = max(10, int(self._v_layout_rows.get()))
                die_names = structs or auto_detect_die_structures(self._gds_text)
                coords = parse_gds2(self._gds_text, die_names)
                if not coords:
                    self.after(0, lambda: self._conv_err(
                        "No die placements found.\n\n"
                        "Set 'Die Structures' to the chip SNAME (e.g. TVSTI309_CHIP)\n"
                        "or leave empty to auto-detect the die array cell."))
                    return
                pitch_x, pitch_y = detect_pitch(coords)
                if not pitch_x or not pitch_y:
                    pitch_x, pitch_y = die_x, die_y
                grid, item_count = build_full_map_grid(
                    coords, pitch_x, pitch_y, diameter, cols, rows)
                if not grid:
                    self.after(0, lambda: self._conv_err("Could not build ASCII map."))
                    return
                out = format_ascii_layout_output(grid, line_mode)
            else:
                coords = parse_gds2(self._gds_text, structs)
                if not coords:
                    self.after(0, lambda: self._conv_err(
                        "No die coordinates found.\n\n"
                        "Check 'Die Structures' matches the SNAME\n"
                        "values in your GDS2 file."))
                    return

                grid = build_grid(coords, diameter, die_x, die_y, show_edge)
                bin_rows = self._get_bin_rows()
                out = format_output(grid, wafer_id, diameter, len(coords), line_mode, bin_rows, die_x=die_x, die_y=die_y)
                item_count = len(coords)
            elapsed = time.perf_counter() - t0

            self.after(0, lambda: self._conv_done(grid, out, item_count, elapsed))
        except ValueError as e:
            self.after(0, lambda: self._conv_err(
                "Invalid setting:\n" + str(e) + "\n\nCheck Diameter / Die Size values."))
        except Exception as e:
            self.after(0, lambda: self._conv_err(str(e)))

    def _conv_done(self, grid, out, n_dies, elapsed):
        self._grid = grid
        self._out_bytes = out
        rows = len(grid)
        cols = len(grid[0]) if grid else 0
        self._s_dies.config(text=f"{n_dies:,}")
        self._s_rows.config(text=str(rows))
        self._s_cols.config(text=str(cols))
        self._s_size.config(text=fmt_size(len(out)))
        self._s_time.config(text=f"{elapsed:.2f}s")
        self._btn_convert.config(state=tk.NORMAL)
        self._btn_export.config(state=tk.NORMAL)
        for tb in (self._tbb_convert, self._tbb_export,
                   self._tbb_zin, self._tbb_zout, self._tbb_fit):
            tb.set_state(tk.NORMAL)
        self._set_status(
            f"Done — {n_dies:,} dies  |  {rows} x {cols}  |  {elapsed:.2f}s",
            fmt_size(len(out)))
        self._empty_lbl.place_forget()
        self._update_raw()
        self._fit_pending = True
        self.after(200, self._fit)

    def _conv_err(self, msg):
        self._btn_convert.config(state=tk.NORMAL)
        self._tbb_convert.set_state(tk.NORMAL)
        self._set_status("Conversion failed.")
        messagebox.showerror("Conversion Error", msg)

    # ── Canvas Drawing ────────────────────────────────────────────────────────
    def _draw(self):
        if not self._grid:
            return

        self._canvas.delete("all")
        grid = self._grid
        rows = len(grid)
        cols = len(grid[0])
        px = self._cell_px

        COL = {'?': DIE_COL, '*': EDGE_COL, '.': OUT_COL, '1': DIE_COL, 'X': EDGE_COL}

        for r in range(rows):
            y1 = r * px
            y2 = y1 + px
            for c in range(cols):
                x1 = c * px
                x2 = x1 + px
                color = COL.get(grid[r][c], OUT_COL)
                self._canvas.create_rectangle(
                    x1, y1, x2, y2,
                    fill=color, outline=color if not self._v_grid.get() else ""
                )

        # Grid lines fast‑render
        if self._v_grid.get() and px >= 6:
            # horizontal
            for r in range(rows + 1):
                y = r * px
                self._canvas.create_line(0, y, cols * px, y, fill="#999999")
            # vertical
            for c in range(cols + 1):
                x = c * px
                self._canvas.create_line(x, 0, x, rows * px, fill="#999999")

        self._canvas.config(scrollregion=(0, 0, cols * px, rows * px))
        self._zoom_lbl.config(text=f"Zoom: {round(px / 5 * 100)}%")

    def _zoom(self, factor):
        self._cell_px = max(1, min(40, round(self._cell_px * factor)))
        self._draw()

    def _on_canvas_configure(self, event):
        """Re-fit the map when the canvas is resized, but only if a fit is pending."""
        if self._fit_pending and self._grid:
            self._fit_pending = False
            self._fit()

    def _fit(self):
        if not self._grid:
            return
        self._canvas.update_idletasks()
        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        # If canvas not yet laid out, retry after a short delay
        if w <= 1 or h <= 1:
            self.after(100, self._fit)
            return
        w -= 8
        h -= 8
        rows = len(self._grid)
        cols = len(self._grid[0]) if self._grid else 1
        self._cell_px = max(1, min(w // cols, h // rows))
        self._draw()

    def _mouse_move(self, event):
        if not self._grid:
            return
        px = self._cell_px
        cx = int(self._canvas.canvasx(event.x) // px)
        cy = int(self._canvas.canvasy(event.y) // px)
        rows = len(self._grid)
        cols = len(self._grid[0]) if self._grid else 0
        if 0 <= cx < cols and 0 <= cy < rows:
            ch = self._grid[cy][cx]
            nm = {'?': 'Die', '*': 'Edge', '.': 'Outside', '1': 'Die', 'X': 'Scribe'}.get(ch, '?')
            self._coord_lbl.config(
                text="  Col " + str(cx) + "   Row " + str(cy) + "   [" + nm + "]  ")

    def _cell_click(self, event):
        """Single-click a cell to cycle: ? → * → . → ?"""
        if not self._grid:
            return
        px = self._cell_px
        cx = int(self._canvas.canvasx(event.x) // px)
        cy = int(self._canvas.canvasy(event.y) // px)
        rows = len(self._grid)
        cols = len(self._grid[0]) if self._grid else 0
        if not (0 <= cx < cols and 0 <= cy < rows):
            return

        # Cycle: ? -> * -> . -> 1 -> X -> ?
        cycle = {'?': '*', '*': '.', '.': '1', '1': 'X', 'X': '?'}
        current = self._grid[cy][cx]
        new_val = cycle.get(current, '?')

        # Update grid in-place
        row = list(self._grid[cy])
        row[cx] = new_val
        self._grid[cy] = row

        # Redraw canvas
        self._draw()

        # Sync back to the Raw Output panel
        try:
            raw_text = self._raw_txt.get("1.0", tk.END)
            lines = raw_text.split('\n')

            # Find where map rows start (same logic as _update_from_raw_auto)
            header_end = None
            map_style = None
            for i, line in enumerate(lines):
                line_s = line.strip()
                if not line_s:
                    continue
                parts = line_s.split('","')
                if len(parts) < 10:
                    if set(line_s).issubset(set(".?*1X")) and len(line_s) >= 5:
                        header_end = i
                        map_style = "plain"
                        break
                    continue
                cells = [p.strip().strip('"') for p in parts]
                if all(c in ('.', '?', '*', '1', 'X') for c in cells if c):
                    header_end = i
                    map_style = "quoted"
                    break

            if header_end is None:
                return

            crlf = '\r\n' in raw_text

            # Rebuild map rows from current grid
            new_map_lines = []
            if map_style == "plain":
                for grid_row in self._grid:
                    new_map_lines.append("".join(grid_row))
                header_lines = []
            else:
                sample = lines[header_end]
                sinf = sample.rstrip('\n').endswith('\r"')
                for grid_row in self._grid:
                    cells_str = ','.join('"' + c + '"' for c in grid_row)
                    if sinf:
                        new_map_lines.append(cells_str + ',"' + '\r' + '"')
                    else:
                        new_map_lines.append(cells_str)
                header_lines = lines[:header_end]

            sep = '\r\n' if crlf else '\n'
            if header_lines:
                new_text = sep.join(header_lines) + sep + sep.join(new_map_lines) + sep
            else:
                new_text = sep.join(new_map_lines) + sep

            # Update raw panel without triggering debounce
            self._raw_txt.unbind("<<Modified>>")
            self._raw_txt.delete("1.0", tk.END)
            self._raw_txt.insert(tk.END, new_text)
            self._raw_txt.edit_modified(False)
            self._raw_txt.bind("<<Modified>>", self._on_text_modified)

            # Update bytes and stats
            self._out_bytes = new_text.encode('latin-1', errors='replace')
            die_count = sum(r.count('?') for r in self._grid)
            self._s_dies.config(text=f"{die_count:,}")
            self._s_size.config(text=fmt_size(len(self._out_bytes)))

        except Exception:
            pass  # Grid already updated visually; raw sync is best-effort


    def _scroll(self, event):
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _ctrl_scroll(self, event):
        if event.delta > 0:
            self._zoom(1.25)
        else:
            self._zoom(0.8)

    def _on_text_zoom(self, event):
        """Zoom text area font size with Ctrl+MouseWheel."""
        if event.delta > 0:
            self._text_font_size = min(24, self._text_font_size + 1)
        else:
            self._text_font_size = max(6, self._text_font_size - 1)

        # Update text widget font
        self._raw_txt.config(font=("Courier New", self._text_font_size))

    # ── Real-time Map Updates ────────────────────────────────────────────────
    def _on_text_modified(self, event):
        """Called when text widget is modified — debounce and update map."""
        # Clear the modified flag
        self._raw_txt.edit_modified(False)

        # Cancel any pending update
        if self._update_timer:
            self.after_cancel(self._update_timer)

        # Schedule update after 500ms of no typing (debounce)
        self._update_timer = self.after(500, self._update_from_raw_auto)

    def _update_from_raw_auto(self):
        """Auto-update map from edited raw text (called after debounce)."""
        try:
            # Get all text from the text widget
            raw_text = self._raw_txt.get("1.0", tk.END)
            lines = raw_text.strip().split('\n')

            # Find the first line that looks like a real map row.
            # Supports quoted rows and plain ASCII rows.
            header_end = None
            style = None
            for i, line in enumerate(lines):
                line_s = line.strip()
                if not line_s:
                    continue
                parts = line_s.split('","')
                if len(parts) < 10:
                    if set(line_s).issubset(set(".?*1X")) and len(line_s) >= 5:
                        header_end = i
                        style = "plain"
                        break
                    continue
                cells = [p.strip().strip('"') for p in parts]
                if all(c in ('.', '?', '*', '1', 'X') for c in cells if c):
                    header_end = i
                    style = "quoted"
                    break

            if header_end is None:
                return  # Silently fail during typing

            # Extract map rows
            map_lines = lines[header_end:]
            new_grid = []

            for line in map_lines:
                line = line.strip()
                if not line:
                    continue
                if style == "plain":
                    if set(line).issubset(set(".?*1X")):
                        new_grid.append(list(line))
                else:
                    parts = line.split('","')
                    cells = [p.strip().strip('"') for p in parts]
                    if cells and all(c in ('.', '?', '*', '1', 'X') for c in cells if c):
                        new_grid.append([c for c in cells if c])

            if not new_grid:
                return  # Silently fail during typing

            # Update grid and redraw
            self._grid = new_grid
            self._draw()

            # Update output bytes to match edited text
            self._out_bytes = raw_text.encode('latin-1')

            # Update stats
            rows = len(new_grid)
            cols = len(new_grid[0]) if new_grid else 0
            die_count = sum(row.count('?') for row in new_grid)

            self._s_rows.config(text=str(rows))
            self._s_cols.config(text=str(cols))
            self._s_dies.config(text=f"{die_count:,}")
            self._s_size.config(text=fmt_size(len(self._out_bytes)))

        except Exception:
            pass  # Silently fail during typing — user may be mid-edit

    # ── Raw View ──────────────────────────────────────────────────────────────
    def _update_raw(self):
        if not self._out_bytes:
            return
        try:
            text = self._out_bytes.decode("latin-1")
        except Exception:
            text = repr(self._out_bytes[:2000])
        self._raw_txt.delete("1.0", tk.END)
        self._raw_txt.insert(tk.END, text)

    def _open_ascii_map(self):
        """Open an existing ASCII wafer map file (.txt/.map/.csv), show in Raw Output,
        parse rows into grid, and render the map (no GDS2 conversion)."""
        path = filedialog.askopenfilename(
            title="Open Wafer Map Text",
            filetypes=[("Wafer map text", "*.txt *.map *.csv"), ("All files", "*.*")]
        )
        if not path:
            return

        p = Path(path)
        try:
            # Read bytes (latin-1 compatible) and reflect in Raw panel
            raw_bytes = p.read_bytes()
            self._out_bytes = raw_bytes
            self._grid = None
            self._gds_text = None  # Not a GDS2 session
            self._input_path = p

            # Update "Input File" label and default export name
            nm = p.name if len(p.name) <= 30 else "..." + p.name[-27:]
            self._file_lbl.config(text=nm, fg=WIN_TEXT)
            self._v_out_name.set(p.stem + "_copy.txt")

            # Show in Raw Output
            self._empty_lbl.place_forget()
            self._update_raw()

            # Parse the raw text into grid using existing logic
            # (reuses the same parser the live-editor uses)
            self._update_from_raw_auto()

            # UI enablement: exporting + view tools on, convert off
            parsed_ok = bool(self._grid)
            self._btn_export.config(state=tk.NORMAL if parsed_ok else tk.DISABLED)
            for tb in (self._tbb_export, self._tbb_zin, self._tbb_zout, self._tbb_fit):
                tb.set_state(tk.NORMAL if parsed_ok else tk.DISABLED)

            self._btn_convert.config(state=tk.DISABLED)
            self._tbb_convert.set_state(tk.DISABLED)

            # Status + stats
            if parsed_ok:
                rows = len(self._grid)
                cols = len(self._grid[0]) if self._grid else 0
                die_count = sum(r.count('?') for r in self._grid)
                self._s_rows.config(text=str(rows))
                self._s_cols.config(text=str(cols))
                self._s_dies.config(text=f"{die_count:,}")
                self._s_size.config(text=fmt_size(len(self._out_bytes)))
                self._set_status(f"Loaded wafer map: {p.name}", fmt_size(len(self._out_bytes)))
                # Fit view for convenience
                self._fit_pending = True
                self.after(150, self._fit)
            else:
                self._s_rows.config(text="—")
                self._s_cols.config(text="—")
                self._s_dies.config(text="—")
                self._s_size.config(text=fmt_size(len(self._out_bytes)))
                self._set_status("Loaded text (couldn't detect map rows). Edit Raw Output and it will re-render.")

        except Exception as e:
            messagebox.showerror("Open Error", str(e))
            self._set_status("Open failed.")
    # ── Export ────────────────────────────────────────────────────────────────
    def _export(self):
        if not self._out_bytes:
            messagebox.showwarning("Nothing to Export",
                                   "Please convert a GDS2 file or open an ASCII map first.")
            return
        default = self._v_out_name.get() or "wafermap.txt"
        path = filedialog.asksaveasfilename(
            title="Save Wafer Map",
            initialfile=default,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            Path(path).write_bytes(self._out_bytes)
            sz = fmt_size(len(self._out_bytes))
            self._set_status("Exported: " + Path(path).name + "  (" + sz + ")")
            messagebox.showinfo("Export Complete",
                                "Wafer map saved successfully.\n\n"
                                "File:  " + path + "\n"
                                                   "Size:  " + sz)
        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    # ── Reset ─────────────────────────────────────────────────────────────────
    def _reset(self):
        self._gds_text = None
        self._grid = None
        self._out_bytes = None
        self._cell_px = 5
        self._input_path = None
        self._update_timer = None
        self._file_lbl.config(text="No file selected", fg=WIN_TEXT2)
        self._canvas.delete("all")
        self._empty_lbl.place(relx=0.5, rely=0.5, anchor="center")
        self._canvas.config(scrollregion=(0, 0, 1, 1))
        self._raw_txt.delete("1.0", tk.END)
        for attr in ("_s_dies", "_s_rows", "_s_cols", "_s_size", "_s_time"):
            getattr(self, attr).config(text="—")
        self._btn_convert.config(state=tk.DISABLED)
        self._btn_export.config(state=tk.DISABLED)
        for tb in (self._tbb_convert, self._tbb_export,
                   self._tbb_zin, self._tbb_zout, self._tbb_fit):
            tb.set_state(tk.DISABLED)
        self._zoom_lbl.config(text="Zoom: 100%")
        self._set_status("Ready")

    # ── Auto-detect Pitch ────────────────────────────────────────────────────
    def _auto_detect_pitch(self):
        if not self._gds_text:
            messagebox.showwarning("No File", "Please open a GDS2 file first.")
            return
        structs = [s.strip() for s in self._v_structs.get().split(",") if s.strip()]
        coords = parse_gds2(self._gds_text, structs)
        if not coords:
            messagebox.showwarning("No Coords", "No die coordinates found.\nCheck Die Structures field.")
            return
        px, py = detect_pitch(coords)
        if px:
            self._v_die_x.set(str(round(px, 6)))
        if py:
            self._v_die_y.set(str(round(py, 6)))
        self._set_status(f"Auto-detected: X={px/1:.4f} mm  Y={py/1:.4f} mm" if px and py else "Could not detect pitch")

    # ── Bin Table Helpers ─────────────────────────────────────────────────────
    def _get_bin_rows(self):
        """Return list of non-empty lines from the bin text editor."""
        raw = self._bin_txt.get("1.0", tk.END)
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]

    def _load_bin_template(self):
        path = filedialog.askopenfilename(
            title="Load Bin Template",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
            self._bin_txt.delete("1.0", tk.END)
            self._bin_txt.insert(tk.END, text)
            self._set_status("Bin template loaded: " + Path(path).name)
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    def _save_bin_template(self):
        path = filedialog.asksaveasfilename(
            title="Save Bin Template",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            text = self._bin_txt.get("1.0", tk.END)
            Path(path).write_text(text, encoding="utf-8")
            self._set_status("Bin template saved: " + Path(path).name)
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    # ── About ─────────────────────────────────────────────────────────────────
    def _about(self):
        messagebox.showinfo("About GDS2 Wafer Map Converter",
                            "GDS2 Wafer Map Converter  v1.2\n\n"
                            "Converts GDS2 text into ASCII wafer maps.\n"
                            "Full map mode: 1=die, X=scribe, .=outside\n"
                            "(SCR08-style drop-in layout).\n\n"
                            "Wafer mode: SINF / KLA product format.\n\n"
                            "Requires: Python 3.8+  (no extra packages)")


if __name__ == "__main__":
    app = App()
    app.mainloop()
