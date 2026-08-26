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

Enrollment tokens are single-use and bind a device directly to a facility. Device access tokens include `facilityId`, which is the synchronization boundary.

The superseded Fastify/React implementation is retained under `legacy_typescript/` for reference.
