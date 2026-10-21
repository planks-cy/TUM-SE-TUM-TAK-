#!/usr/bin/env python3
"""Tum Se Tum Tak - sync QA pipeline.
  python sync_pipeline.py align  audio.wav lyrics.txt out/     # needs MMS_FA weights (internet)
  python sync_pipeline.py render audio.wav out/ out/sync-test.mp4 [--start S --end E]
  python sync_pipeline.py report audio.wav out/
Lyrics = truth for WORDS (display text untouched). Audio = truth for TIMING. Nothing is shifted after alignment."""
import sys, json, subprocess, re
import numpy as np
FPS, W, H = 30, 1080, 1920

def load_words(lyrics):
    words, lines = [], []
    for li, line in enumerate(open(lyrics, encoding="utf-8").read().splitlines()):
        if not line.strip(): continue
        ids = []
        for w in line.split():
            ids.append(len(words)); words.append({"index": len(words), "word": w, "line": len(lines)})
        lines.append(ids)
    return words, lines

def align(audio, lyrics, out):
    import torch, torchaudio, torchaudio.functional as F, soundfile as sf
    words, lines = load_words(lyrics)
    bundle = torchaudio.pipelines.MMS_FA
    model, dic = bundle.get_model(with_star=False), bundle.get_dict(star=None)
    wav, sr = sf.read(audio, dtype="float32", always_2d=True)
    wav = torch.from_numpy(wav.mean(1))[None]
    wav = torchaudio.functional.resample(wav, sr, bundle.sample_rate)
    with torch.inference_mode():
        emission, _ = model(wav)
    emission = torch.log_softmax(emission, -1)
    # internal normalisation ONLY for alignment; display text is untouched
    norm = [re.sub(r"[^a-z]", "", w["word"].lower()) for w in words]
    tokens, spans_idx = [], []
    for n in norm:
        s = len(tokens); tokens += [dic[c] for c in n if c in dic]; spans_idx.append((s, len(tokens)))
    ali, sc = F.forced_align(emission, torch.tensor([tokens], dtype=torch.int32), blank=0)
    ali, sc = ali[0], sc[0].exp()
    tokspans = F.merge_tokens(ali, sc)
    fr = wav.shape[1] / emission.shape[1] / bundle.sample_rate
    res = []
    for w, (a, b) in zip(words, spans_idx):
        ts = tokspans[a:b]
        st, en = ts[0].start * fr, ts[-1].end * fr
        conf = float(np.mean([t.score for t in ts]))
        res.append({"index": w["index"], "word": w["word"], "start": round(st, 3), "end": round(en, 3),
                    "confidence": round(conf, 3), "review_required": False})
    for r in res:
        d = r["end"] - r["start"]
        r["review_required"] = r["confidence"] < 0.5 or d < 0.08 or d > 2.5
    json.dump(res, open(f"{out}/word-timestamps.json", "w"), indent=1, ensure_ascii=False)
    ph = []
    for i, ids in enumerate(lines):
        ph.append({"phrase_id": i, "text": " ".join(words[j]["word"] for j in ids),
                   "start": res[ids[0]]["start"], "end": res[ids[-1]]["end"], "word_indices": ids,
                   "section": "chorus" if lines_text(lyrics)[i].lower().startswith("tum") else "verse",
                   "review_required": any(res[j]["review_required"] for j in ids)})
    json.dump(ph, open(f"{out}/phrases.json", "w"), indent=1, ensure_ascii=False)

def lines_text(lyrics): return [l for l in open(lyrics, encoding="utf-8").read().splitlines() if l.strip()]

def probe(audio):
    return float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",audio]))

def report(audio, out, lyrics="lyrics.txt"):
    res = json.load(open(f"{out}/word-timestamps.json")); words, _ = load_words(lyrics)
    dur = probe(audio); issues = []
    ok_text = [r["word"] for r in res] == [w["word"] for w in words]
    for i, r in enumerate(res):
        if r["end"] <= r["start"]: issues.append({"index": i, "type": "non_positive_duration"})
        if i and r["start"] < res[i-1]["end"] - 1e-6: issues.append({"index": i, "type": "overlap"})
        if i and r["start"] - res[i-1]["end"] > 1.5: issues.append({"index": i, "type": "gap_gt_1.5s", "from": res[i-1]["end"], "to": r["start"]})
        if r["end"] > dur + 0.05: issues.append({"index": i, "type": "beyond_audio"})
    rev = [r for r in res if r["review_required"]]
    rng, cur = [], None
    for r in rev:
        if cur and r["start"] - cur[1] < 1.0: cur[1] = r["end"]
        else:
            cur = [r["start"], r["end"]]; rng.append(cur)
    rep = {"audio_duration_s": dur, "lyric_words": len(words), "timestamped_words": len(res), "text_matches_lyrics_exactly": ok_text,
           "review_required_words": len(rev), "manual_review_ranges_s": [[round(a,2),round(b,2)] for a,b in rng],
           "structural_issues": issues, "note": "Not independently verified by ear; review ranges are minimum, spot-check start/middle/end."}
    json.dump(rep, open(f"{out}/alignment-report.json", "w"), indent=1); print(json.dumps(rep, indent=1))

def render(audio, out, mp4, t0=None, t1=None):
    from PIL import Image, ImageDraw, ImageFont
    res = json.load(open(f"{out}/word-timestamps.json"))
    dur = probe(audio); t0 = t0 or 0.0; t1 = t1 or dur
    font_path = subprocess.check_output(["fc-match","-f","%{file}","DejaVu Sans:bold"]).decode()
    cache = {}
    def img(txt):
        if txt in cache: return cache[txt]
        size = 220
        while True:
            f = ImageFont.truetype(font_path, size); l, t, r, b = f.getbbox(txt)
            if r - l <= W - 160 or size < 40: break
            size -= 8
        im = Image.new("RGB", (W, H), (0, 0, 0)); d = ImageDraw.Draw(im)
        d.text(((W-(r-l))//2 - l, (H-(b-t))//2 - t), txt, font=f, fill=(255, 255, 255))
        cache[txt] = im.tobytes(); return cache[txt]
    n = int(round((t1 - t0) * FPS)); blank = img("")
    cmd = ["ffmpeg","-v","error","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","-",
           "-ss",str(t0),"-t",str(t1-t0),"-i",audio,"-c:v","libx264","-pix_fmt","yuv420p","-r",str(FPS),
           "-c:a","aac","-b:a","256k","-shortest",mp4]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE); j = 0
    for f in range(n):
        t = t0 + f / FPS
        while j < len(res) and res[j]["end"] <= t: j += 1
        p.stdin.write(img(res[j]["word"]) if j < len(res) and res[j]["start"] <= t else blank)
    p.stdin.close(); p.wait()

if __name__ == "__main__":
    c = sys.argv[1]
    if c == "align": align(*sys.argv[2:5])
    elif c == "render":
        a = sys.argv; kw = {k: float(a[i+1]) for i, k in enumerate(a) if k in ("--start","--end")}
        render(a[2], a[3], a[4], kw.get("--start"), kw.get("--end"))
    elif c == "report": report(sys.argv[2], sys.argv[3])
