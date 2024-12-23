#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt
playwright install --with-deps
python manage.py collectstatic --no-input
python manage.py migrate