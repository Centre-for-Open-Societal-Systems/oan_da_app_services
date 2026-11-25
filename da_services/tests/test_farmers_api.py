"""DA-40: Farmer profile API tests (integration tests).

Tests farmer listing, profile retrieval, and permission checks with RBAC enforcement.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime, today

from da_services.services import constants as C
from da_services.tests.fixtures import (
	create_administrative_areas,
	ensure_da_user,
	ensure_kebele_assignment,
)

# Test users
DA1 = "farmer-api-da1@da.local"  # DA-000001, ET04 / ET04-W01
SUP = "farmer-api-sup@da.local"  # Supervisor ET04 / ET04-W01
EXEC = "farmer-api-exec@da.local"  # Executive ET03


def _user(email, roles):
	"""Create or get user with roles."""
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	u = frappe.get_doc("User", email)
	for r in roles:
		if r not in frappe.get_roles(email):
			u.add_roles(r)
	return email


def _grant(user, role, **kw):
	"""Create RBAC assignment."""
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestFarmersAPI(IntegrationTestCase):
	"""Test DA-40 farmer profile API endpoints."""

	def setUp(self):
		"""Set up test users, grants, and seed farmer data."""
		frappe.set_user("Administrator")
		# Plots and profile reads use the in-memory registry fixtures. A site
		# configured for the live Farmer Registry must not leak into these cases.
		self._farmer_backend = frappe.conf.get("farmer_registry_backend")
		frappe.conf.farmer_registry_backend = "mock"

		# Clean up
		frappe.db.delete("DA Farmer Reference")
		frappe.db.delete("DA Farmer Link")
		frappe.db.delete("DA Visit")
		frappe.db.delete("DA Task")
		frappe.db.delete("DA RBAC Assignment")

		# Create users
		_user(DA1, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])

		# Create RBAC grants
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET03")

		# Seed farmer references (simulating cached data from registry)
		self.farmer1_id = "farmer-uuid-001"
		self.farmer2_id = "farmer-uuid-002"
		self.farmer3_id = "farmer-uuid-003"

		self._create_farmer_reference(
			self.farmer1_id,
			"FR-000101",
			"Abebe Kebede",
			"ET04",
			"ET04-W01",
			"ET04-W01-K01",
		)
		self._create_farmer_reference(
			self.farmer2_id,
			"FR-000102",
			"Almaz Tesfaye",
			"ET04",
			"ET04-W01",
			"ET04-W01-K02",
		)
		self._create_farmer_reference(
			self.farmer3_id,
			"FR-000103",
			"Chaltu Deressa",
			"ET03",
			"ET03-W02",
			"Kebele 03",
		)

		# Link farmer1 to DA1, and post the DA to that farmer's Kebele.
		# Listing and edit rights for a DA follow the Effective Kebele assignment.
		self._create_farmer_link(DA1, "DA-000001", self.farmer1_id, "Automatic")
		create_administrative_areas()
		ensure_kebele_assignment("DA-000001", "ET04-W01-K01")

		# Create some visits for farmer1
		self._create_visit("DA-000001", self.farmer1_id, "Physical Visit Complete")

		# Create task for farmer1
		self._create_task("DA-000001", self.farmer1_id)

		frappe.db.commit()

	def tearDown(self):
		if self._farmer_backend is None:
			frappe.conf.pop("farmer_registry_backend", None)
		else:
			frappe.conf.farmer_registry_backend = self._farmer_backend
		frappe.set_user("Administrator")
		frappe.db.delete("DA Kebele Assignment", {"da_id": "DA-000001"})
		frappe.db.commit()

	def _create_farmer_reference(self, farmer_id, reg_id, name, region, woreda, kebele):
		"""Helper to create DA Farmer Reference."""
		doc = frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": farmer_id,
				"reg_id": reg_id,
				"farmer_name": name,
				"given_name": name.split()[0],
				"family_name": name.split()[1] if len(name.split()) > 1 else "",
				"region": region,
				"woreda": woreda,
				"kebele": kebele,
				"phone": "911234567",
				"registration_state": "APPROVED",
				"registered_at": now_datetime(),
				"total_land_hectares": 2.5,
				"land_ownership_type": "OWNER",
			}
		)
		doc.insert(ignore_permissions=True, ignore_links=True)
		return doc

	def _create_farmer_link(self, user, da_id, farmer_id, link_type):
		"""Helper to create DA Farmer Link."""
		doc = frappe.get_doc(
			{
				"doctype": "DA Farmer Link",
				"da_id": da_id,
				"farmer_id": farmer_id,
				"link_type": link_type,
				"is_active": 1,
			}
		)
		doc.insert(ignore_permissions=True, ignore_links=True)
		return doc

	def _create_visit(self, da_id, farmer_id, status):
		"""Helper to create DA Visit."""
		# Visits must start in valid initial state (Planned, Confirmed, or In progress)
		initial_status = "Planned" if status not in ("Planned", "Confirmed", "In progress") else status

		doc = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"user": ensure_da_user(da_id),
				"da_id": da_id,
				"farmer_id": farmer_id,
				"farmer_name": "Test Farmer",
				"visit_type": "Crop Survey",
				"physical_status": initial_status,
				"scheduled_at": now_datetime(),
				"region": "ET04",
				"woreda": "ET04-W01",
				"kebele": "ET04-W01-K01",
			}
		)
		doc.insert(ignore_permissions=True, ignore_links=True)

		# If target status is "Physical Visit Complete", follow state machine: Planned → In progress → Complete
		if status == "Physical Visit Complete":
			# First move to "In progress"
			doc.reload()
			doc.physical_status = "In progress"
			doc.save(ignore_permissions=True)

			# Then move to "Physical Visit Complete"
			doc.reload()
			doc.physical_status = "Physical Visit Complete"
			doc.completed_at = now_datetime()
			doc.outcome = "Test outcome"
			doc.save(ignore_permissions=True)
		elif status != initial_status:
			# For other status transitions, update directly
			doc.physical_status = status
			doc.save(ignore_permissions=True)

		return doc

	def _create_task(self, da_id, farmer_id):
		"""Helper to create DA Task."""
		doc = frappe.get_doc(
			{
				"doctype": "DA Task",
				"da_id": da_id,
				"farmer_id": farmer_id,
				"title": "Test task",
				"task_type": "Follow-up",
				"status": "Open",
				"due_at": now_datetime(),
				"region": "ET04",
				"woreda": "ET04-W01",
			}
		)
		doc.insert(ignore_permissions=True, ignore_links=True)
		return doc

	# -- DA-272: live search, change requests, registry metadata ---------------

	def test_change_requests_for_farmer_in_scope(self):
		frappe.set_user(SUP)

		from da_services.api.v1.farmers import get_farmer_change_requests

		result = get_farmer_change_requests(self.farmer1_id)
		self.assertEqual(result["status"], "success")
		rows = result["data"]["change_requests"]
		self.assertEqual([r["change_request_id"] for r in rows], ["cr-001-pending", "cr-001-approved"])
		self.assertEqual(rows[0]["status"], "PENDING")
		self.assertEqual(rows[0]["tab_id"], "farmer_location_tab")
		self.assertEqual(result["data"]["total"], 2)

		pending = get_farmer_change_requests("FR-000101", status="pending")["data"]["change_requests"]
		self.assertEqual([r["change_request_id"] for r in pending], ["cr-001-pending"])

	def test_change_requests_scope_and_validation(self):
		frappe.set_user(SUP)

		from da_services.api.v1.farmers import get_farmer_change_requests

		# handle_api_errors turns the raised errors into error envelopes.
		denied = get_farmer_change_requests(self.farmer3_id)
		self.assertEqual((denied["status"], denied["code"]), ("error", "PERMISSION_DENIED"))
		missing = get_farmer_change_requests("farmer-uuid-404")
		self.assertEqual((missing["status"], missing["code"]), ("error", "NOT_FOUND"))
		bad = get_farmer_change_requests(self.farmer1_id, status="maybe")
		self.assertEqual((bad["status"], bad["code"]), ("error", "VALIDATION_ERROR"))

	def test_registries_registers_and_intake_form(self):
		frappe.set_user(DA1)

		from da_services.api.v1.registries import get_intake_form, list_registers

		registers = list_registers("farmer")["data"]
		self.assertEqual(registers["registry"], "farmer")
		self.assertIn("Farmer", [r["register_mnemonic"] for r in registers["registers"]])
		self.assertEqual(registers["total"], len(registers["registers"]))

		layout = get_intake_form("farmer")["data"]
		self.assertEqual(layout["form_id"], "mock-intake-form")
		self.assertEqual(layout["sections"][0]["register_mnemonic"], "Household")

		unknown = list_registers("passport")
		self.assertEqual((unknown["status"], unknown["code"]), ("error", "NOT_FOUND"))

	def test_profile_and_plots_accept_the_farmer_number(self):
		"""FR-000101 (reg_id) resolves to the cached farmer, so profile / plots / permissions work with it."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import check_farmer_permissions, get_farmer_plots, get_farmer_profile

		profile = get_farmer_profile("FR-000101")
		self.assertEqual(profile["status"], "success", profile)
		self.assertEqual(profile["data"]["farmer_id"], self.farmer1_id)
		plots = get_farmer_plots("FR-000101")
		self.assertEqual(plots["status"], "success", plots)
		self.assertEqual(len(plots["data"]["plots"]), 1)
		self.assertTrue(check_farmer_permissions("FR-000101")["data"]["can_edit"])

	def test_get_farmer_profile_success(self):
		"""Get farmer profile with merged visit data."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import get_farmer_profile

		result = get_farmer_profile(self.farmer1_id)
		self.assertEqual(result["status"], "success")

		profile = result["data"]
		self.assertEqual(profile["farmer_id"], self.farmer1_id)
		self.assertEqual(profile["name"], "Abebe Kebede")
		self.assertEqual(profile["kebele"], "ET04-W01-K01")
		self.assertTrue(profile["can_edit"])
		self.assertFalse(profile["is_linked"])  # no caller DA-ID to link against
		self.assertIsNotNone(profile["last_visit_date"])
		self.assertEqual(profile["pending_actions"], 1)  # One task

	def test_get_farmer_profile_not_in_scope(self):
		"""DA cannot access farmer from different woreda."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import get_farmer_profile

		result = get_farmer_profile(self.farmer3_id)  # ET03, not in DA1's scope
		self.assertEqual(result["status"], "error")

	def test_get_farmer_profile_not_found(self):
		"""Get non-existent farmer returns error."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import get_farmer_profile

		result = get_farmer_profile("non-existent-farmer-id")
		self.assertEqual(result["status"], "error")

	def test_get_farmer_visits(self):
		"""Get visit history for a farmer."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import get_farmer_visits

		result = get_farmer_visits(self.farmer1_id)
		visits = result["data"]["visits"]

		self.assertEqual(len(visits), 1)
		self.assertEqual(visits[0]["farmer_id"], self.farmer1_id)
		self.assertEqual(visits[0]["physical_status"], "Physical Visit Complete")
		self.assertEqual(result["data"]["total"], 1)

	def test_get_farmer_plots_mock(self):
		"""Get farmer plots from mock registry."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import get_farmer_plots

		result = get_farmer_plots(self.farmer1_id)
		plots = result["data"]["plots"]

		# Mock returns 1 plot for farmer-uuid-001
		self.assertEqual(len(plots), 1)
		self.assertEqual(plots[0]["land_kebele"], "Bako 01")

	def test_get_farmer_photo_mock(self):
		"""The photo link comes from the registry record, also by Farmer-ID."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import get_farmer_photo

		result = get_farmer_photo("FR-000101")
		self.assertEqual(result["status"], "success", result)
		self.assertEqual(result["data"]["document_id"], "mock-photo-001")
		self.assertTrue(result["data"]["photo_url"].startswith("https://"))

	def test_visits_scope_leak_denied(self):
		"""Test DA cannot read visits of out-of-scope farmer (PR #12 fix)."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import get_farmer_visits

		# farmer-uuid-003 is in ET03-W02, outside DA1's scope (ET04-W01)
		# API functions use @handle_api_errors which returns error response
		result = get_farmer_visits("farmer-uuid-003")
		self.assertEqual(result["status"], "error")
		self.assertIn("permission", result["message"].lower())

	def test_plots_scope_leak_denied(self):
		"""Test DA cannot read plots of out-of-scope farmer (PR #12 fix)."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers import get_farmer_plots

		# farmer-uuid-003 is in ET03-W02, outside DA1's scope
		result = get_farmer_plots("farmer-uuid-003")
		self.assertEqual(result["status"], "error")
		self.assertIn("permission", result["message"].lower())
