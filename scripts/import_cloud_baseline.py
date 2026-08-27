import json
import os
import uuid

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinic_sync.settings")
django.setup()

from django.db import transaction

from sync_core.models import (
    Device,
    FacilityDataRecord,
    FacilitySyncEntity,
    FacilitySyncEvent,
)


def import_baseline(package_path):
    with open(package_path, encoding="utf-8") as package_file:
        package = json.load(package_file)

    if package.get("formatVersion") != 1:
        raise ValueError("Unsupported baseline package format.")

    device = Device.objects.select_related("facility").get(
        id=package["sourceDeviceId"],
        facility_id=package["facilityId"],
        status="enrolled",
    )

    records = package["records"]
    events = package["events"]
    entity_keys = set()
    for event in events:
        key = (event["entityType"], event["entityPublicId"])
        if key in entity_keys:
            raise ValueError(f"Duplicate baseline entity: {key[0]}/{key[1]}")
        entity_keys.add(key)
        if event["baseVersion"] != 0 or event["entityVersion"] != 1:
            raise ValueError(f"Baseline event is not version 1: {event['eventId']}")

    with transaction.atomic():
        FacilityDataRecord.objects.filter(facility=device.facility).delete()
        FacilitySyncEvent.objects.filter(facility=device.facility).delete()
        FacilitySyncEntity.objects.filter(facility=device.facility).delete()

        FacilityDataRecord.objects.bulk_create([
            FacilityDataRecord(
                facility=device.facility,
                source_device=device,
                table_name=record["tableName"],
                record_id=record["recordId"],
                payload=record["payload"],
                version=record["version"],
                deleted=record["deleted"],
            )
            for record in records
        ], batch_size=500)

        FacilitySyncEvent.objects.bulk_create([
            FacilitySyncEvent(
                facility=device.facility,
                source_device=device,
                event_id=uuid.UUID(event["eventId"]),
                entity_type=event["entityType"],
                entity_public_id=event["entityPublicId"],
                operation=event["operation"],
                base_version=event["baseVersion"],
                entity_version=event["entityVersion"],
                payload=event["payload"],
            )
            for event in events
        ], batch_size=200)

        FacilitySyncEntity.objects.bulk_create([
            FacilitySyncEntity(
                facility=device.facility,
                source_device=device,
                entity_type=event["entityType"],
                entity_public_id=event["entityPublicId"],
                version=event["entityVersion"],
                payload=event["payload"],
                deleted=event["operation"] == "delete",
            )
            for event in events
        ], batch_size=500)

    return {
        "records": FacilityDataRecord.objects.filter(facility=device.facility).count(),
        "events": FacilitySyncEvent.objects.filter(facility=device.facility).count(),
        "entities": FacilitySyncEntity.objects.filter(facility=device.facility).count(),
    }


if __name__ == "__main__":
    print(json.dumps(import_baseline(os.environ["BASELINE_PACKAGE"]), sort_keys=True))