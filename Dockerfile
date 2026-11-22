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

# Inside the container the process must listen on 0.0.0.0; publish it to the host
# loopback only (docker-compose maps 127.0.0.1:8765:8765).  The Host/Origin guard
# still requires Host: 127.0.0.1:8765, so a LAN client cannot use it, and the
# entrypoint's "single worker" rule is kept (--workers 1).
CMD ["python", "-m", "uvicorn", "facebook_backend.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
