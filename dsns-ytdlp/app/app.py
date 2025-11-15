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
    fn = secure_filename(title)
    if not fn:
        fn = re.sub(r"[^\w\-_\. ]", "_", title)[:200]
    return fn


def yt_dlp_json(url):
    """Run yt-dlp -j to get info JSON (no download). Returns dict or raise."""
    cmd = ["yt-dlp", "--no-warnings", "--no-playlist", "-j", url]
    logging.info("Running: %s", " ".join(shlex.quote(c) for c in cmd))
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    return json.loads(out)


def choose_progressive_format(info):
    """Return a format dict with both audio+video (single file)."""
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


def best_video_format(info):
    """Best video-only format with a direct url."""
    best = None
    for f in info.get("formats", []):
        if f.get("acodec") != "none" or f.get("vcodec") == "none":
            continue
        if f.get("protocol", "").startswith("m3u8"):
            continue
        score = (f.get("height") or 0) * 1_000_000 + (f.get("tbr") or 0)
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None


def best_audio_format(info):
    """Best audio-only format with a direct url."""
    best = None
    for f in info.get("formats", []):
        if f.get("vcodec") != "none" or f.get("acodec") == "none":
            continue
        score = f.get("abr") or 0
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None


def stream_url(url, headers=None, chunk_size=1024 * 1024):
    """Yield chunks from a direct media URL."""
    with requests.get(url, stream=True, headers=headers or {}, timeout=15) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk


def ffmpeg_merge(video_url, vheaders, audio_url, aheaders, chunk_size=1024 * 1024):
    """
    Merge a video-only and audio-only stream on-the-fly with ffmpeg.
    Output is MP4 (libx264 + aac).
    """
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", "pipe:0",           # video from stdin
        "-i", "pipe:1",           # audio from stdin (ffmpeg will request it)
        "-c:v", "copy",           # keep original video codec (usually already h264)
        "-c:a", "aac", "-b:a", "192k",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov",
        "pipe:1",                 # output to stdout
    ]

    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=chunk_size,
    )

    # Feed video stream into ffmpeg's stdin (pipe:0)
    def video_feeder():
        for chunk in stream_url(video_url, vheaders, chunk_size):
            proc.stdin.write(chunk)   # type: ignore
        proc.stdin.close()            # type: ignore

    # Start feeding video in a background thread
    from threading import Thread
    t = Thread(target=video_feeder, daemon=True)
    t.start()

    # ffmpeg will request the audio stream on its own (pipe:1) via HTTP
    # We just forward the request through ffmpeg's built-in downloader.
    # To make it work we add the audio URL as a second input with headers.
    # Unfortunately ffmpeg cannot read headers from a pipe, so we
    # launch a tiny helper that adds the headers and writes to pipe:1.

    def audio_feeder():
        with requests.get(audio_url, stream=True, headers=aheaders or {}, timeout=15) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=chunk_size):
                proc.stdin.write(chunk)   # type: ignore

    # ffmpeg expects the second input on its own; we give it the URL directly
    # and let ffmpeg handle the download (it respects http_headers if we
    # provide a concat demuxer – simpler approach: just let ffmpeg fetch it.
    # The following works because ffmpeg can open http(s) URLs itself.

    # Replace the two-pipe approach with a single ffmpeg call that pulls both URLs:
    ffmpeg_cmd = [
        "ffmpeg",
        "-headers", "\r\n".join(f"{k}: {v}" for k, v in (vheaders or {}).items()) + "\r\n",
        "-i", video_url,
        "-headers", "\r\n".join(f"{k}: {v}" for k, v in (aheaders or {}).items()) + "\r\n",
        "-i", audio_url,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov",
        "pipe:1",
    ]

    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=chunk_size,
    )

    while True:
        chunk = proc.stdout.read(chunk_size)  # type: ignore
        if not chunk:
            break
        yield chunk

    proc.wait()


@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    fmt = request.form.get("format", "video")
    if not url:
        return abort(400, "Missing url")

    try:
        info = yt_dlp_json(url)
    except subprocess.CalledProcessError:
        logging.exception("yt-dlp metadata extraction failed")
        return abort(500, "Failed to fetch metadata")

    title = info.get("title") or info.get("id") or "download"
    safe_name = sanitize_filename(title)

    # ------------------------------------------------------------------
    # AUDIO PATH – always direct requests stream (no conversion)
    # ------------------------------------------------------------------
    if fmt != "video":
        audio_fmt = best_audio_format(info)
        if not audio_fmt or not audio_fmt.get("url"):
            return abort(500, "No suitable audio format found")

        direct_url = audio_fmt["url"]
        headers = audio_fmt.get("http_headers") or {}
        ext = audio_fmt.get("ext", "m4a")
        filename = f"{safe_name}.{ext}"

        if ext in ("m4a", "mp4"):
            mimetype = "audio/mp4"
        elif ext == "webm":
            mimetype = "audio/webm"
        elif ext == "opus":
            mimetype = "audio/ogg"
        else:
            mimetype = "application/octet-stream"

        logging.info("Streaming audio %s → %s", audio_fmt.get("format_id"), direct_url)

        def stream():
            yield from stream_url(direct_url, headers)

        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(
            stream(),
            mimetype=mimetype,
            headers={"Content-Disposition": disposition},
        )

    # ------------------------------------------------------------------
    # VIDEO PATH – always requests stream (progressive or merged)
    # ------------------------------------------------------------------
    filename = f"{safe_name}.mp4"
    mimetype = "video/mp4"

    # 1. Try a single-file progressive format
    prog = choose_progressive_format(info)
    if prog and prog.get("url"):
        direct_url = prog["url"]
        headers = prog.get("http_headers") or {}
        logging.info("Streaming progressive %s → %s", prog.get("format_id"), direct_url)

        def stream():
            yield from stream_url(direct_url, headers)

        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(
            stream(),
            mimetype=mimetype,
            headers={"Content-Disposition": disposition},
        )

    # 2. No progressive → merge best video + best audio on the fly
    vfmt = best_video_format(info)
    afmt = best_audio_format(info)
    if not vfmt or not afmt or not vfmt.get("url") or not afmt.get("url"):
        return abort(500, "Cannot find separate video/audio streams to merge")

    logging.info(
        "Merging video %s + audio %s via ffmpeg",
        vfmt.get("format_id"),
        afmt.get("format_id"),
    )

    def merged_stream():
        yield from ffmpeg_merge(
            vfmt["url"], vfmt.get("http_headers"),
            afmt["url"], afmt.get("http_headers")
        )

    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        merged_stream(),
        mimetype=mimetype,
        headers={"Content-Disposition": disposition},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8989, threaded=True)