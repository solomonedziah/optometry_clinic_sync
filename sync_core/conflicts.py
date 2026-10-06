"""Recording and resolving sync conflicts.

The push API is first-writer-wins: an event built on an outdated version is rejected.
This module keeps those rejections so an administrator can review them, and issues
platform-originated events that carry a decision back to every Main PC.

Password changes need special care. Desktop clients apply an incoming ``userCredential``
event whenever its uuid7 event id is newer than the last one they saw, regardless of
version counters, and re-send their own rejected credential event on top of the server
version. Two consequences follow:

* a credential event issued here with a fresh uuid7 reaches every machine, so it is a
  reliable way to make one password win everywhere;
* a re-sent credential that is *older* than the one the server already holds must not
  be applied, or machines swap passwords. ``is_older_credential`` detects that case.
"""

import hashlib
import os
import time
import uuid

from django.db import transaction
from django.utils import timezone

from .data_catalog import SYNC_ENTITY_PAYLOAD_KEYS
from .models import (
	ConflictResolution,
	ConflictStatus,
	FacilitySyncConflict,
	FacilitySyncEntity,
	FacilitySyncEvent,
)
from .services import project_sync_event

USER_ENTITY = "user"
CREDENTIAL_KEY = "userCredential"
PLATFORM_SOURCE_ID = "clinic-sync-platform"


def uuid7():
	"""Time-ordered UUID (RFC 9562), matching the ids desktop clients generate."""
	value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80
	value |= int.from_bytes(os.urandom(10), "big") & ((1 << 80) - 1)
	value = (value & ~(0xF << 76)) | (0x7 << 76)
	value = (value & ~(0x3 << 62)) | (0x2 << 62)
	return uuid.UUID(int=value)


def _is_uuid7(value):
	try:
		return uuid.UUID(str(value)).version == 7
	except ValueError:
		return False


def credential_from_payload(entity_type, payload):
	"""Return {passwordHash, mustChangePassword} if this user payload sets a password."""
	if entity_type != USER_ENTITY or not isinstance(payload, dict):
		return None
	for key in (CREDENTIAL_KEY, "user"):
		body = payload.get(key)
		if isinstance(body, dict) and body.get("passwordHash"):
			return {"passwordHash": body["passwordHash"], "mustChangePassword": bool(body.get("mustChangePassword"))}
	return None


def password_fingerprint(password_hash):
	"""A short label that tells two password hashes apart without revealing either."""
	if not password_hash:
		return None
	if password_hash.startswith("$2"):
		scheme = "bcrypt"
	elif password_hash.startswith("pbkdf2_sha256$"):
		scheme = "pbkdf2"
	else:
		scheme = "hash"
	return f"{scheme} · {hashlib.sha256(password_hash.encode('utf-8')).hexdigest()[:8]}"


def unwrap_payload(entity_type, payload):
	"""Strip the client's single-key envelope ({"user": {...}}) for display."""
	if not isinstance(payload, dict):
		return {}
	for key in (SYNC_ENTITY_PAYLOAD_KEYS.get(entity_type), CREDENTIAL_KEY):
		if key and isinstance(payload.get(key), dict) and len(payload) == 1:
			return payload[key]
	return payload


def latest_credential_event(facility, public_id):
	events = FacilitySyncEvent.objects.filter(
		facility=facility, entity_type=USER_ENTITY, entity_public_id=public_id,
	).order_by("-sequence")
	for event in events.iterator():
		if credential_from_payload(USER_ENTITY, event.payload):
			return event
	return None


def is_older_credential(facility, incoming):
	"""True when a pushed password change predates the one the server already applied.

	Only password-only events qualify: those are what clients re-send on top after a
	rejection, and they carry nothing else that acknowledging-without-applying would drop.
	"""
	if incoming["entityType"] != USER_ENTITY or not isinstance(incoming["payload"].get(CREDENTIAL_KEY), dict):
		return False
	latest = latest_credential_event(facility, incoming["entityPublicId"])
	if latest is None or not (_is_uuid7(incoming["eventId"]) and _is_uuid7(latest.event_id)):
		return False
	return str(incoming["eventId"]) < str(latest.event_id)


