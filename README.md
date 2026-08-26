# Optometry Clinic Sync

Django monolith for administering institutions, facilities, and facility-scoped Main PC enrollment. Django templates provide the management UI and Django REST Framework provides the Electron device API.

## Local development

```bash
.venv/Scripts/python.exe -m pip install -r requirements.txt
npm run dev:db
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py createsuperuser
.venv/Scripts/python.exe manage.py runserver 4000
```

Open <http://127.0.0.1:4000/>. PostgreSQL data is persisted in `.local-postgres/`.

## Device API

- `GET /health`
- `POST /api/devices/enroll`
- `POST /api/devices/authenticate`
- `GET /api/devices/me`
- `PUT /api/lfs/objects/<sha256>`
- `GET /api/lfs/objects/<sha256>`

Enrollment tokens are single-use and bind a device directly to a facility. Device access tokens include `facilityId`, which is the synchronization boundary.

Large files use content-addressed storage and are deduplicated by SHA-256. Their bytes are kept out of sync event JSON; events carry lightweight LFS pointers. Set `CLINIC_LFS_ROOT` to a persistent mounted volume in production and optionally set `CLINIC_LFS_MAX_BYTES` (default: 100 MB). A Railway deployment must mount a volume at the configured root because the normal container filesystem is ephemeral.

## Railway deployment

1. Create a Railway project and deploy this GitHub repository as a service.
2. Add a Railway PostgreSQL service and expose its `DATABASE_URL` to the web service.
3. Add a persistent volume mounted at `/data`.
4. Set these web-service variables:

```text
DJANGO_SECRET_KEY=<long-random-value>
DEVICE_JWT_SECRET=<long-random-value>
ADMIN_API_KEY=<long-random-value>
DJANGO_DEBUG=false
CLINIC_LFS_ROOT=/data/clinic-lfs
CLINIC_LFS_MAX_BYTES=104857600
```

Railway reads [railway.toml](railway.toml), collects static assets during the build, applies migrations on startup, and runs Gunicorn on Railway's assigned `PORT`. Generate a public domain for the web service; `RAILWAY_PUBLIC_DOMAIN` is trusted automatically. For a custom domain, add it to `DJANGO_ALLOWED_HOSTS` and add its full HTTPS origin to `DJANGO_CSRF_TRUSTED_ORIGINS`.

After the first successful deployment, create the dashboard administrator with `railway run python manage.py createsuperuser` from a linked local checkout.

The superseded Fastify/React implementation is retained under `legacy_typescript/` for reference.
