"""Editable ReportLab source for the public Framecraft case study and brief.
Run: python build_documents.py --repo PATH --output PATH
Only approved product captures and public claims are used.
"""
import argparse,json
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor,Color
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from PIL import Image
from pypdf import PdfReader

ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args();ROOT=args.repo.resolve();OUT=args.output.resolve();OUT.mkdir(parents=True,exist_ok=True)
for name,file in [('Body','DejaVuSans.ttf'),('Bold','DejaVuSans-Bold.ttf')]:pdfmetrics.registerFont(TTFont(name,str(ROOT/'assets/fonts'/file)))
N='#111A25';INK='#182231';PAPER='#F6F3EB';TEAL='#238B79';MINT='#62D7BE';MUTED='#526372';LINE='#D9E1DF';BLUE='#316BB9'
W,H=595.276,841.89;M=36;CW=W-2*M
LAYOUT=[]
def rect(c,x,top,w,h,fill,stroke=None):
    c.setFillColor(HexColor(fill));c.setStrokeColor(HexColor(stroke or fill));c.rect(x,H-top-h,w,h,fill=1,stroke=bool(stroke))
def line(c,x,top,w,col=LINE):c.setStrokeColor(HexColor(col));c.setLineWidth(.7);c.line(x,H-top,x+w,H-top)
def txt(c,t,x,top,size=11,color=INK,bold=False):
    c.setFillColor(HexColor(color));c.setFont('Bold' if bold else 'Body',size);c.drawString(x,H-top-size*.82,t)
def para(c,t,x,top,w,size=11,color=MUTED,bold=False,leading=None):
    st=ParagraphStyle('p',fontName='Bold' if bold else 'Body',fontSize=size,leading=leading or size*1.46,textColor=HexColor(color),spaceAfter=0)
    p=Paragraph(t,st);pw,ph=p.wrap(w,1000)
    assert top+ph<H-39,(t[:80],top,ph)
    p.drawOn(c,x,H-top-ph);LAYOUT.append({'page':c.getPageNumber(),'text':t,'x':x,'top':top,'width':w,'height':ph});return ph
def image(c,name,x,top,w,h):
    path=ROOT/'assets/product'/name
    im=Image.open(path);iw,ih=im.size;scale=min(w/iw,h/ih);dw,dh=iw*scale,ih*scale
    c.drawImage(str(path),x+(w-dw)/2,H-top-h+(h-dh)/2,dw,dh,mask='auto')
def header(c,num,title,kicker='ENGINEERING CASE STUDY'):
    rect(c,0,0,W,H,PAPER);txt(c,'FRAMECRAFT',M,31,11,TEAL,True);txt(c,kicker,285,32,8,MUTED)
    para(c,title,M,83,CW,29,INK,True,34)
    line(c,M,789,CW);txt(c,'KevBrad / AI-assisted product development',M,806,7.8,MUTED)
    txt(c,f'{num:02d} / 06',514,806,8,MUTED)
def note(c,text,top=752):para(c,text,M,top,CW,8.1,MUTED,False,11.7)
def finish(c):c.showPage()
def tag(c,label,value,x,top,w):
    line(c,x,top,w);txt(c,value,x,top+16,31,BLUE,True);para(c,label,x,top+61,w,10,MUTED)

case=OUT/'Framecraft_Engineering_Case_Study_2026.pdf';c=canvas.Canvas(str(case),pagesize=(W,H),pageCompression=1)
c.setTitle('Framecraft - Engineering Case Study 2026');c.setAuthor('KevBrad');c.setSubject('Verified local video engineering and AI-assisted product development')
# 1. Overview
header(c,1,'Building an AI video\nclipping platform')
para(c,'A working local application for finding candidate moments, refining the edit and exporting real short-form video.',M,180,CW,13,INK)
image(c,'editor-viewport.png',M,252,CW,303)
txt(c,'Real application capture with isolated, approved demonstration content',M,565,8.2,MUTED)
tag(c,'Automated tests plus four subtests\nRerun 29 October 2026','290',M,613,155)
tag(c,'Continuous source processed\nRelease evidence, 23 October','60 min',220,613,155)
tag(c,'Real H.264/AAC MP4\nSource-quality limits disclosed','1080p',404,613,155)
note(c,'Stage: working local prototype. Python / FastAPI / React / TypeScript / SQLite / Whisper / OpenCV / FFmpeg. Project: github.com/KevBrad/framecraft-ai',739)
finish(c)
# 2. Product approach, not another investor slide.
header(c,2,'The product problem starts\nwith context')
para(c,'A sentence can sound interesting and still fail as a short clip when its question, explanation or payoff is missing. The product must also preserve the creator’s final edit.',M,181,CW,12,INK)
sections=[('Selection','Locate a real source interval, retain the surrounding answer and expose why the candidate ranks.'),('Presentation','Correct recognized text and timing, choose emphasis and framing, then inspect the encoded result.'),('Reliability','Handle long recordings, interruption and media errors without overwriting source files or losing edits.')]
for i,(head,body) in enumerate(sections):
    y=290+i*112;txt(c,f'0{i+1}',M,y,12,TEAL,True);txt(c,head,84,y-3,18,INK,True);para(c,body,84,y+31,468,11.5)
