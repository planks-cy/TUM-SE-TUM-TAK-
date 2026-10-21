#!/usr/bin/env python3
"""Cinematic lyric motion-graphics renderer (deterministic: same timestamps -> same frames).
  python motion.py audio48.wav out/ out/cinematic.mp4 [--start S --end E]
Reads out/word-timestamps.json + out/phrases.json. Words/timings are never altered."""
import os, sys, json, math, subprocess, random, shutil
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
BLACK, CHAR, SILVER, IVORY, CRIMSON = (5,5,6), (21,21,25), (213,211,208), (244,239,231), (143,38,55)


def require_tool(name):
    if shutil.which(name) is None:
        raise SystemExit(f"Missing required tool: {name}. Install it before running motion.py.")


def default_font_path(name, fallback):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    if name.lower().endswith("serif"):
        for p in candidates[:2]:
            if os.path.exists(p):
                return p
    if name.lower().endswith("sans"):
        for p in candidates[2:]:
            if os.path.exists(p):
                return p
    if os.path.exists(fallback):
        return fallback
    return fallback

# ---------- easing ----------
def clamp(x): return max(0.0, min(1.0, x))
def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
def ease_io(x): x = clamp(x); return 3*x*x - 2*x**3
def ease_back(x): x = clamp(x); c = 1.4; return 1 + (c+1)*(x-1)**3 + c*(x-1)**2

# ---------- fonts ----------
def find_font(names, fallback):
    if shutil.which("fc-match"):
        for n in names:
            try:
                p = subprocess.check_output(["fc-match", "-f", "%{file}", n], stderr=subprocess.DEVNULL).decode()
                if n.split(":")[0].lower().split()[0] in p.lower(): return p
            except Exception:
                pass
        try:
            return subprocess.check_output(["fc-match", "-f", "%{file}", fallback], stderr=subprocess.DEVNULL).decode()
        except Exception:
            pass
    return default_font_path(fallback, fallback)

