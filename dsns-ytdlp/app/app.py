import json
import logging
import re
import shlex
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
    # secure_filename from werkzeug removes problematic characters; keep a sanitized unicode name.
    fn = secure_filename(title)
    if not fn:
        # fallback if secure_filename removed everything
        fn = re.sub(r"[^\w\-_\. ]", "_", title)[:200]
    return fn


def yt_dlp_json(url):
    """Run yt-dlp -j to get info JSON (no download). Returns dict or raise."""
    cmd = ["yt-dlp", "--no-warnings", "--no-playlist", "-j", url]
    logging.info("Running: %s", " ".join(shlex.quote(c) for c in cmd))
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    return json.loads(out)


def choose_progressive_format(info):
    """
    Return a format dict which is a single-file (has audio+video)
    Prefer highest resolution/bitrate and non-m3u8 protocols if possible.
    """
    best = None
    for f in info.get("formats", []):
        # skip formats that are just audio-only or video-only
        if f.get("vcodec") == "none" or f.get("acodec") == "none":
            continue
        # prefer non-m3u8 / non-dash if possible (direct .mp4/.m4a/.webm)
        proto = f.get("protocol", "")
        # avoid formats that are manifest playlists (hls native) where direct streaming may be tricky
        if proto and proto.startswith("m3u8"):
            continue
        # choose by resolution or bitrate
        score = (f.get("height") or 0) * 1000000 + (f.get("tbr") or 0)
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None


@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    fmt = request.form.get("format", "video")
    if not url:
        return abort(400, "Missing url")

    try:
        info = yt_dlp_json(url)
    except subprocess.CalledProcessError as e:
        logging.exception("yt-dlp metadata extraction failed")
        return abort(500, "Failed to fetch metadata")

    title = info.get("title") or info.get("id") or "download"
    safe_name = sanitize_filename(title)
    if fmt == "video":
        filename = f"{safe_name}.mp4"
        mimetype = "video/mp4"
    else:
        # we'll stream the audio container if possible (may be m4a/webm)
        filename = f"{safe_name}.mp3"  # we try to name mp3, but we may stream original container
        mimetype = "audio/mpeg"

    # Try to find a single-file progressive format (audio+video) and stream that URL directly.
    prog_format = choose_progressive_format(info) if fmt == "video" else None

    # For audio requests, prefer best single audio file (no conversion) for speed.
    if fmt != "video":
        # Find the highest quality audio-only format
        best_audio = None
        for f in info.get("formats", []):
            if f.get("vcodec") != "none":  # skip video
                continue
            if f.get("acodec") == "none":  # skip no-audio formats
                continue
            score = f.get("abr") or 0
            if best_audio is None or score > best_audio[0]:
                best_audio = (score, f)

        if best_audio:
            audio_format = best_audio[1]
            direct_url = audio_format["url"]
            headers = audio_format.get("http_headers") or {}
            ext = audio_format.get("ext", "audio")
            filename = f"{safe_name}.{ext}"

            # Set MIME type based on extension
            if ext in ["m4a", "mp4"]:
                mimetype = "audio/mp4"
            elif ext == "webm":
                mimetype = "audio/webm"
            elif ext == "opus":
                mimetype = "audio/ogg"
            else:
                mimetype = "application/octet-stream"

            logging.info(
                "Streaming best audio format %s from %s",
                audio_format.get("format_id"),
                direct_url,
            )

            def stream_direct():
                with requests.get(
                    direct_url, stream=True, headers=headers, timeout=10
                ) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            yield chunk

            disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
            return Response(
                stream_direct(),
                mimetype=mimetype,
                headers={"Content-Disposition": disposition},
            )

    if prog_format and prog_format.get("url"):
        # Stream the direct media URL with requests — usually fastest, no temporary merge.
        direct_url = prog_format["url"]
        headers = prog_format.get("http_headers") or {}
        logging.info(
            "Streaming direct format %s from %s",
            prog_format.get("format_id"),
            direct_url,
        )

        def stream_direct():
            # Stream the direct URL using requests
            with requests.get(
                direct_url, stream=True, headers=headers, timeout=10
            ) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        yield chunk

        # RFC5987 filename* header for UTF-8 filename
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(
            stream_direct(),
            mimetype=mimetype,
            headers={"Content-Disposition": disposition},
        )

    # No single-file progressive format found (likely segmented DASH/HLS requiring merge).
    # Fall back to streaming yt-dlp's stdout. This may cause yt-dlp to create temp files for merging.
    # Use format expression that prefers merging bestvideo+bestaudio but allows direct best if available.
    if fmt == "video":
        ytdlp_format_expr = "bestvideo+bestaudio/best"
        mimetype = "video/mp4"
        out_name = filename
        # Add flags to try to speed up fragment downloads
        cmd = [
            "yt-dlp",
            "-f",
            ytdlp_format_expr,
            "--no-playlist",
            "--no-warnings",
            "--no-part",
            "--concurrent-fragments",
            "16",
            "--fragment-retries",
            "5",
            "--buffer-size",
            "16M",
            "-o",
            "-",  # stream to stdout
            url,
        ]
    else:
        # Request best audio as a raw stream. Converting to mp3 requires ffmpeg merging and may create temp files.
        # To avoid extra work, we stream the best audio container (m4a/webm) and set mime accordingly.
        ytdlp_format_expr = "bestaudio/best"
        cmd = [
            "yt-dlp",
            "-f",
            ytdlp_format_expr,
            "--no-playlist",
            "--no-warnings",
            "--no-part",
            "--concurrent-fragments",
            "16",
            "--fragment-retries",
            "5",
            "--buffer-size",
            "16M",
            "-o",
            "-",
            url,
        ]
        # We may not actually be mp3; derive mime from info if possible
        # try to get ext of best audio format
        best_audio_ext = None
        for f in reversed(info.get("formats", [])):
            if f.get("acodec") != "none" and f.get("vcodec") == "none":
                best_audio_ext = f.get("ext")
                break
        if best_audio_ext == "m4a" or best_audio_ext == "mp4":
            mimetype = "audio/mp4"
            filename = f"{safe_name}.m4a"
        elif best_audio_ext == "webm":
            mimetype = "audio/webm"
            filename = f"{safe_name}.webm"
        else:
            mimetype = "application/octet-stream"

    logging.info(
        "Falling back to yt-dlp subprocess: %s", " ".join(shlex.quote(c) for c in cmd)
    )

    def generate_from_proc():
        # Start the yt-dlp process and stream stdout.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1024 * 1024 * 4
        )
        try:
            while True:
                chunk = proc.stdout.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            # ensure process cleanup
            try:
                proc.kill()
            except Exception:
                pass
            proc.wait()

    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        generate_from_proc(),
        mimetype=mimetype,
        headers={"Content-Disposition": disposition},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8989, threaded=True)
