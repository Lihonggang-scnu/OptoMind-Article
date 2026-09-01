# OptoMind-Article live research console.
#
# Build from the repository root:
#   docker build -t optomind-article .
# Run with a persistent output volume and provider keys supplied at runtime.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    OPTOMIND_HOST=0.0.0.0 \
    OPTOMIND_OUTPUT_ROOT=/data/runs \
    PORT=8080

WORKDIR /app

COPY code/requirements-runtime.txt /tmp/requirements-runtime.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements-runtime.txt \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin optomind \
    && mkdir -p /data/runs \
    && chown -R optomind:optomind /data

# Keep the live harness and the exact VeriTMM source tree side by side. The
# runner deliberately resolves the sibling /app/veritmm tree first so the
# console uses the same engine layout as the checked-in six-run records.
COPY --chown=optomind:optomind code /app/code
COPY --chown=optomind:optomind veritmm /app/veritmm

USER optomind
WORKDIR /app/code

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/healthz', timeout=4).read()"

CMD ["python", "-u", "scripts/run_research_console.py", "--no-open"]
