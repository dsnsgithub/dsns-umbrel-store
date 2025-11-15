from flask import Flask, request, Response, abort
import subprocess
import json
import shlex
import requests
import re
from urllib.parse import quote
from werkzeug.utils import secure_filename
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# --- Helpers ---

def sanitize_filename(title, fallback='download'):
    """Make a safe filename."""
    if not title:
        return fallback
    fn = secure_filename(title)
    if not fn:
        fn = re.sub(r'[^\w\-_\. ]', '_', title)[:200]
    return fn

def yt_dlp_json(url):
    """Get video/audio info as JSON."""
    cmd = ['yt-dlp', '--no-warnings', '--no-playlist', '-j', url]
    logging.info("Running: %s", " ".join(shlex.quote(c) for c in cmd))
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    return json.loads(out)

def choose_best_format(info, only_audio=False):
    """Pick the best format (audio-only if requested)."""
    best = None
    for f in info.get('formats', []):
        if only_audio:
            if f.get('vcodec') != 'none':
                continue
            score = f.get('abr') or 0
        else:
            if f.get('vcodec') == 'none' or f.get('acodec') == 'none':
                continue
            # score by resolution + bitrate
            score = (f.get('height') or 0) * 1000000 + (f.get('tbr') or 0)
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None

def get_mime_type(ext):
    """Map container extension to MIME type."""
    return {
        'mp4': 'video/mp4',
        'm4a': 'audio/mp4',
        'webm': 'video/webm',
        'opus': 'audio/ogg',
        'mp3': 'audio/mpeg',
    }.get(ext, 'application/octet-stream')

def stream_url(url, headers=None, chunk_size=4*1024*1024):
    """Stream a direct media URL using requests."""
    headers = headers or {}
    headers.setdefault('Accept-Encoding', 'identity')
    with requests.get(url, stream=True, headers=headers, timeout=10) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk

# --- Flask route ---

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    fmt = request.form.get('format', 'video').lower()
    if not url:
        return abort(400, "Missing url")

    try:
        info = yt_dlp_json(url)
    except subprocess.CalledProcessError:
        logging.exception("yt-dlp metadata extraction failed")
        return abort(500, "Failed to fetch metadata")

    title = info.get('title') or info.get('id') or 'download'
    safe_name = sanitize_filename(title)

    # choose format
    only_audio = fmt != 'video'
    best = choose_best_format(info, only_audio=only_audio)
    if not best or not best.get('url'):
        return abort(500, "No suitable format found")

    direct_url = best['url']
    headers = best.get('http_headers') or {}
    ext = best.get('ext') or ('mp4' if not only_audio else 'm4a')
    filename = f"{safe_name}.{ext}"
    mimetype = get_mime_type(ext)

    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(stream_url(direct_url, headers=headers),
                    mimetype=mimetype,
                    headers={'Content-Disposition': disposition})

# --- Run server ---

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8989, threaded=True)
