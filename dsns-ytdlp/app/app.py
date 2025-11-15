import logging
import shlex
import subprocess
import json
from flask import Flask, abort, request, redirect

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def yt_dlp_json(url):
    """Run yt-dlp -j to get info JSON."""
    cmd = ["yt-dlp", "--no-warnings", "--no-playlist", "-j", url]
    logging.info("Running: %s", " ".join(shlex.quote(c) for c in cmd))
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    return json.loads(out)

def best_progressive_format(info):
    """Return highest quality format with both audio and video (single file)."""
    best = None
    for f in info.get("formats", []):
        if f.get("vcodec") == "none" or f.get("acodec") == "none":
            continue
        if f.get("protocol", "").startswith("m3u8"):
            continue
        score = (f.get("height") or 0) * 1_000_000 + (f.get("tbr") or 0)
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None

def best_audio_format(info):
    """Return highest bitrate audio-only format."""
    best = None
    for f in info.get("formats", []):
        if f.get("vcodec") != "none" or f.get("acodec") == "none":
            continue
        score = f.get("abr") or 0
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None

@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    fmt = request.form.get("format", "video")  # "video" or "audio"

    if not url:
        return abort(400, "Missing url")

    try:
        info = yt_dlp_json(url)
    except subprocess.CalledProcessError:
        logging.exception("yt-dlp failed")
        return abort(500, "Failed to fetch metadata")

    if fmt == "audio":
        fmt_obj = best_audio_format(info)
        if not fmt_obj or not fmt_obj.get("url"):
            return abort(500, "No audio format available")
    else:
        fmt_obj = best_progressive_format(info)
        if not fmt_obj or not fmt_obj.get("url"):
            return abort(500, "No video format available")

    direct_url = fmt_obj["url"]
    logging.info("Redirecting to %s format: %s", fmt, direct_url)
    
    return redirect(direct_url)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8989, threaded=True)