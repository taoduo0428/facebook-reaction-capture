# Backend-only image: HTTP + database + scheduler.
#
# It deliberately contains NO ADB, NO Android SDK and NO emulator: the capture
# executor (PowerShell on Windows, Python on macOS) must run natively on the
# host where the device is attached.  See docs/backend-modernization/DEPLOYMENT.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FACEBOOK_API_LOAD_LOCAL_CONFIG=0 \
    FACEBOOK_LOG_LEVEL=INFO

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY facebook_backend ./facebook_backend
COPY facebook_capture_api.py facebook_http_security.py facebook_share_contract.py facebook_local.py ./
COPY web ./web
COPY alembic.ini ./

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=3).status==200 else 1)"

# The image has no ADB, Android SDK or emulator, so it must not own execution:
# a recovered capture process could never be verified from in here.  The
# control-plane entrypoint serves reads/health/metrics and refuses submissions
# with an explicit 503; native installs keep running ``python -m facebook_backend``.
# It listens on 0.0.0.0 inside the container and compose publishes it to host
# loopback only.  One worker is a hard rule of the app, so no --workers flag.
CMD ["python", "-m", "facebook_backend.container"]
