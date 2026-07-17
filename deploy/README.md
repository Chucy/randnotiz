# Deploying Randnotiz

Randnotiz ships as a single container that listens on `127.0.0.1:8300` and expects a
TLS-terminating reverse proxy in front of it. This directory contains an example nginx
site + Let's Encrypt setup. Adapt the domain and paths to your environment.

> All example values (`your-domain.example.com`) are placeholders — replace them.
> Your real secrets live in `.env` (see `.env.example`) and are never committed.

## 1. Start the app

```bash
cp .env.example .env      # set RANDNOTIZ_ADMIN_KEY (e.g. openssl rand -hex 32)
docker compose up -d --build
```

The container binds to `127.0.0.1:8300` only — it is never exposed directly.
Verify it is healthy:

```bash
docker ps                                   # STATUS should show "(healthy)"
curl -s http://127.0.0.1:8300/healthz       # {"status":"ok"}
```

## 2. Reverse proxy (nginx)

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/your-domain.example.com
# edit the file: replace your-domain.example.com everywhere
sudo ln -s ../sites-available/your-domain.example.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 3. TLS certificate (Let's Encrypt)

```bash
sudo certbot --nginx -d your-domain.example.com --agree-tos -m you@example.com \
    --non-interactive --redirect
```

certbot installs a renewal timer automatically. The example nginx config already
serves the ACME HTTP-01 challenge from `/var/www/certbot` and redirects HTTP→HTTPS.

## 4. Backups

Schedule `python -m app.backup` (e.g. nightly cron) — it writes a consistent
`data/randnotiz-snapshot.db` via SQLite's online-backup API plus an integrity check.
**Restore from the snapshot, never from the live `.db`.** Keep the database on a local
disk, not on an NFS/CIFS mount (file locking).
