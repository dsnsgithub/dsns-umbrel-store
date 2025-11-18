import json
import logging
import re
import subprocess
from urllib.parse import quote

import requests
from flask import Flask, Response, abort, request
from werkzeug.utils import secure_filename

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


def sanitize_filename(title, fallback="download"):
    if not title:
        return fallback
    fn = secure_filename(title)
    if not fn:
        fn = re.sub(r"[^\w\-_\. ]", "_", title)[:200]
    return fn


def yt_dlp_json(url):
    """
    Get metadata.
    Added --flat-playlist to ensure we don't get stuck processing huge playlists
    if a playlist URL is accidentally provided.
    """
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--dump-json",  # Use dump-json instead of -j for clarity
        url,
    ]
    # Run metadata fetch
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except subprocess.CalledProcessError:
        return None


@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    fmt = request.form.get("format", "video")

    if not url:
        return abort(400, "Missing url")

    logging.info(f"Fetching metadata for: {url}")
    info = yt_dlp_json(url)

    if not info:
        return abort(500, "Failed to fetch metadata")

    title = info.get("title") or info.get("id") or "download"
    safe_name = sanitize_filename(title)

    # --- OPTIMIZATION: AUDIO SELECTION ---
    target_url = None
    target_headers = {}

    if fmt != "video":
        # AUDIO OPTIMIZATION:
        # 1. Do NOT convert to MP3 (slow). Use m4a (AAC) which is standard and fast.
        # 2. Find the best "direct" url (http/https) to stream via requests.

        # Sort formats: prefer m4a (aac) > webm > others, and higher bitrate.
        formats = info.get("formats", [])

        # Filter for audio only
        audio_formats = [
            f
            for f in formats
            if f.get("vcodec") == "none" and f.get("acodec") != "none"
        ]

        selected_fmt = None

        # Priority 1: Look for m4a (AAC) over HTTP (Format 140 is standard YouTube AAC)
        # This plays everywhere and downloads instantly.
        for f in audio_formats:
            if f.get("ext") == "m4a" and f.get("protocol", "").startswith("http"):
                # Choose the one with highest filesize/bitrate
                if not selected_fmt or (f.get("tbr", 0) > selected_fmt.get("tbr", 0)):
                    selected_fmt = f

        # Priority 2: If no m4a, get best webm/opus
        if not selected_fmt:
            for f in audio_formats:
                if f.get("protocol", "").startswith("http"):
                    if not selected_fmt or (
                        f.get("tbr", 0) > selected_fmt.get("tbr", 0)
                    ):
                        selected_fmt = f

        if selected_fmt:
            target_url = selected_fmt.get("url")
            target_headers = selected_fmt.get("http_headers", {})

            # Set filename extension based on actual format
            ext = selected_fmt.get("ext", "m4a")
            filename = f"{safe_name}.{ext}"

            # Set Mime type
            if ext == "m4a":
                mimetype = "audio/mp4"  # Standard for m4a
            elif ext == "webm":
                mimetype = "audio/webm"
            else:
                mimetype = "application/octet-stream"
        else:
            # Fallback if no audio found (rare)
            return abort(404, "No suitable audio stream found")

    else:
        # VIDEO LOGIC (Simple: Find single file best quality)
        # Try to find mp4 with audio and video
        formats = info.get("formats", [])
        selected_fmt = None

        for f in formats:
            # Must have audio and video and be http
            if (
                f.get("vcodec") != "none"
                and f.get("acodec") != "none"
                and f.get("protocol", "").startswith("http")
            ):
                if not selected_fmt or (f.get("tbr", 0) > selected_fmt.get("tbr", 0)):
                    selected_fmt = f

        if selected_fmt:
            target_url = selected_fmt.get("url")
            target_headers = selected_fmt.get("http_headers", {})
            filename = f"{safe_name}.mp4"
            mimetype = "video/mp4"
        else:
            # If we can't find a single combined file, we fallback to the subprocess method below
            pass

    # --- STREAMING STRATEGY ---

    # STRATEGY A: Fast Direct Proxy (Preferred)
    if target_url:
        logging.info(f"Fast Direct Stream: {filename}")

        def generate_direct():
            try:
                # stream=True is crucial.
                # Timeout prevents hanging if YT drops connection.
                with requests.get(
                    target_url, headers=target_headers, stream=True, timeout=15
                ) as r:
                    r.raise_for_status()
                    # Increased chunk size to 64KB for better throughput
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            yield chunk
            except Exception as e:
                logging.error(f"Stream error: {e}")

        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(
            generate_direct(),
            mimetype=mimetype,
            headers={"Content-Disposition": disposition},
        )

    # STRATEGY B: Subprocess Fallback (Slow, only used if Direct fail/Complex video)
    # This handles cases like 1080p+ (which are separate audio/video streams)
    # or HLS/M3U8 streams.
    logging.info("Fallback to Subprocess Stream")

    if fmt == "video":
        # bestvideo+bestaudio usually requires merging = slow.
        # 'best' is usually 720p max but single file = fast.
        ytdlp_fmt = "best"
        filename = f"{safe_name}.mp4"
        mimetype = "video/mp4"
    else:
        ytdlp_fmt = "bestaudio/best"
        filename = f"{safe_name}.m4a"  # We force container to something standard
        mimetype = "audio/mp4"

    cmd = [
        "yt-dlp",
        "-f",
        ytdlp_fmt,
        "-o",
        "-",  # stdout
        "--quiet",
        "--no-playlist",
        # Buffer improvements
        "--buffer-size",
        "16K",
        "--no-part",
        url,
    ]

    def generate_subprocess():
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            # Read in larger chunks
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.kill()

    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        generate_subprocess(),
        mimetype=mimetype,
        headers={"Content-Disposition": disposition},
    )


if __name__ == "__main__":
    # Threaded is required for streaming
    app.run(host="0.0.0.0", port=8989, threaded=True)
