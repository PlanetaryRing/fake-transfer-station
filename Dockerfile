FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY fake_transfer_station/ ./fake_transfer_station/
COPY config.example.yaml ./config.example.yaml
COPY contents/ ./contents/
EXPOSE 8317
CMD ["python", "-m", "fake_transfer_station"]
