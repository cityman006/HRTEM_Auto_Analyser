"""Interactive microscopy workspace; presentation edits never trigger measurement."""
from __future__ import annotations

from copy import deepcopy, copy
from datetime import datetime
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.patches import Circle, Rectangle, FancyArrowPatch
from matplotlib import colors

from hrtem_export import DEFAULT, validate_settings, resolve_font
from workbench_model import WorkbenchModel
from workbench_storage import save_project, load_project, capture_source, save_preset, load_preset, list_presets


def object_view(obj):
    return obj.get('view', 'image')


class ImageWorkbench:
    def __init__(self, parent, root, source=None, mode='hrtem', params=None,
                 settings=None, session=None, on_output=None):
        self.project_root = Path(root)
        self.on_output = on_output
        self.model = None
        self.initial_mode = mode
        self.initial_params = params or {}
        self.settings = validate_settings(settings or DEFAULT)
        self.selected = set()
        self.drag = None
        self.busy = False
        self.closed = False
        self.dirty = False
        self.after_id = None
        self.messages = queue.Queue()
        self.window = tk.Toplevel(parent)
        self.window.title('图像工作区 / Image Workspace')
        self.window.geometry('1280x900')
        self.window.minsize(940, 650)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.tool = tk.StringVar(self.window, '选择/拖动')
        self.view = tk.StringVar(self.window, 'image')
        self.mode = tk.StringVar(self.window, mode)
        self.brightness = tk.DoubleVar(self.window, 0)
        self.contrast = tk.DoubleVar(self.window, 1)
        self.radius = tk.DoubleVar(self.window, 6)
        self.status = tk.StringVar(self.window, '打开图像或项目；滚轮缩放，工具栏平移。')
        self._build()
        if session:
            self.safe(lambda: self.load_session(Path(session)))
        elif source:
            self.safe(lambda: self.open_image(Path(source)))
        self.after_id = self.window.after(100, self.poll)

    def _build(self):
        top = ttk.Frame(self.window, padding=6); top.pack(fill='x')
        for label, command in [('打开图像', self.choose_image), ('打开项目', self.choose_project),
                               ('保存项目', self.save), ('参数/标定', self.parameters),
                               ('保存预设', self.save_preset), ('加载预设', self.load_preset),
                               ('输出样式', self.styles)]:
            ttk.Button(top, text=label, command=lambda c=command: self.safe(c)).pack(side='left', padx=2)
        bar = ttk.Frame(self.window, padding=6); bar.pack(fill='x')
        self.mode_box=ttk.Combobox(bar, textvariable=self.mode, values=['hrtem', 'saed'], width=8, state='readonly')
        self.mode_box.pack(side='left')
        for label, command in [('自动分析', self.analyze), ('更新 IFFT', self.ifft),
                               ('撤销', self.undo), ('重做', self.redo), ('恢复自动结果', self.restore),
                               ('导出结果', self.export)]:
            ttk.Button(bar, text=label, command=lambda c=command: self.safe(c)).pack(side='left', padx=2)
        self.views = ttk.Combobox(bar, textvariable=self.view, values=['image'], width=24, state='readonly')
        self.views.pack(side='right'); self.views.bind('<<ComboboxSelected>>', self.change_view)
        panes = ttk.Panedwindow(self.window, orient='horizontal'); panes.pack(fill='both', expand=True)
        canvas_frame = ttk.Frame(panes); side = ttk.Frame(panes, padding=8)
        panes.add(canvas_frame, weight=4); panes.add(side, weight=1)
        self.figure = Figure(figsize=(8, 7), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=canvas_frame)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, canvas_frame)
        for event, callback in [('button_press_event', self.press), ('motion_notify_event', self.motion),
                                ('button_release_event', self.release), ('scroll_event', self.scroll)]:
            self.canvas.mpl_connect(event, callback)
        ttk.Label(side, text='编辑工具').pack(anchor='w')
        ttk.Combobox(side, textvariable=self.tool, state='readonly', width=24,
                     values=['选择/拖动', '缩放 ROI', '框选', '新增 ROI', '新增斑点', '新增环',
                             '拖动标签', '新增箭头', '新增比例尺']).pack(fill='x')
        ttk.Label(side, text='Ctrl 点击多选；拖动对象或标签\nROI 缩放：选中后拖动右下角\n框选支持一次选择多个 FFT 斑点', wraplength=220).pack(anchor='w', pady=5)
        row = ttk.Frame(side); row.pack(fill='x')
        for label, command in [('复制', self.duplicate), ('删除', self.delete)]:
            ttk.Button(row, text=label, command=lambda c=command: self.safe(c)).pack(side='left')
        self.items = tk.Listbox(side, selectmode='extended', exportselection=False, height=10)
        self.items.pack(fill='both', pady=6); self.items.bind('<<ListboxSelect>>', self.select_list)
        ttk.Button(side, text='修改所选文字/颜色', command=lambda: self.safe(self.edit_annotation)).pack(fill='x')
        ttk.Button(side, text='标签自动避让', command=lambda: self.safe(self.arrange_labels)).pack(fill='x', pady=3)
        row = ttk.Frame(side); row.pack(fill='x', pady=4)
        ttk.Label(row, text='mask/环半径 px').pack(side='left')
        ttk.Entry(row, textvariable=self.radius, width=8).pack(side='left')
        ttk.Button(side, text='应用半径到所选对象', command=lambda: self.safe(self.apply_radius)).pack(fill='x')
        ttk.Label(side, text='图层显示').pack(anchor='w', pady=(8, 0))
        self.layer_vars = {}
        layer_frame = ttk.Frame(side); layer_frame.pack(fill='x')
        for i, (key, label) in enumerate([('image','原图'), ('roi','ROI'), ('ring','环'), ('spot','斑点'),
                                        ('label','标签'), ('arrow','箭头'), ('scale','比例尺')]):
            var = tk.BooleanVar(self.window, True); self.layer_vars[key] = var
            ttk.Checkbutton(layer_frame, text=label, variable=var,
                            command=lambda k=key: self.toggle_layer(k)).grid(row=i//2, column=i%2, sticky='w')
        for label, var, low, high in [('亮度', self.brightness, -1, 1), ('对比度', self.contrast, .1, 5)]:
            ttk.Label(side, text=label).pack(anchor='w')
            scale = ttk.Scale(side, variable=var, from_=low, to=high, command=self.display_change)
            scale.pack(fill='x'); scale.bind('<ButtonPress-1>', lambda e: self.checkpoint())
        ttk.Label(self.window, textvariable=self.status, padding=6, wraplength=1200).pack(fill='x')
        self.window.bind('<Control-z>', lambda e: self.safe(self.undo))
        self.window.bind('<Control-y>', lambda e: self.safe(self.redo))

    def safe(self, func):
        if self.busy:
            self.status.set('正在计算，请等待完成。'); return
        try:
            return func()
        except Exception as exc:
            self.status.set(str(exc)); messagebox.showerror('工作区', str(exc), parent=self.window)

    def checkpoint(self):
        if self.model and not self.busy:
            self.model.checkpoint(); self.dirty = True

    def replace_model(self, model):
        self.model = model; self.selected.clear(); self.drag = None
        self.mode_box.configure(state='disabled')
        self.mode.set(model.state['mode']); self.settings = model.state['settings']
        self.view.set(model.state.get('view', 'image'))
        self.brightness.set(model.state.get('brightness', 0)); self.contrast.set(model.state.get('contrast', 1))
        for key, var in self.layer_vars.items(): var.set(model.state['layers'].get(key, True))
        self.refresh(reset=True)
        saved_limits=model.state.get('view_limits',{}).get(self.view.get())
        if saved_limits:
            self.ax.set_xlim(saved_limits[0]); self.ax.set_ylim(saved_limits[1]); self.canvas.draw_idle()

    def choose_image(self):
        name = filedialog.askopenfilename(parent=self.window, filetypes=[('Microscopy', '*.dm3 *.dm4 *.tif *.tiff *.png')])
        if name: self.open_image(Path(name))

    def open_image(self, path):
        if not self.confirm_replace(): return
        from microscopy_frames import list_frames, load_frame
        entries = list_frames(path)
        if not entries: raise ValueError('文件没有可读取的图像帧。')
        index = 0
        if len(entries) > 1:
            index = simpledialog.askinteger('选择数据集 / 帧',
                '\n'.join(f'{i}: {entry}' for i, entry in enumerate(entries[:40])) +
                f'\n输入序号 0–{len(entries)-1}', minvalue=0, maxvalue=len(entries)-1, parent=self.window)
            if index is None: return
        entry = entries[index]
        image, metadata = load_frame(path, entry['dataset'], entry['frame'])
        source = capture_source(path, dataset=entry['dataset'], frame=entry['frame'])
        model = WorkbenchModel(image, source, self.mode.get(), self.initial_params, self.settings)
        model.state['source_metadata'] = metadata
        self.replace_model(model); self.dirty = False
        self.status.set(f'已打开 {path.name}，数据集 {entry["dataset"]} / 帧 {entry["frame"]}')

    def choose_project(self):
        name = filedialog.askopenfilename(parent=self.window, filetypes=[('HRTEM project', '*.hrtemproj')])
        if name: self.load_session(Path(name))

    def load_session(self, path):
        if not self.confirm_replace(): return
        if path.suffix.lower() == '.hrtemproj':
            state, arrays, warnings = load_project(path)
            model = WorkbenchModel(arrays['image'], state.get('source', {}),
                                   state['mode'], state['params'], state['settings'])
            model.state, model.arrays = state, arrays
            self.replace_model(model)
            if warnings: messagebox.showwarning('原图核验', '\n'.join(warnings), parent=self.window)
            self.status.set('项目已恢复：' + str(path) + ('；原图核验警告' if warnings else ''))
        else:
            self.replace_model(WorkbenchModel.from_session(path, self.initial_params, self.settings))
            self.status.set('历史会话已打开：' + str(path))
            if self.model.state.get('warnings'):
                messagebox.showwarning('历史会话提示', '\n'.join(self.model.state['warnings']), parent=self.window)
        self.dirty = False

    def confirm_replace(self):
        if not self.dirty: return True
        answer=messagebox.askyesnocancel('未保存的编辑','打开其他文件前保存当前项目吗？',parent=self.window)
        if answer is None: return False
        if answer:
            self.save()
            return not self.dirty
        return True

    def save(self):
        if self.model is None: return
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension='.hrtemproj',
                 filetypes=[('HRTEM project', '*.hrtemproj')], initialfile='microscopy_project.hrtemproj')
        if path:
            self.model.state['view'] = self.view.get()
            self.capture_limits()
            save_project(path, self.model.state, self.model.arrays)
            self.dirty = False; self.status.set('项目已保存：' + path)

    def current_image(self):
        view = self.view.get()
        if view == 'image': return self.model.arrays['image']
        kind, roi_id = view.split(':', 1)
        key = 'fft_log' if kind == 'fft' else 'ifft' if kind == 'ifft' else 'mask'
        return self.model.arrays.get(f'roi:{roi_id}:{key}', self.model.arrays['image'])

    def visible_objects(self):
        if self.model is None: return []
        view = self.view.get()
        return [o for o in self.model.state['objects'] if object_view(o) == view]

    def render(self, ax, exporting=False):
        image = self.current_image(); state = self.model.state; settings = state['settings']
        h, w = image.shape
        if state['layers'].get('image', True):
            lo, hi = np.percentile(image, [.2, 99.8]); span = max(hi-lo, 1e-12)
            midpoint = (hi+lo)/2 - state['brightness']*span
            span /= state['contrast']
            ax.imshow(image, cmap='magma' if self.view.get().startswith('fft:') else 'gray',
                      vmin=midpoint-span/2, vmax=midpoint+span/2, origin='upper', interpolation='nearest')
        ax.set_xlim(-.5, w-.5); ax.set_ylim(h-.5, -.5)
        font = resolve_font(settings, settings['label_size'])
        self.label_artists = {}
        for o in self.visible_objects():
            kind = o['kind']
            if not state['layers'].get(kind if kind != 'center' else 'spot', True): continue
            color = o.get('color', settings['arrow_color'])
            lw = settings['arrow_linewidth']
            if not exporting and o['id'] in self.selected: color, lw = '#ff40ff', max(2.5, lw)
            x, y = o['x'], o['y']
            if kind == 'roi':
                ax.add_patch(Rectangle((x,y), o['w'], o['h'], fill=False, ec=color, lw=lw))
                if not exporting and o['id'] in self.selected:
                    ax.plot(x+o['w'], y+o['h'], 's', color=color, markersize=6)
            elif kind in ('ring','spot','center'):
                ax.add_patch(Circle((x,y), o.get('radius', 5), fill=False, ec=color, lw=lw))
            elif kind in ('arrow','scale'):
                ax.add_patch(FancyArrowPatch((x,y), (o.get('x2',x+30),o.get('y2',y)),
                    arrowstyle='<->' if kind == 'arrow' else '|-|', color=color, lw=lw,
                    mutation_scale=settings['arrow_size']*2))
            if state['layers'].get('label', True):
                label = o.get('label', '')
                if label:
                    artist = ax.text(o.get('label_x',x+8), o.get('label_y',y-8), label,
                        color=o.get('text_color', settings['text_color']), fontproperties=font,
                        bbox=dict(facecolor=settings['label_background'], alpha=settings['label_background_alpha'], pad=1.5))
                    self.label_artists[o['id']] = artist
        if not settings['show_axes']: ax.set_axis_off()
        else:
            ax.set_xlabel('x (px)', fontproperties=font); ax.set_ylabel('y (px)', fontproperties=font)

    def refresh(self, reset=False):
        if self.model is None: return
        limits = self.ax.get_xlim(), self.ax.get_ylim()
        self.ax.clear(); self.render(self.ax)
        if not reset: self.ax.set_xlim(limits[0]); self.ax.set_ylim(limits[1])
        self.canvas.draw_idle()
        values = ['image']
        for roi_id in self.model.state['results']:
            if f'roi:{roi_id}:fft_log' in self.model.arrays:
                values += [f'fft:{roi_id}', f'mask:{roi_id}', f'ifft:{roi_id}']
        self.views.configure(values=values)
        self.items.delete(0, 'end'); self.list_ids=[]
        for i,o in enumerate(self.visible_objects()):
            self.list_ids.append(o['id'])
            self.items.insert('end', f"{o['kind']} | {o.get('label') or o['id']}")
            if o['id'] in self.selected: self.items.selection_set(i)

    def change_view(self, event=None):
        if self.busy or self.model is None: return
        self.model.state['view'] = self.view.get(); self.selected.clear(); self.refresh(reset=True)
        limits = self.model.state.get('view_limits', {}).get(self.view.get())
        if limits:
            self.ax.set_xlim(limits[0]); self.ax.set_ylim(limits[1]); self.canvas.draw_idle()

    def capture_limits(self):
        if self.model:
            self.model.state.setdefault('view_limits',{})[self.view.get()]=[
                list(map(float,self.ax.get_xlim())),list(map(float,self.ax.get_ylim()))]

    def select_list(self, event=None):
        if self.busy: return
        self.selected = {self.list_ids[i] for i in self.items.curselection()}
        self.refresh()

    def scroll(self, event):
        if self.busy or self.model is None or event.inaxes != self.ax: return
        factor = .8 if event.button == 'up' else 1.25
        x,y=event.xdata,event.ydata
        self.ax.set_xlim([x+(v-x)*factor for v in self.ax.get_xlim()])
        self.ax.set_ylim([y+(v-y)*factor for v in self.ax.get_ylim()]); self.capture_limits(); self.canvas.draw_idle()

    def hit(self, event):
        x,y=event.xdata,event.ydata
        tolerance = abs(self.ax.get_xlim()[1]-self.ax.get_xlim()[0])*.015
        objects=self.visible_objects()
        if self.tool.get() == '拖动标签':
            self.canvas.draw()
            for o in reversed(objects):
                artist=self.label_artists.get(o['id'])
                if artist and artist.get_window_extent().contains(event.x,event.y): return o
            return None
        for o in reversed(objects):
            if not self.model.state['layers'].get(o['kind'] if o['kind']!='center' else 'spot',True): continue
            if o['kind']=='roi':
                if o['x']<=x<=o['x']+o['w'] and o['y']<=y<=o['y']+o['h']: return o
            elif o['kind']=='ring':
                if abs(np.hypot(x-o['x'],y-o['y'])-o['radius'])<tolerance: return o
            elif np.hypot(x-o['x'],y-o['y'])<o.get('radius',5)+tolerance: return o
        return None

    def press(self, event):
        if self.busy or self.model is None or event.inaxes!=self.ax or event.button!=1 or self.toolbar.mode: return
        self.canvas.get_tk_widget().focus_set()
        tool=self.tool.get(); point=(event.xdata,event.ydata)
        obj=self.hit(event)
        if tool in ('选择/拖动','拖动标签','缩放 ROI'):
            if not obj: self.selected.clear(); self.refresh(); return
            if event.key and 'control' in event.key:
                self.selected.symmetric_difference_update({obj['id']}); self.refresh(); return
            if obj['id'] not in self.selected: self.selected={obj['id']}
            self.checkpoint()
            self.drag={'start':point, 'original':deepcopy(self.model.state['objects']), 'tool':tool,'hit':obj['id']}
        elif tool=='框选':
            self.drag={'start':point,'tool':tool}
        else:
            self.checkpoint(); self.drag={'start':point,'tool':tool}
        self.refresh()

    def motion(self, event):
        if self.busy or not self.drag or event.inaxes!=self.ax: return
        tool=self.drag['tool']; x0,y0=self.drag['start']; x,y=event.xdata,event.ydata
        if 'original' not in self.drag: return
        dx,dy=x-x0,y-y0
        old={o['id']:o for o in self.drag['original']}
        h,w=self.current_image().shape
        for o in self.model.state['objects']:
            if o['id'] not in self.selected: continue
            before=old[o['id']]
            if tool=='拖动标签':
                o['label_x']=before.get('label_x',before['x']+8)+dx
                o['label_y']=before.get('label_y',before['y']-8)+dy
            elif tool=='缩放 ROI' and o['kind']=='roi':
                o['w']=max(64,min(w-o['x'],x-o['x'])); o['h']=max(64,min(h-o['y'],y-o['y']))
            elif o['kind']=='ring': o['radius']=max(1,float(np.hypot(x-o['x'],y-o['y'])))
            else:
                o['x']=float(np.clip(before['x']+dx,0,max(0,w-o.get('w',1))))
                o['y']=float(np.clip(before['y']+dy,0,max(0,h-o.get('h',1))))
                if 'x2' in o: o['x2']=before['x2']+dx; o['y2']=before['y2']+dy
                if o['kind']=='center':
                    for ring in self.model.state['objects']:
                        if ring['kind']=='ring': ring['x'],ring['y']=o['x'],o['y']
        self.refresh()

    def release(self, event):
        if self.toolbar.mode:
            self.capture_limits(); return
        if self.busy or not self.drag or self.model is None: return
        drag=self.drag; self.drag=None
        if event.inaxes!=self.ax:
            if 'original' in drag: self.geometry_changed(drag['tool'])
            return
        x0,y0=drag['start']; x,y=event.xdata,event.ydata; tool=drag['tool']
        def finish():
            if tool=='框选':
                self.selected={o['id'] for o in self.visible_objects()
                    if min(x,x0)<=o['x']<=max(x,x0) and min(y,y0)<=o['y']<=max(y,y0)}
            elif tool=='新增 ROI':
                if self.view.get()!='image' or self.mode.get()!='hrtem': raise ValueError('ROI 只能添加到 HRTEM 原图。')
                if abs(x-x0)<64 or abs(y-y0)<64: raise ValueError('ROI 宽高至少为 64 px。')
                self.model.add_object('roi',view='image',x=int(min(x,x0)),y=int(min(y,y0)),w=int(abs(x-x0)),h=int(abs(y-y0)))
            elif tool=='新增斑点':
                if self.mode.get() == 'hrtem' and not self.view.get().startswith('fft:'):
                    raise ValueError('HRTEM 选斑请先切换到对应 ROI 的 FFT 视图。')
                self.model.add_object('spot',view=self.view.get(),x=x,y=y,radius=float(self.radius.get()))
            elif tool=='新增环':
                center=next((o for o in self.model.state['objects'] if o['kind']=='center'),None)
                if center is None: raise ValueError('请先运行 SAED 自动分析，建立衍射中心。')
                self.model.add_object('ring',view='image',x=center['x'],y=center['y'],radius=float(np.hypot(x-center['x'],y-center['y'])))
            elif tool in ('新增箭头','新增比例尺'):
                length=float(np.hypot(x-x0,y-y0)); nm=self.model.state['params'].get('nm_per_pixel')
                label=f'{length:.2f} px'
                if self.mode.get()=='hrtem' and nm and self.view.get()=='image': label=f'{length*nm:.4g} nm'
                self.model.add_object('scale' if tool=='新增比例尺' else 'arrow',view=self.view.get(),x=x0,y=y0,x2=x,y2=y,label=label)
            self.geometry_changed(tool); self.refresh()
        self.safe(finish)

    def geometry_changed(self, tool):
        if tool=='拖动标签' or tool=='框选': return
        if self.view.get()=='image' and self.mode.get()=='hrtem':
            for oid in list(self.selected):
                o=next((o for o in self.model.state['objects'] if o['id']==oid),None)
                if o and o['kind']=='roi': self.model.invalidate_roi(oid)
        if self.mode.get()=='saed' and 'saed' in self.model.state['results']: self.model.recompute_saed()
        if self.view.get().startswith('fft:'):
            self.status.set('FFT 斑点已修改。点击“更新 IFFT”生成新的 mask 和重建图。')
            self.model.state.setdefault('ifft_dirty',{})[self.view.get().split(':',1)[1]]=True
        self.dirty=True

    def delete(self):
        if not self.model: return
        self.model.delete(self.selected); self.selected.clear(); self.geometry_changed('删除'); self.refresh()

    def duplicate(self):
        if not self.model: return
        self.model.duplicate(self.selected); self.geometry_changed('复制'); self.refresh()

    def apply_radius(self):
        radius=float(self.radius.get())
        if not np.isfinite(radius) or radius<=0: raise ValueError('半径必须为正数。')
        self.checkpoint()
        for o in self.model.state['objects']:
            if o['id'] in self.selected and o['kind'] in ('spot','ring'): o['radius']=radius
        self.geometry_changed('半径'); self.refresh()

    def edit_annotation(self):
        if not self.selected: return
        label=simpledialog.askstring('文字','输入文字（取消保留原文字）',parent=self.window)
        color=colorchooser.askcolor(parent=self.window,title='标注颜色')[1]
        self.checkpoint()
        for o in self.model.state['objects']:
            if o['id'] in self.selected:
                if label is not None: o['label']=label
                if color: o['color']=color; o['text_color']=color
        self.refresh()

    def arrange_labels(self):
        if not self.model: return
        self.checkpoint(); self.canvas.draw()
        renderer=self.canvas.get_renderer(); occupied=[]
        for o in self.visible_objects():
            artist=self.label_artists.get(o['id'])
            if artist is None: continue
            anchor = (o['x'], o['y'])
            if o['kind'] == 'ring':
                anchor = (o.get('label_x', o['x']+.707*o['radius']),
                          o.get('label_y', o['y']-.707*o['radius']))
            px,py=self.ax.transData.transform(anchor)
            chosen=None; best_score=float('inf')
            for radius in (15,30,50,75,110,150):
                for angle in np.linspace(0,2*np.pi,16,endpoint=False):
                    tx,ty=self.ax.transData.inverted().transform((px+radius*np.cos(angle),py+radius*np.sin(angle)))
                    artist.set_position((tx,ty)); box=artist.get_window_extent(renderer).expanded(1.08,1.2)
                    overlap=sum(box.overlaps(other) for other in occupied)
                    inside=self.ax.bbox.contains(box.x0,box.y0) and self.ax.bbox.contains(box.x1,box.y1)
                    score=overlap*1000+(0 if inside else 10000)+radius
                    if score<best_score: chosen=(tx,ty,box); best_score=score
                    if overlap==0 and inside: break
                if best_score==radius: break
            o['label_x'],o['label_y']=float(chosen[0]),float(chosen[1]); occupied.append(chosen[2])
        self.refresh(); self.status.set('标签已自动避让；密集区域可继续手动拖动。')

    def toggle_layer(self,key):
        if self.model and not self.busy:
            self.checkpoint(); self.model.state['layers'][key]=self.layer_vars[key].get(); self.refresh()

    def display_change(self,unused=None):
        if self.model and not self.busy:
            self.model.state.update(brightness=self.brightness.get(),contrast=self.contrast.get()); self.dirty=True; self.refresh()

    def undo(self):
        if self.model: self.model.undo(); self.replace_model(self.model); self.dirty=True

    def redo(self):
        if self.model: self.model.redo(); self.replace_model(self.model); self.dirty=True

    def restore(self):
        if self.model: self.model.restore_auto(); self.replace_model(self.model); self.dirty=True

    def compute(self, operation):
        if self.model is None: raise ValueError('请先打开图像。')
        clone=copy(self.model)
        clone.state=deepcopy(self.model.state); clone.arrays=dict(self.model.arrays)
        clone._undo=[]; clone._redo=[]
        clone.state['mode']=self.mode.get()
        self.busy=True; self.status.set('正在后台计算…')
        def work():
            started=time.perf_counter()
            try:
                operation(clone)
                clone.state['elapsed_seconds']=time.perf_counter()-started
                clone.state['created_at']=datetime.now().astimezone().isoformat()
                self.messages.put(('done',clone))
            except Exception as exc: self.messages.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        if self.closed: return
        try:
            kind,value=self.messages.get_nowait(); self.busy=False
            if kind=='error': messagebox.showerror('分析失败',value,parent=self.window); self.status.set(value)
            else:
                self.model.checkpoint()
                self.model.state,self.model.arrays=value.state,value.arrays
                self.replace_model(self.model); self.dirty=True
                self.status.set('计算完成。切换视图编辑 FFT 斑点，或导出/保存项目。')
        except queue.Empty: pass
        self.after_id=self.window.after(100,self.poll)

    def analyze(self):
        if self.model is None: raise ValueError('请先打开图像。')
        if self.model.state['mode'] != self.mode.get():
            raise ValueError('请用相应模式重新打开图像，避免混合 HRTEM 与 SAED 对象。')
        mode=self.mode.get()
        self.compute(lambda m: m.analyze_saed() if mode=='saed' else m.analyze_rois())

    def ifft(self):
        if not self.view.get().startswith(('fft:','ifft:','mask:')): raise ValueError('请先切换到某个 ROI 的 FFT 视图。')
        roi_id=self.view.get().split(':',1)[1]
        self.compute(lambda m: m.update_ifft(roi_id))

    def parameters(self):
        from workbench_model import DEFAULT_PARAMS
        params=deepcopy(self.model.state['params'] if self.model else {**DEFAULT_PARAMS,**self.initial_params})
        win=tk.Toplevel(self.window); win.title('参数与标定'); variables={}
        labels={'min_spacing':'最小周期 px','max_spacing':'最大周期 px','min_peak_ratio':'FFT 峰值阈值',
                'nm_per_pixel':'HRTEM 标定 nm/px（空为无）','calibration_nm_inv_per_px':'SAED 倒易标定 nm⁻¹/px（空为无）',
                'max_rings':'最多环数','max_spots':'最多斑点数','mask_radius_px':'mask 半径 px','auto_spot_count':'自动 FFT 斑点数'}
        for i,(key,label) in enumerate(labels.items()):
            ttk.Label(win,text=label).grid(row=i,column=0,sticky='w',padx=8,pady=5)
            variables[key]=tk.StringVar(win,'' if params.get(key) is None else str(params[key]))
            ttk.Entry(win,textvariable=variables[key]).grid(row=i,column=1,padx=8)
        def apply():
            try:
                from workbench_model import validate_params
                raw={key:(None if not var.get().strip() else float(var.get())) for key,var in variables.items()}
                clean=validate_params(raw)
                self.initial_params=clean
                if self.model:
                    self.checkpoint(); self.model.state['params']=clean
                    self.model.state['parameters_changed']=True
                self.status.set('参数已保存；点击自动分析更新测量结果。'); win.destroy()
            except Exception as exc: messagebox.showerror('参数无效',str(exc),parent=win)
        ttk.Button(win,text='应用',command=apply).grid(row=len(labels),column=1,pady=10)

    def save_preset(self):
        name=simpledialog.askstring('保存预设','仪器/倍率/条件名称',parent=self.window)
        if name:
            params=self.model.state['params'] if self.model else self.initial_params
            save_preset(self.project_root,name,params,self.settings); self.status.set('预设已保存：'+name)

    def load_preset(self):
        names=list_presets(self.project_root)
        name=simpledialog.askstring('加载预设','可用预设：'+', '.join(names)+'\n输入名称',parent=self.window)
        if name:
            data=load_preset(self.project_root,name)
            self.initial_params=data['parameters']; self.settings=data['export_settings']
            if self.model:
                self.checkpoint(); self.model.state['params']=deepcopy(self.initial_params)
                self.model.state['settings']=deepcopy(self.settings); self.model.state['parameters_changed']=True
                self.refresh()
            self.status.set('预设已加载；参数变化需要重新分析。')

    def styles(self):
        from hrtem_export_ui import open_settings
        def changed(settings):
            self.settings=settings
            if self.model: self.checkpoint(); self.model.state['settings']=deepcopy(settings); self.refresh()
        open_settings(self.window,self.project_root,self.settings,changed)

    def export(self):
        if self.model is None: return
        if self.model.state.get('parameters_changed'): raise ValueError('分析参数已改变，请先自动分析后导出。')
        if any(self.model.state.get('ifft_dirty',{}).values()): raise ValueError('FFT 斑点已修改，请先更新对应 ROI 的 IFFT。')
        output=self.project_root/'outputs'/('workspace_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        output.mkdir(parents=True,exist_ok=False)
        self.model.export_results(output)
        from annotation_export import _figure, _save
        original_view=self.view.get()
        try:
            for view in ['image']+[f'fft:{rid}' for rid in self.model.state['results'] if f'roi:{rid}:fft_log' in self.model.arrays]:
                self.view.set(view)
                figure=_figure(self.settings,self.current_image().shape); axis=figure.add_subplot(111)
                self.render(axis,exporting=True)
                _save(figure,output/('workspace_'+view.replace(':','_')),self.settings)
        finally:
            self.view.set(original_view); self.model.state['view']=original_view
            self.refresh()
        self.model.state['output_dir']=str(output)
        self.capture_limits()
        save_project(output/'workspace.hrtemproj',self.model.state,self.model.arrays)
        self.refresh(); self.dirty=False; self.status.set('已导出：'+str(output))
        if self.on_output: self.on_output(output,self.mode.get())

    def close(self):
        if self.busy:
            messagebox.showinfo('计算中','请等待当前计算完成再关闭。',parent=self.window); return
        if self.dirty:
            answer=messagebox.askyesnocancel('保存项目','关闭前保存当前项目吗？',parent=self.window)
            if answer is None: return
            if answer:
                self.save()
                if self.dirty: return
        self.dispose()

    def dispose(self):
        self.closed=True
        if self.after_id:
            self.window.after_cancel(self.after_id); self.after_id=None
        idle=getattr(self.canvas,'_idle_draw_id',None)
        if idle:
            self.canvas.get_tk_widget().after_cancel(idle)
            self.canvas._idle_draw_id=None
        self.window.destroy()


def open_workspace(parent, root, **kwargs):
    return ImageWorkbench(parent,root,**kwargs)
