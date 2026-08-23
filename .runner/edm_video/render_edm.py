import math, os, subprocess, json, random
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W,H,FPS = 720,1280,30
DURATION = 60.0
TOTAL = int(DURATION*FPS)
OUT = Path('edm-video-output')
OUT.mkdir(exist_ok=True)

songs = [
    (10,'TSUNAMI','DVBBS & Borgeous'),
    (9,'HEROES','Alesso ft. Tove Lo'),
    (8,'THE NIGHTS','Avicii'),
    (7,'SUMMER','Calvin Harris'),
    (6,'TITANIUM','David Guetta ft. Sia'),
    (5,'ANIMALS','Martin Garrix'),
    (4,"DON’T YOU WORRY CHILD",'Swedish House Mafia'),
    (3,'CLARITY','Zedd ft. Foxes'),
    (2,'WAKE ME UP','Avicii'),
    (1,'LEVELS','Avicii'),
]

palettes = [
    ((9,15,28),(36,203,255),(118,255,209)),
    ((18,10,34),(190,85,255),(255,100,190)),
    ((10,22,30),(0,224,180),(72,126,255)),
    ((28,14,8),(255,151,45),(255,77,90)),
    ((14,15,24),(146,113,255),(78,210,255)),
    ((7,18,19),(0,255,188),(255,220,77)),
    ((24,12,18),(255,87,128),(255,179,71)),
    ((10,17,35),(72,142,255),(170,101,255)),
    ((24,17,7),(255,195,78),(255,97,72)),
    ((8,20,24),(46,255,190),(88,173,255)),
]

font_paths = [
 '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
 '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'
]
regular_paths = [
 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
 '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'
]
font_bold = next(p for p in font_paths if os.path.exists(p))
font_reg = next(p for p in regular_paths if os.path.exists(p))

def F(size,bold=True):
    return ImageFont.truetype(font_bold if bold else font_reg,size=size)

fonts = {
 'kicker':F(17), 'title':F(43), 'subtitle':F(16,False),
 'rankBig':F(126), 'hero':F(46), 'artist':F(18,False),
 'rowRank':F(18), 'rowTitle':F(18), 'rowArtist':F(12,False),
 'outro':F(38), 'small':F(13,False), 'introBig':F(68)
}

# deterministic particle field
rng = random.Random(2010)
particles=[(rng.random(),rng.random(),rng.uniform(.5,1.6),rng.uniform(0,math.tau)) for _ in range(78)]

def hexcol(c,a=255): return (*c,a)

def fit_font(text,max_width,start,min_size=10,bold=True):
    s=start
    while s>min_size:
        f=F(s,bold)
        if f.getlength(text)<=max_width:return f
        s-=1
    return F(min_size,bold)

