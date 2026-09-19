FROM python:3.14.7-slim-trixie AS builder

WORKDIR /build
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --target /build/site-packages \
    -r requirements.txt

FROM dhi.io/python:3.14.7-debian13@sha256:71245bfb85e3632288e5f9816dd9fb809e65b10e1e20f25bedf4d1a92b0a4ddd

WORKDIR /app
COPY --from=builder /build/site-packages /opt/site-packages
COPY app.py .

ENV PYTHONPATH=/opt/site-packages
USER 65532

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
