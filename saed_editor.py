"""Manual ring/spot correction window for saved SAED detections."""
from __future__ import annotations

from copy import deepcopy
import math
import tkinter as tk
from tkinter import messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle

from saed_analysis import add_ring, add_spot, delete_item


def open_saed_editor(parent, image, result: dict, on_saved):
    window = tk.Toplevel(parent)
    window.title("SAED 手动修正 / Manual correction")
    window.geometry("1060x760")
    window.minsize(900, 640)
    data = deepcopy(result)
    mode = tk.StringVar(window, value="add_spot")

    outer = ttk.Frame(window, padding=10); outer.pack(fill="both", expand=True)
    outer.columnconfigure(0, weight=1); outer.rowconfigure(0, weight=1)
    figure = Figure(figsize=(7.2, 6.8), dpi=100)
    axis = figure.add_subplot(111)
    canvas = FigureCanvasTkAgg(figure, master=outer)
    canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    side = ttk.Frame(outer); side.grid(row=0, column=1, sticky="ns")

    ttk.Label(side, text="点击图像以添加：").pack(anchor="w")
    ttk.Radiobutton(side, text="衍射斑点", variable=mode, value="add_spot").pack(anchor="w")
    ttk.Radiobutton(side, text="衍射环（点击环上任一点）", variable=mode, value="add_ring").pack(anchor="w")
    ttk.Label(side, text="衍射环").pack(anchor="w", pady=(12, 2))
    ring_list = tk.Listbox(side, width=34, height=12); ring_list.pack(fill="x")
    ttk.Label(side, text="衍射斑点").pack(anchor="w", pady=(12, 2))
    spot_list = tk.Listbox(side, width=34, height=15); spot_list.pack(fill="x")

    def refresh():
        ring_list.delete(0, "end"); spot_list.delete(0, "end")
        for item in data["rings"]:
            ring_list.insert("end", f"R{item['id']}  r={item['radius_px']:.2f}px" + ("  [manual]" if item.get("manual") else ""))
        for item in data["spots"]:
            spot_list.insert("end", f"S{item['id']}  ({item['x_px']:.1f},{item['y_px']:.1f})" + ("  [manual]" if item.get("manual") else ""))
        axis.clear(); axis.imshow(image, cmap="gray", origin="upper")
        cx, cy = data["center_px"]
        for item in data["rings"]:
            axis.add_patch(Circle((cx, cy), item["radius_px"], fill=False, edgecolor="#00ffc8", linewidth=1.2))
            axis.text(cx+item["radius_px"], cy, f"R{item['id']}", color="white", fontsize=8)
        for item in data["spots"]:
            axis.add_patch(Circle((item["x_px"], item["y_px"]), 7, fill=False, edgecolor="#ffcc33", linewidth=1.2))
            axis.text(item["x_px"]+8, item["y_px"], f"S{item['id']}", color="white", fontsize=8)
        axis.set_title("Click to add; select a list item to delete")
        canvas.draw_idle()

    def click(event):
        if event.inaxes is not axis or event.xdata is None or event.ydata is None:
            return
        try:
            if mode.get() == "add_ring":
                cx, cy = data["center_px"]
                add_ring(data, math.hypot(event.xdata-cx, event.ydata-cy))
            else:
                x = min(max(int(round(event.xdata)), 0), image.shape[1]-1)
                y = min(max(int(round(event.ydata)), 0), image.shape[0]-1)
                add_spot(data, event.xdata, event.ydata, float(image[y, x]))
            refresh()
        except ValueError as exc:
            messagebox.showerror("无法添加", str(exc), parent=window)

    canvas.mpl_connect("button_press_event", click)

    def remove(kind):
        widget = ring_list if kind == "ring" else spot_list
        chosen = widget.curselection()
        if not chosen:
            return
        item_id = data["rings" if kind == "ring" else "spots"][chosen[0]]["id"]
        delete_item(data, kind, item_id); refresh()

    ttk.Button(side, text="删除所选环", command=lambda: remove("ring")).pack(fill="x", pady=(8, 2))
    ttk.Button(side, text="删除所选斑点", command=lambda: remove("spot")).pack(fill="x", pady=2)
    ttk.Separator(side).pack(fill="x", pady=12)
    ttk.Button(side, text="保存修正并重新导出", command=lambda: (on_saved(data), window.destroy())).pack(fill="x")
    ttk.Button(side, text="取消", command=window.destroy).pack(fill="x", pady=(6, 0))
    refresh()
    return window
