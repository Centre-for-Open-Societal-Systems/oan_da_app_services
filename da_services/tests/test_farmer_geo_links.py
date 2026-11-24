"""The farmer cache links a farmer's place to the geo masters.

The Farmer Registry gives Region / Zone / Woreda / Kebele by name; DA Farmer Reference
holds them as Links (codes), which is what the User Permissions of the DA Hierarchy
narrow on. Covers ``geo.codes_for_names``, the cache write, the profile answer, and who
may read the farmer.
"""

from unittest.mock import patch

import frappe
from frappe.permissions import add_user_permission
from frappe.tests import IntegrationTestCase

from da_services.integrations.farmer_registry.schemas import Farmer
from da_services.services import constants as C
from da_services.services import farmers as svc
from da_services.services import geo
from da_services.tests.fixtures import as_user, ensure_geo_masters

REGION, ZONE = "GL01", "GL0101"
WOREDA_A, WOREDA_B = "GL010101", "GL010102"
KEBELE_A, KEBELE_B = "GL01010101", "GL01010201"
# The same Kebele name under two Woredas, as in the real masters.
MASTERS = [
	("DA Region", "region", REGION, "Geolink Region", "country", "ET"),
	("DA Zone", "zone", ZONE, "Geolink Zone", "region", REGION),
	("DA Woreda", "woreda", WOREDA_A, "Geolink Woreda A", "zone", ZONE),
	("DA Woreda", "woreda", WOREDA_B, "Geolink Woreda B", "zone", ZONE),
	("DA Kebele", "kebele", KEBELE_A, "Geolink Kebele", "woreda", WOREDA_A),
	("DA Kebele", "kebele", KEBELE_B, "Geolink Kebele", "woreda", WOREDA_B),
]
NAMES_A = {
	"region_name": "Geolink Region",
	"zone_name": "Geolink Zone",
	"woreda_name": "Geolink Woreda A",
	"kebele_name": "Geolink Kebele",
}
NOTHING = {"region": None, "zone": None, "woreda": None, "kebele": None}
CLIENT = "da_services.services.farmers.get_farmer_registry_client"


