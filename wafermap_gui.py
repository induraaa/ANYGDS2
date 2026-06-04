#!/usr/bin/env python3
"""
GDS text → ASCII layout (1 = geometry, . = empty).
Flattens the chosen top cell into its bounding box — no wafer shape or die grid logic.
Run: python wafermap_gui.py
"""

import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

GDS_SCALE = 1e-6  # file units → mm (matches typical KLayout text export)
XY_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)")
ELEMENTS = frozenset({"BOUNDARY", "PATH", "BOX", "SREF"})


def _xy_pairs(line):
    return [(float(m.group(1)) * GDS_SCALE, float(m.group(2)) * GDS_SCALE)
            for m in XY_RE.finditer(line)]


def _line_has_coords(line):
    return bool(XY_RE.search(line))


def _flush_poly(cells, cell, in_element, cur_points, sref_name, sref_xy):
    if in_element in ("BOUNDARY", "PATH", "BOX") and len(cur_points) >= 2:
        cells[cell]["polys"].append(cur_points)
    elif in_element == "SREF" and sref_name and sref_xy:
        cells[cell]["srefs"].append((sref_name, sref_xy[0], sref_xy[1]))


def parse_gds_layout(text):
    """
    Parse GDSII text into cells: local polygons + child placements (SREF).
    Returns (cells dict, root_cell name).
    """
    cells = {}
    current_cell = None
    in_element = None
    cur_points = []
    sref_name = None
    sref_xy = None

    def ensure_cell(name):
        if name not in cells:
            cells[name] = {"polys": [], "srefs": []}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("STRNAME "):
            current_cell = line.split(None, 1)[1].strip()
            ensure_cell(current_cell)
            continue
        if line in ELEMENTS:
            if in_element and current_cell:
                _flush_poly(cells, current_cell, in_element, cur_points, sref_name, sref_xy)
            in_element = line
            cur_points = []
            sref_name = None
            sref_xy = None
            continue
        if not in_element or not current_cell:
            continue
        if line.startswith("SNAME "):
            sref_name = line.split(None, 1)[1].strip()
            continue
        if line == "ENDEL":
            _flush_poly(cells, current_cell, in_element, cur_points, sref_name, sref_xy)
            in_element = None
            cur_points = []
            sref_name = None
            sref_xy = None
            continue
        if in_element in ("BOUNDARY", "PATH", "BOX", "SREF") and _line_has_coords(line):
            if line.startswith("XY"):
                line = line[2:].strip()
            cur_points.extend(_xy_pairs(line))
            if in_element == "SREF" and cur_points:
                sref_xy = cur_points[-1]

    if not cells:
        return {}, None

    root = max(
        cells.keys(),
        key=lambda n: len(cells[n]["srefs"]) * 1000 + len(cells[n]["polys"]),
    )
    return cells, root


def flatten_cell(cells, name, ox, oy, out_polys):
    """
    Layout as drawn in the top cell: local geometry plus each SREF's own
    polygons (one level). Nested references inside child cells are not expanded.
    """
    if name not in cells:
        out_polys.append([(ox, oy)])
        return
    cell = cells[name]
    for poly in cell["polys"]:
        out_polys.append([(x + ox, y + oy) for x, y in poly])
    for child, cx, cy in cell["srefs"]:
        ref = cells.get(child)
        if ref and ref["polys"]:
            for poly in ref["polys"]:
                out_polys.append([(x + ox + cx, y + oy + cy) for x, y in poly])
        else:
            out_polys.append([(ox + cx, oy + cy)])


def _fill_polygon(grid, px_pts, cols, rows):
    """Scanline fill in pixel coordinates."""
    if len(px_pts) < 3:
        return
    ys = [p[1] for p in px_pts]
    y_min = max(0, min(ys))
    y_max = min(rows - 1, max(ys))
    n = len(px_pts)
    for y in range(y_min, y_max + 1):
        crossings = []
        for i in range(n):
            x1, y1 = px_pts[i]
            x2, y2 = px_pts[(i + 1) % n]
            if y1 == y2:
                continue
            if (y1 <= y < y2) or (y2 <= y < y1):
                crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        crossings.sort()
        for j in range(0, len(crossings) - 1, 2):
            x_lo = max(0, int(crossings[j]))
            x_hi = min(cols - 1, int(crossings[j + 1]))
            for x in range(x_lo, x_hi + 1):
                grid[y][x] = "1"


