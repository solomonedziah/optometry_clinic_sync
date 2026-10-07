import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Status(models.TextChoices):
	ACTIVE = "active", "Active"
	INACTIVE = "inactive", "Inactive"


class DeviceStatus(models.TextChoices):
	PENDING = "pending_enrollment", "Awaiting enrollment"
	ENROLLED = "enrolled", "Enrolled"
	REVOKED = "revoked", "Revoked"


class TimestampedModel(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		abstract = True


class Institution(TimestampedModel):
	name = models.CharField(max_length=160)
	code = models.CharField(max_length=32, unique=True)
	address = models.CharField(max_length=300, blank=True)
	timezone = models.CharField(max_length=80, default="Africa/Accra")
	status = models.CharField(max_length=16, choices=Status, default=Status.ACTIVE)

	class Meta:
		ordering = ["name"]

	def save(self, *args, **kwargs):
		self.code = self.code.strip().upper()
		super().save(*args, **kwargs)

	def __str__(self):
		return self.name


class Facility(TimestampedModel):
	institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="facilities")
	name = models.CharField(max_length=160)
	code = models.CharField(max_length=32)
	address = models.CharField(max_length=300, blank=True)
	timezone = models.CharField(max_length=80, default="Africa/Accra")
	status = models.CharField(max_length=16, choices=Status, default=Status.ACTIVE)
	sync_enabled = models.BooleanField(default=True)

	class Meta:
		ordering = ["name"]
		constraints = [
			models.UniqueConstraint(fields=["institution", "code"], name="unique_facility_code_per_institution"),
		]

	def save(self, *args, **kwargs):
		self.code = self.code.strip().upper()
		super().save(*args, **kwargs)

	def __str__(self):
		return f"{self.institution.name} - {self.name}"


class Device(TimestampedModel):
	facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="devices")
	installation_id = models.UUIDField(null=True, blank=True, unique=True)
	device_name = models.CharField(max_length=160)
	location_name = models.CharField(max_length=160, blank=True)
	platform = models.CharField(max_length=80, blank=True)
	app_version = models.CharField(max_length=40, blank=True)
	# Host the device last reached us on, to confirm every Main PC has moved after an address change.
	last_sync_host = models.CharField(max_length=200, blank=True)
	status = models.CharField(max_length=32, choices=DeviceStatus, default=DeviceStatus.PENDING)
	enrolled_at = models.DateTimeField(null=True, blank=True)
	last_seen_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		ordering = ["-created_at"]

	def __str__(self):
		return self.device_name


class EnrollmentToken(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="enrollment_tokens")
	device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="enrollment_tokens")
	token_hash = models.CharField(max_length=64, unique=True)
	expires_at = models.DateTimeField()
	consumed_at = models.DateTimeField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	@property
	def is_usable(self):
		return self.consumed_at is None and self.expires_at > timezone.now()


class DeviceCredential(models.Model):
	device = models.OneToOneField(Device, on_delete=models.CASCADE, related_name="credential")
	client_key = models.CharField(max_length=80, unique=True)
	client_secret_hash = models.CharField(max_length=256)
	issued_at = models.DateTimeField(auto_now_add=True)
	last_authenticated_at = models.DateTimeField(null=True, blank=True)
	revoked_at = models.DateTimeField(null=True, blank=True)


class FacilityDataRecord(models.Model):
	id = models.BigAutoField(primary_key=True)
	facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="data_records")
	source_device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="data_records")
	table_name = models.CharField(max_length=100)
	record_id = models.CharField(max_length=160)
	payload = models.JSONField(default=dict)
	version = models.PositiveBigIntegerField(default=1)
	deleted = models.BooleanField(default=False)
	source_updated_at = models.DateTimeField(null=True, blank=True)
	received_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-source_updated_at", "-received_at", "record_id"]
		constraints = [
			models.UniqueConstraint(
				fields=["facility", "table_name", "record_id"],
				name="unique_facility_table_record",
			),
		]
		indexes = [
			models.Index(fields=["facility", "table_name", "deleted"], name="facility_table_live_idx"),
		]


class FacilitySyncEntity(models.Model):
	id = models.BigAutoField(primary_key=True)
	facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="sync_entities")
	entity_type = models.CharField(max_length=100)
	entity_public_id = models.CharField(max_length=160)
	version = models.PositiveBigIntegerField(default=0)
	payload = models.JSONField(default=dict)
	deleted = models.BooleanField(default=False)
	source_device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, related_name="sync_entities")
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["facility", "entity_type", "entity_public_id"],
				name="unique_facility_sync_entity",
			),
		]


