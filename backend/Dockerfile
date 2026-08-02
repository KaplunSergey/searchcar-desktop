FROM mcr.microsoft.com/playwright/python:v1.54.0-noble
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
