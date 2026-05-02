FROM python:3.13-slim

# Disable Python output buffering for real-time logs
ENV PYTHONUNBUFFERED=1

# Install rclone
RUN apt-get update && apt-get install -y curl unzip && \
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
COPY shared/storage.py .

# Copy listener-specific modules
COPY listener/recall.py .
COPY listener/listener.py .

EXPOSE 5001

CMD ["python3", "listener.py"]