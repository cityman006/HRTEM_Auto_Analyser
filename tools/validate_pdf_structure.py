"""Structural PDF/SVG checks using the bundled validation runtime."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from pypdf import PdfReader

suite=Path(__file__).resolve().parents[1]/'outputs'/'font_vector_validation_20261019_174736_885154'
records=[]
for folder,expected in [('Times_New_Roman','TimesNewRoman'),('Arial','Arial')]:
    root=suite/folder
    def pdf(name):
        reader=PdfReader(root/name)
        assert len(reader.pages)==1
        page=reader.pages[0]
        resources=page['/Resources']
        fonts=[]
        for ref in resources.get('/Font',{}).values():
            font=ref.get_object()
            fonts.append(str(font.get('/BaseFont','')))
            if font.get('/Subtype')=='/Type0':
                descendant=font['/DescendantFonts'][0].get_object()
                descriptor=descendant['/FontDescriptor'].get_object()
                assert descriptor.get('/FontFile2') or descriptor.get('/FontFile3'), (name,descriptor)
        images=[]
        for ref in resources.get('/XObject',{}).values():
            obj=ref.get_object()
            if obj.get('/Subtype')=='/Image': images.append((obj['/Width'],obj['/Height']))
        operations=page.get_contents().get_data()
        assert b' m\n' in operations or b' m ' in operations
        return dict(fonts=fonts,images=images,content_bytes=len(operations))
    mixed=pdf('lattice_spacing_annotated.pdf')
    diagnostic=pdf('fft_acf_diagnostics.pdf')
    pure=pdf('curves_only.pdf')
    assert any(expected in font for font in mixed['fonts']),mixed
    assert any(w>=2048 and h>=2048 for w,h in mixed['images']),mixed
    assert len(diagnostic['images'])>=2,diagnostic
    assert pure['images']==[],pure
    def label_positions(name):
        tree=ET.parse(root/name)
        return tree.getroot().get('viewBox'), sorted((elem.text,elem.get('x'),elem.get('y'))
            for elem in tree.iter() if elem.tag.endswith('text') and elem.text and elem.text.startswith('d = '))
    full_box,full_labels=label_positions('lattice_spacing_annotated.svg')
    overlay_box,overlay_labels=label_positions('annotations_only.svg')
    assert full_box==overlay_box
    if folder=='Times_New_Roman':
        assert overlay_labels==full_labels and len(full_labels)==16,(len(full_labels),len(overlay_labels))
    records.append(dict(font=folder,mixed_pdf=mixed,diagnostic_pdf=diagnostic,curves_pdf=pure,
                        same_svg_canvas=full_box==overlay_box,label_count=len(full_labels)))
(suite/'pdf_svg_structure.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
print(json.dumps(records,indent=2))