SERIF = find_font(["Cormorant Garamond:semibold", "Playfair Display"], "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
SANS = find_font(["Inter", "Manrope"], "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_fc = {}
def font(path, size):
    k = (path, size)
    if k not in _fc: _fc[k] = ImageFont.truetype(path, size)
    return _fc[k]

# ---------- word sprites (baseline-anchored RGBA) ----------
_sc = {}
def sprite(text, size, color, kind="serif", track=0.0):
    k = (text, size, color, kind, round(track, 1))
    if k in _sc: return _sc[k]
    f = font(SERIF if kind == "serif" else SANS, size); pad = size // 2
    adv = [f.getlength(c) + track for c in text]; w = int(sum(adv)) + 2*pad; asc = int(size*1.2)
    im = Image.new("RGBA", (max(w, 4), int(size*1.7)), color + (0,)); d = ImageDraw.Draw(im); x = pad
    for c, a in zip(text, adv): d.text((x, asc), c, font=f, fill=color + (255,), anchor="ls"); x += a
    _sc[k] = (im, pad, asc, w - 2*pad); return _sc[k]

def letter_sprites(text, size, color, kind):
    f = font(SERIF if kind == "serif" else SANS, size); out = []; x = 0
    for c in text:
        adv = f.getlength(c); im = Image.new("RGBA", (int(adv)+size, int(size*1.7)), color+(0,))
        ImageDraw.Draw(im).text((size//2, int(size*1.2)), c, font=f, fill=color+(255,), anchor="ls")
        out.append((im, x, adv)); x += adv
    return out, x

def with_alpha(im, op):
    if op >= 0.999: return im
    r, g, b, a = im.split(); return Image.merge("RGBA", (r, g, b, a.point(lambda v: int(v*op))))

# ---------- animation presets: (a=in progress 0..1, o=out progress 0..1, ctx) -> params ----------
def P(op=1, dx=0, dy=0, sc=1, blur=0, clip=1, track=None, frag=0, **k): return dict(op=op, dx=dx, dy=dy, sc=sc, blur=blur, clip=clip, track=track, frag=frag)
def fade_reveal(a, o, c):      return P(op=ease_out(a))
def tracking_expand(a, o, c):  return P(op=ease_out(a), track=(1-ease_out(a))*-0.14 + 0.03)
def vertical_drift(a, o, c):   return P(op=ease_out(a), dy=(1-ease_out(a))*70 - 50*ease_io(o))
def masked_reveal(a, o, c):    return P(op=min(1, a*3), clip=ease_io(a))
def letter_fragment(a, o, c):  return P(op=ease_out(a), frag=(1-ease_out(a)) + 1.6*ease_io(o))
def soft_blur_in(a, o, c):     return P(op=ease_out(a), blur=(1-ease_out(a))*14 + 10*ease_io(o))
def kinetic_cut(a, o, c):      return P(op=1 if a > 0 else 0, sc=1 + 0.18*(1-ease_back(min(1, a*4))))
def split_composition(a, o, c): s = -1 if c["i"] % 2 == 0 else 1; return P(op=ease_out(a), dx=s*(1-ease_out(a))*320)
def slow_scale(a, o, c):       return P(op=ease_out(a*0.6))
def fade_to_black(a, o, c):    return P(op=ease_out(a))
PRESETS = {f.__name__: f for f in (fade_reveal, tracking_expand, vertical_drift, masked_reveal, letter_fragment,
                                   soft_blur_in, kinetic_cut, split_composition, slow_scale, fade_to_black)}
IN_DUR = {"kinetic_cut": 0.12, "letter_fragment": 0.45, "masked_reveal": 0.4}

# ---------- screenplay: keyword -> (preset, motif, intensity) ----------
RULES = [("jogi", "vertical_drift", None, .1), ("pinjare", "masked_reveal", "cage", .1), ("dilon", "kinetic_cut", None, .15),
 ("jebo", "fade_reveal", None, .05), ("baant", "split_composition", "split", .1), ("tu jo mujhko", "split_composition", "split", .1),
 ("rab ka", "fade_reveal", None, .1), ("dil to tera", "vertical_drift", None, .1), ("bekaraari", "masked_reveal", None, .15),
 ("raazdaari", "masked_reveal", None, .15), ("door jo", "split_composition", "split", .1), ("paas kyun", "split_composition", "split", .1),
 ("mahfile", "soft_blur_in", "lights", .25), ("yaad hi", "soft_blur_in", None, .1), ("kab tum", "fade_reveal", None, .1),
 ("hai baat", "fade_reveal", None, .1), ("kaisa hu", "tracking_expand", "clock", .1), ("ab jo", "letter_fragment", "dust", .1),
 ("maatam", "kinetic_cut", "accent", .35), ("atakan", "kinetic_cut", "accent", .4), ("khud ko", "kinetic_cut", "accent", .5),
 ("tum se tum tak hum", "fade_to_black", "tumtak", .2), ("tum se tum tak", "tracking_expand", "tumtak", .25),
 ("tumse hume", "tracking_expand", "tumtak", .25), ("raaton", "soft_blur_in", None, .2), ("kara tune", "soft_blur_in", "accent", .3),
 ("pilu", "soft_blur_in", "accent", .3), ("tod do", "letter_fragment", "cage", .2), ("aadha", "split_composition", "split", .1),
 ("fanda", "slow_scale", None, 0.0), ("kursee", "slow_scale", None, 0.0), ("kaisi yeh", "slow_scale", None, 0.0), ("sir pe", "slow_scale", None, 0.0),
 ("kapde", "soft_blur_in", "dust", .1), ("khushboo", "soft_blur_in", "dust", .1)]
EMPH = {"musafir", "jogi", "pinjare", "azaad", "parinda", "ishq", "tum", "tak", "tumse", "khatam", "zeher", "jaadu", "jalan", "tera", "tumhaari", "khodu", "kabar", "rab", "dil"}
def style_for(text, idx, n, pi, total):
    t = text.lower()
    for key, pre, motif, inten in RULES:
        if t.startswith(key) or (key in t and len(key) > 6): return pre, motif, inten
    return "fade_reveal", None, 0.1

# ---------- background ----------
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
_r = np.sqrt(((xx-W/2)/(W*0.75))**2 + ((yy-H/2)/(H*0.7))**2)
VIG = np.clip(1 - _r, 0, 1)[..., None] ** 1.6
base = np.array(BLACK, np.float32) + (np.array(CHAR, np.float32) - np.array(BLACK, np.float32)) * VIG
_rng = np.random.default_rng(7)
HAZE = np.array(Image.fromarray((_rng.random((24, 14))*255).astype(np.uint8)).resize((270*2, 480*2), Image.BICUBIC), np.float32) / 255
PART = [(random.Random(i).random(), random.Random(i+99).random(), random.Random(i+7).uniform(.01, .05), random.Random(i+3).uniform(1, 3.2)) for i in range(70)]

def background(t, leak):
    hz = np.roll(HAZE, (int(t*6), int(t*3)), (0, 1))
    hz = np.array(Image.fromarray((hz*255).astype(np.uint8)).resize((W, H), Image.BILINEAR), np.float32)[..., None] / 255
    f = base + hz * VIG * np.array([22, 22, 24], np.float32)
    if leak > 0:  # restrained crimson light leak, slow breathing
        k = np.clip(1 - np.sqrt(((xx-W*1.05)/(W*0.9))**2 + ((yy-H*0.1)/(H*0.5))**2), 0, 1)[..., None]**2
        f = f + k * np.array(CRIMSON, np.float32) * leak * (0.75 + 0.25*math.sin(t*0.9))
    return f

def grain(f, fi):
    n = np.random.default_rng(fi).integers(-4, 5, (H//2, W//2), dtype=np.int16)
    n = np.repeat(np.repeat(n, 2, 0), 2, 1)[..., None]
    return np.clip(f + n, 0, 255).astype(np.uint8)

def particles(layer, t, amount):
    if amount <= 0: return
    d = ImageDraw.Draw(layer)
    for i, (px, py, sp, r) in enumerate(PART[:int(70*min(1, amount*3))]):
        x = (px*W + 20*math.sin(t*.5+i)) % W; y = (py*H - t*sp*H*0.6) % H
        al = int(70 + 60*math.sin(t*.8 + i)); d.ellipse((x-r, y-r, x+r, y+r), fill=SILVER+(max(10, al),))

# ---------- motifs ----------
def motif(layer, kind, t, ts, te, box):
    d = ImageDraw.Draw(layer); a = ease_out((t-ts)/0.6); x0, y0, x1, y1 = box
    if kind == "cage":
        c = SILVER + (int(110*a),); m = 60
        for (ax, ay, bx, by) in [(x0-m, y0-m, x1+m, y0-m), (x1+m, y0-m, x1+m, y1+m), (x1+m, y1+m, x0-m, y1+m), (x0-m, y1+m, x0-m, y0-m)]:
            d.line((ax, ay, ax+(bx-ax)*a, ay+(by-ay)*a), fill=c, width=2)
    elif kind == "split":
        L = H*0.3*a; d.line((W/2, H/2-L, W/2, H/2+L), fill=SILVER+(int(80*a),), width=1)
    elif kind == "tumtak":
        p = clamp((t-ts)/max(0.5, te-ts)); ya, yb = H*0.2, H*0.8; yc = ya + (yb-ya)*ease_io(p)
        d.line((W/2, ya, W/2, yc), fill=CRIMSON+(int(200*a),), width=3)
        for rad, al in ((26, 25), (14, 55), (6, 255)): d.ellipse((W/2-rad, yc-rad, W/2+rad, yc+rad), fill=IVORY+(al,))
        for txt, y in (("TUM", ya-70), ("TAK", yb+70)):
            sp, pad, asc, w = sprite(txt, 64, SILVER, "serif", 18); layer.alpha_composite(with_alpha(sp, a*.9), (int(W/2-sp.width/2), int(y-asc)))
    elif kind == "clock":
        for i in range(12):
            ang = i/12*2*math.pi; r0, r1 = 420, 450 if i % 3 else 480
            d.line((W/2+r0*math.cos(ang), H/2+r0*math.sin(ang), W/2+r1*math.cos(ang), H/2+r1*math.sin(ang)), fill=SILVER+(int(70*a),), width=2)
        ang = (t*0.35) % (2*math.pi); d.line((W/2, H/2, W/2+420*math.cos(ang), H/2+420*math.sin(ang)), fill=CRIMSON+(int(90*a),), width=2)
    elif kind == "lights":
        for i in range(9):
            x = W*(0.1+0.1*i); y = H*(0.25 + 0.5*((i*37) % 10)/10); r = 40 + (i*13) % 40
            glow = Image.new("RGBA", (r*4, r*4), (0, 0, 0, 0)); ImageDraw.Draw(glow).ellipse((r, r, 3*r, 3*r), fill=(255, 200, 140, int(50*a*(1-ease_io((t-te)/0.8)))))
            layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(r*0.6)), (int(x-2*r), int(y-2*r)))
    elif kind == "accent":
        d.line((W/2-120*a, y1+70, W/2+120*a, y1+70), fill=CRIMSON+(int(230*a),), width=3)

# ---------- phrase layout ----------
def layout(words, size_base):
    items = []
    for w in words:
        t = w["word"]; emph = t.lower().strip(",.") in EMPH or len(t) >= 8
        size = int(size_base*(1.25 if emph else 0.82)); kind = "serif" if emph or len(t) > 4 else "sans"
        col = IVORY if emph else SILVER
        items.append(dict(w=w, size=size, kind=kind, color=col, emph=emph, width=int(font(SERIF if kind == "serif" else SANS, size).getlength(t)) + 0))
    lines, cur, cw = [], [], 0
    for it in items:
        gap = 28 if cur else 0
        if cur and cw + gap + it["width"] > W - 220: lines.append(cur); cur, cw = [], 0; gap = 0
        cur.append(it); cw += gap + it["width"]
    if cur: lines.append(cur)
    lh = int(size_base*1.55); y0 = H/2 - (len(lines)-1)*lh/2 + size_base*0.3
    for li, ln in enumerate(lines):
        tot = sum(i["width"] for i in ln) + 28*(len(ln)-1); x = (W-tot)/2
        for it in ln: it["x"] = x; it["y"] = y0 + li*lh; x += it["width"] + 28
    box = (min(i["x"] for i in items), min(i["y"] for i in items)-size_base, max(i["x"]+i["width"] for i in items), max(i["y"] for i in items))
    return items, box

# ---------- main render ----------
def render(audio, out, mp4, t0=None, t1=None):
    require_tool("ffprobe")
    require_tool("ffmpeg")
    if not os.path.exists(audio):
        raise SystemExit(f"Audio file not found: {audio}")
    if not os.path.exists(f"{out}/word-timestamps.json") or not os.path.exists(f"{out}/phrases.json"):
        raise SystemExit(f"Missing alignment output in {out}. Run the sync pipeline first.")
    words = json.load(open(f"{out}/word-timestamps.json")); phrases = json.load(open(f"{out}/phrases.json"))
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio]))
    t0 = t0 or 0.0; t1 = t1 or dur; n = int(round((t1-t0)*FPS))
    plan = []
    for pi, ph in enumerate(phrases):
        ws = [words[i] for i in ph["word_indices"]]; pre, mo, inten = style_for(ph["text"], pi, len(ws), pi, len(phrases))
        size = 140 if len(ws) <= 4 else 116 if len(ws) <= 7 else 96
        items, box = layout(ws, size)
        nxt = phrases[pi+1]["start"] if pi+1 < len(phrases) else dur
        hold = 0.15 if pre != "fade_to_black" else 0.1; od = 0.5 if pre != "fade_to_black" else 2.5
        out_s = min(ph["end"] + hold, max(ph["end"], nxt)); plan.append(dict(ph=ph, items=items, box=box, pre=pre, motif=mo, inten=inten, ts=ph["start"], te=ph["end"], out_s=out_s, od=od))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-ss", str(t0), "-t", str(t1-t0), "-i", audio, "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-pix_fmt", "yuv420p",
           "-r", str(FPS), "-c:a", "aac", "-b:a", "256k", "-shortest", mp4]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fi in range(n):
        t = t0 + fi/FPS
        act = [q for q in plan if q["ts"]-0.4 <= t < q["out_s"]+q["od"]]
        leak = max([q["inten"]*ease_io((t-q["ts"]+0.4)/0.8)*(1-ease_io((t-q["out_s"])/q["od"])) for q in act] or [0.04]) + 0.03
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); particles(layer, t, max([q["inten"] for q in act] or [0.05]) if act else 0.05)
        for q in act:
            o = clamp((t-q["out_s"])/q["od"]); pre = PRESETS[q["pre"]]; pdur = max(0.3, q["te"]-q["ts"])
            zoom = 1 + (0.07 if q["pre"] == "slow_scale" else 0.025) * clamp((t-q["ts"])/(pdur+1))
            if q["motif"]:
                tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0)); motif(tmp, q["motif"], t, q["ts"], q["te"], q["box"])
                layer.alpha_composite(with_alpha(tmp, 1-o))
            for i, it in enumerate(q["items"]):
                a = (t - it["w"]["start"]) / IN_DUR.get(q["pre"], 0.35)
                if a <= 0 and q["pre"] != "masked_reveal": continue
                if a <= 0: continue
                a = clamp(a); c = dict(i=i); prm = pre(a, o, c)
                if q["pre"] not in ("kinetic_cut", "fade_to_black"): prm["op"] *= 1 - ease_io(o) if q["pre"] != "letter_fragment" else 1-ease_io(o)
                else: prm["op"] *= 1 - ease_io(o)
                active = it["w"]["start"] <= t < it["w"]["end"]
                col = CRIMSON if (q["motif"] == "accent" and active and it["emph"]) else (IVORY if active or it["emph"] else SILVER)
                if q["pre"] == "fade_to_black": prm["op"] *= 0.35 + 0.65*(1-ease_io(o))
                sz = it["size"]; cx, cy = W/2, H/2
                px = cx + (it["x"] + prm["dx"] - cx)*zoom; py = cy + (it["y"] + prm["dy"] - cy)*zoom; sc = prm["sc"]*zoom
                txt = it["w"]["word"]
                if prm["frag"] > 0.01:
                    ls, tw = letter_sprites(txt, sz, col, it["kind"]); rr = random.Random(it["w"]["index"])
                    for li, (im, lx, adv) in enumerate(ls):
                        ox, oy = rr.uniform(-1, 1)*140*prm["frag"], rr.uniform(-1, 1)*140*prm["frag"]
                        im2 = with_alpha(im, prm["op"]); layer.alpha_composite(im2, (int(px + lx*zoom - sz//2 + ox), int(py - sz*1.2 + oy)))
                    continue
                sp, pad, asc, w = sprite(txt, sz, col, it["kind"], (prm["track"] or 0.0)*sz if prm["track"] is not None else 0.0)
                im = sp
                if prm["clip"] < 0.999:
                    im = im.copy(); im.paste((0, 0, 0, 0), (int(pad + w*prm["clip"]) + 1, 0, im.width, im.height))
                if prm["blur"] > 0.4: im = im.filter(ImageFilter.GaussianBlur(prm["blur"]))
                if abs(sc-1) > 0.004: im = im.resize((int(im.width*sc), int(im.height*sc)), Image.BILINEAR); pad_s, asc_s, wd = pad*sc, asc*sc, w*sc
                else: pad_s, asc_s, wd = pad, asc, w
                if prm["track"] is not None: px_adj = px - (wd - it["width"])/2
                else: px_adj = px - (wd - it["width"]*sc)/2 if sc != 1 else px
                layer.alpha_composite(with_alpha(im, prm["op"]), (int(px_adj - pad_s), int(py - asc_s)))
        f = background(t, leak)
        la = np.array(layer, np.float32); al = la[..., 3:4]/255
        f = f*(1-al) + la[..., :3]*al
        p.stdin.write(grain(f, fi).tobytes())
    p.stdin.close(); p.wait()

if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit("Usage: python motion.py audio.wav out/ out/cinematic.mp4 [--start S --end E]")
    a = sys.argv; kw = {k: float(a[i+1]) for i, k in enumerate(a) if k in ("--start", "--end")}
    render(a[1], a[2], a[3], kw.get("--start"), kw.get("--end"))
