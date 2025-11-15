from flask import Flask, request, Response, render_template_string
import subprocess

app = Flask(__name__)

@app.route('/')
def index():
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>YT-DLP Stream</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 600px; margin: 40px auto; }
        form { display: flex; flex-direction: column; gap: 10px; }
        input, select, button { padding: 10px; font-size: 16px; }
        button { background: #007bff; color: white; border: none; cursor: pointer; }
    </style>
</head>
<body>
    <h1>YT-DLP Stream</h1>
    <p>Enter a video URL and choose format. The content will stream directly as a download—no files saved on the server.</p>
    <form method="post" action="/download">
        <input type="text" name="url" placeholder="Video URL (e.g., https://youtube.com/watch?v=...)" required>
        <select name="format">
            <option value="video">Best Video (MP4)</option>
            <option value="audio">Best Audio (MP3)</option>
        </select>
        <button type="submit">Stream & Download</button>
    </form>
</body>
</html>
''')

@app.route('/download', methods=['POST'])
def download():
    url = request.form['url']
    fmt = request.form['format']

    if fmt == 'video':
        # Best video + audio merged, stream to stdout
        cmd = ['yt-dlp', '-f', 'bestvideo+bestaudio/best', '--no-part', '-o', '-', url]
        mimetype = 'video/mp4'
        filename = 'video.mp4'
    else:
        # Extract audio to MP3, stream to stdout
        cmd = ['yt-dlp', '-x', '--audio-format', 'mp3', '-o', '-', url]
        mimetype = 'audio/mpeg'
        filename = 'audio.mp3'

    def generate():
        # Run yt-dlp as subprocess, stream stdout in chunks
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1024*1024)
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            yield chunk
        proc.wait()  # Ensure process cleanup

    # Stream the response with attachment headers
    return Response(generate(), mimetype=mimetype, headers={
        'Content-Disposition': f'attachment; filename={filename}'
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081, threaded=True)