import logging
import shlex
import subprocess
import json
import re
import requests
from flask import Flask, abort, request, Response

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


def yt_dlp_json(url):
    """Run yt-dlp -j to get info JSON."""
    cmd = ["yt-dlp", "--no-warnings", "--no-playlist", "-j", url]
    logging.info("Running: %s", " ".join(shlex.quote(c) for c in cmd))
    try:
        # Run the command to get metadata
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        logging.exception("yt-dlp command failed")
        raise e  # Re-raise for the route to handle

    try:
        # Parse the JSON output
        return json.loads(out)
    except json.JSONDecodeError as e:
        logging.exception("Failed to parse yt-dlp JSON output")
        raise e  # Re-raise for the route to handle


def best_progressive_format(info):
    """Return highest quality format with both audio and video (single file)."""
    best = None
    for f in info.get("formats", []):
        # Skip if audio-only or video-only
        if f.get("vcodec") == "none" or f.get("acodec") == "none":
            continue
        # Skip m3u8 playlists
        if f.get("protocol", "").startswith("m3u8"):
            continue
        # Score by height, then bitrate
        score = (f.get("height") or 0) * 1_000_000 + (f.get("tbr") or 0)
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None


def best_audio_format(info):
    """Return highest bitrate audio-only format."""
    best = None
    for f in info.get("formats", []):
        # Skip if it has video
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
        # Get video metadata
        info = yt_dlp_json(url)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        # Error is already logged by yt_dlp_json
        return abort(500, "Failed to fetch video metadata")

    # Find the best format object
    if fmt == "audio":
        fmt_obj = best_audio_format(info)
        if not fmt_obj or not fmt_obj.get("url"):
            return abort(500, "No audio format available")
    else:
        fmt_obj = best_progressive_format(info)
        if not fmt_obj or not fmt_obj.get("url"):
            return abort(500, "No video format available")

    direct_url = fmt_obj["url"]

    # --- Start: Filename and Streaming Logic ---

    # Get title and extension from metadata
    title = info.get("title", "download")
    ext = fmt_obj.get("ext", "bin")

    # Sanitize the title to create a safe filename
    # Allow letters, numbers, underscore, dot, hyphen, and space
    safe_title = re.sub(r'[^\w\._\-\s]', '_', title)
    # Consolidate multiple spaces and strip leading/trailing whitespace
    safe_title = re.sub(r'\s+', ' ', safe_title).strip()
    if not safe_title:
        safe_title = "download"  # Fallback filename

    filename = f"{safe_title}.{ext}"

    logging.info("Streaming %s format for '%s' from %s", fmt, title, direct_url)

    try:
        # Make a streaming request to the direct URL
        r = requests.get(direct_url, stream=True, timeout=10)
        r.raise_for_status()  # Raise an exception for bad status codes

        # Stream the content to the client without loading it all into memory
        return Response(
            r.iter_content(chunk_size=8192),
            content_type=fmt_obj.get("mimetype", "application/octet-stream"),
            headers={
                # This header tells the browser to download the file with the given name
                "Content-Disposition": f"attachment; filename=\"{filename}\""
            }
        )
    except requests.exceptions.RequestException as e:
        logging.error("Failed to stream content from %s: %s", direct_url, e)
        return abort(502, "Failed to stream content")
    # --- End: Filename and Streaming Logic ---


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8989, threaded=True)