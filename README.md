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

The superseded Fastify/React implementation is retained under `legacy_typescript/` for reference.
