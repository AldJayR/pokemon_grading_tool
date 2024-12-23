#!/usr/bin/env bash
# exit on error
set -o errexit

apt-get update
apt-get install -y \
    libgstgl-1.0.so.0 \
    libgstcodecparsers-1.0.so.0 \
    libavif.so.15 \
    libenchant-2.so.2 \
    libsecret-1.so.0 \
    libmanette-0.2.so.0 \
    libGLESv2.so.2

pip install -r requirements.txt
playwright install
python manage.py collectstatic --no-input
python manage.py migrate