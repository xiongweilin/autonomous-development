FROM python:3.14.7-alpine3.24 AS builder

WORKDIR /build
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --target /build/site-packages \
    -r requirements.txt

FROM dhi.io/python:3.14.7-alpine3.24@sha256:7b9528fefc51c9d3753ccd25dcf1dea9dd327e7c2f8bef89cd2cf3b34f847408

WORKDIR /app
COPY --from=builder /build/site-packages /opt/site-packages
COPY app.py .

ENV PYTHONPATH=/opt/site-packages
USER 65532

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
