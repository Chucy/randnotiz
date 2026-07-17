FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
ENV RANDNOTIZ_DB=/srv/data/randnotiz.db
VOLUME /srv/data
# Non-root: fester UID/GID, damit das Data-Volume auf dem Host passend gechownt werden kann.
RUN groupadd -g 10001 app && useradd -u 10001 -g 10001 -m app \
    && mkdir -p /srv/data && chown -R 10001:10001 /srv
USER app
EXPOSE 8000
# Kein curl im slim-Image — Python-stdlib-One-Liner reicht und bläht das Image nicht auf.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=2).status==200 else 1)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
