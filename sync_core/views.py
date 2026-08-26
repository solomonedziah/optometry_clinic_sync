import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .forms import DeviceEnrollmentForm, FacilityForm, InstitutionForm
from .data_catalog import DATA_TABLE_MAP, DATA_TABLES
from .models import DeviceStatus, Facility, FacilityDataRecord, FacilitySyncEntity, FacilitySyncEvent, Institution
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
		"form": form,
		"enrollment": enrollment,
	})


def _facility_for_institution(institution_id, facility_id):
	return get_object_or_404(Facility.objects.select_related("institution"), id=facility_id, institution_id=institution_id)


def _mask_payload(payload):
	sensitive_markers = ("password", "secret", "token", "credential", "private_key")
	return {
		key: "••••••••" if any(marker in key.lower() for marker in sensitive_markers) else value
		for key, value in payload.items()
	}


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
			device_name=data["deviceName"],
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
			if entity.version != incoming["baseVersion"]:
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
			"sourceDeviceId": str(event.source_device_id),
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
