"""Render vertical 9:16 clips with hook text + burned word captions.
Usage: python render.py plan.json out_dir
plan.json: {"clips":[{"id":"kai_001","vod":"<url>","start":123.4,"end":171.0,
             "hook":"KAI DIDN'T SEE THIS COMING", "layout":"fit"|"zoom", "accent":"&H0000E5FF"}]}
"""
import json, os, subprocess, sys

FONT = "Poppins ExtraBold"

def sh(cmd):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True)

def ts(t):
    t = max(0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def esc(s):
    s = "".join(ch for ch in s if ord(ch) < 0x2190).strip()
    return s.replace("{", "(").replace("}", ")").replace("\n", " ")

def build_ass(words, hook, dur, accent, path):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{FONT},74,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,18,0,8,70,70,300,1
Style: Cap,{FONT},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,3,2,60,60,470,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 1,{ts(0)},{ts(dur)},Hook,,0,0,0,,{{\\fad(150,0)}}{esc(hook.upper())}")
    # captions: chunks of up to 3 words, active word highlighted in accent colour
    chunk = []
    def flush(ch):
        if not ch: return
        for i, w in enumerate(ch):
            st = w["start"]; en = ch[i + 1]["start"] if i + 1 < len(ch) else w["end"] + 0.15
            parts = []
            for j, x in enumerate(ch):
                txt = esc(x["w"].strip().upper())
                parts.append(f"{{\\c{accent}}}{txt}{{\\c&H00FFFFFF&}}" if j == i else txt)
            ev.append(f"Dialogue: 0,{ts(st)},{ts(en)},Cap,,0,0,0,,{{\\fscx105\\fscy105}}" + " ".join(parts))
    for w in words:
        chunk.append(w)
        if len(chunk) == 3 or w["w"].strip()[-1:] in ".?!,":
            flush(chunk); chunk = []
    flush(chunk)
    open(path, "w", encoding="utf-8").write(head + "\n".join(ev) + "\n")

def main():
    plan = json.load(open(sys.argv[1])); out = sys.argv[2]; os.makedirs(out, exist_ok=True)
    from faster_whisper import WhisperModel
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    done = []
    for c in plan["clips"]:
      try:
          raw = os.path.join(out, c["id"] + "_raw.mp4")
          sh(["yt-dlp", c["vod"], "-f", "best[height<=1080]/best", "--download-sections",
              f"*{c['start']}-{c['end']}", "--force-keyframes-at-cuts", "-o", raw])
          dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", raw],
                                     capture_output=True, text=True).stdout.strip())
          segs, _ = model.transcribe(raw, word_timestamps=True, vad_filter=True)
          words = [{"start": w.start, "end": w.end, "w": w.word} for s in segs for w in (s.words or [])]
          ass = os.path.join(out, c["id"] + ".ass")
          build_ass(words, c.get("hook", ""), dur, c.get("accent", "&H0000E5FF"), ass)
          if c.get("layout") == "split":
              x, y0, w, h = c["cam"]  # facecam box as fractions of the source frame
              camh = int(1080 * (h * 9) / (w * 16) / 2) * 2  # keep cam aspect at 1080 wide
              camh = min(camh, 760); gh = 1920 - camh
              vf = (f"[0:v]split=2[a][b];[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y0},scale=1080:{camh},setsar=1[cam];"
                    f"[b]scale=-2:{gh},crop=1080:{gh},setsar=1[game];[cam][game]vstack=2,"
                    f"ass={ass}:fontsdir=fonts,fps=30,format=yuv420p[v]")
          else:
              if c.get("layout") == "zoom":
                  fg = "[0:v]scale=-2:1100,crop=1080:1100[fg]"; y = "(H-h)/2-60"
              else:
                  fg = "[0:v]scale=1080:-2[fg]"; y = "(H-h)/2-40"
              vf = (f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,"
                    f"eq=brightness=-0.18:saturation=1.2[bg];{fg};[bg][fg]overlay=0:{y},"
                    f"ass={ass}:fontsdir=fonts,fps=30,format=yuv420p[v]")
          final = os.path.join(out, c["id"] + ".mp4")
          sh(["ffmpeg", "-y", "-i", raw, "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
              "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-movflags", "+faststart", final])
          sh(["ffmpeg", "-y", "-ss", "1.5", "-i", final, "-frames:v", "1", "-vf", "scale=540:-2", final.replace(".mp4", ".jpg")])
          os.remove(raw)
          c["duration"] = round(dur, 1); c["transcript"] = " ".join(w["w"].strip() for w in words)
          done.append(c)
      except Exception as e:
        print("CLIP FAILED", c["id"], e, flush=True)
    plan["clips"] = done
    json.dump(plan, open(os.path.join(out, "rendered.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
