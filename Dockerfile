FROM python:3.14-alpine
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
ENV RANDNOTIZ_DB=/srv/data/randnotiz.db \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
VOLUME /srv/data
# Non-root: fixed UID/GID so the host data volume can be chowned to match.
RUN addgroup -g 10001 app && adduser -u 10001 -G app -D -H app \
    && mkdir -p /srv/data && chown -R 10001:10001 /srv
USER app
EXPOSE 8000
# No curl in the minimal image — a Python stdlib one-liner is enough and adds no bloat.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=2).status==200 else 1)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
