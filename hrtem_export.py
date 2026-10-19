"""Presentation-only exports for saved HRTEM measurements.

All FFT, ACF, ROI and calibration calculations remain in hrtem_spacing.py.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import shutil

import matplotlib as mpl
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import Rectangle
import numpy as np
import tifffile
from matplotlib import image as mpl_image

DEFAULT = {
    'font_family': 'DejaVu Sans', 'font_file': '', 'font_style': 'normal',
    'label_size': 9.5, 'title_size': 12.0, 'axis_size': 9.5,
    'tick_size': 8.5, 'legend_size': 9.0,
    'text_color': '#ffffff', 'arrow_color': '#00ffc8',
    'roi_ok_color': '#00ffc8', 'roi_verify_color': '#ffcc33',
    'label_background': '#000000', 'label_background_alpha': 0.83,
    'arrow_size': 6.0, 'arrow_linewidth': 2.1, 'roi_linewidth': 0.85,
    'label_position': 'top', 'show_title': True, 'show_axes': True,
    'show_roi': True, 'background': 'white', 'formats': ['png'],
    'dpi': 300, 'width_mm': 180.0, 'height_mm': 0.0,
    'svg_text_mode': 'editable',
}
FORMAT_CHOICES = ('png', 'tiff', 'svg', 'pdf')

def available_fonts() -> list[str]:
    """Names indexed from actual local font files, plus Matplotlib's bundled fonts."""
    font_manager._load_fontmanager(try_read_cache=False)
    return sorted(set(font_manager.fontManager.get_font_names()), key=str.casefold)

def _font_style(style: str) -> tuple[str, str]:
    return ('bold' if 'bold' in style else 'normal',
            'italic' if 'italic' in style else 'normal')

def resolve_font(settings: dict, size: float) -> font_manager.FontProperties:
    weight, slant = _font_style(settings['font_style'])
    filename = settings['font_file']
    if filename:
        path = Path(filename).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in ('.ttf', '.otf'):
            raise ValueError(f'Font file missing or unsupported: {path}')
        font_manager.fontManager.addfont(str(path))
        prop = font_manager.FontProperties(fname=str(path), size=size)
    else:
        prop = font_manager.FontProperties(family=[settings['font_family']],
                                           weight=weight, style=slant, size=size)
        try:
            font_manager.findfont(prop, fallback_to_default=False)
        except ValueError as exc:
            raise ValueError(f'Font is not installed: {settings["font_family"]}') from exc
    return prop

