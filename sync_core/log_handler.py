"""Persist server log records so they can be read on the System Logs page.

Attach ``DatabaseLogHandler`` in settings.LOGGING. Callers add structured detail with
``extra={"device": device, "facility": facility, "context": {...}}``; Django's own
``django.request`` / ``django.security`` records carry ``request`` and ``status_code``,
which are unpacked here. Writing never raises: a logging failure must not break a request.
"""

import logging
import threading
import traceback
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

_state = threading.local()
_PRUNE_EVERY = 500
_writes_since_prune = 0


def _request_details(request):
	if request is None:
		return {}
	meta = getattr(request, "META", {})
	forwarded = meta.get("HTTP_X_FORWARDED_FOR", "")
	user = getattr(request, "user", None)
	device = getattr(request, "sync_device", None)
	return {
		"method": getattr(request, "method", "") or "",
		# get_host() raises for a disallowed host, which is exactly what we may be logging.
		"path": (getattr(request, "path", "") or "")[:300],
		"host": meta.get("HTTP_HOST", "")[:200],
		"client_ip": (forwarded.split(",")[0].strip() or meta.get("REMOTE_ADDR", ""))[:64],
		"username": user.get_username() if getattr(user, "is_authenticated", False) else "",
		"device": device,
	}


class DatabaseLogHandler(logging.Handler):
	def emit(self, record):
		if getattr(_state, "busy", False):
			return
		_state.busy = True
		try:
			self._write(record)
		except Exception:
			# Fall back to stderr through the standard mechanism; never propagate.
			self.handleError(record)
		finally:
			_state.busy = False

	def _write(self, record):
		from .models import SystemLog

		request = getattr(record, "request", None)
		details = _request_details(request) if hasattr(request, "META") else {}
		device = getattr(record, "device", None) or details.get("device")
		facility = getattr(record, "facility", None) or (device.facility if device is not None else None)
		context = getattr(record, "context", None)
		trace = ""
		if record.exc_info:
			trace = "".join(traceback.format_exception(*record.exc_info))
		with transaction.atomic():
			SystemLog.objects.create(
				level=record.levelno,
				level_name=record.levelname,
				source=record.name[:120],
				message=record.getMessage()[:10000],
				method=details.get("method", ""),
				path=details.get("path", ""),
				host=details.get("host", ""),
				status_code=getattr(record, "status_code", None),
				client_ip=details.get("client_ip", ""),
				username=details.get("username", ""),
				facility=facility,
				device=device,
				context=context if isinstance(context, dict) else {},
				traceback=trace[:50000],
			)
		_maybe_prune()


def _maybe_prune():
	global _writes_since_prune
	_writes_since_prune += 1
	if _writes_since_prune < _PRUNE_EVERY:
		return
	_writes_since_prune = 0
	from .models import SystemLog

	cutoff = timezone.now() - timedelta(days=getattr(settings, "SYSTEM_LOG_RETENTION_DAYS", 30))
	SystemLog.objects.filter(created_at__lt=cutoff).delete()