def record_conflict(device, incoming, entity, *, resolution=""):
	conflict, created = FacilitySyncConflict.objects.get_or_create(
		event_id=incoming["eventId"],
		defaults={
			"facility": device.facility,
			"device": device,
			"entity_type": incoming["entityType"],
			"entity_public_id": incoming["entityPublicId"],
			"operation": incoming["operation"],
			"base_version": incoming["baseVersion"],
			"incoming_payload": incoming["payload"],
			"canonical_version": entity.version,
			"canonical_payload": entity.payload,
			"canonical_deleted": entity.deleted,
			"canonical_device": entity.source_device,
		},
	)
	if not created:
		conflict.attempts += 1
		conflict.base_version = incoming["baseVersion"]
		conflict.incoming_payload = incoming["payload"]
		conflict.canonical_version = entity.version
		conflict.canonical_payload = entity.payload
		conflict.canonical_deleted = entity.deleted
		conflict.canonical_device = entity.source_device
	if resolution and conflict.status == ConflictStatus.OPEN:
		conflict.status = ConflictStatus.AUTO_RESOLVED
		conflict.resolution = resolution
		conflict.resolved_at = timezone.now()
	conflict.save()
	return conflict


def settle_resent_conflict(event_id):
	"""A device re-sent a rejected event on top of the server version and it was accepted."""
	FacilitySyncConflict.objects.filter(event_id=event_id, status=ConflictStatus.OPEN).update(
		status=ConflictStatus.AUTO_RESOLVED,
		resolution=ConflictResolution.DEVICE_RESENT,
		resolved_at=timezone.now(),
		updated_at=timezone.now(),
	)


def _issue_platform_event(entity, operation, payload):
	event = FacilitySyncEvent.objects.create(
		facility=entity.facility,
		source_device=None,
		event_id=uuid7(),
		entity_type=entity.entity_type,
		entity_public_id=entity.entity_public_id,
		operation=operation,
		base_version=entity.version,
		entity_version=entity.version + 1,
		payload=payload,
	)
	entity.version = event.entity_version
	entity.payload = payload
	entity.deleted = operation == "delete"
	entity.source_device = None
	entity.save()
	project_sync_event(event)
	return event


def _credential_event_payload(public_id, credential):
	return {CREDENTIAL_KEY: {"publicId": public_id, **credential}}


def _locked_entity(conflict):
	return FacilitySyncEntity.objects.select_for_update().get(
		facility=conflict.facility,
		entity_type=conflict.entity_type,
		entity_public_id=conflict.entity_public_id,
	)


@transaction.atomic
def resolve_conflict(conflict, *, choice, user):
	"""Apply an administrator's decision. ``choice`` is "device" or "server"."""
	conflict = FacilitySyncConflict.objects.select_for_update().get(pk=conflict.pk)
	if conflict.status != ConflictStatus.OPEN:
		raise ValueError("This conflict has already been settled.")
	entity = _locked_entity(conflict)
	event = None
	if choice == "device":
		credential = credential_from_payload(conflict.entity_type, conflict.incoming_payload)
		if credential and CREDENTIAL_KEY in conflict.incoming_payload:
			payload = _credential_event_payload(conflict.entity_public_id, credential)
		else:
			payload = conflict.incoming_payload
		event = _issue_platform_event(entity, conflict.operation, payload)
		conflict.resolution = ConflictResolution.APPLIED_DEVICE
	elif choice == "server":
		# Machines that drifted on a password only converge if they receive a fresh,
		# newer credential event; re-broadcast the one the server holds.
		latest = latest_credential_event(conflict.facility, conflict.entity_public_id) if conflict.entity_type == USER_ENTITY else None
		if latest is not None and credential_from_payload(conflict.entity_type, conflict.incoming_payload):
			credential = credential_from_payload(USER_ENTITY, latest.payload)
			event = _issue_platform_event(entity, "update", _credential_event_payload(conflict.entity_public_id, credential))
		conflict.resolution = ConflictResolution.KEPT_SERVER
	else:
		raise ValueError("Unknown resolution.")
	conflict.status = ConflictStatus.RESOLVED
	conflict.resolution_event_id = event.event_id if event else None
	conflict.resolved_by = user
	conflict.resolved_at = timezone.now()
	conflict.save()
	return conflict


