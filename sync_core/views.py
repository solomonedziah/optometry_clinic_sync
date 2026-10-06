import json
import hashlib
import os
import re
import tempfile
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .conflicts import (
	PLATFORM_SOURCE_ID,
	USER_ENTITY,
	apply_password_everywhere,
	credential_from_payload,
	is_older_credential,
	password_fingerprint,
	password_versions,
	record_conflict,
	resolve_conflict,
	settle_resent_conflict,
	staff_accounts,
	unwrap_payload,
)
from .forms import DeviceEnrollmentForm, FacilityForm, InstitutionForm
from .data_catalog import DATA_TABLE_MAP, DATA_TABLES
from .models import (
	ConflictResolution,
	ConflictStatus,
	DeviceStatus,
	Facility,
	FacilityDataRecord,
	FacilitySyncConflict,
	FacilitySyncEntity,
	FacilitySyncEvent,
	Institution,
	LargeFileObject,
	LargeFileReference,
)
from .serializers import (
	AuthenticateDeviceSerializer,
	EnrollDeviceSerializer,
	RepositoryBatchSerializer,
	SyncPullSerializer,
	SyncPushSerializer,
)
from .services import (
	ACCESS_TOKEN_MAX_AGE,
	authenticate_device,
	create_pending_device,
	device_from_access_token,
	enroll_device,
	issue_access_token,
	project_sync_event,
)


def device_payload(device):
	return {
		"id": str(device.id),
		"facilityId": str(device.facility_id),
		"installationId": str(device.installation_id) if device.installation_id else None,
		"deviceName": device.device_name,
		"locationName": device.location_name or None,
		"platform": device.platform or None,
		"appVersion": device.app_version or None,
		"status": device.status,
		"enrolledAt": device.enrolled_at.isoformat() if device.enrolled_at else None,
		"lastSeenAt": device.last_seen_at.isoformat() if device.last_seen_at else None,
		"createdAt": device.created_at.isoformat(),
		"updatedAt": device.updated_at.isoformat(),
	}


def _authenticated_device(request):
	authorization = request.headers.get("Authorization", "")
	if not authorization.startswith("Bearer "):
		return None
	return device_from_access_token(authorization.removeprefix("Bearer ").strip())


def _large_file_key(oid):
	return Path("objects") / oid[:2] / oid[2:4] / oid


def _large_file_path(oid):
	return Path(settings.CLINIC_LFS_ROOT) / _large_file_key(oid)


def _large_file_pointer(file_object, reference):
	return {
		"oid": file_object.oid,
		"size": file_object.size_bytes,
		"contentType": file_object.content_type,
		"fileName": reference.file_name,
		"referenceId": str(reference.id),
	}


@login_required
@require_http_methods(["GET", "POST"])
def institution_list(request):
	form = InstitutionForm(request.POST or None)
	if request.method == "POST" and form.is_valid():
		institution = form.save()
		messages.success(request, f"{institution.name} was created.")
		return redirect("institution-detail", institution_id=institution.id)
	return render(request, "sync_core/institution_list.html", {
		"institutions": Institution.objects.all(),
		"form": form,
	})


@login_required
@require_http_methods(["GET", "POST"])
def institution_detail(request, institution_id):
	institution = get_object_or_404(Institution, id=institution_id)
	form = FacilityForm(request.POST or None)
	if request.method == "POST" and form.is_valid():
		facility = form.save(commit=False)
		facility.institution = institution
		facility.save()
		messages.success(request, f"{facility.name} was created.")
		return redirect("facility-detail", institution_id=institution.id, facility_id=facility.id)
	return render(request, "sync_core/institution_detail.html", {
		"institution": institution,
		"facilities": institution.facilities.all(),
		"form": form,
	})


