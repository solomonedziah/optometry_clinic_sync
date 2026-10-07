from django.conf import settings

SYNC_URL_HEADER = "X-Clinic-Sync-Url"


class SyncAddressMiddleware:
	"""Advertise the official sync address on every device-facing response.

	Listed first in MIDDLEWARE so it also wraps the 400 Django returns for a host
	that is no longer accepted: a Main PC still pointed at an old address learns
	the new one from that response.
	"""

	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		response = self.get_response(request)
		if settings.SYNC_PUBLIC_URL and (request.path.startswith("/api/") or request.path == "/health"):
			response[SYNC_URL_HEADER] = settings.SYNC_PUBLIC_URL
		return response