def draw_glow_line(layer, pts, color, width=3, glow=11, alpha=180):
    g=Image.new('RGBA',(W,H),(0,0,0,0)); d=ImageDraw.Draw(g)
    d.line(pts,fill=(*color,max(20,alpha//3)),width=glow,joint='curve')
    g=g.filter(ImageFilter.GaussianBlur(glow//2))
    layer.alpha_composite(g)
    ImageDraw.Draw(layer).line(pts,fill=(*color,alpha),width=width,joint='curve')

def draw_center_visual(base,t,idx,segp):
    bg,accent,accent2=palettes[idx]
    d=ImageDraw.Draw(base,'RGBA')
    # central stage area
    x0,y0,x1,y1=38,196,W-38,810
    d.rounded_rectangle((x0,y0,x1,y1),radius=32,fill=(5,8,15,235),outline=(*accent,45),width=1)
    # perspective grid / horizon
    horizon=590
    for k in range(8):
        y=int(horizon + (k/8)**1.8*(y1-horizon))
        d.line((x0+18,y,x1-18,y),fill=(*accent,18),width=1)
    for k in range(-7,8):
        xx=W//2+k*42
        d.line((W//2,horizon,xx,y1-12),fill=(*accent2,14),width=1)
    # moving light beams
    for j in range(9):
        ang=-1.05 + j*(2.1/8) + math.sin(t*.8+j)*.035
        length=330
        cx=W//2; cy=620
        ex=int(cx+math.sin(ang)*length); ey=int(cy-math.cos(ang)*length)
        d.polygon([(cx-3,cy),(cx+3,cy),(ex+18,ey),(ex-18,ey)],fill=(*accent,(8+j%3*3)))
    # particles
    for px,py,sp,phase in particles:
        yy=(py + t*0.02*sp)%1
        xx=px + math.sin(t*.55+phase)*.015
        x=int(x0+18+xx*(x1-x0-36)); y=int(y0+18+yy*(y1-y0-36))
        r=1 if sp<1 else 2
        d.ellipse((x-r,y-r,x+r,y+r),fill=(*accent2,70))
    # waveform
    pts=[]
    for i in range(110):
        x=x0+26+i*(x1-x0-52)/109
        envelope=.25+.75*math.sin(math.pi*i/109)**1.3
        y=704 + math.sin(i*.62+t*7.0+idx*.9)*28*envelope + math.sin(i*.15+t*2.2)*9
        pts.append((x,y))
    draw_glow_line(base,pts,accent2,2,12,170)
    d=ImageDraw.Draw(base,'RGBA')
    # orb/rings
    cx,cy=W//2,430
    pulse=1+.035*math.sin(t*3.4)
    for r,aa,wid in [(158,25,2),(118,45,2),(78,80,3)]:
        rr=int(r*pulse)
        bbox=(cx-rr,cy-rr,cx+rr,cy+rr)
        start=(t*36 + idx*21 + r)%360
        d.arc(bbox,start=start,end=start+230,fill=(*accent,aa),width=wid)
        d.arc(bbox,start=start+250,end=start+330,fill=(*accent2,aa+25),width=wid)
    # rotating ticks
    for j in range(22):
        a=t*.55 + j*math.tau/22
        r1=172; r2=180+(j%4)*4
        p1=(cx+math.cos(a)*r1,cy+math.sin(a)*r1)
        p2=(cx+math.cos(a)*r2,cy+math.sin(a)*r2)
        d.line((*p1,*p2),fill=(*accent2,40),width=1)
    # rank + hero label
    rank,title,artist=songs[idx]
    num=str(rank).zfill(2)
    fnum=fonts['rankBig']
    numw=fnum.getlength(num)
    d.text((cx-numw/2,326),num,font=fnum,fill=(245,248,255,238),stroke_width=1,stroke_fill=(*accent,100))
    fh=fit_font(title,540,46,24,True)
    tw=fh.getlength(title)
    d.text((cx-tw/2,548),title,font=fh,fill=(255,255,255,245))
    fa=fonts['artist']; aw=fa.getlength(artist)
    d.text((cx-aw/2,600),artist,font=fa,fill=(*accent2,210))
    # segment progress
    d.rounded_rectangle((x0+22,772,x1-22,778),radius=3,fill=(255,255,255,18))
    d.rounded_rectangle((x0+22,772,x0+22+(x1-x0-44)*max(0,min(1,segp)),778),radius=3,fill=(*accent2,170))


def make_ranking_panel(active_idx):
    # active_idx -1 for intro, 0..9 otherwise
    im=Image.new('RGBA',(W,H),(0,0,0,0)); d=ImageDraw.Draw(im,'RGBA')
    panel=(38,835,W-38,1225)
    d.rounded_rectangle(panel,radius=30,fill=(4,7,13,235),outline=(255,255,255,16),width=1)
    y=858
    row_h=34
    for i,(rank,title,artist) in enumerate(songs):
        revealed = active_idx>=i
        current = active_idx==i
        if current:
            accent=palettes[i][1]
            d.rounded_rectangle((54,y-3,W-54,y+29),radius=10,fill=(*accent,32),outline=(*accent,110),width=1)
            d.rounded_rectangle((54,y-3,58,y+29),radius=2,fill=(*accent,210))
        alpha=245 if revealed else 55
        rank_text=str(rank)
        d.text((70,y),rank_text,font=fonts['rowRank'],fill=(255,255,255,alpha))
        if revealed:
            maxw=430
            ft=fit_font(title,maxw,18,12,True)
            d.text((112,y),title,font=ft,fill=(255,255,255,245 if current else 205))
            fa=fonts['rowArtist']
            at=' • '+artist
            rem=maxw-ft.getlength(title)-6
            if fa.getlength(at)<=max(0,rem):
                d.text((112+ft.getlength(title)+6,y+4),at,font=fa,fill=(255,255,255,110))
        else:
            d.text((112,y+3),'—',font=fonts['rowTitle'],fill=(255,255,255,45))
        y += row_h
    return im

panels=[make_ranking_panel(i) for i in range(-1,10)]

def base_frame(idx,t):
    # dark gradient keyed to current segment palette
    if idx<0: bg=(8,12,20); accent=(73,225,255); accent2=(167,104,255)
    else: bg,accent,accent2=palettes[idx]
    yy=np.linspace(0,1,H,dtype=np.float32)[:,None]
    top=np.array(bg,dtype=np.float32)
    bot=np.array((2,4,9),dtype=np.float32)
    arr=(top[None,None,:]*(1-yy[:,:,None]) + bot[None,None,:]*yy[:,:,None])
    arr=np.repeat(arr,W,axis=1).astype(np.uint8)
    im=Image.fromarray(arr,'RGB').convert('RGBA')
    d=ImageDraw.Draw(im,'RGBA')
    # ambient blobs
    x1=90+45*math.sin(t*.37); y1=120+25*math.sin(t*.22)
    x2=W-70+55*math.sin(t*.31+1.7); y2=500+40*math.sin(t*.27+2.4)
    glow=Image.new('RGBA',(W,H),(0,0,0,0)); gd=ImageDraw.Draw(glow,'RGBA')
    gd.ellipse((x1-160,y1-160,x1+160,y1+160),fill=(*accent,35))
    gd.ellipse((x2-180,y2-180,x2+180,y2+180),fill=(*accent2,25))
    glow=glow.filter(ImageFilter.GaussianBlur(90))
    im.alpha_composite(glow)
    return im

intro_end=2.4
seg_len=5.35
outro_start=intro_end+10*seg_len

def render_frame(n):
    t=n/FPS
    if t<intro_end:
        idx=-1; segp=t/intro_end
    elif t<outro_start:
        idx=min(9,int((t-intro_end)//seg_len)); segp=((t-intro_end)-idx*seg_len)/seg_len
    else:
        idx=9; segp=1
    im=base_frame(idx,t)
    d=ImageDraw.Draw(im,'RGBA')
    # global header
    d.text((40,42),'NOSTALGIA INDEX  /  2010—2019',font=fonts['kicker'],fill=(255,255,255,150))
    d.text((40,74),'TOP 10 EDM ANTHEMS',font=fonts['title'],fill=(255,255,255,248))
    d.text((42,130),'THE SONGS THAT MADE THE DECADE FEEL ELECTRIC',font=fonts['subtitle'],fill=(255,255,255,110))
    d.line((40,166,W-40,166),fill=(255,255,255,18),width=1)
    if t<intro_end:
        # intro hero
        cx,cy=W//2,470
        for j in range(5):
            r=105+j*28+10*math.sin(t*3+j)
            col=(72,218,255) if j%2==0 else (170,95,255)
            d.arc((cx-r,cy-r,cx+r,cy+r),start=t*50+j*31,end=t*50+j*31+215,fill=(*col,110-j*13),width=3)
        s='2010s'
        fs=fonts['introBig']; sw=fs.getlength(s)
        d.text((cx-sw/2,430),s,font=fs,fill=(255,255,255,245))
        tag='EDM WAS DIFFERENT.'
        ft=F(22); tw=ft.getlength(tag)
        d.text((cx-tw/2,530),tag,font=ft,fill=(255,255,255,185))
        d.text((cx-134,612),'COUNTING DOWN IN 60 SECONDS',font=fonts['small'],fill=(255,255,255,100))
        im.alpha_composite(panels[0])
    elif t<outro_start:
        draw_center_visual(im,t,idx,segp)
        im.alpha_composite(panels[idx+1])
    else:
        # outro / final reveal
        draw_center_visual(im,t,9,1)
        im.alpha_composite(panels[10])
        fade=min(1,(t-outro_start)/.45)
        overlay=Image.new('RGBA',(W,H),(0,0,0,int(125*fade)))
        im.alpha_composite(overlay)
        od=ImageDraw.Draw(im,'RGBA')
        q='WHAT’S YOUR #1?'
        fq=fonts['outro']; qw=fq.getlength(q)
        od.rounded_rectangle((78,488,W-78,646),radius=28,fill=(4,7,13,220),outline=(255,255,255,28),width=1)
        od.text(((W-qw)/2,520),q,font=fq,fill=(255,255,255,245))
        sub='2010s EDM WAS DIFFERENT.'
        fs=fonts['subtitle']; sw=fs.getlength(sub)
        od.text(((W-sw)/2,590),sub,font=fs,fill=(255,255,255,145))
    # grain / vignette
    d=ImageDraw.Draw(im,'RGBA')
    d.rectangle((0,0,W,H),outline=(0,0,0,80),width=18)
    return im.convert('RGB')

# render rawvideo into ffmpeg, upscale to 1080x1920
mp4=OUT/'edm_nostalgia_top10_silent.mp4'
cmd=['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-',
     '-vf','scale=1080:1920:flags=lanczos,format=yuv420p','-c:v','libx264','-preset','medium','-crf','18','-movflags','+faststart','-t',str(DURATION),str(mp4)]
proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
for n in range(TOTAL):
    frame=render_frame(n)
    proc.stdin.write(np.asarray(frame,dtype=np.uint8).tobytes())
proc.stdin.close(); code=proc.wait()
if code!=0: raise SystemExit(code)

# ffprobe
probe=json.loads(subprocess.check_output(['ffprobe','-v','quiet','-print_format','json','-show_format','-show_streams',str(mp4)]))
(OUT/'ffprobe.json').write_text(json.dumps(probe,indent=2))

# cue sheet for DHS
cues={'fps':FPS,'duration':DURATION,'intro_end':intro_end,'outro_start':outro_start,'songs':[]}
for i,(rank,title,artist) in enumerate(songs):
    start=round(intro_end+i*seg_len,3); end=round(start+seg_len,3)
    cues['songs'].append({'rank':rank,'title':title,'artist':artist,'start':start,'end':end})
(OUT/'music-cues.json').write_text(json.dumps(cues,indent=2))

# contact sheet frames
shot_times=[0.8,3.0,13.5,24.2,35.0,46.0,55.2,58.5]
frames=[]
for st in shot_times:
    fr=render_frame(min(TOTAL-1,int(st*FPS))).resize((270,480),Image.Resampling.LANCZOS)
    frames.append(fr)
sheet=Image.new('RGB',(1080,960),(8,10,16))
for i,fr in enumerate(frames): sheet.paste(fr,((i%4)*270,(i//4)*480))
sheet.save(OUT/'contact-sheet.jpg',quality=92)
print(mp4)