@login_required
@require_http_methods(["GET", "POST"])
def facility_detail(request, institution_id, facility_id):
	facility = get_object_or_404(Facility, id=facility_id, institution_id=institution_id)
	form = DeviceEnrollmentForm(request.POST or None)
	enrollment = request.session.pop("enrollment", None)
	if request.method == "POST" and form.is_valid():
		device, token, expires_at = create_pending_device(facility, **form.cleaned_data)
		request.session["enrollment"] = {
			"device_name": device.device_name,
			"token": token,
			"expires_at": expires_at.isoformat(),
		}
		return redirect("facility-detail", institution_id=institution_id, facility_id=facility.id)
	return render(request, "sync_core/facility_detail.html", {
		"institution": facility.institution,
		"facility": facility,
		"devices": facility.devices.all(),
		"open_conflict_count": FacilitySyncConflict.objects.filter(facility=facility, status=ConflictStatus.OPEN).count(),
		"form": form,
		"enrollment": enrollment,
		"cloud_service_url": request.build_absolute_uri("/").rstrip("/"),
	})


def _facility_for_institution(institution_id, facility_id):
	return get_object_or_404(Facility.objects.select_related("institution"), id=facility_id, institution_id=institution_id)


def _is_sensitive(key):
	return any(marker in key.lower() for marker in ("password", "secret", "token", "credential", "private_key"))


def _mask_payload(payload):
	return {key: "••••••••" if _is_sensitive(key) else value for key, value in payload.items()}


def _display_value(value):
	if value is None or value == "":
		return "—"
	if isinstance(value, (dict, list)):
		return json.dumps(value, ensure_ascii=True, sort_keys=True)
	if isinstance(value, bool):
		return "Yes" if value else "No"
	return str(value)


@login_required
@require_http_methods(["GET"])
def facility_data_browser(request, institution_id, facility_id):
	facility = _facility_for_institution(institution_id, facility_id)
	counts = {
		row["table_name"]: row["count"]
		for row in FacilityDataRecord.objects.filter(facility=facility, deleted=False)
		.values("table_name")
		.annotate(count=Count("id"))
	}
	groups = []
	for table in DATA_TABLES:
		if not groups or groups[-1]["name"] != table.group:
			groups.append({"name": table.group, "tables": []})
		groups[-1]["tables"].append({"name": table.name, "label": table.label, "count": counts.get(table.name, 0)})
	return render(request, "sync_core/facility_data_browser.html", {
		"institution": facility.institution,
		"facility": facility,
		"groups": groups,
		"total_records": sum(counts.values()),
	})


@login_required
@require_http_methods(["GET"])
def facility_data_table(request, institution_id, facility_id, table_name):
	facility = _facility_for_institution(institution_id, facility_id)
	table = DATA_TABLE_MAP.get(table_name)
	if table is None:
		raise Http404("Unknown application table.")

	queryset = FacilityDataRecord.objects.filter(facility=facility, table_name=table_name, deleted=False)
	page = Paginator(queryset, 50).get_page(request.GET.get("page"))
	masked_payloads = [_mask_payload(record.payload) for record in page.object_list]
	columns = sorted({key for payload in masked_payloads for key in payload})[:20]
	rows = []
	for record, payload in zip(page.object_list, masked_payloads, strict=True):
		rows.append({
			"record_id": record.record_id,
			"version": record.version,
			"updated_at": record.source_updated_at or record.received_at,
			"cells": [_display_value(payload.get(column)) for column in columns],
		})
	return render(request, "sync_core/facility_data_table.html", {
		"institution": facility.institution,
		"facility": facility,
		"table": table,
		"columns": columns,
		"rows": rows,
		"page": page,
	})


def _comparison_rows(entity_type, server_payload, device_payload):
	server = unwrap_payload(entity_type, server_payload)
	device = unwrap_payload(entity_type, device_payload)
	rows = []
	for key in sorted(set(server) | set(device)):
		server_value, device_value = server.get(key), device.get(key)
		if key == "passwordHash":
			shown = (password_fingerprint(server_value) or "—", password_fingerprint(device_value) or "—")
		elif _is_sensitive(key):
			shown = ("••••••••" if server_value else "—", "••••••••" if device_value else "—")
		else:
			shown = (_display_value(server_value), _display_value(device_value))
		rows.append({
			"field": key,
			"server": shown[0],
			"device": shown[1],
			"differs": key in server and key in device and server_value != device_value,
			"only_server": key not in device,
			"only_device": key not in server,
		})
	return rows


