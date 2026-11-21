"""Find viral-moment candidates in a Twitch VOD using chat spikes, then transcribe them.
Usage: python scan.py <vod_url_or_channel> <out_dir> [--hours 3] [--top 8]
"""
import argparse, json, os, re, subprocess, sys, statistics, collections

EMOTE_BOOST = re.compile(r"(LMAO|LOL|KEKW|OMEGALUL|LUL|ICANT|💀|😂|W\b|HOLY|NO WAY|CLIP|INSANE|BRO)", re.I)

def sh(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw).stdout

def resolve_vod(target):
    if re.search(r"/videos/\d+|youtube\.com/watch|youtu\.be/|kick\.com/.+/videos/", target):
        return target
    if target.startswith("yt:"):  # yt:@handle -> latest finished/ongoing live stream
        out = sh(["yt-dlp", f"https://www.youtube.com/{target[3:]}/streams", "--flat-playlist", "--playlist-end", "1", "--print", "url"])
        return out.strip().splitlines()[0]
    if target.startswith("kick:"):
        out = sh(["yt-dlp", f"https://kick.com/{target[5:]}/videos", "--flat-playlist", "--playlist-end", "1", "--print", "url"])
        return out.strip().splitlines()[0]
    ch = target.rstrip("/").split("/")[-1]
    out = sh(["yt-dlp", f"https://www.twitch.tv/{ch}/videos?filter=archives&sort=time",
              "--flat-playlist", "--playlist-end", "1", "--print", "url"])
    return out.strip().splitlines()[0]

def vod_info(url):
    j = json.loads(sh(["yt-dlp", "-J", url]))
    return {"id": j.get("id","").lstrip("v"), "title": j.get("title"), "duration": j.get("duration") or 0,
            "uploader": j.get("uploader"), "is_live": j.get("is_live")}

def chat(vid, begin, end, out):
    sh(["./TwitchDownloaderCLI", "chatdownload", "--id", vid, "-b", str(int(begin)), "-e", str(int(end)),
        "-o", out, "--collision", "Overwrite"])
    data = json.load(open(out, encoding="utf-8"))
    msgs = []
    for c in data.get("comments", []):
        t = c.get("content_offset_seconds")
        body = (c.get("message") or {}).get("body", "")
        if t is not None and not body.strip().startswith("!"):
            msgs.append((float(t), body))
    return msgs

def yt_chat(url, begin, end, out_dir):
    try:
        sh(["yt-dlp", url, "--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", os.path.join(out_dir, "ytchat")])
    except subprocess.CalledProcessError:
        return []
    path = os.path.join(out_dir, "ytchat.live_chat.json")
    if not os.path.exists(path): return []
    msgs = []
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
            act = d["replayChatItemAction"]; t = int(act["videoOffsetTimeMsec"]) / 1000
            for a in act.get("actions", []):
                r = a.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
                if r:
                    body = "".join(x.get("text", "") for x in r.get("message", {}).get("runs", []))
                    if begin <= t <= end and not body.startswith("!"): msgs.append((t, body))
        except Exception:
            continue
    return msgs

def audio_events(url, begin, end, out_dir, bucket=10):
    """No chat available (e.g. Kick): use loudness spikes (screams, laughter) as pseudo-chat."""
    a = os.path.join(out_dir, "audio.m4a")
    sh(["yt-dlp", url, "-f", "bestaudio/worst", "--download-sections", f"*{begin}-{end}", "-x", "--audio-format", "m4a", "-o", a])
    r = subprocess.run(["ffmpeg", "-i", a, "-af", f"astats=metadata=1:reset={bucket*44100//1024},ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-"],
                       capture_output=True, text=True)
    vals = [float(m) for m in re.findall(r"RMS_level=(-?[\d.]+)", r.stderr) if m not in ("-inf",)]
    msgs = []
    if vals:
        step = (end - begin) / len(vals); med = statistics.median(vals)
        for i, v in enumerate(vals):
            n = int(max(0, v - med) * 2)  # each dB above median = 2 pseudo-messages
            msgs += [(begin + i * step, "LOUD")] * n
    return msgs

def peaks(msgs, begin, end, top, bucket=10, gap=120):
    counts = collections.Counter(); boost = collections.Counter()
    for t, b in msgs:
        k = int(t // bucket); counts[k] += 1
        if EMOTE_BOOST.search(b): boost[k] += 1
    keys = range(int(begin // bucket), int(end // bucket) + 1)
    series = [counts[k] + 0.5 * boost[k] for k in keys]
    if not series: return []
    med = statistics.median(series) or 1
    scored = []
    for i, k in enumerate(keys):
        window = sum(series[max(0, i-1):i+2])  # 30s smoothing
        scored.append((window / (3 * med), k * bucket))
    scored.sort(reverse=True)
    chosen = []
    for score, t in scored:
        if all(abs(t - c[1]) > gap for c in chosen):
            chosen.append((round(score, 2), t))
        if len(chosen) >= top: break
    return chosen

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target"); ap.add_argument("out")
    ap.add_argument("--hours", type=float, default=3); ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    url = resolve_vod(a.target); info = vod_info(url)
    dur = info["duration"]
    if not dur:  # e.g. Kick VODs report no duration in metadata
        try:
            m3u8 = sh(["yt-dlp", "-g", url]).strip().splitlines()[0]
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", m3u8],
                                       capture_output=True, text=True).stdout.strip() or 0)
        except Exception:
            dur = 0
        info["duration"] = dur
    begin = max(0, dur - a.hours * 3600); end = dur
    if "twitch.tv" in url:
        msgs = chat(info["id"], begin, end, os.path.join(a.out, "chat.json"))
    elif "youtube" in url or "youtu.be" in url:
        msgs = yt_chat(url, begin, end, a.out) or audio_events(url, begin, end, a.out)
    else:
        msgs = audio_events(url, begin, end, a.out)
    print(f"{len(msgs)} chat/audio events", flush=True)
    cands = []
    from faster_whisper import WhisperModel
    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    for score, t in peaks(msgs, begin, end, a.top):
        s, e = max(0, t - 55), min(dur, t + 20)  # reactions lag the moment
        clip = os.path.join(a.out, f"cand_{int(s)}.mp4")
        sh(["yt-dlp", url, "-f", "best[height<=1080]/best", "--download-sections", f"*{s}-{e}",
            "--force-keyframes-at-cuts", "-o", clip])
        segs, _ = model.transcribe(clip, word_timestamps=True, vad_filter=True)
        words, lines = [], []
        for sg in segs:
            lines.append({"start": round(s + sg.start, 2), "end": round(s + sg.end, 2), "text": sg.text.strip()})
            for w in sg.words or []:
                words.append({"start": round(s + w.start, 2), "end": round(s + w.end, 2), "w": w.word})
        top_chat = [b for tt, b in msgs if t - 10 <= tt <= t + 20][:25]
        sh(["ffmpeg", "-y", "-ss", "20", "-i", clip, "-frames:v", "1", "-vf", "scale=640:-2", clip.replace(".mp4", ".jpg")])
        cands.append({"vod": url, "vod_start": s, "vod_end": e, "peak": t, "chat_score": score,
                      "transcript": lines, "words": words, "chat_sample": top_chat,
                      "file": os.path.basename(clip)})
    json.dump({"info": info, "url": url, "candidates": cands}, open(os.path.join(a.out, "candidates.json"), "w"), indent=1)
    print(f"{len(cands)} candidates")

if __name__ == "__main__":
    main()