def _draw_line(grid, x0, y0, x1, y1, cols, rows):
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        if 0 <= x0 < cols and 0 <= y0 < rows:
            grid[y0][x0] = "1"
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


def suggest_grid_size(text, px_per_mm=80, max_dim=2400, root_cell=None):
    """Pick cols×rows from layout bounding box (no wafer assumptions)."""
    cells, auto_root = parse_gds_layout(text)
    if not cells:
        return 340, 567
    root = root_cell.strip() if root_cell and root_cell in cells else auto_root
    polys = []
    flatten_cell(cells, root, 0.0, 0.0, polys)
    if not polys:
        return 340, 567
    xs = [x for p in polys for x, y in p]
    ys = [y for p in polys for x, y in p]
    span_x = max(max(xs) - min(xs), 0.001)
    span_y = max(max(ys) - min(ys), 0.001)
    cols = min(max_dim, max(80, round(span_x * px_per_mm)))
    rows = min(max_dim, max(80, round(span_y * px_per_mm)))
    return cols, rows


def build_layout_grid(text, cols, rows, root_cell=None):
    """
    Flatten GDS hierarchy and rasterize into cols×rows ASCII grid.
    """
    cells, auto_root = parse_gds_layout(text)
    if not cells:
        return [], 0, None

    root = root_cell.strip() if root_cell and root_cell in cells else auto_root
    polys = []
    flatten_cell(cells, root, 0.0, 0.0, polys)

    if not polys:
        return [], 0, root

    xs, ys = [], []
    for poly in polys:
        for x, y in poly:
            xs.append(x)
            ys.append(y)
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-12)
    span_y = max(max_y - min_y, 1e-12)

    grid = [["." for _ in range(cols)] for _ in range(rows)]

    def to_px(x, y):
        px = int(round((x - min_x) / span_x * (cols - 1)))
        py = int(round((max_y - y) / span_y * (rows - 1)))
        return px, py

    for poly in polys:
        if len(poly) == 1:
            px, py = to_px(poly[0][0], poly[0][1])
            if 0 <= px < cols and 0 <= py < rows:
                grid[py][px] = "1"
            continue
        px_pts = [to_px(x, y) for x, y in poly]
        if len(px_pts) == 2:
            for px, py in px_pts:
                if 0 <= px < cols and 0 <= py < rows:
                    grid[py][px] = "1"
        elif len(px_pts) >= 3:
            _fill_polygon(grid, px_pts, cols, rows)

    filled = sum(row.count("1") for row in grid)
    return grid, filled, root


def grid_to_bytes(grid):
    return ("\n".join("".join(row) for row in grid) + "\n").encode("latin-1", errors="replace")


def fmt_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


# ── UI ────────────────────────────────────────────────────────────────────────

