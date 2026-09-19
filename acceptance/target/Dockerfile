FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:68b1e82cd69ade271c092bc1959fa6fc820945d0c8dbb7a88cf6c1824a184688 AS builder

RUN dnf -y install python3.14 python3.14-pip \
    && dnf -y --installroot=/runtime --releasever=2023 \
      --setopt=install_weak_deps=0 \
      install python3.14 \
    && dnf clean all

WORKDIR /build
COPY requirements.txt .
RUN python3.14 -m pip install --no-cache-dir --disable-pip-version-check \
    --target /runtime/opt/site-packages \
    -r requirements.txt \
    && mkdir -p /runtime/app \
    && rm -rf /runtime/var/cache/dnf

COPY app.py /runtime/app/app.py

FROM scratch

COPY --from=builder /runtime /

WORKDIR /app
ENV PYTHONPATH=/opt/site-packages
USER 65532

EXPOSE 8000
CMD ["/usr/bin/python3.14", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