line(c,M,655,CW)
para(c,'A source-grounded story check',M,681,CW,16,INK,True)
para(c,'Question + initial answer + later contrast + payoff. A final development comparison retained the complete budget answer and the distinction between a video and a television production. This is a calibration example, not independent editorial-quality validation.',M,716,CW,10.3)
finish(c)
# 3. Actual architecture.
header(c,3,'One local app,\nexplicit processing boundaries')
nodes=[('Authorized intake','providers / import service / source library'),('Normalized, bounded audio','pipeline / 16 kHz PCM / saved cores'),('Timestamped speech','faster-whisper / model profiles'),('Evidence-based candidates','extractive topic map / editorial heuristics'),('Creator-owned edit','captions / director / persistent versions'),('Real rendered file','FFmpeg + libass / ffprobe / ZIP')]
for i,(head,detail) in enumerate(nodes):
    y=190+i*82;rect(c,M,y,CW,65,'#E6ECE8');txt(c,f'0{i+1}',M+15,y+16,11,TEAL,True);txt(c,head,M+55,y+13,15,INK,True);txt(c,detail,M+55,y+40,9.5,MUTED)
    if i<5:
        c.setStrokeColor(HexColor(TEAL));c.line(W/2,H-y-65,W/2,H-y-80)
para(c,'React/TypeScript and FastAPI form the control plane. Separate durable import and analysis consumers use SQLite WAL schema 2 and local files. No distributed cloud queue or hosted inference service is implied.',M,707,CW,10.5)
note(c,'Current module map: docs/ARCHITECTURE.md. Saved state covers projects, sources, imports, jobs, edits and versioned exports.',759)
finish(c)
# 4. Memory failure, root-cause caution and actual mechanism.
header(c,4,'A memory failure changed\nthe processing boundary')
txt(c,'243 MiB',M,184,36,BLUE,True);para(c,'One reported failed complex128 allocation',M,230,241,10.5)
txt(c,'30 seconds',314,184,36,TEAL,True);para(c,'Recognition core, plus two seconds of context per side',314,230,245,10.5)
para(c,'Saved release evidence records an array shaped (1, 79376, 201) and a previous whole-WAV path into speech recognition. Large time-frequency intermediates were consistent with duration-dependent pressure. The exact internal allocation path remains an inference; the old library stack was not re-instrumented for this publication.',M,287,CW,11)
txt(c,'Bound the input, then save each completed core',M,414,16,INK,True)
for i,(a,b,label) in enumerate([(0,32,'Core 0-30'),(28,62,'Core 30-60'),(58,92,'Core 60-90')]):
    y=459+i*55;rect(c,M+100,y,340,29,'#DCE5DF');rect(c,M+100+i*4,y,310,29,'#BDEBDD');txt(c,label,M+112+i*4,y+7,10,INK,True);txt(c,f'Input {a}-{b} s',M,y+8,9,MUTED)
para(c,'Word midpoint ownership avoids duplicates. Atomic checkpoints retain completed work. The gated Windows worker keeps its 2,048 MiB budget. In 23 October tests, 60 minutes of speech finished analysis in 311.62 seconds with 1,397.46 MiB peak sampled private memory.',M,648,CW,11)
note(c,'Current code and bounded-window tests verify the mechanism. Historical 60-minute processing completed within the retained budget. No upstream-library defect or universal memory bound is claimed.',746)
finish(c)
# 5. Quality and verification, with honest dates.
header(c,5,'Validation covers files,\nworkflows and saved state')
rows=[('Current local regression','290 tests + 4 subtests, no skips','29 Oct 2026'),('Current publication checks','177 lightweight tests + frontend build','29 Oct 2026'),('Historical browser workflows','9 new + 11 retained real checks','23 Oct 2026'),('Historical continuous sources','13 min 29 s / 15 min / 60 min','23 Oct 2026')]
for i,(name,value,date) in enumerate(rows):
    y=190+i*66;rect(c,M,y,CW,62,'#E7EDE8' if i%2==0 else PAPER);txt(c,name,M+10,y+11,11,INK,True);txt(c,value,M+10,y+34,10,MUTED);txt(c,date,452,y+14,8.5,MUTED)
