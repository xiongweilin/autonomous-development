FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:68b1e82cd69ade271c092bc1959fa6fc820945d0c8dbb7a88cf6c1824a184688 AS builder

RUN dnf -y install \
      python3.14-3.14.7-1.amzn2023.0.1 \
      python3.14-pip-26.1.1-1.amzn2023.0.3 \
    && dnf clean all \
    && rm -rf /var/cache/dnf

WORKDIR /build
COPY requirements.txt .
RUN python3.14 -m pip install --no-cache-dir --disable-pip-version-check \
    --target /build/site-packages \
    -r requirements.txt

FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:68b1e82cd69ade271c092bc1959fa6fc820945d0c8dbb7a88cf6c1824a184688

RUN dnf -y install \
      python3.14-3.14.7-1.amzn2023.0.1 \
    && dnf clean all \
    && rm -rf /var/cache/dnf

WORKDIR /app
COPY --from=builder /build/site-packages /opt/site-packages
COPY app.py .

ENV PYTHONPATH=/opt/site-packages
USER 65532

EXPOSE 8000
CMD ["python3.14", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
