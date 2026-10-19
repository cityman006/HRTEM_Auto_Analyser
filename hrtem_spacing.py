#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HRTEM lattice fringe analysis using local FFT + independent 1-D autocorrelation.

The program never infers an nm/pixel calibration from TIFF display-DPI fields.
It reports px unless explicit, physical pixel calibration is supplied.

Example:
    python hrtem_spacing.py -i sample.tif -o results --grid 4x4 --min-spacing 4 --max-spacing 15
    python hrtem_spacing.py -i sample.tif -o results_nm --nm-per-pixel 0.025
    python hrtem_spacing.py -i sample.tif -o results_nm --scale-points 35,1950,435,1950 --known-distance-nm 10
    python hrtem_spacing.py -i sample.tif -o results_nm --pick-scale --known-distance-nm 10
    python hrtem_spacing.py -i sample.tif -o local --roi 700,700,512,512
    python hrtem_spacing.py --self-test
"""
import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d, map_coordinates, maximum_filter
from scipy.signal import correlate
import tifffile


def load_grayscale(path):
    image = tifffile.imread(str(path)) if Path(path).suffix.lower() in ('.tif', '.tiff') else plt.imread(str(path))
    image = np.asarray(image)
    if image.ndim == 3:
        if image.shape[-1] >= 3:
            image = 0.2126*image[..., 0] + 0.7152*image[..., 1] + 0.0722*image[..., 2]
        else:
            image = image[..., 0]
    if image.ndim != 2:
        raise ValueError(f'Expected a 2D single-image file; received shape {image.shape}.')
    image = image.astype('float64')
    if not np.isfinite(image).all():
        raise ValueError('Image contains invalid pixel values (NaN/Inf).')
    if image.max() == image.min():
        raise ValueError('Image has no intensity contrast.')
    return image


def parabolic_offset(a, b, c):
    """Subpixel displacement from sample at center of three log-values."""
    denom = a - 2*b + c
    if abs(denom) < 1e-12:
        return 0.0
    return float(np.clip(0.5*(a-c)/denom, -0.5, 0.5))


def peak_from_fft(tile, min_spacing=4.0, max_spacing=15.0):
    """Return a principal *fringe periodicity*, not necessarily a unique (hkl)."""
    h, w = tile.shape
    if min(h, w) < 64:
        raise ValueError('ROI must be at least 64x64 pixels.')
    if min_spacing < 2.0 or max_spacing <= min_spacing or max_spacing > min(h, w)/3:
        raise ValueError('Invalid period search range for the size of the selected ROI.')
    # Low-frequency background subtraction, then suppress FFT boundary leakage.
    smoothed = gaussian_filter(tile, sigma=min(16., min(h, w)/12.))
    centered = tile - smoothed
    window = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    spec = np.fft.fftshift(np.fft.fft2(centered * window))
    mag = np.abs(spec)
    logmag = np.log1p(mag)

    yy, xx = np.mgrid[0:h, 0:w]
    fy = (yy - h//2) / h   # cycles / pixel, signed y coordinate
    fx = (xx - w//2) / w
    freq = np.hypot(fx, fy)
    # Only one member of each Friedel pair; avoid double-detection.
    half = (fy > 0) | ((fy == 0) & (fx > 0))
    admissible = ((freq >= 1.0/max_spacing) &
                  (freq <= 1.0/min_spacing) & half)
    peaks = (logmag == maximum_filter(logmag, size=7, mode='nearest')) & admissible
    pyx = np.argwhere(peaks)
    if len(pyx) == 0:
        return None
    strength = logmag[pyx[:, 0], pyx[:, 1]]
    py, px = pyx[np.argmax(strength)]
    # Subpixel center of frequency peak by fitting log amplitude independently in x and y.
    iy = py + parabolic_offset(logmag[py-1, px], logmag[py, px], logmag[py+1, px])
    ix = px + parabolic_offset(logmag[py, px-1], logmag[py, px], logmag[py, px+1])
    kx = (ix-w//2)/w
    ky = (iy-h//2)/h
    d_px = float(1.0 / np.hypot(kx, ky))
    angle_deg = float(np.rad2deg(np.arctan2(ky, kx)) % 180.)
    bkg = np.median(mag[admissible])
    ratio = float(mag[py, px] / max(bkg, 1e-12))
    return dict(d_fft_px=d_px, angle_normal_deg=angle_deg, kx=float(kx), ky=float(ky),
                peak_x=float(ix), peak_y=float(iy), peak_to_median=ratio,
                logmag=logmag)


def estimate_period_autocorrelation(tile, normal_vector, expected_px):
    """Cross-check local period with the 1-D autocorrelation of sampled profiles.

    Sample individual lines normal to fringes, keeping each profile separate;
    do not average intensity profiles (phase offsets could cancel the signal).
    """
    ny, nx = normal_vector[1], normal_vector[0]
    h, w = tile.shape
    xc, yc = (w-1)/2, (h-1)/2
    tangent_x, tangent_y = -ny, nx
    half_len = 0.30 * min(h, w)
    t = np.arange(-half_len, half_len+0.1, 1.0)
    estimated = []
    example_profile, example_acf = None, None
    for offset in (-25., -12., 0., 12., 25.):
        sample_x = xc + t*nx + offset*tangent_x
        sample_y = yc + t*ny + offset*tangent_y
        safe = (sample_x >= 1) & (sample_x < w-2) & (sample_y >= 1) & (sample_y < h-2)
        if np.count_nonzero(safe) < 80:
            continue
        z = map_coordinates(tile, [sample_y[safe], sample_x[safe]], order=1, mode='nearest')
        z = z - gaussian_filter1d(z, sigma=max(8, expected_px*2.2))
        z = z - z.mean()
        if np.std(z) < 1e-5:
            continue
        acf = correlate(z, z, mode='full', method='fft')[len(z)-1:]
        acf = acf / max(acf[0], 1e-12)
        # Only check a lag around the FFT estimate, to avoid a higher harmonic.
        lmin = max(2, int(np.floor(expected_px * 0.8)))
        lmax = min(len(acf)-2, int(np.ceil(expected_px * 1.2)))
        lag_candidates = np.arange(lmin, lmax+1)
        maxima = [int(j) for j in lag_candidates if acf[j] > acf[j-1] and acf[j] >= acf[j+1]]
        if not maxima:
            continue
        j = max(maxima, key=lambda i: acf[i])
        if acf[j] < 0.08:
            continue
        refined = j + parabolic_offset(acf[j-1], acf[j], acf[j+1])
        estimated.append(refined)
        if abs(offset) < 0.01:
            example_profile = z
            example_acf = acf
    if not estimated:
        return None, example_profile, example_acf
    return float(np.median(estimated)), example_profile, example_acf


def analyze_roi(image, roi, min_spacing, max_spacing, min_peak_ratio):
    x, y, w, h = roi
    if min(x, y) < 0 or w < 64 or h < 64 or x+w > image.shape[1] or y+h > image.shape[0]:
        raise ValueError(f'ROI out of bounds or too small: {roi}; image shape {image.shape}')
    tile = image[y:y+h, x:x+w]
    fft = peak_from_fft(tile, min_spacing, max_spacing)
    if fft is None:
        return {'roi': roi, 'status': 'NO_FFT_PEAK'}, None
    if fft['peak_to_median'] < min_peak_ratio:
        return {'roi': roi, 'status': 'LOW_FFT_CONTRAST', 'peak_to_median': fft['peak_to_median']}, None
    acf, profile, correlation = estimate_period_autocorrelation(
        tile, (fft['kx']/np.hypot(fft['kx'],fft['ky']), fft['ky']/np.hypot(fft['kx'],fft['ky'])),
        fft['d_fft_px'])
    agreement = (abs(acf-fft['d_fft_px'])/fft['d_fft_px']) if acf is not None else None
    status = 'OK' if agreement is not None and agreement <= 0.10 else 'VERIFY'
    row = {'roi': roi, 'd_fft_px': fft['d_fft_px'], 'd_acf_px': acf,
           'normal_angle_deg': fft['angle_normal_deg'],
           'peak_to_median': fft['peak_to_median'], 'relative_difference': agreement,
           'status': status}
    diag = {'tile': tile, 'fft': fft, 'profile': profile, 'acf': correlation}
    return row, diag


def choose_rois(shape, grid, raw_rois):
    h, w = shape
    if raw_rois:
        return [tuple(map(int, s.split(','))) for s in raw_rois]
    try:
        cols, rows = [int(q) for q in grid.lower().split('x')]
    except ValueError as exc:
        raise ValueError('--grid format is COLSxROWS, e.g. 4x4') from exc
    if min(cols, rows) < 1:
        raise ValueError('Grid dimensions must be positive.')
    rois = []
    for r in range(rows):
        for c in range(cols):
            x0 = round(c*w/cols); x1 = round((c+1)*w/cols)
            y0 = round(r*h/rows); y1 = round((r+1)*h/rows)
            rois.append((x0, y0, x1-x0, y1-y0))
    return rois


def points_to_px(points, known_distance_nm):
    x1,y1,x2,y2 = [float(n) for n in points.split(',')]
    pixel_distance = math.hypot(x2-x1,y2-y1)
    if pixel_distance <= 1 or known_distance_nm <= 0:
        raise ValueError('Calibration line must be longer than one pixel and the distance must be positive.')
    return known_distance_nm / pixel_distance


def interactive_points(image, purpose):
    fig, ax = plt.subplots(figsize=(9, 9))
    ax.imshow(image, cmap='gray', origin='upper', interpolation='nearest')
    ax.set_title(f'Click first and second point for {purpose}, then close window')
    pts = plt.ginput(2, timeout=0)
    plt.close(fig)
    if len(pts) != 2:
        raise ValueError('Need exactly two mouse clicks to complete selection.')
    return pts


def write_csv(rows, out_path):
    cols = ['roi_id','x','y','width','height','d_fft_px','d_acf_px',
            'd_fft_nm','d_acf_nm','normal_angle_deg','peak_to_median',
            'relative_difference','status']
    with out_path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            rr = row.copy()
            x,y,w,h = rr.pop('roi')
            rr.update(x=x,y=y,width=w,height=h)
            writer.writerow({key:rr.get(key) for key in cols})


def annotate(image, rows, out_file, nm_per_pixel):
    h, w = image.shape
    fig, ax = plt.subplots(figsize=(12, 12), dpi=170)
    low, high = np.percentile(image, [0.2, 99.8])
    ax.imshow(image, cmap='gray', vmin=low, vmax=high, interpolation='nearest')
    cols = ['#00ffc8', '#ffcc33']
    for row in rows:
        if 'd_fft_px' not in row:
            continue
        x,y,rw,rh = row['roi']; cx, cy = x+rw/2, y+rh/2
        theta = np.deg2rad(row['normal_angle_deg'])
        nx,ny = np.cos(theta), np.sin(theta)
        half = min(rw, rh)*0.19
        col = cols[0] if row['status'] == 'OK' else cols[1]
        ax.add_patch(Rectangle((x+3,y+3),rw-6,rh-6,fill=False,edgecolor=col,linewidth=0.85,alpha=.6))
        ax.annotate('', xy=(cx+half*nx,cy+half*ny),
                    xytext=(cx-half*nx,cy-half*ny),
                    arrowprops={'arrowstyle':'<->','color':col,'lw':2.1})
        d = row['d_fft_px']
        label = f'd = {d*nm_per_pixel:.4f} nm' if nm_per_pixel else f'd = {d:.2f} px'
        ax.text(cx, cy-min(rw,rh)*0.29, label, ha='center', va='center',
                color='white', fontsize=9.5, weight='bold',
                bbox=dict(facecolor='black', edgecolor=col, alpha=.83, pad=3))
    ax.set_xlim(0,w); ax.set_ylim(h,0)
    ax.set_xlabel('x (pixels)'); ax.set_ylabel('y (pixels)')
    ax.set_title('HRTEM dominant lattice-fringe periodicity | '+
                 ('calibrated in nm' if nm_per_pixel else 'NOT CALIBRATED: pixel period only')+\
                 '\nArrows show the direction normal to fringes; yellow means cross-check needed', fontsize=12)
    fig.tight_layout()
    fig.savefig(out_file, dpi=170, facecolor='white')
    plt.close(fig)


def plot_diagnostics(diagnostic, roi, nm_per_pixel, out_path):
    tile = diagnostic['tile']
    ff = diagnostic['fft']
    logmag = ff['logmag']
    h,w=tile.shape
    fig, axes = plt.subplots(2,2,figsize=(11,9),dpi=135)
    lo,hi=np.percentile(tile,[0.2,99.8])
    axes[0,0].imshow(tile, cmap='gray',vmin=lo,vmax=hi)
    theta=np.deg2rad(ff['angle_normal_deg'])
    cx,cy=w/2,h/2
    dx,dy= min(h,w)*0.20*np.cos(theta),min(h,w)*0.20*np.sin(theta)
    axes[0,0].plot([cx-dx,cx+dx],[cy-dy,cy+dy],color='#ff9f00',lw=2)
    label = f"{ff['d_fft_px']*nm_per_pixel:.4f} nm" if nm_per_pixel else f"{ff['d_fft_px']:.2f} px"
    axes[0,0].set_title('ROI: HRTEM fringe image; d = '+label)
    axes[0,1].imshow(logmag,cmap='magma',vmin=np.percentile(logmag,5),vmax=np.percentile(logmag,99.9))
    xpk,ypk=ff['peak_x'],ff['peak_y']
    axes[0,1].scatter([xpk,w-1-xpk],[ypk,h-1-ypk],s=120,
                      facecolors='none',edgecolors='cyan',linewidths=2)
    axes[0,1].set_xlim(w/2-w*0.27,w/2+w*0.27)
    axes[0,1].set_ylim(h/2+h*0.27,h/2-h*0.27)
    axes[0,1].set_title('Local FFT (log-amplitude), conjugate spots')
    profile=diagnostic['profile']
    if profile is not None:
        axes[1,0].plot(np.arange(len(profile)),profile,lw=.9)
        axes[1,0].set_title('Independent 1-D intensity profile (detrended)')
        axes[1,0].set_xlabel('Distance along fringe normal (pixels)')
        axes[1,0].set_ylabel('Relative intensity')
    else:
        axes[1,0].text(.5,.5,'No valid central line profile',transform=axes[1,0].transAxes,ha='center')
    acf = diagnostic['acf']
    if acf is not None:
        limit = min(len(acf),int(4.5*ff['d_fft_px'])+1)
        axes[1,1].plot(np.arange(limit),acf[:limit],marker='.',markersize=3)
        axes[1,1].axvline(ff['d_fft_px'],color='orange',ls='--',label='FFT period')
        axes[1,1].set_title('1-D autocorrelation; first-period consistency')
        axes[1,1].set_xlabel('Lag (pixels)');axes[1,1].set_ylabel('Normalized ACF')
        axes[1,1].legend()
    else:
        axes[1,1].text(.5,.5,'ACF cross-check unavailable',transform=axes[1,1].transAxes,ha='center')
    for ax in axes[0]:
        ax.set_xlabel('x (pixels)');ax.set_ylabel('y (pixels)')
    fig.suptitle(f'ROI {roi}; FFT peak / background = {ff["peak_to_median"]:.1f}',fontsize=12)
    fig.tight_layout()
    fig.savefig(out_path,facecolor='white')
    plt.close(fig)


def run_selftest():
    rng=np.random.default_rng(12)
    true_period=7.25
    n=512
    y,x=np.mgrid[:n,:n]
    theta=np.deg2rad(64.)
    z=120+48*np.cos(2*np.pi*(x*np.cos(theta)+y*np.sin(theta))/true_period)
    z=z+10*rng.normal(size=z.shape)+3*np.sin(x/37.)
    v=peak_from_fft(z,4,15)
    if v is None:
        raise AssertionError('Test sinusoid not detected.')
    rel=abs(v['d_fft_px']-true_period)/true_period
    print(f'Self-test: true={true_period:.4f} px, measured={v["d_fft_px"]:.4f} px, rel_err={rel:.2%}')
    if rel >= .02:
        raise AssertionError('FFT measurement relative error exceeded 2%.')
    print('SELF-TEST PASSED')


def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('-i','--input',type=Path,help='Source HRTEM image (TIF/TIFF/PNG)')
    p.add_argument('-o','--output',type=Path,default=Path('hrtem_output'))
    p.add_argument('--grid',default='4x4',help='Number of equal regions, COLSxROWS (default: 4x4)')
    p.add_argument('--roi',action='append',help='Rectangular ROI x,y,width,height (can repeat). Overrides --grid')
    p.add_argument('--pick-roi',action='store_true',help='Interactively click opposite ROI corners (desktop UI)')
    p.add_argument('--min-spacing',type=float,default=4,help='Minimum searched lattice period in pixels')
    p.add_argument('--min-peak-ratio',type=float,default=6.0,help='Minimum peak-to-FFT-band median amplitude for automatic acceptance (default: 6)')
    p.add_argument('--max-spacing',type=float,default=15,help='Maximum searched lattice period in pixels')
    p.add_argument('--nm-per-pixel',type=float,help='Physical calibration; never auto-guessed from display DPI')
    p.add_argument('--scale-points',type=str,help='Two endpoints x1,y1,x2,y2 of known distance')
    p.add_argument('--pick-scale',action='store_true',help='Interactively click known-distance endpoints')
    p.add_argument('--known-distance-nm',type=float,help='Physical distance between calibration endpoints in nm')
    p.add_argument('--self-test',action='store_true',help='Test FFT period detection on synthetic image')
    p.add_argument('--export-settings',type=Path,help='Validated export settings JSON')
    p.add_argument('--re-export-session',type=Path,help='Re-draw saved measurements without running FFT or ACF')
    args=p.parse_args()
    if args.self_test:
        run_selftest(); return
    from hrtem_export import export_all, load_settings, load_settings_file, reexport, save_session
    if args.export_settings:
        settings=load_settings_file(args.export_settings)
    else:
        settings,warning=load_settings(Path(__file__).resolve().parent)
        if warning: print('EXPORT SETTINGS WARNING:',warning)
    if args.re_export_session:
        from datetime import datetime
        target=(args.re_export_session/'reexports'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                if args.output==Path('hrtem_output') else args.output)
        files=reexport(args.re_export_session,target,settings)
        print('Re-exported from saved measurements, without FFT/ACF:',target)
        print('Files:',', '.join(files))
        return
    if args.input is None:
        p.error('--input is required unless --self-test is specified')
    if args.nm_per_pixel is not None and args.nm_per_pixel <= 0:
        p.error('--nm-per-pixel must be positive')
    if (args.scale_points is not None or args.pick_scale) and args.known_distance_nm is None:
        p.error('--known-distance-nm is required when calibrating from a known-distance line')
    if args.nm_per_pixel is not None and (args.scale_points or args.pick_scale):
        p.error('Supply either --nm-per-pixel or a scale-point calibration, not both')

    image=load_grayscale(args.input)
    nm_per_pixel=args.nm_per_pixel
    if args.pick_scale:
        pts=interactive_points(image,'known scale length')
        p1,p2=pts
        nm_per_pixel=args.known_distance_nm/math.dist(p1,p2)
    elif args.scale_points:
        nm_per_pixel=points_to_px(args.scale_points,args.known_distance_nm)
    if args.pick_roi:
        (x1,y1),(x2,y2)=interactive_points(image,'ROI opposite corners')
        x,y=int(min(x1,x2)),int(min(y1,y2));w,h=int(abs(x2-x1)),int(abs(y2-y1))
        args.roi=[f'{x},{y},{w},{h}']

    rois=choose_rois(image.shape,args.grid,args.roi)
    args.output.mkdir(parents=True,exist_ok=True)
    rows=[]
    best_diag=None; best_roi=None
    for i,roi in enumerate(rois,1):
        row,diag=analyze_roi(image,roi,args.min_spacing,args.max_spacing,args.min_peak_ratio)
        row['roi_id']=i
        if 'd_fft_px' in row:
            row['d_fft_nm']=row['d_fft_px']*nm_per_pixel if nm_per_pixel else None
            row['d_acf_nm']=row['d_acf_px']*nm_per_pixel if nm_per_pixel and row['d_acf_px'] else None
        rows.append(row)
        if diag is not None and row['status']=='OK' and (best_diag is None or row['peak_to_median'] > best_diag['fft']['peak_to_median']):
            best_diag,best_roi=diag,roi
        sp=f'{row["d_fft_px"]:.3f}px' if 'd_fft_px' in row else 'unavailable'
        print(f'ROI {i:02d} ({roi}): {sp}; status={row["status"]}')
    write_csv(rows,args.output/'lattice_spacing_results.csv')
    exported=export_all(image,rows,nm_per_pixel,best_diag,best_roi,args.output,settings)
    print('Exported figures:',', '.join(exported))
    valid=[r['d_fft_px'] for r in rows if r.get('status')=='OK']
    summary={'input':str(args.input),'shape':list(image.shape),
             'nm_per_pixel':nm_per_pixel,'calibration_valid':nm_per_pixel is not None,
             'warning':'Missing reliable nm/pixel calibration: reported spacing is in pixels, NOT nanometers.' if nm_per_pixel is None else
                       'Physical calibration supplied by the user; verify that the image has not been rescaled.',
             'count_rois':len(rois),'count_ok':len(valid),
             'roi_fft_acf_relative_difference_max': max([r['relative_difference'] for r in rows if r.get('status')=='OK'], default=None),
             'median_spacing_px_valid_rois':float(np.median(valid)) if valid else None,
             'min_spacing_px_valid_rois':float(np.min(valid)) if valid else None,
             'max_spacing_px_valid_rois':float(np.max(valid)) if valid else None,
             'median_spacing_nm_valid_rois':float(np.median(valid)*nm_per_pixel) if valid and nm_per_pixel else None,
             'note':'The strongest local FFT peak measures image-fringe periodicity. It does NOT independently identify crystal plane (hkl), prove true physical lattice spacing or quantify dislocation strain.'}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    save_session(args.output,args.input,rows,nm_per_pixel,best_diag,best_roi)
    print('\nSUMMARY:',json.dumps(summary,ensure_ascii=False,indent=2))
    print('Output:',args.output.resolve())

if __name__=='__main__':
    main()
