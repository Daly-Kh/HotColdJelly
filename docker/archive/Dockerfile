FROM python:3.13-slim

# Disable Python output buffering for real-time logs
ENV PYTHONUNBUFFERED=1

# Install rclone
RUN apt-get update && apt-get install -y curl unzip cron && \
    curl -O https://downloads.rclone.org/rclone-current-linux-arm64.zip && \
    unzip rclone-current-linux-arm64.zip && \
    mv rclone-*/rclone /usr/local/bin/ && \
    chmod +x /usr/local/bin/rclone && \
    rm -rf rclone-* && \
    apt-get remove -y curl unzip && \
    apt-get autoremove -y && \
    apt-get clean

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules
COPY shared/config.py .
COPY shared/jellyfin.py .
COPY shared/logger.py .
COPY shared/stats.py .
COPY shared/storage.py .

# Copy archive-specific modules
COPY archive/movies.py .
COPY archive/shows.py .
COPY archive/display.py .
COPY archive/main.py .

# Cron — every Monday at 2am
RUN echo "0 2 * * 1 cd /app && /usr/local/bin/python3 main.py >> /logs/cron.log 2>&1" > /etc/cron.d/archive && \
    chmod 0644 /etc/cron.d/archive && \
    crontab /etc/cron.d/archive

CMD ["cron", "-f"]