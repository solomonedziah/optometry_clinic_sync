from django.contrib import admin

from .models import Device, DeviceCredential, EnrollmentToken, Facility, Institution, LargeFileObject, LargeFileReference


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
	list_display = ["name", "code", "status", "created_at"]
	search_fields = ["name", "code"]


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
	list_display = ["name", "institution", "code", "sync_enabled", "status"]
	list_filter = ["institution", "sync_enabled", "status"]
	search_fields = ["name", "code"]


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
	list_display = ["device_name", "facility", "status", "last_seen_at"]
	list_filter = ["status", "facility__institution", "facility"]
	search_fields = ["device_name", "installation_id"]


admin.site.register(EnrollmentToken)
admin.site.register(DeviceCredential)
admin.site.register(LargeFileObject)
admin.site.register(LargeFileReference)
