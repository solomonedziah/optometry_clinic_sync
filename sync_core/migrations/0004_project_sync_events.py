from django.db import migrations


ENTITY_TABLE_MAP = {
    "activity_log": "activity_logs",
    "appointment": "appointments",
    "consultation_version": "consultation_versions",
    "finance_payment": "payments",
    "inventory_category": "inventory_categories",
    "inventory_item": "inventory_items",
    "inventory_movement": "inventory_movements",
    "patient": "patients_patient",
    "pharmacy_order": "pharmacy_orders",
    "user": "users",
}

ENTITY_PAYLOAD_KEYS = {
    "appointment": "appointment",
    "consultation_version": "version",
    "finance_payment": "payment",
    "inventory_category": "category",
    "inventory_item": "item",
    "inventory_movement": "movement",
    "patient": "patient",
    "pharmacy_order": "order",
    "user": "user",
}


def project_existing_events(apps, schema_editor):
    FacilitySyncEvent = apps.get_model("sync_core", "FacilitySyncEvent")
    FacilityDataRecord = apps.get_model("sync_core", "FacilityDataRecord")

    for event in FacilitySyncEvent.objects.order_by("sequence").iterator():
        table_name = ENTITY_TABLE_MAP.get(event.entity_type)
        if table_name is None:
            continue
        payload_key = ENTITY_PAYLOAD_KEYS.get(event.entity_type)
        payload = event.payload.get(payload_key, event.payload) if payload_key else event.payload
        if not isinstance(payload, dict):
            payload = event.payload
        FacilityDataRecord.objects.update_or_create(
            facility_id=event.facility_id,
            table_name=table_name,
            record_id=event.entity_public_id,
            defaults={
                "source_device_id": event.source_device_id,
                "payload": payload,
                "version": event.entity_version,
                "deleted": event.operation == "delete",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("sync_core", "0003_facilitysyncentity_facilitysyncevent")]

    operations = [migrations.RunPython(project_existing_events, migrations.RunPython.noop)]