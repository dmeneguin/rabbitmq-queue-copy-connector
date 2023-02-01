FROM python:3.12-rc-bullseye
COPY app/app.py app.py
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
