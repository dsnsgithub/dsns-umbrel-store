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

    # ====================================================================
    # NEW "FAST PATH" FOR AUDIO - Try this before anything else
    # ====================================================================
    if fmt != "video":
        # Try to get a direct, non-fragmented audio URL with `yt-dlp -g`
        # This is *much* faster than processing the full JSON if it works.
        # 'ba[protocol^=http]' = Best Audio that is a direct HTTP stream
        cmd_get_url = [
            "yt-dlp",
            "-g",
            "-f",
            "ba[protocol^=http]",
            "--no-playlist",
            "--no-warnings",
            "--no-part",
            "--fragment-retries",
            "3",
            url,
        ]
        logging.info(
            "Audio Request: Trying fast path with: %s",
            " ".join(shlex.quote(c) for c in cmd_get_url),
        )

        try:
            # Run with a short timeout. If it's slow, fall back.
            direct_url_output = (
                subprocess.check_output(cmd_get_url, stderr=subprocess.PIPE, timeout=8)
                .decode("utf-8")
                .strip()
            )

            # -g might return multiple URLs (e.g., for some formats).
            # We just want the first HTTP(S) URL.
            direct_url = next(
                (
                    line
                    for line in direct_url_output.splitlines()
                    if line.startswith("http")
                ),
                None,
            )

            if direct_url:
                logging.info("Audio Fast Path SUCCESS. Streaming from: %s", direct_url)

                def stream_direct_audio():
                    # Use requests to stream the URL we found
                    with requests.get(direct_url, stream=True, timeout=10) as r:
                        r.raise_for_status()
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                yield chunk

                # We have to guess title and mimetype since we skipped the -j call
                # This is a small price for speed.
                try:
                    # Quick subprocess to get *just* the title
                    title_cmd = [
                        "yt-dlp",
                        "--get-title",
                        "--no-playlist",
                        "--no-warnings",
                        url,
                    ]
                    title_bytes = subprocess.check_output(
                        title_cmd, stderr=subprocess.DEVNULL, timeout=5
                    )
                    title = title_bytes.decode("utf-8").strip()
                except Exception:
                    title = "audio_download"

                safe_name = sanitize_filename(title)

                # Guess mimetype from URL extension
                if ".webm" in direct_url:
                    mimetype = "audio/webm"
                    filename = f"{safe_name}.webm"
                elif ".m4a" in direct_url:
                    mimetype = "audio/mp4"
                    filename = f"{safe_name}.m4a"
                else:
                    mimetype = "audio/mpeg"
                    filename = f"{safe_name}.mp3"

                disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
                return Response(
                    stream_direct_audio(),
                    mimetype=mimetype,
                    headers={"Content-Disposition": disposition},
                )

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logging.warning(
                "Audio Fast Path (-g) failed. Falling back to full JSON method. Error: %s",
                e,
            )

    # ====================================================================
    # END OF NEW FAST PATH. Original logic follows.
    # ====================================================================

    # If we're here, it's either a video request OR the audio fast path failed.
    # Proceed with the original (slower) JSON-based method.
    try:
        info = yt_dlp_json(url)
    except subprocess.CalledProcessError as e:
        logging.exception("yt-dlp metadata extraction failed")
        return abort(500, "Failed to fetch metadata")

    title = info.get("title") or info.get("id") or "download"
    safe_name = sanitize_filename(title)

    prog_format = None
    if fmt == "video":
        filename = f"{safe_name}.mp4"
        mimetype = "video/mp4"
        # Try to find a single-file progressive format (audio+video)
        prog_format = choose_progressive_format(info)
    else:
        # Audio request, and the '-g' fast path failed.
        # Fall back to the JSON-based "find direct URL" method.
        filename = f"{safe_name}.mp3"
        mimetype = "audio/mpeg"

        audio_formats = []
        for f in info.get("formats", []):
            if f.get("acodec") != "none" and f.get("vcodec") == "none":
                score = f.get("abr") or 0
                audio_formats.append((score, f))

        audio_formats.sort(key=lambda x: x[0], reverse=True)

        for score, f in audio_formats:
            if f.get("url"):  # Find best-quality one that has a direct URL
                prog_format = f
                logging.info(
                    "Found direct streamable audio format in JSON: %s (abr=%s)",
                    f.get("format_id"),
                    score,
                )
                break

    # === Direct Stream (from JSON) ===
    if prog_format and prog_format.get("url"):
        direct_url = prog_format["url"]
        headers = prog_format.get("http_headers") or {}
        logging.info(
            "Streaming direct format %s from %s",
            prog_format.get("format_id"),
            direct_url,
        )

        def stream_direct_json():
            with requests.get(
                direct_url, stream=True, headers=headers, timeout=10
            ) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        yield chunk

        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(
            stream_direct_json(),
            mimetype=mimetype,
            headers={"Content-Disposition": disposition},
        )

    # === Fallback to Subprocess (Slowest) ===
    # If we are here, it means:
    # 1. It's a video request for a fragmented stream (like DASH)
    # 2. It's an audio request, and BOTH fast paths failed.
    logging.info(
        "No direct streamable URL found. Falling back to yt-dlp subprocess (slow)."
    )

    if fmt == "video":
        ytdlp_format_expr = "bestvideo+bestaudio/best"
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
    else:
        # Audio fallback: get the best possible, which will require a merge.
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

        # Try to set a better mimetype/filename for the fallback
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

    logging.info("Running slow fallback: %s", " ".join(shlex.quote(c) for c in cmd))

    def generate_from_proc():
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
