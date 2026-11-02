# Property Video Studio — production image
#
#   docker build -t property-video-studio .
#   docker run -p 8000:8000 \
#     -v apvg-config:/app/config \
#     -v apvg-uploads:/app/uploads \
#     -v apvg-music:/app/music_library \
#     -v apvg-videos:/app/generated_videos \
#     property-video-studio
#
# Serve HTTPS in front of this container (Caddy, nginx, Render, Fly, Railway...).
# The studio has no login: anyone with the URL can create a video. Set PORT to
# whatever your host routes to (Render and Railway inject it automatically).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# DejaVu is the renderer's font fallback on Linux. FFmpeg comes from the
# imageio-ffmpeg wheel, so no system FFmpeg package is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY . .
RUN mkdir -p uploads music_library generated_videos logs config

# One process, several threads: the render lock and job store live in memory.
EXPOSE 8000
CMD ["sh", "-c", "waitress-serve --listen=0.0.0.0:${PORT:-8000} --threads=4 wsgi:app"]
