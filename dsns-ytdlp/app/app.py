from flask import Flask, request, Response
import subprocess

app = Flask(__name__)

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
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1024*1024 * 4)
        while True:
            chunk = proc.stdout.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
        proc.wait()  # Ensure process cleanup

    # Stream the response with attachment headers
    return Response(generate(), mimetype=mimetype, headers={
        'Content-Disposition': f'attachment; filename={filename}'
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8989, threaded=True)