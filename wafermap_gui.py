#!/usr/bin/env python3
"""
GDS text → ASCII full wafer map (1=die, X=scribe, .=outside).
Run: python wafermap_gui.py
"""

import re
import math
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path


# ── Converter ─────────────────────────────────────────────────────────────────

def parse_gds2(text, die_structure_names):
    coords = []
    in_sref, cur_sname = False, None
    for line in text.splitlines():
        line = line.strip()
        if line == "SREF":
            in_sref, cur_sname = True, None
            continue
        if not in_sref:
            continue
        m = re.match(r"^SNAME\s+(.+)$", line)
        if m:
            cur_sname = m.group(1)
            continue
        m = re.match(r"^XY\s+(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)", line)
        if m:
            if cur_sname and (not die_structure_names or cur_sname in die_structure_names):
                coords.append((float(m.group(1)) / 1e6, float(m.group(2)) / 1e6, cur_sname))
            continue
        if line == "ENDEL":
            in_sref = False
    return coords


def detect_pitch(coords):
    from collections import Counter
    xs = sorted(set(x for x, y, _ in coords))
    ys = sorted(set(y for x, y, _ in coords))

    def step(vals):
        if len(vals) < 2:
            return None
        diffs = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
        diffs = [d for d in diffs if d > 0]
        return Counter(diffs).most_common(1)[0][0] if diffs else None

    return step(xs), step(ys)


def auto_detect_die_structures(text):
    from collections import defaultdict
    by_name = defaultdict(list)
    in_sref, sname = False, None
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

    best_name, best_sites = None, 0
    for name, pts in by_name.items():
        if len(pts) < 4:
            continue
        px, py = detect_pitch(pts)
        if not px or not py:
            continue
        sites = cluster_die_sites(pts, px, py)
        if len(sites) > best_sites:
            best_sites, best_name = len(sites), name
    return [best_name] if best_name else []


def cluster_die_sites(coords, pitch_x, pitch_y):
    sites = set()
    for x, y, *_ in coords:
        sites.add((round(x / pitch_x), round(y / pitch_y)))
    return sites


