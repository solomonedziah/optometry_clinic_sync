import { defineRailway, project, service } from "railway/iac";

// Last resort for a per-service CaC repo. Prefer one .railway file for the
// project and drop this if you later combine services into that file.
export const partial = "web";

export default defineRailway(() => {
  const web = service("web", {
    build: "pip install -r requirements.txt && python manage.py collectstatic --noinput",
    start: "python manage.py migrate && gunicorn clinic_sync.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120",
    healthcheck: "/health",
    healthcheckTimeout: 120,
    // builder from CaC: "NIXPACKS"
  });

  return project("web", {
    resources: [web],
  });
});
