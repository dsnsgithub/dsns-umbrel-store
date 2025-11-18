import json
import logging
import subprocess
import re
from urllib.parse import quote
from flask import Flask, Response, abort, request
from werkzeug.utils import secure_filename

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Configure yt-dlp behavior
YTDLP_OPTS = [
    "yt-dlp",
    "--no-warnings",
    "--no-playlist",
    "--dump-json",
    # Use Android client to avoid throttling and age-gating
    "--extractor-args", "youtube:player_client=android", 
]

def sanitize_filename(title):
    if not title:
        return "download"
    fn = secure_filename(title)
    if not fn:
        fn = re.sub(r"[^\w\-_\. ]", "_", title)[:200]
    return fn

def get_metadata(url):
    try:
        cmd = YTDLP_OPTS + [url]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except subprocess.CalledProcessError:
        return None

def get_headers_list(headers_dict):
    """Convert JSON headers to FFmpeg -headers format"""
    header_str = ""
    if not headers_dict:
        return ""
    for k, v in headers_dict.items():
        header_str += f"{k}: {v}\r\n"
    return header_str

@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    fmt = request.form.get("format", "video")

    if not url:
        return abort(400, "Missing url")

    logging.info(f"Fetching metadata for: {url}")
    info = get_metadata(url)

    if not info:
        return abort(500, "Failed to fetch metadata")

    title = info.get("title", "video")
    clean_title = sanitize_filename(title)
    formats = info.get("formats", [])
    
    # ---------------------------------------------------------
    # AUDIO DOWNLOAD (Fast AAC Stream)
    # ---------------------------------------------------------
    if fmt == "audio":
        best_audio = None
        # Priority 1: M4A/AAC
        for f in formats:
            if f.get('ext') == 'm4a' and f.get('acodec') != 'none':
                if not best_audio or f.get('tbr', 0) > best_audio.get('tbr', 0):
                    best_audio = f
        
        # Priority 2: Any audio
        if not best_audio:
            best_audio = next((f for f in formats if f.get('acodec') != 'none'), None)

        if not best_audio:
            return abort(404, "No audio found")

        target_url = best_audio.get('url')
        headers = get_headers_list(best_audio.get('http_headers', {}))
        
        filename = f"{clean_title}.aac"
        mimetype = "audio/aac"

        cmd = [
            "ffmpeg",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-headers", headers,
            "-i", target_url,
            "-vn", "-c:a", "copy", "-f", "adts", "-"
        ]

    # ---------------------------------------------------------
    # VIDEO DOWNLOAD (High Quality Merge)
    # ---------------------------------------------------------
    else:
        # 1. Find Best Video (vcodec != none)
        video_streams = [f for f in formats if f.get('vcodec') != 'none']
        video_streams.sort(key=lambda x: x.get('tbr', 0) or 0, reverse=True)
        best_video = video_streams[0] if video_streams else None

        # 2. Find Best Audio (acodec != none)
        audio_streams = [f for f in formats if f.get('acodec') != 'none']
        audio_streams.sort(key=lambda x: x.get('tbr', 0) or 0, reverse=True)
        best_audio = audio_streams[0] if audio_streams else None

        if not best_video:
             return abort(404, "No video found")

        cmd = [
            "ffmpeg",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-headers", get_headers_list(best_video.get('http_headers', {})),
            "-i", best_video.get('url')
        ]

        # --- FIX APPLIED HERE: Use .get('format_id') instead of ['id'] ---
        has_separate_audio = False
        if best_audio:
            v_id = best_video.get('format_id')
            a_id = best_audio.get('format_id')
            # Only merge if they are actually different streams
            if v_id != a_id:
                has_separate_audio = True
                cmd.extend([
                    "-headers", get_headers_list(best_audio.get('http_headers', {})),
                    "-i", best_audio.get('url'),
                    "-map", "0:v", # Video from input 0
                    "-map", "1:a", # Audio from input 1
                ])

        if not has_separate_audio:
            # Just map the single input if we didn't add a second one
            cmd.extend(["-map", "0"])

        filename = f"{clean_title}.mkv"
        mimetype = "video/x-matroska"

        cmd.extend([
            "-c", "copy",       # No re-encoding
            "-f", "matroska",   # Streamable container
            "-"                 # Stdout
        ])

    # ---------------------------------------------------------
    # EXECUTE STREAM
    # ---------------------------------------------------------
    def stream_ffmpeg():
        proc = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.DEVNULL,
            bufsize=10**6
        )
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        except GeneratorExit:
            proc.terminate()
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()

    logging.info(f"Streaming: {filename}")
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    
    return Response(
        stream_ffmpeg(),
        mimetype=mimetype,
        headers={"Content-Disposition": disposition}
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8989, threaded=True)