def password_versions(facility, public_id):
	"""Every password a machine has sent for this account, accepted or rejected, newest first."""
	versions = []
	events = FacilitySyncEvent.objects.filter(
		facility=facility, entity_type=USER_ENTITY, entity_public_id=public_id,
	).select_related("source_device")
	for event in events:
		credential = credential_from_payload(USER_ENTITY, event.payload)
		if credential:
			versions.append({
				"key": f"event:{event.sequence}",
				"credential": credential,
				"fingerprint": password_fingerprint(credential["passwordHash"]),
				"device": event.source_device,
				"from_platform": event.source_device_id is None,
				"at": event.created_at,
				"accepted": True,
				"version": event.entity_version,
			})
	conflicts = FacilitySyncConflict.objects.filter(
		facility=facility, entity_type=USER_ENTITY, entity_public_id=public_id,
	).select_related("device")
	for conflict in conflicts:
		credential = credential_from_payload(USER_ENTITY, conflict.incoming_payload)
		if credential:
			versions.append({
				"key": f"conflict:{conflict.pk}",
				"credential": credential,
				"fingerprint": password_fingerprint(credential["passwordHash"]),
				"device": conflict.device,
				"from_platform": False,
				"at": conflict.created_at,
				"accepted": False,
				"conflict": conflict,
				"version": None,
			})
	versions.sort(key=lambda version: version["at"], reverse=True)
	return versions


@transaction.atomic
def apply_password_everywhere(facility, public_id, version_key, *, user):
	"""Send one chosen password to every Main PC and close this account's password conflicts."""
	chosen = next((v for v in password_versions(facility, public_id) if v["key"] == version_key), None)
	if chosen is None:
		raise ValueError("That password version no longer exists.")
	entity = FacilitySyncEntity.objects.select_for_update().get(
		facility=facility, entity_type=USER_ENTITY, entity_public_id=public_id,
	)
	event = _issue_platform_event(entity, "update", _credential_event_payload(public_id, chosen["credential"]))
	now = timezone.now()
	for conflict in FacilitySyncConflict.objects.select_for_update().filter(
		facility=facility, entity_type=USER_ENTITY, entity_public_id=public_id, status=ConflictStatus.OPEN,
	):
		if credential_from_payload(USER_ENTITY, conflict.incoming_payload) is None:
			continue
		conflict.status = ConflictStatus.RESOLVED
		conflict.resolution = (
			ConflictResolution.APPLIED_DEVICE if version_key == f"conflict:{conflict.pk}" else ConflictResolution.KEPT_SERVER
		)
		conflict.resolution_event_id = event.event_id
		conflict.resolved_by = user
		conflict.resolved_at = now
		conflict.save()
	return event, chosen


def staff_accounts(facility):
	"""Summarise synced user accounts, flagging password drift and duplicate usernames."""
	accounts = {}
	events = FacilitySyncEvent.objects.filter(facility=facility, entity_type=USER_ENTITY).order_by("sequence")
	for event in events.iterator():
		account = accounts.setdefault(event.entity_public_id, {"public_id": event.entity_public_id, "profile": {}, "device_passwords": {}})
		snapshot = event.payload.get("user") if isinstance(event.payload, dict) else None
		if isinstance(snapshot, dict):
			account["profile"] = snapshot
	for public_id, account in accounts.items():
		latest_by_device = {}
		for version in password_versions(facility, public_id):
			if version["from_platform"] or version["device"] is None:
				continue
			latest_by_device.setdefault(version["device"].pk, version)
		account["device_passwords"] = list(latest_by_device.values())
		account["password_count"] = len({v["fingerprint"] for v in latest_by_device.values()})
		account["passwords_differ"] = account["password_count"] > 1
		account["username"] = account["profile"].get("username") or ""
		account["name"] = " ".join(filter(None, [account["profile"].get("firstName"), account["profile"].get("lastName")]))
		account["open_conflicts"] = FacilitySyncConflict.objects.filter(
			facility=facility, entity_type=USER_ENTITY, entity_public_id=public_id, status=ConflictStatus.OPEN,
		).count()

	by_username = {}
	for account in accounts.values():
		if account["username"]:
			by_username.setdefault(account["username"].strip().lower(), []).append(account)
	duplicates = [group for group in by_username.values() if len(group) > 1]
	for group in duplicates:
		for account in group:
			account["duplicate"] = True
	ordered = sorted(accounts.values(), key=lambda a: (not a["passwords_differ"], not a.get("duplicate"), a["username"].lower()))
	return ordered, duplicates