def _masters() -> None:
	ensure_geo_masters()
	for doctype, prefix, code, name, parent_field, parent in MASTERS:
		if not frappe.db.exists(doctype, code):
			frappe.get_doc(
				{
					"doctype": doctype,
					f"{prefix}_code": code,
					f"{prefix}_name": name,
					parent_field: parent,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)


def _user(email: str, role: str, **permissions) -> str:
	"""A user holding `role`, with a User Permission per geo doctype given (``DA_Kebele=code``)."""
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	frappe.get_doc("User", email).add_roles(role)
	for doctype, code in permissions.items():
		add_user_permission(doctype.replace("_", " "), code, email, ignore_permissions=True)
	return email


class _Registry:
	"""A registry holding the farmers given, by internal id."""

	def __init__(self, *farmers: Farmer):
		self.farmers = {f.farmer_id: f for f in farmers}

	def get_farmer(self, farmer_id):
		return self.farmers.get(farmer_id)


class TestCodesForNames(IntegrationTestCase):
	def setUp(self):
		_masters()

	def test_names_become_the_codes_of_the_masters(self):
		self.assertEqual(
			geo.codes_for_names(
				region="Geolink Region",
				zone="Geolink Zone",
				woreda="Geolink Woreda A",
				kebele="Geolink Kebele",
			),
			{"region": REGION, "zone": ZONE, "woreda": WOREDA_A, "kebele": KEBELE_A},
		)

	def test_a_kebele_name_is_read_under_its_own_woreda(self):
		found = geo.codes_for_names(
			region="Geolink Region", zone="Geolink Zone", woreda="Geolink Woreda B", kebele="Geolink Kebele"
		)
		self.assertEqual((found["woreda"], found["kebele"]), (WOREDA_B, KEBELE_B))

	def test_a_code_is_kept_and_its_parents_filled(self):
		self.assertEqual(
			geo.codes_for_names(kebele=KEBELE_B),
			{"region": REGION, "zone": ZONE, "woreda": WOREDA_B, "kebele": KEBELE_B},
		)

	def test_a_level_with_no_name_is_skipped(self):
		# The list rows of the registry carry no Zone.
		self.assertEqual(
			geo.codes_for_names(region="Geolink Region", woreda="Geolink Woreda A", kebele="Geolink Kebele"),
			{"region": REGION, "zone": ZONE, "woreda": WOREDA_A, "kebele": KEBELE_A},
		)

	def test_the_walk_stops_at_an_unknown_name(self):
		self.assertEqual(
			geo.codes_for_names(
				region="Geolink Region", zone="Geolink Zone", woreda="No Such Woreda", kebele="Geolink Kebele"
			),
			{"region": REGION, "zone": ZONE, "woreda": None, "kebele": None},
		)

	def test_a_name_held_twice_with_no_parent_is_not_guessed(self):
		self.assertEqual(geo.codes_for_names(kebele="Geolink Kebele"), NOTHING)

	def test_nothing_given(self):
		self.assertEqual(geo.codes_for_names(), NOTHING)


class TestFarmerReferenceGeoLinks(IntegrationTestCase):
	"""GET /farmers/{id}/profile, /plots and /visits all cache the farmer first."""

	IN_A, IN_B, NOWHERE = "geolink-farmer-a", "geolink-farmer-b", "geolink-farmer-x"

	def setUp(self):
		_masters()
		frappe.db.delete("DA Farmer Reference", {"name": ("in", [self.IN_A, self.IN_B, self.NOWHERE])})
		self.registry = _Registry(
			Farmer(farmer_id=self.IN_A, record_name="Farmer A", state="ACTIVE", **NAMES_A),
			Farmer(
				farmer_id=self.IN_B,
				record_name="Farmer B",
				state="ACTIVE",
				**{**NAMES_A, "woreda_name": "Geolink Woreda B"},
			),
			Farmer(
				farmer_id=self.NOWHERE,
				record_name="Farmer X",
				state="ACTIVE",
				region_name="Tigray Not Synced",
				kebele_name="Lekatit Not Synced",
			),
		)
		patcher = patch(CLIENT, return_value=self.registry)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_the_cache_row_links_the_masters(self):
		svc.get_farmer_profile(self.IN_A)
		row = frappe.db.get_value(
			"DA Farmer Reference", self.IN_A, ["region", "zone", "woreda", "kebele"], as_dict=True
		)
		self.assertEqual(row, {"region": REGION, "zone": ZONE, "woreda": WOREDA_A, "kebele": KEBELE_A})

	def test_the_profile_gives_the_places_by_code(self):
		for _ in range(2):  # a fresh fetch, then the cached row
			profile = svc.get_farmer_profile(self.IN_A)
			self.assertEqual(
				(profile["region"], profile["woreda"], profile["kebele"]), (REGION, WOREDA_A, KEBELE_A)
			)

	def test_a_place_the_masters_do_not_have_does_not_fail_the_read(self):
		profile = svc.get_farmer_profile(self.NOWHERE)
		self.assertEqual(profile["name"], "Farmer X")
		self.assertEqual((profile["region"], profile["woreda"], profile["kebele"]), (None, None, None))

	def test_a_development_agent_reads_the_farmers_of_their_kebele_only(self):
		agent = _user("geolink-da@da.local", C.ROLE_DA, DA_Kebele=KEBELE_A)
		with as_user(agent):
			self.assertEqual(svc.get_farmer_profile(self.IN_A)["name"], "Farmer A")
			with self.assertRaises(frappe.PermissionError):
				svc.get_farmer_profile(self.IN_B)

	def test_a_supervisor_reads_the_farmers_of_their_woreda_only(self):
		supervisor = _user("geolink-sup@da.local", C.ROLE_SUPERVISOR, DA_Woreda=WOREDA_B)
		with as_user(supervisor):
			self.assertEqual(svc.get_farmer_profile(self.IN_B)["name"], "Farmer B")
			with self.assertRaises(frappe.PermissionError):
				svc.get_farmer_profile(self.IN_A)

	def test_an_administrator_reads_every_farmer(self):
		admin = _user("geolink-admin@da.local", C.ROLE_ADMIN)
		with as_user(admin):
			for farmer_id in (self.IN_A, self.IN_B):
				self.assertEqual(svc.get_farmer_profile(farmer_id)["farmer_id"], farmer_id)
