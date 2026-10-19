"""Tk export settings dialog for the HRTEM analyzer."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from hrtem_export import available_fonts, resolve_font, save_settings, validate_settings


def open_settings(parent: tk.Tk, root: Path, current: dict, on_saved) -> tk.Toplevel:
    window=tk.Toplevel(parent)
    window.title('输出设置 / Export Settings')
    window.geometry('820x690')
    window.minsize(760,620)
    window.transient(parent)
    data=deepcopy(current)
    vars={}
    for key,value in data.items():
        if key=='formats': continue
        vars[key]=(tk.BooleanVar if isinstance(value,bool) else tk.StringVar)(window,value=value)
    format_vars={fmt:tk.BooleanVar(window,value=fmt in data['formats']) for fmt in ('png','tiff','svg','pdf')}
    outer=ttk.Frame(window,padding=12)
    outer.pack(fill='both',expand=True)
    notebook=ttk.Notebook(outer)
    notebook.pack(fill='both',expand=True)
    font_tab=ttk.Frame(notebook,padding=10)
    style_tab=ttk.Frame(notebook,padding=10)
    format_tab=ttk.Frame(notebook,padding=10)
    notebook.add(font_tab,text='字体 / Font')
    notebook.add(style_tab,text='标注样式 / Annotation')
    notebook.add(format_tab,text='格式和图幅 / Export')
    for tab in (font_tab,style_tab,format_tab): tab.columnconfigure(1,weight=1)
    def entry(tab,row,label,key,values=None):
        ttk.Label(tab,text=label).grid(row=row,column=0,sticky='w',pady=4,padx=(0,10))
        widget=(ttk.Combobox(tab,textvariable=vars[key],values=values,state='readonly' if values else 'normal')
                if values else ttk.Entry(tab,textvariable=vars[key]))
        widget.grid(row=row,column=1,sticky='ew',pady=4)
        return widget
    fonts=available_fonts()
    ttk.Label(font_tab,text='搜索已安装字体').grid(row=0,column=0,sticky='w',pady=4)
    search=tk.StringVar(window)
    ttk.Entry(font_tab,textvariable=search).grid(row=0,column=1,sticky='ew',pady=4)
    family=entry(font_tab,1,'字体名称', 'font_family',fonts)
    family.configure(state='normal')
    family.bind('<<ComboboxSelected>>', lambda event: vars['font_file'].set(''))
    def filter_fonts(*_):
        needle=search.get().casefold().strip()
        family['values']=[name for name in fonts if needle in name.casefold()]
    search.trace_add('write',filter_fonts)
    def refresh_fonts():
        nonlocal fonts
        fonts=available_fonts()
        filter_fonts()
    ttk.Button(font_tab,text='刷新系统字体列表',command=refresh_fonts).grid(row=2,column=1,sticky='w',pady=5)
    entry(font_tab,3,'自定义 .ttf / .otf 文件','font_file')
    def browse_font():
        name=filedialog.askopenfilename(parent=window,title='选择本机字体文件',
               filetypes=[('Font files','*.ttf *.otf')])
        if name: vars['font_file'].set(name)
    ttk.Button(font_tab,text='选择字体文件…',command=browse_font).grid(row=4,column=1,sticky='w',pady=5)
    entry(font_tab,5,'字形','font_style',['normal','bold','italic','bold italic'])
    for row,key,label in ((6,'label_size','间距标签字号 (pt)'),(7,'title_size','标题字号 (pt)'),
                          (8,'axis_size','坐标轴字号 (pt)'),(9,'tick_size','刻度字号 (pt)'),
                          (10,'legend_size','图例字号 (pt)')):
        entry(font_tab,row,label,key)
    preview_frame=ttk.LabelFrame(font_tab,text='实际绘图字体预览')
    preview_frame.grid(row=11,column=0,columnspan=2,sticky='ew',pady=(12,4))
    preview_frame.columnconfigure(0,weight=1)
    figure=Figure(figsize=(6.8,1.05),dpi=100)
    axis=figure.add_axes([.04,.15,.92,.8]); axis.set_axis_off()
    canvas=FigureCanvasTkAgg(figure,master=preview_frame)
    canvas.get_tk_widget().grid(row=0,column=0,sticky='ew')
    def collect():
        raw={key:variable.get() for key,variable in vars.items()}
        raw['formats']=[fmt for fmt,variable in format_vars.items() if variable.get()]
        return validate_settings(raw)
    def preview():
        try:
            settings=collect()
            prop=resolve_font(settings,settings['label_size'])
            axis.clear();axis.set_axis_off()
            axis.text(.02,.57,'d = 7.25 px   μm  Å  −1  x₂  x³',fontproperties=prop,
                      color=settings['text_color'],va='center',
                      bbox=dict(facecolor=settings['label_background'],alpha=settings['label_background_alpha']))
            canvas.draw()
        except Exception as exc:
            messagebox.showerror('字体预览失败',str(exc),parent=window)
    ttk.Button(font_tab,text='预览当前字体',command=preview).grid(row=12,column=1,sticky='w',pady=5)
    for row,key,label in ((0,'text_color','文字颜色'),(1,'arrow_color','箭头颜色'),
                          (2,'roi_ok_color','OK 区域框颜色'),(3,'roi_verify_color','VERIFY 区域框颜色'),
                          (4,'label_background','标签底色'),(5,'label_background_alpha','标签底色透明度 0-1'),
                          (6,'arrow_size','箭头大小'),(7,'arrow_linewidth','箭头线宽'),
                          (8,'roi_linewidth','区域框线宽')):
        entry(style_tab,row,label,key)
    entry(style_tab,9,'标签位置','label_position',['top','center','bottom'])
    for row,key,label in ((10,'show_title','显示标题'),(11,'show_axes','显示坐标轴'),(12,'show_roi','显示 ROI 方框')):
        ttk.Checkbutton(style_tab,text=label,variable=vars[key]).grid(row=row,column=0,columnspan=2,sticky='w',pady=4)
    entry(style_tab,13,'画布背景','background',['white','transparent'])
    ttk.Label(format_tab,text='可同时选择多种输出格式').grid(row=0,column=0,columnspan=2,sticky='w',pady=5)
    formats=ttk.Frame(format_tab);formats.grid(row=1,column=0,columnspan=2,sticky='w')
    for i,fmt in enumerate(format_vars):
        ttk.Checkbutton(formats,text=fmt.upper(),variable=format_vars[fmt]).grid(row=0,column=i,padx=(0,25),pady=8)
    dpi_entry=entry(format_tab,2,'DPI（300 / 600 / 1200）','dpi',['300','600','1200'])
    dpi_entry.configure(state='normal')
    ttk.Label(format_tab,text='也可输入自定义 DPI 72-1200').grid(row=3,column=1,sticky='w')
    width_entry=entry(format_tab,4,'宽度 (mm；90 单栏 / 180 双栏)','width_mm',['90','180','120','240'])
    width_entry.configure(state='normal')
    ttk.Label(format_tab,text='可输入自定义宽度 50-400 mm').grid(row=5,column=1,sticky='w')
    entry(format_tab,6,'高度 (mm；0 按源图比例)','height_mm')
    entry(format_tab,7,'SVG 文字模式','svg_text_mode',['editable','path'])
    ttk.Label(format_tab,text='SVG/PDF 中原始显微图像仍是位图；文字、箭头和线条保留矢量。',
              wraplength=680).grid(row=8,column=0,columnspan=2,sticky='w',pady=12)
    def save_and_close():
        try:
            saved=save_settings(root,collect())
        except Exception as exc:
            messagebox.showerror('设置无效',str(exc),parent=window);return
        on_saved(saved)
        window.destroy()
    buttons=ttk.Frame(outer)
    buttons.pack(fill='x',pady=(10,0))
    ttk.Button(buttons,text='保存设置',command=save_and_close).pack(side='right')
    ttk.Button(buttons,text='取消',command=window.destroy).pack(side='right',padx=(0,10))
    try: preview()
    except Exception: pass
    return window
