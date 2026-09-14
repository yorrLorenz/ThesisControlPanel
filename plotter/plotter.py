import tkinter as tk
from tkinter import ttk, filedialog, colorchooser
import itertools
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("TkAgg")
from matplotlib import dates as mdates
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.transforms import blended_transform_factory
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

PALETTE = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
           "#8c564b","#e377c2","#17becf","#bcbd22","#7f7f7f"]

def fmt(v):
    if v is None: return "—"
    try:
        if pd.isna(v): return "—"
    except Exception:
        pass
    if isinstance(v, (int, float, np.integer, np.floating)):
        return f"{v:.4g}"
    return str(v)


class Crosshair:
    """Blitting crosshair, linked across axes, via blended transforms + add_artist
    so the cursor lines never affect autoscale/data limits."""
    def __init__(self, canvas, fig):
        self.canvas = canvas; self.fig = fig
        self.axes = []; self.vlines = {}; self.hlines = {}
        self.text = None; self.bg = None
        self.value_provider = lambda ax, x: ""
        canvas.mpl_connect("draw_event", self._on_draw)
        canvas.mpl_connect("motion_notify_event", self._on_move)
        canvas.mpl_connect("axes_leave_event", lambda e: self._blit_hidden())

    def set_axes(self, axes):
        self.axes = list(axes); self.vlines = {}; self.hlines = {}
        self.text = self.fig.text(0.5, 0.985, "", ha="center", va="top",
                                  fontsize=13, animated=True,
                                  bbox=dict(boxstyle="round", fc="white",
                                            ec="0.6", alpha=0.9))
        for ax in self.axes:
            vt = blended_transform_factory(ax.transData, ax.transAxes)
            v = Line2D([0, 0], [0, 1], color="0.3", lw=0.8,
                       transform=vt, animated=True, visible=False)
            ht = blended_transform_factory(ax.transAxes, ax.transData)
            h = Line2D([0, 1], [0, 0], color="0.3", lw=0.8,
                       transform=ht, animated=True, visible=False)
            ax.add_artist(v); ax.add_artist(h)
            self.vlines[ax] = v; self.hlines[ax] = h
        self.bg = None

    def _on_draw(self, event):
        self.bg = self.canvas.copy_from_bbox(self.fig.bbox)

    def _blit_hidden(self):
        if self.bg:
            self.canvas.restore_region(self.bg); self.canvas.blit(self.fig.bbox)

    def _on_move(self, event):
        if self.bg is None or not self.axes: return
        if event.inaxes not in self.axes or event.xdata is None:
            self._blit_hidden(); return
        self.canvas.restore_region(self.bg)
        hovered, x = event.inaxes, event.xdata
        for ax in self.axes:
            v = self.vlines[ax]; v.set_xdata([x, x]); v.set_visible(True)
            ax.draw_artist(v)
        h = self.hlines[hovered]; h.set_ydata([event.ydata, event.ydata])
        h.set_visible(True); hovered.draw_artist(h)
        self.text.set_text(self.value_provider(hovered, x)); self.text.set_visible(True)
        self.fig.draw_artist(self.text)
        self.canvas.blit(self.fig.bbox)


