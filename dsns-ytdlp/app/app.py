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
    # Allow unicode but strip dangerous chars
    fn = secure_filename(title)
    if not fn:
        # Fallback for non-ascii titles
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

    # Filter formats from JSON
    formats = info.get("formats", [])
    
    # ---------------------------------------------------------
    # LOGIC: AUDIO DOWNLOAD (Fast AAC Stream)
    # ---------------------------------------------------------
    if fmt == "audio":
        # Look for best m4a (AAC) audio
        # yt-dlp format 140 is usually the best AAC audio
        best_audio = None
        for f in formats:
            if f.get('ext') == 'm4a' and f.get('acodec') != 'none':
                if not best_audio or f.get('tbr', 0) > best_audio.get('tbr', 0):
                    best_audio = f
        
        if not best_audio:
             # Fallback to any audio
            best_audio = next((f for f in formats if f.get('acodec') != 'none'), None)

        if not best_audio:
            return abort(404, "No audio found")

        target_url = best_audio['url']
        headers = get_headers_list(best_audio.get('http_headers', {}))
        
        # We stream as .aac (ADTS) because it is streamable. 
        # .m4a (MP4 container) requires the MOOV atom at the end, making it bad for streaming.
        filename = f"{clean_title}.aac"
        mimetype = "audio/aac"

        # FFmpeg command to copy stream without re-encoding (Very Fast)
        cmd = [
            "ffmpeg",
            "-reconnect", "1", 
            "-reconnect_streamed", "1", 
            "-reconnect_delay_max", "5",
            "-headers", headers,
            "-i", target_url,
            "-vn",              # No video
            "-c:a", "copy",     # Direct copy (no CPU usage)
            "-f", "adts",       # Streamable AAC format
            "-"                 # Output to stdout
        ]

    # ---------------------------------------------------------
    # LOGIC: VIDEO DOWNLOAD (High Quality Merge)
    # ---------------------------------------------------------
    else:
        # 1. Find Best Video (prefer 1080p/4k)
        # We look for video-only streams usually (vcodec!=none, acodec=none)
        video_streams = [f for f in formats if f.get('vcodec') != 'none']
        # Sort by bitrate (tbr) descending
        video_streams.sort(key=lambda x: x.get('tbr', 0) or 0, reverse=True)
        best_video = video_streams[0] if video_streams else None

        # 2. Find Best Audio
        audio_streams = [f for f in formats if f.get('acodec') != 'none']
        audio_streams.sort(key=lambda x: x.get('tbr', 0) or 0, reverse=True)
        best_audio = audio_streams[0] if audio_streams else None

        if not best_video:
             return abort(404, "No video found")

        # Prepare FFmpeg Inputs
        cmd = [
            "ffmpeg",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-headers", get_headers_list(best_video.get('http_headers', {})),
            "-i", best_video['url']
        ]

        # If we have separate audio, add it as second input
        if best_audio and best_audio['id'] != best_video['id']:
            cmd.extend([
                "-headers", get_headers_list(best_audio.get('http_headers', {})),
                "-i", best_audio['url'],
                "-map", "0:v", # Use video from first input
                "-map", "1:a", # Use audio from second input
            ])
        else:
            # Fallback: Single file found (rarely happens for high quality)
            cmd.extend(["-map", "0"])

        # 3. OUTPUT CONFIGURATION
        # We MUST use Matroska (MKV) for streaming. 
        # MP4 cannot be streamed reliably while merging (requires seeking to write header).
        filename = f"{clean_title}.mkv"
        mimetype = "video/x-matroska"

        cmd.extend([
            "-c", "copy",       # Copy streams directly (Zero CPU re-encoding)
            "-f", "matroska",   # Streamable container
            "-"                 # Pipe to stdout
        ])

    # ---------------------------------------------------------
    # EXECUTE STREAM
    # ---------------------------------------------------------
    def stream_ffmpeg():
        # Start FFmpeg process
        proc = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.DEVNULL, # Hide FFmpeg logs
            bufsize=10**6 # Large buffer for smooth network
        )
        
        try:
            # Yield data as it comes in
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

    logging.info(f"Streaming ({fmt}): {filename}")
    
    # Modern browsers handle Content-Disposition better with UTF-8
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    
    return Response(
        stream_ffmpeg(),
        mimetype=mimetype,
        headers={"Content-Disposition": disposition}
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8989, threaded=True)