class FacilitySyncEvent(models.Model):
	sequence = models.BigAutoField(primary_key=True)
	facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="sync_events")
	# Null when the event was issued from the sync platform itself (a conflict resolution).
	source_device = models.ForeignKey(Device, on_delete=models.PROTECT, null=True, blank=True, related_name="sync_events")
	event_id = models.UUIDField(unique=True)
	entity_type = models.CharField(max_length=100)
	entity_public_id = models.CharField(max_length=160)
	operation = models.CharField(max_length=16)
	base_version = models.PositiveBigIntegerField()
	entity_version = models.PositiveBigIntegerField()
	payload = models.JSONField(default=dict)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["sequence"]
		indexes = [models.Index(fields=["facility", "sequence"], name="facility_sync_cursor_idx")]


class ConflictStatus(models.TextChoices):
	OPEN = "open", "Needs review"
	RESOLVED = "resolved", "Resolved"
	AUTO_RESOLVED = "auto_resolved", "Settled automatically"


class ConflictResolution(models.TextChoices):
	KEPT_SERVER = "kept_server", "Kept the server version"
	APPLIED_DEVICE = "applied_device", "Applied the device version"
	DEVICE_RESENT = "device_resent", "Device re-sent its change on top"
	OLDER_CREDENTIAL_IGNORED = "older_credential_ignored", "Older password change ignored"


class FacilitySyncConflict(models.Model):
	"""A pushed event the server rejected because the device built it on an outdated version."""

	id = models.BigAutoField(primary_key=True)
	facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="sync_conflicts")
	device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="sync_conflicts")
	event_id = models.UUIDField(unique=True)
	entity_type = models.CharField(max_length=100)
	entity_public_id = models.CharField(max_length=160)
	operation = models.CharField(max_length=16)
	base_version = models.PositiveBigIntegerField()
	incoming_payload = models.JSONField(default=dict)
	canonical_version = models.PositiveBigIntegerField()
	canonical_payload = models.JSONField(default=dict)
	canonical_deleted = models.BooleanField(default=False)
	canonical_device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
	attempts = models.PositiveIntegerField(default=1)
	status = models.CharField(max_length=16, choices=ConflictStatus, default=ConflictStatus.OPEN)
	resolution = models.CharField(max_length=32, choices=ConflictResolution, blank=True)
	resolution_event_id = models.UUIDField(null=True, blank=True)
	resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
	resolved_at = models.DateTimeField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-created_at"]
		indexes = [
			models.Index(fields=["facility", "status"], name="facility_conflict_status_idx"),
			models.Index(fields=["facility", "entity_type", "entity_public_id"], name="facility_conflict_entity_idx"),
		]


class LargeFileObject(models.Model):
	oid = models.CharField(max_length=64, primary_key=True)
	size_bytes = models.PositiveBigIntegerField()
	content_type = models.CharField(max_length=160, default="application/octet-stream")
	storage_path = models.CharField(max_length=300, unique=True)
	created_at = models.DateTimeField(auto_now_add=True)


class LargeFileReference(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="large_file_references")
	source_device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="large_file_references")
	file_object = models.ForeignKey(LargeFileObject, on_delete=models.PROTECT, related_name="references")
	entity_type = models.CharField(max_length=100)
	entity_public_id = models.CharField(max_length=160)
	file_name = models.CharField(max_length=255)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["facility", "file_object", "entity_type", "entity_public_id"],
				name="unique_facility_large_file_reference",
			),
		]
		indexes = [models.Index(fields=["facility", "entity_type", "entity_public_id"], name="facility_lfs_entity_idx")]


class SystemLog(models.Model):
	"""A server log record, written by sync_core.log_handler.DatabaseLogHandler."""

	id = models.BigAutoField(primary_key=True)
	created_at = models.DateTimeField(default=timezone.now, db_index=True)
	level = models.PositiveSmallIntegerField(db_index=True)
	level_name = models.CharField(max_length=16)
	source = models.CharField(max_length=120)
	message = models.TextField()
	method = models.CharField(max_length=10, blank=True)
	path = models.CharField(max_length=300, blank=True)
	host = models.CharField(max_length=200, blank=True)
	status_code = models.PositiveSmallIntegerField(null=True, blank=True)
	client_ip = models.CharField(max_length=64, blank=True)
	username = models.CharField(max_length=150, blank=True)
	facility = models.ForeignKey(Facility, on_delete=models.SET_NULL, null=True, blank=True, related_name="system_logs")
	device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="system_logs")
	context = models.JSONField(default=dict, blank=True)
	traceback = models.TextField(blank=True)

	class Meta:
		ordering = ["-created_at", "-id"]
		indexes = [models.Index(fields=["level", "-created_at"], name="system_log_level_idx")]
