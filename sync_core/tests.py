import uuid
import hashlib
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .data_catalog import DATA_TABLE_MAP
from .models import Device, DeviceStatus, Facility, FacilityDataRecord, FacilitySyncEvent, Institution
from .services import create_pending_device


class DataCatalogTests(TestCase):
	def test_catalog_covers_safe_electron_tables_and_excludes_repository_internals(self):
		self.assertEqual(len(DATA_TABLE_MAP), 67)
		self.assertTrue({
			"role_permissions",
			"patient_family_ocular_conditions",
			"consultation_objective_refraction",
			"case_management_guide_items",
			"examinations_internalexternalcondition",
			"visual_acuity_prescription_types",
		}.issubset(DATA_TABLE_MAP))
		self.assertTrue({
			"payments_new",
			"institution_tokens",
			"sync_node_state",
			"sync_entity_versions",
			"sync_outbox",
			"sync_inbox",
			"sync_conflicts",
		}.isdisjoint(DATA_TABLE_MAP))


class HierarchyViewsTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="admin", password="temporary-password")
		self.client.force_login(self.user)

	def test_institution_facility_and_device_creation(self):
		response = self.client.post(reverse("institution-list"), {
			"name": "University of Cape Coast",
			"code": "ucc",
			"address": "Cape Coast",
			"timezone": "Africa/Accra",
		})
		institution = Institution.objects.get()
		self.assertRedirects(response, reverse("institution-detail", args=[institution.id]))
		self.assertEqual(institution.code, "UCC")

		response = self.client.post(reverse("institution-detail", args=[institution.id]), {
			"name": "Optometry Eye Clinic",
			"code": "eye",
			"address": "University Campus",
			"timezone": "Africa/Accra",
			"sync_enabled": "on",
		})
		facility = Facility.objects.get()
		self.assertRedirects(response, reverse("facility-detail", args=[institution.id, facility.id]))

		response = self.client.post(reverse("facility-detail", args=[institution.id, facility.id]), {
			"device_name": "Main PC A",
			"location_name": "Reception",
			"expires_in_hours": "24",
		}, follow=True)
		self.assertContains(response, "Enrollment created")
		self.assertContains(response, "http://testserver")
		self.assertEqual(Device.objects.get().facility, facility)

	def test_facility_repository_browser_is_scoped_and_masks_sensitive_fields(self):
		institution = Institution.objects.create(name="University of Cape Coast", code="UCC")
		facility = Facility.objects.create(institution=institution, name="Eye Clinic", code="EYE")
		other_facility = Facility.objects.create(institution=institution, name="Other Clinic", code="OTHER")
		FacilityDataRecord.objects.create(
			facility=facility,
			table_name="users",
			record_id="user-1",
			payload={"username": "clinician", "password_hash": "never-render-this-secret", "email": "clinic@example.com"},
			version=2,
		)
		FacilityDataRecord.objects.create(
			facility=other_facility,
			table_name="users",
			record_id="user-2",
			payload={"username": "other-clinic"},
		)

		response = self.client.get(reverse("facility-data-browser", args=[institution.id, facility.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Users")
		self.assertContains(response, "Patients")
		self.assertContains(response, "Appointments")
		self.assertContains(response, "Labs")

		response = self.client.get(reverse("facility-data-table", args=[institution.id, facility.id, "users"]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "clinician")
		self.assertNotContains(response, "other-clinic")
		self.assertNotContains(response, "never-render-this-secret")
		self.assertContains(response, "••••••••")

	def test_facility_repository_rejects_unknown_tables(self):
		institution = Institution.objects.create(name="University of Cape Coast", code="UCC")
		facility = Facility.objects.create(institution=institution, name="Eye Clinic", code="EYE")
		response = self.client.get(reverse("facility-data-table", args=[institution.id, facility.id, "device_credentials"]))
		self.assertEqual(response.status_code, 404)

class DeviceApiTests(TestCase):
	def setUp(self):
		institution = Institution.objects.create(name="University of Cape Coast", code="UCC")
		self.facility = Facility.objects.create(institution=institution, name="Optometry Eye Clinic", code="EYE")

	def test_enrollment_authentication_and_identity_are_facility_scoped(self):
		device, token, _ = create_pending_device(self.facility, device_name="Main PC A")
		enrollment = self.client.post(reverse("api-device-enroll"), {
			"token": token,
			"installationId": str(uuid.uuid4()),
			"deviceName": "Electron Override",
			"platform": "win32",
			"appVersion": "1.0.0",
		}, content_type="application/json")
		self.assertEqual(enrollment.status_code, 201)
		body = enrollment.json()
		self.assertEqual(body["facilityId"], str(self.facility.id))
		self.assertEqual(body["device"]["facilityId"], str(self.facility.id))
		device.refresh_from_db()
		self.assertEqual(device.status, DeviceStatus.ENROLLED)
		self.assertEqual(device.device_name, "Main PC A")

		authentication = self.client.post(reverse("api-device-authenticate"), {
			"clientKey": body["credentials"]["clientKey"],
			"clientSecret": body["credentials"]["clientSecret"],
		}, content_type="application/json")
		self.assertEqual(authentication.status_code, 200)
		identity = self.client.get(
			reverse("api-device-me"),
			HTTP_AUTHORIZATION=f"Bearer {authentication.json()['accessToken']}",
		)
		self.assertEqual(identity.status_code, 200)
		self.assertEqual(identity.json()["device"]["facilityId"], str(self.facility.id))

		replay = self.client.post(reverse("api-device-enroll"), {
			"token": token,
			"installationId": str(uuid.uuid4()),
			"deviceName": "Main PC B",
			"platform": "win32",
			"appVersion": "1.0.0",
		}, content_type="application/json")
		self.assertEqual(replay.status_code, 400)

	def test_repository_records_are_bound_to_authenticated_device_facility(self):
		device, token, _ = create_pending_device(self.facility, device_name="Main PC A")
		enrollment = self.client.post(reverse("api-device-enroll"), {
			"token": token,
			"installationId": str(uuid.uuid4()),
			"deviceName": "Main PC A",
			"platform": "win32",
			"appVersion": "1.0.0",
		}, content_type="application/json").json()
		headers = {"HTTP_AUTHORIZATION": f"Bearer {enrollment['accessToken']}"}

		response = self.client.post(reverse("api-repository-records"), {
			"records": [{
				"tableName": "patients_patient",
				"recordId": "patient-1",
				"payload": {"first_name": "Ama", "last_name": "Mensah"},
				"version": 2,
			}],
		}, content_type="application/json", **headers)
		self.assertEqual(response.status_code, 200)
		record = FacilityDataRecord.objects.get()
		self.assertEqual(record.facility, self.facility)
		self.assertEqual(record.source_device, device)

		stale = self.client.post(reverse("api-repository-records"), {
			"records": [{
				"tableName": "patients_patient",
				"recordId": "patient-1",
				"payload": {"first_name": "Stale"},
				"version": 1,
			}],
		}, content_type="application/json", **headers)
		self.assertEqual(stale.status_code, 200)
		self.assertEqual(stale.json(), {"accepted": 0, "skipped": 1})
		self.assertEqual(FacilityDataRecord.objects.get().payload["first_name"], "Ama")

		unknown = self.client.post(reverse("api-repository-records"), {
			"records": [{"tableName": "device_credentials", "recordId": "secret", "payload": {}, "version": 1}],
		}, content_type="application/json", **headers)
		self.assertEqual(unknown.status_code, 400)
		self.assertEqual(FacilityDataRecord.objects.count(), 1)

	def test_large_file_objects_are_deduplicated_and_facility_scoped(self):
		_device, token, _ = create_pending_device(self.facility, device_name="Main PC A")
		enrollment = self.client.post(reverse("api-device-enroll"), {
			"token": token,
			"installationId": str(uuid.uuid4()),
			"deviceName": "Main PC A",
			"platform": "win32",
			"appVersion": "1.0.0",
		}, content_type="application/json").json()
		headers = {
			"HTTP_AUTHORIZATION": f"Bearer {enrollment['accessToken']}",
			"HTTP_X_CLINIC_ENTITY_TYPE": "lab_request",
			"HTTP_X_CLINIC_ENTITY_ID": "request-1",
			"HTTP_X_CLINIC_FILE_NAME": "scan.pdf",
		}
		content = b"clinic scan content"
		oid = hashlib.sha256(content).hexdigest()
		with tempfile.TemporaryDirectory() as directory:
			with self.settings(CLINIC_LFS_ROOT=Path(directory)):
				mismatch = self.client.put(
					reverse("api-lfs-object", args=["0" * 64]),
					content,
					content_type="application/pdf",
					**headers,
				)
				self.assertEqual(mismatch.status_code, 422)

				created = self.client.put(
					reverse("api-lfs-object", args=[oid]),
					content,
					content_type="application/pdf",
					**headers,
				)
				self.assertEqual(created.status_code, 201)
				self.assertEqual(created.json()["asset"]["oid"], oid)
				self.assertFalse(created.json()["deduplicated"])

				retry = self.client.put(
					reverse("api-lfs-object", args=[oid]),
					content,
					content_type="application/pdf",
					**headers,
				)
				self.assertEqual(retry.status_code, 200)
				self.assertTrue(retry.json()["deduplicated"])

				download = self.client.get(reverse("api-lfs-object", args=[oid]), **headers)
				self.assertEqual(download.status_code, 200)
				self.assertEqual(b"".join(download.streaming_content), content)

				other_facility = Facility.objects.create(
					institution=self.facility.institution,
					name="Other Clinic",
					code="OTHER",
				)
				_device, other_token, _ = create_pending_device(other_facility, device_name="Other Main PC")
				other_enrollment = self.client.post(reverse("api-device-enroll"), {
					"token": other_token,
					"installationId": str(uuid.uuid4()),
					"deviceName": "Other Main PC",
					"platform": "win32",
					"appVersion": "1.0.0",
				}, content_type="application/json").json()
				isolated = self.client.get(
					reverse("api-lfs-object", args=[oid]),
					HTTP_AUTHORIZATION=f"Bearer {other_enrollment['accessToken']}",
				)
				self.assertEqual(isolated.status_code, 404)

	def test_sync_push_pull_is_idempotent_and_rejects_stale_concurrent_writes(self):
		def enroll(name):
			_device, token, _ = create_pending_device(self.facility, device_name=name)
			body = self.client.post(reverse("api-device-enroll"), {
				"token": token,
				"installationId": str(uuid.uuid4()),
				"deviceName": name,
				"platform": "win32",
				"appVersion": "1.0.0",
			}, content_type="application/json").json()
			return {"HTTP_AUTHORIZATION": f"Bearer {body['accessToken']}"}

		first_headers = enroll("Main PC A")
		second_headers = enroll("Main PC B")
		first_event_id = str(uuid.uuid4())
		first_event = {
			"eventId": first_event_id,
			"entityType": "patient",
			"entityPublicId": "patient-1",
			"operation": "create",
			"baseVersion": 0,
			"entityVersion": 1,
			"payload": {"patient": {"firstName": "Ama"}},
		}

		accepted = self.client.post(
			reverse("api-sync-push"), {"events": [first_event]}, content_type="application/json", **first_headers,
		)
		self.assertEqual(accepted.status_code, 200)
		self.assertEqual(accepted.json(), {"acceptedEventIds": [first_event_id], "conflicts": []})
		projected = FacilityDataRecord.objects.get(
			facility=self.facility,
			table_name="patients_patient",
			record_id="patient-1",
		)
		self.assertEqual(projected.payload, {"firstName": "Ama"})
		self.assertEqual(projected.version, 1)
		retry = self.client.post(
			reverse("api-sync-push"), {"events": [first_event]}, content_type="application/json", **first_headers,
		)
		self.assertEqual(retry.json(), accepted.json())
		self.assertEqual(FacilityDataRecord.objects.filter(table_name="patients_patient").count(), 1)

		equivalent_event_id = str(uuid.uuid4())
		equivalent = self.client.post(
			reverse("api-sync-push"),
			{"events": [{**first_event, "eventId": equivalent_event_id}]},
			content_type="application/json",
			**second_headers,
		)
		self.assertEqual(equivalent.json(), {"acceptedEventIds": [equivalent_event_id], "conflicts": []})
		self.assertEqual(FacilitySyncEvent.objects.count(), 1)

		stale_event_id = str(uuid.uuid4())
		stale = self.client.post(reverse("api-sync-push"), {"events": [{
			**first_event,
			"eventId": stale_event_id,
			"payload": {"patient": {"firstName": "Efua"}},
		}]}, content_type="application/json", **second_headers)
		self.assertEqual(stale.status_code, 200)
		self.assertEqual(stale.json()["acceptedEventIds"], [])
		self.assertEqual(stale.json()["conflicts"][0]["canonicalVersion"], 1)
		self.assertEqual(stale.json()["conflicts"][0]["canonicalPayload"], first_event["payload"])

		pulled = self.client.post(
			reverse("api-sync-pull"), {"after": 0, "limit": 200}, content_type="application/json", **second_headers,
		)
		self.assertEqual(pulled.status_code, 200)
		self.assertEqual(len(pulled.json()["events"]), 1)
		self.assertEqual(pulled.json()["events"][0]["eventId"], first_event_id)
		self.assertGreater(pulled.json()["latestSequence"], 0)
