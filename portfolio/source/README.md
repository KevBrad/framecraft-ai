# Editable document source

The PowerPoint file contains editable native text, shapes, tables and images. The pitch PDF is a visual edition generated from the same reviewed slide model at 2560 x 1440 pixels per slide. It preserves the layout without depending on an installed presentation font; its pages are image-based. The editable PowerPoint retains searchable native text and source citations in speaker notes.

The Python source here regenerates the six-page case study and one-page overview using the approved repository assets. Optional document-generation tools are separate from the application dependencies:

```powershell
python -m pip install reportlab pypdf pillow
python portfolio/source/build_documents.py --repo . --output portfolio/generated
```

Public product media and the documented manual editor refinements are described in assets/product/PROVENANCE.md. No private workspace or test recordings are required for these document sources.
