import os
import re
import subprocess
import tempfile

from flask import Flask, request, send_file

app = Flask(__name__)


def sanitize_filename(filename):
    """Remove/replace characters that are invalid in filenames"""
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)
    filename = re.sub(r"\s+", " ", filename)
    filename = filename.strip(". ")
    if len(filename) > 200:
        filename = filename[:200]
    return filename or "download"


def get_video_title(url):
    """Get video title using yt-dlp"""
    try:
        cmd = ["yt-dlp", "--get-title", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            title = result.stdout.strip()
            return sanitize_filename(title)
    except Exception as e:
        print(f"Error getting title: {e}")
    return "download"


@app.route("/download", methods=["POST"])
def download():
    url = request.form["url"]
    fmt = request.form["format"]
    title = get_video_title(url)

    if fmt == "video":
        ext = "mp4"
        cmd = [
            "yt-dlp",
            "--external-downloader",
            "aria2c",
            "--external-downloader-args",
            "aria2c:-x 16 -s 16 -k 1M",
            "-f",
            "bestvideo+bestaudio/best",
        ]
    else:
        ext = "mp3"
        cmd = [
            "yt-dlp",
            "--external-downloader",
            "aria2c",
            "--external-downloader-args",
            "aria2c:-x 16 -s 16 -k 1M",
            "-x",
            "--audio-format",
            "mp3",
        ]

    # Create a temporary file for yt-dlp to write into
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmpfile:
        temp_path = tmpfile.name

    cmd += ["-o", temp_path, url]

    # Run yt-dlp + aria2c
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        os.remove(temp_path)
        return f"Download failed: {e}", 500

    # Stream the file to client
    response = send_file(temp_path, as_attachment=True, download_name=f"{title}.{ext}")

    # Clean up temp file after response is sent
    @response.call_on_close
    def cleanup():
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8989, threaded=True)