def validate_settings(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('Export settings must be a JSON object.')
    settings = deepcopy(DEFAULT)
    unknown = set(value) - set(DEFAULT)
    if unknown:
        raise ValueError(f'Unknown export settings: {sorted(unknown)}')
    settings.update(value)
    if settings['font_style'] not in ('normal', 'bold', 'italic', 'bold italic'):
        raise ValueError('Font style must be normal, bold, italic, or bold italic.')
    if settings['svg_text_mode'] not in ('editable', 'path'):
        raise ValueError('SVG text mode must be editable or path.')
    if settings['label_position'] not in ('top', 'center', 'bottom'):
        raise ValueError('Label position must be top, center, or bottom.')
    if settings['background'] not in ('white', 'transparent'):
        raise ValueError('Background must be white or transparent.')
    formats = settings['formats']
    if not isinstance(formats, list) or not formats or len(set(formats)) != len(formats) or any(fmt not in FORMAT_CHOICES for fmt in formats):
        raise ValueError('Select one or more unique output formats.')
    for name in ('label_size','title_size','axis_size','tick_size','legend_size'):
        settings[name] = float(settings[name])
        if not 5 <= settings[name] <= 72: raise ValueError(f'{name} must be 5-72 pt.')
    for name in ('arrow_size','arrow_linewidth','roi_linewidth'):
        settings[name] = float(settings[name])
        if not 0.2 <= settings[name] <= 50: raise ValueError(f'{name} is out of range.')
    settings['label_background_alpha'] = float(settings['label_background_alpha'])
    if not 0 <= settings['label_background_alpha'] <= 1: raise ValueError('Background opacity must be 0-1.')
    settings['dpi'] = int(settings['dpi'])
    settings['width_mm'] = float(settings['width_mm'])
    settings['height_mm'] = float(settings['height_mm'])
    if not 72 <= settings['dpi'] <= 1200: raise ValueError('DPI must be 72-1200.')
    if not 50 <= settings['width_mm'] <= 400: raise ValueError('Width must be 50-400 mm.')
    if settings['height_mm'] and not 50 <= settings['height_mm'] <= 400: raise ValueError('Height must be 50-400 mm, or 0 for auto.')
    for name in ('show_title','show_axes','show_roi'):
        if not isinstance(settings[name], bool): raise ValueError(f'{name} must be true or false.')
    for name in ('text_color','arrow_color','roi_ok_color','roi_verify_color','label_background'):
        if not mpl.colors.is_color_like(settings[name]): raise ValueError(f'Invalid color: {name}')
    if settings['roi_ok_color'] == settings['roi_verify_color']:
        raise ValueError('OK and VERIFY ROI colors must differ.')
    if not isinstance(settings['font_family'], str) or not settings['font_family'].strip():
        raise ValueError('Select a font family.')
    if not isinstance(settings['font_file'], str): raise ValueError('Font file must be a path.')
    resolve_font(settings, settings['label_size'])
    return settings

def settings_path(root: Path) -> Path:
    return root / 'config' / 'export_settings.json'

def _settings_log(root: Path, message: str) -> None:
    folder = root / 'logs'
    folder.mkdir(exist_ok=True)
    with (folder / 'export_settings.log').open('a', encoding='utf-8') as stream:
        stream.write(datetime.now().isoformat() + ' ' + message + '\n')

def save_settings(root: Path, settings: dict) -> dict:
    clean = validate_settings(settings)
    path = settings_path(root)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)
    return clean