@login_required
@require_http_methods(["GET"])
def facility_conflicts(request, institution_id, facility_id):
	facility = _facility_for_institution(institution_id, facility_id)
	conflicts = FacilitySyncConflict.objects.filter(facility=facility).select_related("device", "canonical_device")
	accounts, duplicates = staff_accounts(facility)
	return render(request, "sync_core/facility_conflicts.html", {
		"institution": facility.institution,
		"facility": facility,
		"open_conflicts": conflicts.filter(status=ConflictStatus.OPEN),
		"settled_conflicts": conflicts.exclude(status=ConflictStatus.OPEN).order_by("-resolved_at")[:25],
		"accounts": accounts,
		"drifting_accounts": [account for account in accounts if account["passwords_differ"]],
		"duplicates": duplicates,
	})


@login_required
@require_http_methods(["GET", "POST"])
def facility_conflict_detail(request, institution_id, facility_id, conflict_id):
	facility = _facility_for_institution(institution_id, facility_id)
	conflict = get_object_or_404(
		FacilitySyncConflict.objects.select_related("device", "canonical_device", "resolved_by"),
		pk=conflict_id, facility=facility,
	)
	if request.method == "POST":
		try:
			resolve_conflict(conflict, choice=request.POST.get("choice"), user=request.user)
		except (ValueError, FacilitySyncEntity.DoesNotExist) as error:
			messages.error(request, str(error))
		else:
			messages.success(request, "Conflict resolved. Main PCs pick up the decision on their next sync.")
		return redirect("facility-conflict-detail", institution_id=institution_id, facility_id=facility_id, conflict_id=conflict.pk)
	is_password = credential_from_payload(conflict.entity_type, conflict.incoming_payload) is not None
	return render(request, "sync_core/facility_conflict_detail.html", {
		"institution": facility.institution,
		"facility": facility,
		"conflict": conflict,
		"rows": _comparison_rows(conflict.entity_type, conflict.canonical_payload, conflict.incoming_payload),
		"is_password": is_password,
		"is_user": conflict.entity_type == USER_ENTITY,
	})


@login_required
@require_http_methods(["GET", "POST"])
def facility_account_detail(request, institution_id, facility_id, public_id):
	facility = _facility_for_institution(institution_id, facility_id)
	if not FacilitySyncEntity.objects.filter(facility=facility, entity_type=USER_ENTITY, entity_public_id=public_id).exists():
		raise Http404("Unknown staff account.")
	if request.method == "POST":
		try:
			_event, chosen = apply_password_everywhere(facility, public_id, request.POST.get("version"), user=request.user)
		except ValueError as error:
			messages.error(request, str(error))
		else:
			messages.success(request, f"Password {chosen['fingerprint']} sent to every Main PC. It applies on each machine's next sync.")
		return redirect("facility-account-detail", institution_id=institution_id, facility_id=facility_id, public_id=public_id)
	accounts, _duplicates = staff_accounts(facility)
	account = next((item for item in accounts if item["public_id"] == public_id), None)
	versions = password_versions(facility, public_id)
	current = next((version for version in versions if version["accepted"]), None)
	return render(request, "sync_core/facility_account_detail.html", {
		"institution": facility.institution,
		"facility": facility,
		"account": account,
		"versions": versions,
		"current": current,
		"conflicts": FacilitySyncConflict.objects.filter(
			facility=facility, entity_type=USER_ENTITY, entity_public_id=public_id,
		).select_related("device"),
	})


@api_view(["GET"])
def health(request):
	return Response({"status": "ok"})


