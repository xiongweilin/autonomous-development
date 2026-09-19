FROM cgr.dev/chainguard/python@sha256:dc0368ac6a4792f563e7f523d207f6c5ed17e3d7ed16e7424ac01d47772b096b AS builder

WORKDIR /build
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --target /build/site-packages \
    -r requirements.txt

FROM cgr.dev/chainguard/python@sha256:011e73b4e30e0fe9407a42b82a920b4fa13ebc0bf029a48b714f950df254ca20

WORKDIR /app
COPY --from=builder /build/site-packages /opt/site-packages
COPY app.py .

ENV PYTHONPATH=/opt/site-packages

EXPOSE 8000
CMD ["-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
