"""TikTok Content Posting API - upload a clip to the creator's TikTok inbox as a draft.
Env: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN
Usage:
  python tiktok.py exchange <auth_code>      -> prints refresh token (store it as the TIKTOK_REFRESH_TOKEN secret)
  python tiktok.py upload <path/to/clip.mp4> -> sends the clip to the TikTok inbox (drafts)
"""
import json, os, sys, urllib.parse, urllib.request

REDIRECT = "https://rodheraihustle-design.github.io/gta6-clip-engine/callback.html"
API = "https://open.tiktokapis.com"

def post(url, data=None, headers=None, raw=None, method="POST"):
    body = raw if raw is not None else (urllib.parse.urlencode(data).encode() if isinstance(data, dict) and headers is None else json.dumps(data).encode())
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        t = r.read().decode() or "{}"
        return json.loads(t) if t.strip().startswith("{") else {"raw": t}

def token(grant):
    d = {"client_key": os.environ["TIKTOK_CLIENT_KEY"], "client_secret": os.environ["TIKTOK_CLIENT_SECRET"], **grant}
    return post(f"{API}/v2/oauth/token/", d)

def main():
    cmd = sys.argv[1]
    if cmd == "exchange":
        r = token({"grant_type": "authorization_code", "code": sys.argv[2], "redirect_uri": REDIRECT})
        print("::add-mask::" + r.get("refresh_token", ""))
        print("OPEN_ID", r.get("open_id"), "SCOPE", r.get("scope"), "ERROR", r.get("error"), r.get("error_description"))
        with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a") as f:
            f.write("Copy this refresh token into the TIKTOK_REFRESH_TOKEN repository secret, then delete this run:\n\n`" + r.get("refresh_token", "none") + "`\n")
    elif cmd == "upload":
        r = token({"grant_type": "refresh_token", "refresh_token": os.environ["TIKTOK_REFRESH_TOKEN"]})
        at = r["access_token"]; path = sys.argv[2]; size = os.path.getsize(path)
        h = {"Authorization": f"Bearer {at}", "Content-Type": "application/json; charset=UTF-8"}
        init = post(f"{API}/v2/post/publish/inbox/video/init/",
                    {"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}}, h)
        print("init", init.get("error"), init.get("data", {}).get("publish_id"))
        url = init["data"]["upload_url"]
        with open(path, "rb") as f:
            data = f.read()
        post(url, raw=data, method="PUT", headers={"Content-Type": "video/mp4", "Content-Length": str(size),
                                                    "Content-Range": f"bytes 0-{size-1}/{size}"})
        print("uploaded to TikTok inbox:", os.path.basename(path))

if __name__ == "__main__":
    main()
