FROM --platform=linux/amd64 python:3.14.7-alpine3.24@sha256:4677924bcc0e94505a3270e87cb1601c2af54cd92d021b8dc306618a14333bbe AS builder

ARG ZLIB_FIX_COMMIT=df84af25dc1942490e1d1c899a07619152a46148

WORKDIR /build
COPY requirements.txt .
USER 0
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --target /build/site-packages \
    -r requirements.txt

RUN set -eux; \
    apk add --no-cache build-base ca-certificates curl; \
    curl -fsSL "https://github.com/madler/zlib/archive/${ZLIB_FIX_COMMIT}.tar.gz" \
        -o /tmp/zlib.tar.gz; \
    mkdir -p /tmp/zlib-src; \
    tar -xzf /tmp/zlib.tar.gz --strip-components=1 -C /tmp/zlib-src; \
    cd /tmp/zlib-src; \
    ./configure --prefix=/usr --libdir=/usr/lib --shared; \
    make -j "$(nproc)"; \
    make DESTDIR=/tmp/zlib-root install; \
    rm -rf /tmp/zlib-root/usr/include /tmp/zlib-root/usr/share; \
    rm -f /tmp/zlib-root/usr/lib/libz.a /tmp/zlib-root/usr/lib/pkgconfig/zlib.pc; \
    apk mkpkg --arch x86_64 \
        --info name:zlib \
        --info version:1.3.2.1-r0 \
        --info 'description:zlib with upstream fix for CVE-2026-85091' \
        --info 'url:https://github.com/madler/zlib' \
        --info license:Zlib \
        --info depends:so:libc.musl-x86_64.so.1 \
        --info provides:so:libz.so.1=1.3.2.1 \
        --info replaces:zlib \
        --files /tmp/zlib-root \
        --output /tmp/zlib-1.3.2.1-r0.apk

FROM --platform=linux/amd64 python:3.14.7-alpine3.24@sha256:4677924bcc0e94505a3270e87cb1601c2af54cd92d021b8dc306618a14333bbe

WORKDIR /app
COPY --from=builder /build/site-packages /opt/site-packages
COPY --from=builder /tmp/zlib-1.3.2.1-r0.apk /tmp/zlib-fixed.apk
COPY app.py .

RUN set -eux; \
    apk add --no-cache --allow-untrusted /tmp/zlib-fixed.apk; \
    apk info -a zlib | grep -F 'zlib-1.3.2.1-r0'; \
    python -c 'import zlib; assert "1.3.2.1" in zlib.ZLIB_RUNTIME_VERSION, zlib.ZLIB_RUNTIME_VERSION; print(zlib.ZLIB_RUNTIME_VERSION)'; \
    rm -f /tmp/zlib-fixed.apk

ENV PYTHONPATH=/opt/site-packages
USER 65532

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