image(c,'export.png',M,478,250,193);image(c,'ranking-breakdown.png',349,465,180,216)
txt(c,'Actual encoded export preview',M,685,8.5,MUTED);txt(c,'Actual ranking evidence',337,685,8.5,MUTED)
para(c,'The current audit preserved all database rows and retained original-file hashes. Two long-source exports re-probed as 1080 x 1920 H.264/AAC at 30 fps. No mock response substitutes for an encoded file.',M,709,CW,10.3)
note(c,'These are engineering results, not users, revenue or market traction. Full test scope, historical timing/memory conditions and limitations: docs/VERIFIED_RESULTS.md.',760)
finish(c)
# 6. Reflection, ownership and next steps.
header(c,6,'Product ownership and\nengineering tradeoffs')
para(c,'What the project demonstrates',M,180,CW,17,INK,True)
para(c,'Product direction translated into testable workflows. The implementation connects media intake, bounded AI work, persistent editing and verifiable files. Iteration used actual failure cases rather than a compilation result alone.',M,217,CW,11.5)
para(c,'How AI assistance contributed',M,316,CW,17,INK,True)
para(c,'KevBrad led requirements, research priorities and iterative acceptance testing. Codex materially assisted code creation, debugging and test authoring.',M,353,CW,11.5)
para(c,'Tradeoffs and next evidence gates',M,463,CW,17,INK,True)
para(c,'Local CPU processing and heuristics keep the application usable without paid inference, but recognition and semantic selection remain imperfect. Reliable speaker attribution and forced alignment are incomplete. The next steps are independently labelled highlight/timing data, creator workflow trials and deployment/security review before a hosted pilot.',M,500,CW,11.5)
rect(c,M,654,CW,92,N);txt(c,'Project and professional profile',M+18,672,12,MINT,True)
txt(c,'github.com/KevBrad/framecraft-ai',M+18,700,12,PAPER,True);c.linkURL('https://github.com/KevBrad/framecraft-ai',(M+18,H-724,M+400,H-696),relative=0)
txt(c,'github.com/KevBrad',M+18,727,9.5,'#A9BBC7');c.linkURL('https://github.com/KevBrad',(M+18,H-742,M+220,H-722),relative=0)
note(c,'Seeking product mentorship, creator introductions and independent technical review. Product validation and hosted operational readiness remain future work.',757)
finish(c);c.save()
# One-page product brief uses the same identity and approved real screenshot.
brief=OUT/'Framecraft_One_Page_Overview.pdf';c=canvas.Canvas(str(brief),pagesize=(W,H),pageCompression=1);c.setTitle('Framecraft - One Page Overview');c.setAuthor('KevBrad')
rect(c,0,0,W,H,PAPER);rect(c,0,0,W,211,N);txt(c,'FRAMECRAFT',M,38,33,PAPER,True)
para(c,'Editable stories from\nlong-form video',M,95,CW,25,PAPER,True,31)
para(c,'A working local AI-assisted video-clipping application',M,174,CW,11,'#AEC0CA')
image(c,'editor-viewport.png',M,232,CW,255)
txt(c,'Real application capture / original demo graphics / approved public speech source',M,493,7.8,MUTED)
para(c,'For independent podcasters, educators and creators',M,532,CW,16,INK,True)
para(c,'Import authorized recordings, inspect ranked source moments, correct captions and framing, then export a real MP4 or ZIP. Local Whisper recognition, resumable processing and persistent edits support the working workflow.',M,568,CW,10.7)
line(c,M,651,CW)
para(c,'Verified engineering',M,670,240,13,INK,True);para(c,'290 tests + 4 subtests rerun 29 Oct. Continuous 60-minute processing and 1080p export recorded in the 23 Oct release.',M,699,248,9.5)
para(c,'Next opportunity',316,670,242,13,INK,True);para(c,'Creator trials and product mentorship. Customer demand, pricing and commercial traction remain unvalidated.',316,699,243,9.5)
txt(c,'github.com/KevBrad/framecraft-ai',M,781,11,TEAL,True);c.linkURL('https://github.com/KevBrad/framecraft-ai',(M,H-797,M+440,H-778),relative=0)
txt(c,'Local prototype / AI-assisted implementation / no competitive-superiority claim',M,812,7.5,MUTED)
c.save()
assert len(PdfReader(case).pages)==6 and len(PdfReader(brief).pages)==1
(OUT/'document-layout-review.json').write_text(json.dumps(LAYOUT,indent=2),encoding='utf-8')
print(json.dumps({'case_pages':6,'brief_pages':1,'case_bytes':case.stat().st_size,'brief_bytes':brief.stat().st_size}))

