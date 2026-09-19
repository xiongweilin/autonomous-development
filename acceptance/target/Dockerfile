FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal AS builder

RUN dnf -y install python3.14 python3.14-pip \
    && dnf clean all

WORKDIR /build
COPY requirements.txt .
RUN python3.14 -m pip install --no-cache-dir --disable-pip-version-check \
    --target /build/site-packages \
    -r requirements.txt

FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal

RUN dnf -y install python3.14 \
    && dnf clean all \
    && rm -rf /var/cache/dnf

WORKDIR /app
COPY --from=builder /build/site-packages /opt/site-packages
COPY app.py .

ENV PYTHONPATH=/opt/site-packages
USER 65532

EXPOSE 8000
CMD ["python3.14", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
