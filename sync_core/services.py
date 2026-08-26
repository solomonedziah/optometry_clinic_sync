import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone as datetime_timezone

import jwt
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from .models import Device, DeviceCredential, DeviceStatus, EnrollmentToken, Facility

ACCESS_TOKEN_MAX_AGE = 900


def hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@transaction.atomic
def create_pending_device(facility: Facility, *, device_name, location_name="", expires_in_hours=24):
    device = Device.objects.create(
        facility=facility,
        device_name=device_name,
        location_name=location_name,
    )
    raw_token = secrets.token_urlsafe(32)
    enrollment = EnrollmentToken.objects.create(
        facility=facility,
        device=device,
        token_hash=hash_token(raw_token),
        expires_at=timezone.now() + timedelta(hours=expires_in_hours),
    )
    return device, raw_token, enrollment.expires_at


@transaction.atomic
def enroll_device(*, token, installation_id, device_name, platform, app_version):
    try:
        enrollment = (
            EnrollmentToken.objects.select_for_update()
            .select_related("device", "facility__institution")
            .get(token_hash=hash_token(token))
        )
    except EnrollmentToken.DoesNotExist as error:
        raise ValueError("INVALID_ENROLLMENT") from error

    device = enrollment.device
    if not enrollment.is_usable or device.status != DeviceStatus.PENDING:
        raise ValueError("INVALID_ENROLLMENT")
    if Device.objects.exclude(pk=device.pk).filter(installation_id=installation_id).exists():
        raise ValueError("INSTALLATION_ALREADY_BOUND")

    now = timezone.now()
    device.installation_id = installation_id
    device.device_name = device_name
    device.platform = platform
    device.app_version = app_version
    device.status = DeviceStatus.ENROLLED
    device.enrolled_at = now
    device.last_seen_at = now
    device.save()
    enrollment.consumed_at = now
    enrollment.save(update_fields=["consumed_at"])

    client_key = f"dev_{secrets.token_urlsafe(24)}"
    client_secret = secrets.token_urlsafe(48)
    DeviceCredential.objects.create(
        device=device,
        client_key=client_key,
        client_secret_hash=make_password(client_secret),
    )
    return device, client_key, client_secret


def authenticate_device(*, client_key, client_secret):
    try:
        credential = DeviceCredential.objects.select_related("device__facility__institution").get(
            client_key=client_key,
            revoked_at__isnull=True,
            device__status=DeviceStatus.ENROLLED,
        )
    except DeviceCredential.DoesNotExist:
        return None
    if not check_password(client_secret, credential.client_secret_hash):
        return None

    now = timezone.now()
    credential.last_authenticated_at = now
    credential.save(update_fields=["last_authenticated_at"])
    credential.device.last_seen_at = now
    credential.device.save(update_fields=["last_seen_at", "updated_at"])
    return credential.device


def issue_access_token(device):
    now = datetime.now(datetime_timezone.utc)
    return jwt.encode(
        {
            "deviceId": str(device.id),
            "facilityId": str(device.facility_id),
            "iat": now,
            "exp": now + timedelta(seconds=ACCESS_TOKEN_MAX_AGE),
        },
        os.environ["DEVICE_JWT_SECRET"],
        algorithm="HS256",
    )


def device_from_access_token(raw_token):
    try:
        claims = jwt.decode(raw_token, os.environ["DEVICE_JWT_SECRET"], algorithms=["HS256"])
        return Device.objects.select_related("facility__institution").get(
            id=claims["deviceId"],
            facility_id=claims["facilityId"],
            status=DeviceStatus.ENROLLED,
        )
    except (jwt.PyJWTError, KeyError, Device.DoesNotExist):
        return None