BG = "#f8f8f8"
FG = "#222"
MUTED = "#666"
FILL_COL = "#22c55e"
EMPTY_COL = "#e2e8f0"
FONT = ("Segoe UI", 10)
FONT_SM = ("Segoe UI", 9)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GDS → ASCII Layout")
        self.geometry("960x640")
        self.minsize(720, 480)
        self.configure(bg=BG)

        try:
            icon = Path(__file__).parent / "cat.ico"
            if icon.exists():
                self.iconbitmap(str(icon))
        except Exception:
            pass

        self._gds_text = None
        self._grid = None
        self._out_bytes = None
        self._cell_px = 4

        self._v_root = tk.StringVar(value="")
        self._v_cols = tk.StringVar(value="340")
        self._v_rows = tk.StringVar(value="567")
        self._v_px_mm = tk.StringVar(value="80")
        self._v_out = tk.StringVar(value="layout.txt")

        self._build()
        self.bind("<Control-o>", lambda e: self._open())
        self.bind("<Control-s>", lambda e: self._export())
        self._status("Open a GDS text file")

    def _build(self):
        top = tk.Frame(self, bg=BG, padx=12, pady=10)
        top.pack(fill=tk.X)

        for label, cmd in (
            ("Open…", self._open),
            ("Convert", self._convert),
            ("Export…", self._export),
            ("Fit", self._fit),
        ):
            tk.Button(
                top, text=label, command=cmd, font=FONT,
                bg="white", fg=FG, relief=tk.GROOVE, bd=1, padx=10, pady=4,
                activebackground="#eee", cursor="hand2",
            ).pack(side=tk.LEFT, padx=(0, 6))

        self._stat = tk.Label(top, text="", bg=BG, fg=MUTED, font=FONT_SM, anchor="w")
        self._stat.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))

        body = tk.Frame(self, bg=BG, padx=12, pady=(0, 12))
        body.pack(fill=tk.BOTH, expand=True)

        side = tk.Frame(body, bg=BG, width=200)
        side.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        side.pack_propagate(False)

        self._file_lbl = tk.Label(
            side, text="No file", bg=BG, fg=MUTED, font=FONT_SM,
            wraplength=180, justify=tk.LEFT)
        self._file_lbl.pack(anchor="w", pady=(0, 12))

        self._field(side, "Top cell (STRNAME)", self._v_root,
                    "Empty = auto (main layout)")
        self._field(side, "Columns", self._v_cols)
        self._field(side, "Rows", self._v_rows)
        self._field(side, "Detail (px/mm)", self._v_px_mm,
                    "Used by Auto size")
        tk.Button(
            side, text="Auto size", command=self._auto_size, font=FONT_SM,
            bg="white", fg=FG, relief=tk.GROOVE, bd=1, padx=8, pady=2,
        ).pack(anchor="w", pady=(0, 8))
        self._field(side, "Export name", self._v_out)

        tk.Label(side, text="1 = geometry  ·  . = empty",
                 bg=BG, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(16, 4))

        self._info = tk.Label(side, text="", bg=BG, fg=FG, font=FONT_SM,
                              justify=tk.LEFT, wraplength=180)
        self._info.pack(anchor="w", pady=(8, 0))

        preview = tk.Frame(body, bg="white", relief=tk.SOLID, bd=1)
        preview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(preview, bg=EMPTY_COL, highlightthickness=0)
        xsb = tk.Scrollbar(preview, orient=tk.HORIZONTAL, command=self._canvas.xview)
        ysb = tk.Scrollbar(preview, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._hint = tk.Label(
            preview, text="Open GDS text  →  Convert",
            bg=EMPTY_COL, fg=MUTED, font=FONT)
        self._hint.place(relx=0.5, rely=0.5, anchor="center")

        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<Control-MouseWheel>", self._wheel_zoom)

    def _field(self, parent, label, var, hint=None):
        tk.Label(parent, text=label, bg=BG, fg=FG, font=FONT_SM).pack(anchor="w")
        tk.Entry(parent, textvariable=var, font=FONT_SM, width=18,
                 relief=tk.SOLID, bd=1).pack(anchor="w", fill=tk.X, pady=(2, 8))
        if hint:
            tk.Label(parent, text=hint, bg=BG, fg=MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 4))

    def _status(self, msg):
        self._stat.config(text=msg)

    def _auto_size(self):
        if not self._gds_text:
            messagebox.showwarning("No file", "Open a GDS text file first.")
            return
        try:
            px_mm = float(self._v_px_mm.get())
            root = self._v_root.get().strip() or None
            cols, rows = suggest_grid_size(
                self._gds_text, px_per_mm=px_mm, root_cell=root)
            self._v_cols.set(str(cols))
            self._v_rows.set(str(rows))
            self._status(f"Auto size: {cols}×{rows}")
        except ValueError:
            messagebox.showerror("Invalid value", "Detail (px/mm) must be a number.")

    def _open(self):
        path = filedialog.askopenfilename(
            title="Open GDS text",
            filetypes=[("Text", "*.txt"), ("All", "*.*")],
        )
        if not path:
            return
        p = Path(path)
        try:
            self._gds_text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            messagebox.showerror("Open failed", str(e))
            return
        self._file_lbl.config(text=p.name, fg=FG)
        self._v_out.set(p.stem + "_layout.txt")
        try:
            root = self._v_root.get().strip() or None
            px_mm = float(self._v_px_mm.get())
            cols, rows = suggest_grid_size(self._gds_text, px_per_mm=px_mm, root_cell=root)
            self._v_cols.set(str(cols))
            self._v_rows.set(str(rows))
        except ValueError:
            pass
        self._status(f"Loaded {p.name} ({fmt_size(p.stat().st_size)})")

    def _convert(self):
        if not self._gds_text:
            messagebox.showwarning("No file", "Open a GDS text file first.")
            return
        self._status("Converting…")
        self.update_idletasks()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            cols = max(20, int(self._v_cols.get()))
            rows = max(10, int(self._v_rows.get()))
            root = self._v_root.get().strip() or None

            grid, filled, used_root = build_layout_grid(
                self._gds_text, cols, rows, root_cell=root)
            if not grid or filled == 0:
                self.after(0, lambda: self._fail(
                    "No geometry found.\n"
                    "Check the file is GDSII text with BOUNDARY/PATH/SREF data."))
                return

            out = grid_to_bytes(grid)
            self.after(0, lambda: self._done(grid, out, filled, used_root))
        except ValueError as e:
            self.after(0, lambda: self._fail(f"Invalid columns/rows.\n{e}"))
        except Exception as e:
            self.after(0, lambda: self._fail(str(e)))

    def _done(self, grid, out, filled, root):
        self._grid = grid
        self._out_bytes = out
        rows, cols = len(grid), len(grid[0])
        self._hint.place_forget()
        pct = 100.0 * filled / (cols * rows) if cols * rows else 0
        self._info.config(
            text=f"Cell: {root}\nGrid: {cols}×{rows}\nMarked: {filled:,} ({pct:.0f}%)\nFile: {fmt_size(len(out))}")
        self._status(f"{cols}×{rows} · {pct:.0f}% geometry · {fmt_size(len(out))}")
        self._fit()

    def _fail(self, msg):
        self._status("Failed")
        messagebox.showerror("Convert failed", msg)

    def _export(self):
        if not self._out_bytes:
            messagebox.showwarning("Nothing to save", "Convert a file first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save ASCII layout",
            initialfile=self._v_out.get() or "layout.txt",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_bytes(self._out_bytes)
            self._status(f"Saved {Path(path).name}")
        except OSError as e:
            messagebox.showerror("Save failed", str(e))

    def _draw(self):
        if not self._grid:
            return
        self._canvas.delete("all")
        rows, cols = len(self._grid), len(self._grid[0])
        px = self._cell_px
        for r in range(rows):
            y1, y2 = r * px, (r + 1) * px
            for c in range(cols):
                x1, x2 = c * px, (c + 1) * px
                fill = FILL_COL if self._grid[r][c] == "1" else EMPTY_COL
                self._canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=fill)
        self._canvas.config(scrollregion=(0, 0, cols * px, rows * px))

    def _fit(self):
        if not self._grid:
            return
        self._canvas.update_idletasks()
        w = max(self._canvas.winfo_width() - 4, 1)
        h = max(self._canvas.winfo_height() - 4, 1)
        rows, cols = len(self._grid), len(self._grid[0])
        self._cell_px = max(1, min(w // cols, h // rows))
        self._draw()

    def _on_resize(self, _event=None):
        if self._grid:
            self._fit()

    def _wheel_zoom(self, event):
        if not self._grid:
            return
        self._cell_px = max(1, min(24, self._cell_px + (1 if event.delta > 0 else -1)))
        self._draw()


if __name__ == "__main__":
    App().mainloop()
