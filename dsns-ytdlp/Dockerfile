FROM python:3.12-slim

# Install dependencies: ffmpeg for processing, yt-dlp and Flask via pip
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir flask yt-dlp

# Copy the app code
COPY app.py /app/app.py

# Set working directory
WORKDIR /app

# Expose the port
EXPOSE 8081

# Run the Flask app
CMD ["python", "app.py"]