@api_view(["POST"])
def enroll_device_api(request):
	serializer = EnrollDeviceSerializer(data=request.data)
	serializer.is_valid(raise_exception=True)
	data = serializer.validated_data
	try:
		device, client_key, client_secret = enroll_device(
			token=data["token"],
			installation_id=data["installationId"],
			platform=data["platform"],
			app_version=data["appVersion"],
		)
	except ValueError as error:
		if str(error) == "INSTALLATION_ALREADY_BOUND":
			return Response({"error": "This installation is already registered to another device."}, status=status.HTTP_409_CONFLICT)
		return Response({"error": "Enrollment token is invalid, expired, or already used."}, status=status.HTTP_400_BAD_REQUEST)

	return Response({
		"institution": {"id": str(device.facility.institution_id), "name": device.facility.institution.name},
		"facility": {"id": str(device.facility_id), "name": device.facility.name},
		"device": device_payload(device),
		"credentials": {"clientKey": client_key, "clientSecret": client_secret},
		"accessToken": issue_access_token(device),
		"expiresIn": ACCESS_TOKEN_MAX_AGE,
		"institutionId": str(device.facility.institution_id),
		"facilityId": str(device.facility_id),
		"nodeId": str(device.id),
	}, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def authenticate_device_api(request):
	serializer = AuthenticateDeviceSerializer(data=request.data)
	serializer.is_valid(raise_exception=True)
	device = authenticate_device(
		client_key=serializer.validated_data["clientKey"],
		client_secret=serializer.validated_data["clientSecret"],
	)
	if device is None:
		return Response({"error": "Invalid device credentials."}, status=status.HTTP_401_UNAUTHORIZED)
	return Response({"accessToken": issue_access_token(device), "expiresIn": ACCESS_TOKEN_MAX_AGE})


@api_view(["GET"])
def device_me_api(request):
	device = _authenticated_device(request)
	if device is None or device.status != DeviceStatus.ENROLLED:
		return Response({"error": "Device authentication required."}, status=status.HTTP_401_UNAUTHORIZED)
	return Response({"device": device_payload(device)})


@api_view(["PUT", "GET"])
def large_file_object_api(request, oid):
	device = _authenticated_device(request)
	if device is None or device.status != DeviceStatus.ENROLLED:
		return Response({"error": "Device authentication required."}, status=status.HTTP_401_UNAUTHORIZED)
	if not re.fullmatch(r"[0-9a-f]{64}", oid):
		return Response({"error": "A lowercase SHA-256 object ID is required."}, status=status.HTTP_400_BAD_REQUEST)

	if request.method == "GET":
		reference = LargeFileReference.objects.select_related("file_object").filter(
			facility=device.facility,
			file_object_id=oid,
		).first()
		if reference is None:
			return Response({"error": "Large file object was not found."}, status=status.HTTP_404_NOT_FOUND)
		object_path = Path(settings.CLINIC_LFS_ROOT) / reference.file_object.storage_path
		if not object_path.is_file():
			return Response({"error": "Large file content is unavailable."}, status=status.HTTP_410_GONE)
		response = FileResponse(object_path.open("rb"), content_type=reference.file_object.content_type)
		response["Content-Length"] = reference.file_object.size_bytes
		response["Content-Disposition"] = f'attachment; filename="{Path(reference.file_name).name}"'
		response["X-Clinic-Oid"] = oid
		return response

	entity_type = request.headers.get("X-Clinic-Entity-Type", "").strip()
	entity_public_id = request.headers.get("X-Clinic-Entity-Id", "").strip()
	file_name = Path(request.headers.get("X-Clinic-File-Name", "file")).name[:255]
	content_type = request.headers.get("Content-Type", "application/octet-stream")[:160]
	if not entity_type or len(entity_type) > 100 or not entity_public_id or len(entity_public_id) > 160:
		return Response({"error": "Large file entity metadata is required."}, status=status.HTTP_400_BAD_REQUEST)

	declared_size = request.headers.get("Content-Length")
	if declared_size:
		try:
			if int(declared_size) > settings.CLINIC_LFS_MAX_BYTES:
				return Response({"error": "Large file exceeds the configured size limit."}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
		except ValueError:
			return Response({"error": "Content-Length must be an integer."}, status=status.HTTP_400_BAD_REQUEST)

	root = Path(settings.CLINIC_LFS_ROOT)
	temporary_directory = root / "tmp"
	temporary_directory.mkdir(parents=True, exist_ok=True)
	digest = hashlib.sha256()
	size_bytes = 0
	temporary_path = None
	try:
		with tempfile.NamedTemporaryFile(dir=temporary_directory, delete=False) as temporary_file:
			temporary_path = Path(temporary_file.name)
			while chunk := request._request.read(1024 * 1024):
				size_bytes += len(chunk)
				if size_bytes > settings.CLINIC_LFS_MAX_BYTES:
					return Response({"error": "Large file exceeds the configured size limit."}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
				digest.update(chunk)
				temporary_file.write(chunk)
		if digest.hexdigest() != oid:
			return Response({"error": "Large file SHA-256 does not match its object ID."}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

		object_path = _large_file_path(oid)
		object_path.parent.mkdir(parents=True, exist_ok=True)
		deduplicated = object_path.exists()
		if not deduplicated:
			os.replace(temporary_path, object_path)
			temporary_path = None
		with transaction.atomic():
			file_object, created = LargeFileObject.objects.get_or_create(
				oid=oid,
				defaults={
					"size_bytes": size_bytes,
					"content_type": content_type,
					"storage_path": _large_file_key(oid).as_posix(),
				},
			)
			if file_object.size_bytes != size_bytes:
				return Response({"error": "Large file size conflicts with the existing object."}, status=status.HTTP_409_CONFLICT)
			reference, _ = LargeFileReference.objects.get_or_create(
				facility=device.facility,
				source_device=device,
				file_object=file_object,
				entity_type=entity_type,
				entity_public_id=entity_public_id,
				defaults={"file_name": file_name},
			)
		return Response({
			"uploaded": created,
			"deduplicated": deduplicated or not created,
			"asset": _large_file_pointer(file_object, reference),
		}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
	finally:
		if temporary_path is not None:
			temporary_path.unlink(missing_ok=True)


@api_view(["POST"])
def sync_push_api(request):
	device = _authenticated_device(request)
	if device is None or device.status != DeviceStatus.ENROLLED:
		return Response({"error": "Device authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

	serializer = SyncPushSerializer(data=request.data)
	serializer.is_valid(raise_exception=True)
	accepted_event_ids = []
	conflicts = []
	for incoming in serializer.validated_data["events"]:
		with transaction.atomic():
			existing_event = FacilitySyncEvent.objects.filter(event_id=incoming["eventId"]).first()
			if existing_event:
				if existing_event.facility_id != device.facility_id:
					return Response({"error": "Event ID belongs to another facility."}, status=status.HTTP_409_CONFLICT)
				project_sync_event(existing_event)
				accepted_event_ids.append(str(existing_event.event_id))
				continue

			entity, _ = FacilitySyncEntity.objects.select_for_update().get_or_create(
				facility=device.facility,
				entity_type=incoming["entityType"],
				entity_public_id=incoming["entityPublicId"],
				defaults={"source_device": device},
			)
			if is_older_credential(device.facility, incoming):
				# Acknowledge without applying: the device will pull the newer password.
				record_conflict(device, incoming, entity, resolution=ConflictResolution.OLDER_CREDENTIAL_IGNORED)
				accepted_event_ids.append(str(incoming["eventId"]))
				continue
			if entity.version != incoming["baseVersion"]:
				is_equivalent = (
					entity.version == incoming["entityVersion"]
					and entity.payload == incoming["payload"]
					and entity.deleted == (incoming["operation"] == "delete")
				)
				if is_equivalent:
					accepted_event_ids.append(str(incoming["eventId"]))
					continue
				record_conflict(device, incoming, entity)
				conflicts.append({
					"eventId": str(incoming["eventId"]),
					"entityType": incoming["entityType"],
					"entityPublicId": incoming["entityPublicId"],
					"localBaseVersion": incoming["baseVersion"],
					"canonicalVersion": entity.version,
					"canonicalPayload": entity.payload,
					"canonicalDeleted": entity.deleted,
				})
				continue

			event = FacilitySyncEvent.objects.create(
				facility=device.facility,
				source_device=device,
				event_id=incoming["eventId"],
				entity_type=incoming["entityType"],
				entity_public_id=incoming["entityPublicId"],
				operation=incoming["operation"],
				base_version=incoming["baseVersion"],
				entity_version=incoming["entityVersion"],
				payload=incoming["payload"],
			)
			entity.version = incoming["entityVersion"]
			entity.payload = incoming["payload"]
			entity.deleted = incoming["operation"] == "delete"
			entity.source_device = device
			entity.save()
			project_sync_event(event)
			settle_resent_conflict(event.event_id)
			accepted_event_ids.append(str(event.event_id))

	return Response({"acceptedEventIds": accepted_event_ids, "conflicts": conflicts})


@api_view(["POST"])
def sync_pull_api(request):
	device = _authenticated_device(request)
	if device is None or device.status != DeviceStatus.ENROLLED:
		return Response({"error": "Device authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

	serializer = SyncPullSerializer(data=request.data)
	serializer.is_valid(raise_exception=True)
	after = serializer.validated_data["after"]
	limit = serializer.validated_data["limit"]
	events = list(
		FacilitySyncEvent.objects.filter(facility=device.facility, sequence__gt=after)
		.select_related("source_device")
		.order_by("sequence")[:limit]
	)
	return Response({
		"events": [{
			"sequence": event.sequence,
			"eventId": str(event.event_id),
			"sourceDeviceId": str(event.source_device_id) if event.source_device_id else PLATFORM_SOURCE_ID,
			"entityType": event.entity_type,
			"entityPublicId": event.entity_public_id,
			"operation": event.operation,
			"baseVersion": event.base_version,
			"entityVersion": event.entity_version,
			"payload": event.payload,
			"createdAt": event.created_at.isoformat(),
		} for event in events],
		"latestSequence": events[-1].sequence if events else after,
	})


@api_view(["POST"])
def repository_records_api(request):
	device = _authenticated_device(request)
	if device is None or device.status != DeviceStatus.ENROLLED:
		return Response({"error": "Device authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

	serializer = RepositoryBatchSerializer(data=request.data)
	serializer.is_valid(raise_exception=True)
	records = serializer.validated_data["records"]
	unknown_tables = sorted({record["tableName"] for record in records if record["tableName"] not in DATA_TABLE_MAP})
	if unknown_tables:
		return Response({"error": "Unknown application table.", "tables": unknown_tables}, status=status.HTTP_400_BAD_REQUEST)

	accepted = 0
	skipped = 0
	with transaction.atomic():
		for incoming in records:
			record, created = FacilityDataRecord.objects.select_for_update().get_or_create(
				facility=device.facility,
				table_name=incoming["tableName"],
				record_id=incoming["recordId"],
				defaults={
					"source_device": device,
					"payload": incoming["payload"],
					"version": incoming["version"],
					"deleted": incoming["deleted"],
					"source_updated_at": incoming.get("sourceUpdatedAt"),
				},
			)
			if not created and incoming["version"] < record.version:
				skipped += 1
				continue
			if not created:
				record.source_device = device
				record.payload = incoming["payload"]
				record.version = incoming["version"]
				record.deleted = incoming["deleted"]
				record.source_updated_at = incoming.get("sourceUpdatedAt")
				record.save()
			accepted += 1
	return Response({"accepted": accepted, "skipped": skipped})
