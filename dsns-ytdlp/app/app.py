import re
import subprocess

from flask import Flask, Response, request

app = Flask(__name__)


def sanitize_filename(filename):
    """Remove/replace characters that are invalid in filenames"""
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)
    # Replace multiple spaces with single space
    filename = re.sub(r"\s+", " ", filename)
    # Trim spaces and dots from start/end
    filename = filename.strip(". ")
    # Limit length to 200 characters
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

    # Get the video title
    title = get_video_title(url)

    if fmt == "video":
        # Best video + audio merged, stream to stdout with aria2c
        cmd = [
            "yt-dlp",
            "--external-downloader",
            "aria2c",
            "--external-downloader-args",
            "aria2c:-x 16 -s 16 -k 1M",
            "-f",
            "bestvideo+bestaudio/best",
            "--no-part",
            "-o",
            "-",
            url,
        ]
        mimetype = "video/mp4"
        filename = f"{title}.mp4"
    else:
        # Extract audio to MP3, stream to stdout with aria2c
        cmd = [
            "yt-dlp",
            "--external-downloader",
            "aria2c",
            "--external-downloader-args",
            "aria2c:-x 16 -s 16 -k 1M",
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            "-",
            url,
        ]
        mimetype = "audio/mpeg"
        filename = f"{title}.mp3"

    def generate():
        # Run yt-dlp as subprocess, stream stdout in chunks
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1024 * 1024
        )
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            yield chunk
        proc.wait()  # Ensure process cleanup

    # Stream the response with attachment headers
    return Response(
        generate(),
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8989, threaded=True)
