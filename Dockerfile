ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM $BUILD_FROM

# Install dependencies for Pillow and native hardware tools
RUN apk add --no-cache \
  python3 py3-pip \
  gcc musl-dev python3-dev \
  linux-headers \
  jpeg-dev zlib-dev freetype-dev \
  ttf-dejavu

# Install Python packages
RUN pip3 install --break-system-packages --no-cache-dir Pillow spidev requests

COPY display.py /
COPY logo.png /
RUN chmod a+x /display.py

CMD [ "python3", "/display.py" ]