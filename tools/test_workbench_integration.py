"""Hidden Tk integration: real event handlers, measurements, project and vector export."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from image_workbench import ImageWorkbench
from workbench_model import WorkbenchModel
from workbench_storage import save_project,load_project
from history_browser import scan_history
from hrtem_export import DEFAULT


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.folder=Path(self.tmp.name)
        self.root=tk.Tk(); self.root.withdraw()
        self.settings={**DEFAULT,'formats':['png','svg','pdf','tiff'],'dpi':72,'width_mm':70}
        self.ui=ImageWorkbench(self.root,self.folder,settings=self.settings)
        self.ui.window.withdraw()
        yy,xx=np.indices((192,192))
        image=100+30*np.sin(2*np.pi*(xx*.9+yy*.435889894)/8)
        self.ui.replace_model(WorkbenchModel(image,{},settings=self.settings))
        self.root.update()

    def tearDown(self):
        self.ui.dispose(); self.root.destroy(); self.tmp.cleanup()

    def event(self,x,y):
        px,py=self.ui.ax.transData.transform((x,y))
        return SimpleNamespace(inaxes=self.ui.ax,xdata=x,ydata=y,x=px,y=py,button=1,key=None)

    def gesture(self,tool,a,b):
        self.ui.tool.set(tool); self.ui.press(self.event(*a)); self.ui.motion(self.event(*b)); self.ui.release(self.event(*b))

    def test_roi_drag_resize_fft_roundtrip_export(self):
        self.gesture('新增 ROI',(12,12),(140,140))
        roi=self.ui.model.state['objects'][0]; rid=roi['id']
        self.gesture('选择/拖动',(30,30),(40,35))
        roi=self.ui.model.state['objects'][0]
        self.assertEqual((roi['x'],roi['y']),(22,17))
        self.ui.undo(); self.assertEqual(self.ui.model.state['objects'][0]['x'],12)
        self.ui.redo(); self.assertEqual(self.ui.model.state['objects'][0]['x'],22)
        self.gesture('缩放 ROI',(100,100),(170,170))
        self.assertEqual(self.ui.model.state['objects'][0]['w'],148)
        self.ui.model.analyze_rois(); self.ui.refresh()
        measurement=json.dumps(self.ui.model.state['results'][rid]['measurement'],sort_keys=True)
        self.ui.view.set('fft:'+rid); self.ui.change_view()
        spots=self.ui.visible_objects(); self.assertGreater(len(spots),0)
        point=spots[0]; self.gesture('选择/拖动',(point['x'],point['y']),(point['x']+1,point['y']+1))
        self.ui.radius.set(4); self.ui.apply_radius()
        self.ui.model.update_ifft(rid)
        self.assertEqual(measurement,json.dumps(self.ui.model.state['results'][rid]['measurement'],sort_keys=True))
        self.gesture('框选',(0,0),(147,152)); self.assertEqual(len(self.ui.selected),len(self.ui.visible_objects()))
        self.ui.layer_vars['label'].set(False); self.ui.toggle_layer('label')
        before=self.ui.model.arrays[f'roi:{rid}:ifft'].copy()
        self.ui.brightness.set(.2); self.ui.display_change()
        self.assertTrue(np.array_equal(before,self.ui.model.arrays[f'roi:{rid}:ifft']))
        self.ui.layer_vars['label'].set(True); self.ui.toggle_layer('label')
        self.ui.view.set('image'); self.ui.change_view()
        self.ui.export()
        projects=list(self.folder.rglob('*.hrtemproj')); self.assertEqual(len(projects),1)
        state,arrays,warnings=load_project(projects[0]); self.assertFalse(warnings)
        self.assertTrue(np.array_equal(before,arrays[f'roi:{rid}:ifft']))
        self.assertEqual(state['brightness'],.2)
        svg=(projects[0].parent/'workspace_image.svg').read_text(encoding='utf-8')
        self.assertIn('<image',svg); self.assertIn('<path',svg)
        self.assertTrue(scan_history(self.folder))

    def test_saed_center_ring_spot_labels_layers_restore(self):
        yy,xx=np.indices((192,192)); r=np.hypot(xx-96,yy-96)
        image=100*np.exp(-(r/3)**2)+25*np.exp(-((r-50)/2)**2)
        model=WorkbenchModel(image,{},mode='saed',settings=self.settings)
        model.analyze_saed(); self.ui.replace_model(model)
        center=next(o for o in model.state['objects'] if o['kind']=='center')
        original=center['x']; self.gesture('选择/拖动',(center['x'],center['y']),(center['x']+3,center['y']+2))
        self.assertAlmostEqual(model.state['results']['saed']['center_px'][0],original+3)
        self.gesture('新增斑点',(135,130),(135,130))
        spot=model.state['objects'][-1]; self.ui.selected={spot['id']}
        with patch('image_workbench.simpledialog.askstring',return_value='Edited spot'),patch('image_workbench.colorchooser.askcolor',return_value=((255,0,0),'#ff0000')):
            self.ui.edit_annotation()
        self.assertEqual(spot['label'],'Edited spot'); self.assertEqual(spot['color'],'#ff0000')
        self.ui.arrange_labels(); self.assertIn('label_x',spot)
        self.ui.layer_vars['ring'].set(False); self.ui.toggle_layer('ring')
        self.ui.model.export_results(self.folder/'saed')
        self.assertTrue((self.folder/'saed'/'workbench_annotation.svg').exists())
        self.ui.restore()
        self.assertAlmostEqual(next(o for o in model.state['objects'] if o['kind']=='center')['x'],original)


if __name__=='__main__': unittest.main(verbosity=2)