def load_settings(root: Path) -> tuple[dict, str | None]:
    path = settings_path(root)
    if not path.exists():
        clean = save_settings(root, DEFAULT)
        return clean, None
    try:
        return validate_settings(json.loads(path.read_text(encoding='utf-8-sig'))), None
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        warning = f'Invalid export settings at {path}; restored defaults: {exc}'
        preserved = path.with_name('export_settings.invalid_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
        shutil.copy2(path, preserved)
        _settings_log(root, warning + f'; preserved original at {preserved}')
        return save_settings(root, DEFAULT), warning

def load_settings_file(path: Path) -> dict:
    return validate_settings(json.loads(path.read_text(encoding='utf-8-sig')))

def _figure(settings: dict, image_shape: tuple[int, int] | None = None, *, curves=False) -> Figure:
    width = settings['width_mm'] / 25.4
    if settings['height_mm']:
        height = settings['height_mm'] / 25.4
    elif curves:
        height = width * .56
    elif image_shape:
        height = width * image_shape[0] / image_shape[1]
    else:
        height = width
    if width*settings['dpi'] * height*settings['dpi'] > 100_000_000:
        raise ValueError('Output would exceed 100 million pixels; reduce size or DPI.')
    fig = Figure(figsize=(width, height), dpi=settings['dpi'],
                 facecolor='none' if settings['background']=='transparent' else 'white')
    FigureCanvasAgg(fig)
    return fig

def _fonts(settings: dict):
    return {key: resolve_font(settings, settings[key]) for key in
            ('label_size','title_size','axis_size','tick_size','legend_size')}

def _style_axis(ax, settings, fonts):
    ax.xaxis.label.set_fontproperties(fonts['axis_size'])
    ax.yaxis.label.set_fontproperties(fonts['axis_size'])
    ax.title.set_fontproperties(fonts['title_size'])
    for txt in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
        txt.set_fontproperties(fonts['tick_size'])
    ax.tick_params(labelsize=settings['tick_size'])

def _annotation_figure(image, rows, nm_per_pixel, settings, *, overlay=False):
    fig = _figure(settings, image.shape)
    ax = fig.add_axes([.09, .09, .82, .82])
    ax.set_aspect('auto')
    fonts = _fonts(settings)
    h,w = image.shape
    if not overlay:
        low,high = np.percentile(image,[.2,99.8])
        ax.imshow(image,cmap='gray',vmin=low,vmax=high,origin='upper',interpolation='none',rasterized=True)
    for row in rows:
        if 'd_fft_px' not in row: continue
        x,y,rw,rh = row['roi']
        status = row['status']
        color = settings['roi_ok_color'] if status=='OK' else settings['roi_verify_color']
        cx,cy = x+rw/2,y+rh/2
        theta = math.radians(row['normal_angle_deg'])
        nx,ny = math.cos(theta),math.sin(theta)
        half = min(rw,rh)*.19
        if settings['show_roi']:
            ax.add_patch(Rectangle((x+3,y+3),rw-6,rh-6,fill=False,edgecolor=color,
                                   linewidth=settings['roi_linewidth'],linestyle='-' if status=='OK' else '--',alpha=.8))
        ax.annotate('',xy=(cx+half*nx,cy+half*ny),xytext=(cx-half*nx,cy-half*ny),
                    arrowprops=dict(arrowstyle='<->',mutation_scale=settings['arrow_size'],
                                    color=settings['arrow_color'],lw=settings['arrow_linewidth']))
        period = row['d_fft_px']
        label = f'd = {period*nm_per_pixel:.4f} nm' if nm_per_pixel else f'd = {period:.2f} px'
        ly = cy if settings['label_position']=='center' else y+rh*(.20 if settings['label_position']=='top' else .80)
        ax.text(cx,ly,label,ha='center',va='center',color=settings['text_color'],
                fontproperties=fonts['label_size'],
                bbox=dict(facecolor=settings['label_background'],edgecolor=color,
                          alpha=settings['label_background_alpha'],pad=3))
    ax.set_xlim(0,w)
    ax.set_ylim(h,0)
    if overlay or not settings['show_axes']:
        ax.set_axis_off()
    else:
        ax.set_xlabel('x (pixels)')
        ax.set_ylabel('y (pixels)')
        _style_axis(ax,settings,fonts)
    if settings['show_title'] and not overlay:
        fig.text(.5,.965,'HRTEM fringe period | '+('calibrated in nm' if nm_per_pixel else 'uncalibrated: pixel period only'),
                 ha='center',va='top',fontproperties=fonts['title_size'],color='black')
    return fig

def _save(fig: Figure, path: Path, settings: dict, fmt: str, *, transparent=False, native_shape=None):
    render_dpi=settings['dpi']
    if native_shape is not None and fmt in ('svg','pdf'):
        render_dpi=max(render_dpi, math.ceil(max(native_shape[1]/(fig.get_figwidth()*.82),
                                                   native_shape[0]/(fig.get_figheight()*.82))))
    with mpl.rc_context({'pdf.fonttype':42,'pdf.use14corefonts':False,
                         'svg.fonttype':'none' if settings['svg_text_mode']=='editable' else 'path',
                         'svg.image_inline':True}):
        fig.savefig(path,format=fmt,dpi=render_dpi,facecolor='none' if transparent else fig.get_facecolor(),
                    transparent=transparent,metadata=None if fmt=='tiff' else {'Creator':'HRTEM export'})
    return render_dpi

def _diagnostic_figure(diag: dict, roi, nm_per_pixel, settings):
    fig = _figure(settings, curves=True)
    axes = fig.subplots(2,2)
    fonts = _fonts(settings)
    tile=diag['tile']; ff=diag['fft']; logmag=ff['logmag'];h,w=tile.shape
    lo,hi=np.percentile(tile,[.2,99.8])
    axes[0,0].imshow(tile,cmap='gray',vmin=lo,vmax=hi,interpolation='none',rasterized=True)
    theta=math.radians(ff['angle_normal_deg'])
    dx,dy=min(h,w)*.2*math.cos(theta),min(h,w)*.2*math.sin(theta)
    axes[0,0].plot([w/2-dx,w/2+dx],[h/2-dy,h/2+dy],color=settings['arrow_color'],lw=settings['arrow_linewidth'])
    label=f"{ff['d_fft_px']*nm_per_pixel:.4f} nm" if nm_per_pixel else f"{ff['d_fft_px']:.2f} px"
    axes[0,0].set_title('Fringe ROI; d = '+label)
    axes[0,1].imshow(logmag,cmap='magma',vmin=np.percentile(logmag,5),vmax=np.percentile(logmag,99.9),
                     interpolation='none',rasterized=True)
    axes[0,1].scatter([ff['peak_x'],w-1-ff['peak_x']],[ff['peak_y'],h-1-ff['peak_y']],
                      s=90,facecolors='none',edgecolors=settings['arrow_color'],linewidths=1.5)
    axes[0,1].set_xlim(w/2-w*.27,w/2+w*.27)
    axes[0,1].set_ylim(h/2+h*.27,h/2-h*.27)
    axes[0,1].set_title('Local FFT (raster intensity)')
    if diag['profile'] is not None:
        axes[1,0].plot(np.arange(len(diag['profile'])),diag['profile'],lw=.9,color='#1767ac')
        axes[1,0].set_title('Intensity profile')
    if diag['acf'] is not None:
        limit=min(len(diag['acf']),int(4.5*ff['d_fft_px'])+1)
        axes[1,1].plot(np.arange(limit),diag['acf'][:limit],lw=1,color='#1767ac')
        axes[1,1].axvline(ff['d_fft_px'],color='#e98200',ls='--',label='FFT period')
        axes[1,1].legend(prop=fonts['legend_size'])
        axes[1,1].set_title('1-D autocorrelation')
    axes[1,0].set_xlabel('Distance (px)')
    axes[1,1].set_xlabel('Lag (px)')
    for ax in axes.flat: _style_axis(ax,settings,fonts)
    fig.subplots_adjust(left=.11,right=.96,bottom=.11,top=.88,wspace=.35,hspace=.5)
    if settings['show_title']:
        fig.suptitle(f'ROI {tuple(roi)} | FFT peak / background {ff["peak_to_median"]:.1f}',
                     fontproperties=fonts['title_size'])
    return fig

def _curves_figure(diag: dict, settings: dict):
    fig=_figure(settings,curves=True)
    fonts=_fonts(settings)
    axes=fig.subplots(1,2)
    for ax,name,title in zip(axes,('profile','acf'),('Intensity profile','Autocorrelation')):
        values=diag[name]
        if values is not None:
            ax.plot(np.arange(len(values)),values,lw=1,color='#1767ac')
        ax.set_title(title)
        ax.set_xlabel('Distance / lag (px)')
        _style_axis(ax,settings,fonts)
    fig.subplots_adjust(left=.10,right=.97,bottom=.18,top=.84,wspace=.35)
    return fig

def export_all(image, rows, nm_per_pixel, diag, roi, output: Path, settings: dict) -> list[str]:
    settings=validate_settings(settings)
    output.mkdir(parents=True,exist_ok=True)
    written=[]
    fig=_annotation_figure(image,rows,nm_per_pixel,settings)
    plot_pixels=[round(fig.get_figwidth()*.82*settings['dpi']),round(fig.get_figheight()*.82*settings['dpi'])]
    warnings=[]
    if plot_pixels[0]<image.shape[1] or plot_pixels[1]<image.shape[0]:
        warnings.append(f'{settings["dpi"]} DPI raster exports place the {image.shape[1]}x{image.shape[0]} source within '
                        f'a {plot_pixels[0]}x{plot_pixels[1]} plot area; use higher DPI or a larger figure for native pixel detail. '
                        'SVG/PDF automatically embed at least the native source pixel count.')
    vector_dpi={}
    try:
        for fmt in settings['formats']:
            name='lattice_spacing_annotated.'+fmt
            used=_save(fig,output/name,settings,fmt,transparent=settings['background']=='transparent',
                       native_shape=image.shape)
            if fmt in ('svg','pdf'): vector_dpi[fmt]=used
            written.append(name)
    finally: fig.clear()
    fig=_annotation_figure(image,rows,nm_per_pixel,settings,overlay=True)
    try:
        _save(fig,output/'annotations_only.svg',settings,'svg',transparent=True)
        written.append('annotations_only.svg')
    finally: fig.clear()
    if diag is not None:
        fig=_diagnostic_figure(diag,roi,nm_per_pixel,settings)
        try:
            for fmt in dict.fromkeys(['png']+[fmt for fmt in settings['formats'] if fmt in ('svg','pdf','tiff')]):
                name='fft_acf_diagnostics.'+fmt
                _save(fig,output/name,settings,fmt,transparent=settings['background']=='transparent')
                written.append(name)
        finally: fig.clear()
        fig=_curves_figure(diag,settings)
        try:
            for fmt in ('svg','pdf'):
                name='curves_only.'+fmt
                _save(fig,output/name,settings,fmt,transparent=True)
                written.append(name)
        finally: fig.clear()
    (output/'export_settings_used.json').write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
    metadata={'source_image_pixels':list(image.shape[::-1]),'raster_plot_pixels_at_requested_dpi':plot_pixels,
              'vector_export_effective_dpi':vector_dpi,'warnings':warnings,
              'diagram_type':'raster microscope image with vector annotations'}
    (output/'export_metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    for warning in warnings: print('EXPORT WARNING:',warning)
    return written

def save_session(output: Path, source: Path, rows: list[dict], nm_per_pixel,
                 diag: dict | None, roi) -> None:
    source=source.resolve()
    manifest={'version':1,'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'rows':rows,'nm_per_pixel':nm_per_pixel,'diagnostic_roi':roi,
              'has_diagnostics':diag is not None}
    (output/'analysis_session.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if diag is not None:
        ff={key:value for key,value in diag['fft'].items() if key!='logmag'}
        (output/'diagnostic_metadata.json').write_text(json.dumps(ff,indent=2),encoding='utf-8')
        np.savez_compressed(output/'diagnostic_arrays.npz',tile=diag['tile'],logmag=diag['fft']['logmag'],
                            profile=diag['profile'] if diag['profile'] is not None else np.array([]),
                            acf=diag['acf'] if diag['acf'] is not None else np.array([]))

def reexport(session_dir: Path, output: Path, settings: dict) -> list[str]:
    session_dir=session_dir.resolve(); output=output.resolve()
    manifest_file=session_dir/'analysis_session.json'
    if not manifest_file.is_file():
        raise ValueError('This legacy result has no saved diagnostic session. Run a new analysis once before re-exporting its full figures.')
    manifest=json.loads(manifest_file.read_text(encoding='utf-8'))
    source=Path(manifest['source'])
    if not source.is_file(): raise ValueError(f'Original image is unavailable: {source}')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=manifest['source_sha256']:
        raise ValueError('Original image changed; re-export cannot claim the saved measurements belong to this image.')
    if output.exists(): raise FileExistsError(f'Re-export destination already exists: {output}')
    image=tifffile.imread(source) if source.suffix.lower() in ('.tif','.tiff') else mpl_image.imread(source)
    image=np.asarray(image)
    if image.ndim==3: image=.2126*image[...,0]+.7152*image[...,1]+.0722*image[...,2]
    rows=manifest['rows']
    diag=None;roi=manifest['diagnostic_roi']
    if manifest['has_diagnostics']:
        meta=json.loads((session_dir/'diagnostic_metadata.json').read_text(encoding='utf-8'))
        with np.load(session_dir/'diagnostic_arrays.npz',allow_pickle=False) as data:
            profile=data['profile'];acf=data['acf']
            diag={'tile':data['tile'].copy(),'fft':dict(meta,logmag=data['logmag'].copy()),
                  'profile':profile.copy() if profile.size else None,
                  'acf':acf.copy() if acf.size else None}
    output.mkdir(parents=True,exist_ok=False)
    written=export_all(image,rows,manifest['nm_per_pixel'],diag,roi,output,settings)
    for name in ('analysis_session.json','diagnostic_metadata.json','diagnostic_arrays.npz',
                 'lattice_spacing_results.csv','summary.json'):
        src=session_dir/name
        if src.is_file(): shutil.copy2(src,output/name)
    return written
