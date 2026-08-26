import uuid

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
	source_device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="sync_events")
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