class CSVViz:
    def __init__(self, root):
        self.root = root
        root.title("CSV Time-Series Visualizer")
        root.geometry("1250x780")

        self.df = None; self.x_col = None
        self.xplot = None; self.xnum = None; self.x_is_time = False
        self.series = {}; self.included = None
        self.normalize = tk.BooleanVar(value=False)
        self.marker_mode = tk.BooleanVar(value=False)
        self.plot_tabs = {}; self.frame_key = {}; self.slot_vars = []
        self._win_copy = ""

        left = ttk.Frame(root, padding=8); left.grid(row=0, column=0, sticky="ns")
        root.columnconfigure(1, weight=1); root.rowconfigure(0, weight=1)

        ttk.Button(left, text="Load CSV", command=self.load_csv).pack(fill="x")
        self.info = ttk.Label(left, text="No file loaded", wraplength=230, foreground="#555")
        self.info.pack(fill="x", pady=(6, 8))

        ttk.Label(left, text="X-axis (time):").pack(anchor="w")
        self.x_combo = ttk.Combobox(left, state="readonly")
        self.x_combo.pack(fill="x")
        self.x_combo.bind("<<ComboboxSelected>>", lambda e: self.on_x_change())

        ttk.Checkbutton(left, text="Normalize all series (0–1)",
                        variable=self.normalize, command=self.refresh_current).pack(anchor="w", pady=6)

        mk = ttk.LabelFrame(left, text="Markers", padding=4); mk.pack(fill="x", pady=4)
        ttk.Checkbutton(mk, text="Click plot to place marker",
                        variable=self.marker_mode).pack(anchor="w")
        ttk.Label(mk, text="Place 2 markers → window stats",
                  foreground="#777").pack(anchor="w")
        ttk.Button(mk, text="Clear markers (this tab)",
                   command=self.clear_markers).pack(fill="x", pady=2)

        ws = ttk.LabelFrame(left, text="Window statistics (mean ± SD)", padding=4)
        ws.pack(fill="x", pady=4)
        self.win_text = tk.Text(ws, height=8, width=30, wrap="none",
                                font=("Consolas", 8), relief="flat", bg="#f7f7f7")
        self.win_text.pack(fill="x")
        ttk.Button(ws, text="Copy stats", command=self.copy_window).pack(fill="x", pady=(2, 0))
        self.set_window_readout("")

        ttk.Label(left, text="Series  (R = right axis):").pack(anchor="w")
        self.series_box = self._scrollable(left)

        self.nb = ttk.Notebook(root); self.nb.grid(row=0, column=1, sticky="nsew")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_current())

    def _scrollable(self, parent):
        outer = ttk.Frame(parent); outer.pack(fill="both", expand=True)
        cv = tk.Canvas(outer, width=230, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        inner = ttk.Frame(cv)
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=inner, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        return inner

    # ---------- data loading ----------
    def load_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All", "*.*")])
        if not path: return
        df = pd.read_csv(path)
        for c in df.columns:
            coerced = pd.to_numeric(df[c], errors="coerce")
            if coerced.notna().mean() > 0.8:
                df[c] = coerced
        self.df = df
        self.included = np.ones(len(df), dtype=bool)
        cols = list(df.columns)
        self.x_combo["values"] = cols
        self.x_col = cols[0]; self.x_combo.set(self.x_col)
        self.prep_x()
        self.info.config(text=f"{len(df)} rows, {len(cols)} columns\nX = {self.x_col}")
        self.build_series(); self.build_tabs()

    def prep_x(self):
        s = self.df[self.x_col]
        if pd.api.types.is_numeric_dtype(s):
            self.xplot = s.values.astype(float); self.xnum = self.xplot; self.x_is_time = False
            return
        xd = pd.to_datetime(s, errors="coerce")
        if xd.notna().mean() > 0.8:
            self.xplot = xd.values; self.xnum = mdates.date2num(pd.to_datetime(xd))
            self.x_is_time = True
        else:
            self.xplot = np.arange(len(s)); self.xnum = self.xplot; self.x_is_time = False

    def numeric_series(self):
        return [c for c in self.df.select_dtypes("number").columns if c != self.x_col]

    def fmt_x(self, xval):
        if self.x_is_time:
            return mdates.num2date(xval).strftime("%H:%M:%S")
        return f"{xval:.4g}"

    def nearest_idx(self, x):
        return int(np.argmin(np.abs(self.xnum - x)))

    def yval(self, col, idx):
        if not self.included[idx]: return None
        return self.df[col].iloc[idx]

    def masked(self, col):
        y = self.df[col].astype(float).values
        return np.where(self.included, y, np.nan)

    def norm(self, y):
        lo, hi = np.nanmin(y), np.nanmax(y)
        if not np.isfinite(lo) or hi == lo: return np.zeros_like(y)
        return (y - lo) / (hi - lo)

    # ---------- window statistics ----------
    def series_stat(self, col, win):
        # raw values only; respects excluded rows; sample SD (ddof=1)
        y = self.df[col].astype(float).values
        sel = win & self.included & np.isfinite(y)
        seg = y[sel]; n = int(seg.size)
        if n == 0: return (np.nan, np.nan, np.nan, 0)
        mean = float(seg.mean())
        sd = float(seg.std(ddof=1)) if n > 1 else np.nan
        cv = (sd / mean * 100) if (n > 1 and mean != 0 and np.isfinite(mean)) else np.nan
        return (mean, sd, cv, n)

    def stat_str(self, col, mean, sd, cv, n):
        if n == 0: return f"{col}: no data in window"
        sds = "—" if not np.isfinite(sd) else f"{sd:.3g}"
        cvs = "—" if not np.isfinite(cv) else f"{cv:.1f}%"
        return f"{col} = {mean:.4g} ± {sds}  (CV {cvs}, n={n})"

    def render_window(self, tab, ax_cols):
        # shade the band between the first 2 markers and report stats per axis
        m = tab["markers"]
        if len(m) < 2:
            self.set_window_readout("")
            return
        lo, hi = sorted(m[:2])
        win = (self.xnum >= lo) & (self.xnum <= hi)
        side = [f"Window: {self.fmt_x(lo)} → {self.fmt_x(hi)}", ""]
        for ax, cols in ax_cols:
            ax.axvspan(lo, hi, color="0.45", alpha=0.12, zorder=0)
            box = []
            for c in cols:
                s = self.stat_str(c, *self.series_stat(c, win))
                box.append(s); side.append(s)
            if box:
                ax.text(0.015, 0.02, "\n".join(box), transform=ax.transAxes,
                        va="bottom", ha="left", fontsize=8, family="monospace",
                        bbox=dict(boxstyle="round", fc="white", ec="0.6", alpha=0.85))
        self.set_window_readout("\n".join(side))

    def set_window_readout(self, text):
        self.win_text.configure(state="normal")
        self.win_text.delete("1.0", "end")
        self.win_text.insert("1.0", text if text else "Place 2 markers on a plot\nto define a window.")
        self.win_text.configure(state="disabled")
        self._win_copy = text or ""

    def copy_window(self):
        if self._win_copy:
            self.root.clipboard_clear(); self.root.clipboard_append(self._win_copy)

    # ---------- sidebar series ----------
    def build_series(self):
        for w in self.series_box.winfo_children(): w.destroy()
        self.series = {}; colcycle = itertools.cycle(PALETTE)
        for col in self.numeric_series():
            row = ttk.Frame(self.series_box); row.pack(fill="x", pady=1)
            show = tk.BooleanVar(value=True); right = tk.BooleanVar(value=False)
            color = next(colcycle)
            ttk.Checkbutton(row, text=col, variable=show,
                            command=self.refresh_current, width=15).pack(side="left")
            btn = tk.Button(row, bg=color, width=2, command=lambda c=col: self.pick_color(c))
            btn.pack(side="left", padx=3)
            ttk.Checkbutton(row, text="R", variable=right,
                            command=self.refresh_current).pack(side="left")
            self.series[col] = {"show": show, "right": right, "color": color, "btn": btn}

    def pick_color(self, col):
        c = colorchooser.askcolor(color=self.series[col]["color"])[1]
        if c:
            self.series[col]["color"] = c; self.series[col]["btn"].config(bg=c)
            self.refresh_current()

    def on_x_change(self):
        self.x_col = self.x_combo.get(); self.prep_x()
        self.info.config(text=f"{len(self.df)} rows, "
                              f"{len(self.df.columns)} columns\nX = {self.x_col}")
        self.build_series(); self.build_tabs()

    # ---------- tabs ----------
    def build_tabs(self):
        for t in list(self.nb.tabs()): self.nb.forget(t)
        self.plot_tabs = {}; self.frame_key = {}
        self._plot_tab("__combined__", "Combined", "combined")
        for col in self.numeric_series():
            self._plot_tab(col, col, "single")
        self._compare_tab()
        self._data_tab()

    def _plot_tab(self, key, label, kind):
        frame = ttk.Frame(self.nb); self.nb.add(frame, text=label)
        fig = Figure(figsize=(6, 4), dpi=100)
        canvas = FigureCanvasTkAgg(fig, master=frame)
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        NavigationToolbar2Tk(canvas, frame).update()
        cross = Crosshair(canvas, fig)
        tab = {"fig": fig, "canvas": canvas, "cross": cross,
               "kind": kind, "col": key, "markers": []}
        canvas.mpl_connect("button_press_event", lambda e, k=key: self.on_click(k, e))
        self.plot_tabs[key] = tab; self.frame_key[str(frame)] = key

    def _compare_tab(self):
        frame = ttk.Frame(self.nb); self.nb.add(frame, text="Compare (2×2)")
        bar = ttk.Frame(frame); bar.pack(fill="x")
        ttk.Label(bar, text="Pick up to 4:").pack(side="left")
        opts = ["(none)"] + self.numeric_series()
        defaults = (self.numeric_series() + ["(none)"] * 4)[:4]
        self.slot_vars = []
        for i in range(4):
            var = tk.StringVar(value=defaults[i])
            cb = ttk.Combobox(bar, state="readonly", values=opts, textvariable=var, width=14)
            cb.pack(side="left", padx=2)
            cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_current())
            self.slot_vars.append(var)
        fig = Figure(figsize=(6, 4), dpi=100)
        canvas = FigureCanvasTkAgg(fig, master=frame)
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        NavigationToolbar2Tk(canvas, frame).update()
        cross = Crosshair(canvas, fig)
        tab = {"fig": fig, "canvas": canvas, "cross": cross,
               "kind": "compare", "markers": []}
        canvas.mpl_connect("button_press_event", lambda e: self.on_click("__compare__", e))
        self.plot_tabs["__compare__"] = tab; self.frame_key[str(frame)] = "__compare__"

    def _data_tab(self):
        frame = ttk.Frame(self.nb); self.nb.add(frame, text="Data (exclude rows)")
        self.frame_key[str(frame)] = "__data__"
        bar = ttk.Frame(frame); bar.pack(fill="x")
        ttk.Button(bar, text="Exclude selected",
                   command=lambda: self.set_selected(False)).pack(side="left")
        ttk.Button(bar, text="Include selected",
                   command=lambda: self.set_selected(True)).pack(side="left", padx=4)
        ttk.Button(bar, text="Include all",
                   command=self.include_all).pack(side="left")
        ttk.Label(bar, text="  (click a row, Shift-click another to select a range)"
                  ).pack(side="left")
        cols = list(self.df.columns)
        self.tree = ttk.Treeview(frame, columns=["inc"] + cols,
                                 show="headings", selectmode="extended")
        self.tree.heading("inc", text="✓"); self.tree.column("inc", width=32, anchor="center")
        for c in cols:
            self.tree.heading(c, text=c); self.tree.column(c, width=90, anchor="center")
        vs = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True); vs.pack(side="right", fill="y")
        self.tree.tag_configure("excluded", foreground="#bbbbbb")
        for i, r in enumerate(self.df.itertuples(index=False)):
            self.tree.insert("", "end", iid=str(i),
                             values=["✓"] + [fmt(v) for v in r])
        self.tree.bind("<Button-1>", self.on_data_click)

    # ---------- row exclusion ----------
    def on_data_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell": return
        if self.tree.identify_column(event.x) != "#1": return
        iid = self.tree.identify_row(event.y)
        if not iid: return
        i = int(iid); self.included[i] = not self.included[i]; self.update_row(i)
        return "break"

    def update_row(self, i):
        vals = list(self.tree.item(str(i))["values"])
        vals[0] = "✓" if self.included[i] else ""
        self.tree.item(str(i), values=vals,
                       tags=("" if self.included[i] else "excluded",))

    def set_selected(self, value):
        for iid in self.tree.selection():
            self.included[int(iid)] = value; self.update_row(int(iid))

    def include_all(self):
        self.included[:] = True
        for i in range(len(self.df)): self.update_row(i)

    # ---------- markers ----------
    def on_click(self, key, event):
        if not self.marker_mode.get() or event.inaxes is None or event.xdata is None:
            return
        tb = self.plot_tabs[key]["canvas"].toolbar
        if tb and tb.mode: return
        m = self.plot_tabs[key]["markers"]
        if len(m) >= 2: m.clear()      # 3rd click starts a fresh window
        m.append(event.xdata); self.redraw(key)

    def clear_markers(self):
        key = self.current_key()
        if key in self.plot_tabs:
            self.plot_tabs[key]["markers"].clear(); self.redraw(key)

    def draw_markers(self, tab, axes):
        for x in tab["markers"]:
            for ax in axes:
                ax.axvline(x, color="crimson", lw=1.0, ls="--")
            if axes:
                axes[0].annotate(self.fmt_x(x), xy=(x, 1), xycoords=("data", "axes fraction"),
                                 ha="center", va="bottom", fontsize=7, color="crimson")

    # ---------- redraw ----------
    def current_key(self):
        return self.frame_key.get(self.nb.select())

    def refresh_current(self):
        k = self.current_key()
        if k and self.df is not None: self.redraw(k)

    def redraw(self, key):
        if self.df is None or key not in self.plot_tabs: return
        tab = self.plot_tabs[key]; kind = tab["kind"]
        if kind == "single": self._draw_single(tab)
        elif kind == "combined": self._draw_combined(tab)
        elif kind == "compare": self._draw_compare(tab)

    def _draw_single(self, tab):
        col = tab["col"]; fig = tab["fig"]; fig.clear()
        ax = fig.add_subplot(111)
        ax.plot(self.xplot, self.masked(col), color=self.series[col]["color"], lw=1.4)
        ax.set_xlabel(self.x_col); ax.set_ylabel(col); ax.grid(alpha=0.25)
        self.draw_markers(tab, [ax])
        self.render_window(tab, [(ax, [col])])
        tab["cross"].set_axes([ax])
        tab["cross"].value_provider = lambda a, x, c=col: (
            f"{self.x_col}: {self.fmt_x(self.xnum[self.nearest_idx(x)])}    "
            f"{c}: {fmt(self.yval(c, self.nearest_idx(x)))}")
        fig.autofmt_xdate(); fig.tight_layout(rect=(0, 0, 1, 0.92))
        tab["canvas"].draw()

    def _draw_combined(self, tab):
        fig = tab["fig"]; fig.clear(); ax = fig.add_subplot(111); ax2 = None
        for col, cfg in self.series.items():
            if not cfg["show"].get(): continue
            y = self.masked(col)
            if self.normalize.get(): y = self.norm(y)
            tgt = ax
            if cfg["right"].get() and not self.normalize.get():
                ax2 = ax2 or ax.twinx(); tgt = ax2
            tgt.plot(self.xplot, y, color=cfg["color"], label=col, lw=1.4)
        ax.set_xlabel(self.x_col)
        ax.set_ylabel("normalized (0–1)" if self.normalize.get() else "value")
        ax.grid(alpha=0.25)
        lines, labels = ax.get_legend_handles_labels()
        if ax2:
            l2, lb2 = ax2.get_legend_handles_labels(); lines += l2; labels += lb2
        if lines: ax.legend(lines, labels, fontsize=8, loc="best")
        self.draw_markers(tab, [ax])
        visible = [c for c, cfg in self.series.items() if cfg["show"].get()]
        self.render_window(tab, [(ax, visible)])
        tab["cross"].set_axes([ax])

        def prov(a, x):
            idx = self.nearest_idx(x)
            parts = [f"{self.x_col}: {self.fmt_x(self.xnum[idx])}"]
            for col, cfg in self.series.items():
                if cfg["show"].get():
                    parts.append(f"{col}={fmt(self.yval(col, idx))}")
            return "    ".join(parts)
        tab["cross"].value_provider = prov
        fig.autofmt_xdate(); fig.tight_layout(rect=(0, 0, 1, 0.92))
        tab["canvas"].draw()

    def _draw_compare(self, tab):
        fig = tab["fig"]; fig.clear()
        axes = fig.subplots(2, 2, sharex=True).ravel()
        chosen = [v.get() for v in self.slot_vars]
        active = []
        for ax, name in zip(axes, chosen):
            if name == "(none)" or name not in self.series:
                ax.axis("off"); continue
            ax.plot(self.xplot, self.masked(name),
                    color=self.series[name]["color"], lw=1.4)
            ax.set_title(name, fontsize=9); ax.grid(alpha=0.25)
            active.append((ax, name))
        for x in tab["markers"]:
            for ax, _ in active: ax.axvline(x, color="crimson", lw=1.0, ls="--")
        self.render_window(tab, [(ax, [name]) for ax, name in active])
        tab["cross"].set_axes([ax for ax, _ in active])

        def prov(a, x):
            idx = self.nearest_idx(x)
            parts = [f"{self.x_col}: {self.fmt_x(self.xnum[idx])}"]
            for _, name in active:
                parts.append(f"{name}={fmt(self.yval(name, idx))}")
            return "    ".join(parts)
        tab["cross"].value_provider = prov
        fig.autofmt_xdate(); fig.tight_layout(rect=(0, 0, 1, 0.92))
        tab["canvas"].draw()


if __name__ == "__main__":
    root = tk.Tk()
    CSVViz(root)
    root.mainloop()