def build_full_map_grid(coords, pitch_x, pitch_y, diameter, cols, rows, street_px=3):
    if not coords or not pitch_x or not pitch_y:
        return [], 0

    sites = cluster_die_sites(coords, pitch_x, pitch_y)
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
        rel_x, rel_y = gx - min_gx, max_gy - gy
        x0 = pad + rel_x * slot_x
        y0 = pad + rel_y * slot_y
        for py in range(y0, min(map_h, y0 + die_px_h)):
            for px in range(x0, min(map_w, x0 + die_px_w)):
                grid[py][px] = "1"

    centers = [(gx * pitch_x, gy * pitch_y) for gx, gy in sites]
    wafer_r = max(diameter / 2.0, max(math.hypot(x, y) for x, y in centers) + max(pitch_x, pitch_y)) * 1.08
    span = 2.0 * wafer_r

    for py in range(map_h):
        for px in range(map_w):
            if grid[py][px] == "1":
                continue
            x_mm = (px + 0.5) / map_w * span - wafer_r
            y_mm = wafer_r - (py + 0.5) / map_h * span
            if math.hypot(x_mm, y_mm) <= wafer_r:
                grid[py][px] = "X"

    if map_w != cols or map_h != rows:
        out = [["." for _ in range(cols)] for _ in range(rows)]
        off_x, off_y = max(0, (cols - map_w) // 2), max(0, (rows - map_h) // 2)
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

    return grid, len(sites)


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
ACCENT = "#2563eb"
DIE_COL = "#22c55e"
SCRIBE_COL = "#64748b"
OUT_COL = "#e2e8f0"
FONT = ("Segoe UI", 10)
FONT_SM = ("Segoe UI", 9)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GDS → ASCII Map")
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
        self._input_path = None

        self._v_structs = tk.StringVar(value="")
        self._v_diameter = tk.StringVar(value="147.3")
        self._v_cols = tk.StringVar(value="340")
        self._v_rows = tk.StringVar(value="567")
        self._v_out = tk.StringVar(value="map.txt")

        self._build()
        self.bind("<Control-o>", lambda e: self._open())
        self.bind("<Control-s>", lambda e: self._export())
        self._status("Open a GDS text file to begin")

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

        self._field(side, "Die cell (SNAME)", self._v_structs,
                    "Empty = auto-detect")
        self._field(side, "Wafer Ø (mm)", self._v_diameter)
        self._field(side, "Columns", self._v_cols)
        self._field(side, "Rows", self._v_rows)
        self._field(side, "Export name", self._v_out)

        tk.Label(side, text="1 die  ·  X scribe  ·  . outside",
                 bg=BG, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(16, 4))

        for sym, col in (("1", DIE_COL), ("X", SCRIBE_COL), (".", OUT_COL)):
            row = tk.Frame(side, bg=BG)
            row.pack(anchor="w", pady=1)
            tk.Label(row, text=sym, width=2, bg=col, fg="white" if sym != "." else FG,
                     font=FONT_SM).pack(side=tk.LEFT, padx=(0, 6))

        self._info = tk.Label(side, text="", bg=BG, fg=FG, font=FONT_SM,
                              justify=tk.LEFT, wraplength=180)
        self._info.pack(anchor="w", pady=(12, 0))

        preview = tk.Frame(body, bg="white", relief=tk.SOLID, bd=1)
        preview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(preview, bg=OUT_COL, highlightthickness=0)
        xsb = tk.Scrollbar(preview, orient=tk.HORIZONTAL, command=self._canvas.xview)
        ysb = tk.Scrollbar(preview, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._hint = tk.Label(
            preview, text="Open GDS text  →  Convert",
            bg=OUT_COL, fg=MUTED, font=FONT)
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
        self._input_path = p
        self._file_lbl.config(text=p.name, fg=FG)
        self._v_out.set(p.stem + "_map.txt")
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
            structs = [s.strip() for s in self._v_structs.get().split(",") if s.strip()]
            names = structs or auto_detect_die_structures(self._gds_text)
            coords = parse_gds2(self._gds_text, names)
            if not coords:
                self.after(0, lambda: self._fail(
                    "No die placements found.\n"
                    "Set die cell SNAME or leave empty for auto-detect."))
                return

            pitch_x, pitch_y = detect_pitch(coords)
            diameter = float(self._v_diameter.get())
            cols = max(20, int(self._v_cols.get()))
            rows = max(10, int(self._v_rows.get()))

            grid, n_dies = build_full_map_grid(
                coords, pitch_x, pitch_y, diameter, cols, rows)
            if not grid:
                self.after(0, lambda: self._fail("Could not build map."))
                return

            out = grid_to_bytes(grid)
            cell = names[0] if names else "?"
            pitch = f"{pitch_x:.4f}×{pitch_y:.4f} mm" if pitch_x and pitch_y else "—"
            self.after(0, lambda: self._done(grid, out, n_dies, cell, pitch))
        except ValueError as e:
            self.after(0, lambda: self._fail(f"Invalid number in settings.\n{e}"))
        except Exception as e:
            self.after(0, lambda: self._fail(str(e)))

    def _done(self, grid, out, n_dies, cell, pitch):
        self._grid = grid
        self._out_bytes = out
        rows, cols = len(grid), len(grid[0])
        self._hint.place_forget()
        self._info.config(
            text=f"Dies: {n_dies:,}\nSize: {cols}×{rows}\nCell: {cell}\nPitch: {pitch}\nFile: {fmt_size(len(out))}")
        self._status(f"{n_dies:,} dies · {cols}×{rows} · {fmt_size(len(out))}")
        self._fit()

    def _fail(self, msg):
        self._status("Failed")
        messagebox.showerror("Convert failed", msg)

    def _export(self):
        if not self._out_bytes:
            messagebox.showwarning("Nothing to save", "Convert a file first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save ASCII map",
            initialfile=self._v_out.get() or "map.txt",
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
        colors = {"1": DIE_COL, "X": SCRIBE_COL, ".": OUT_COL}
        rows, cols = len(self._grid), len(self._grid[0])
        px = self._cell_px
        for r in range(rows):
            y1, y2 = r * px, (r + 1) * px
            for c in range(cols):
                x1, x2 = c * px, (c + 1) * px
                fill = colors.get(self._grid[r][c], OUT_COL)
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
