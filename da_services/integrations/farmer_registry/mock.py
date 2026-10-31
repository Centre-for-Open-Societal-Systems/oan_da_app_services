"""Mock Farmer Registry client for development and testing.

Returns fixture data matching the OpenG2P Farmer Registry schema. Safe to use
without a live Farmer Registry instance.
"""

import uuid
from datetime import date, datetime

from .client import FarmerRegistryClient, FarmerRegistryError
from .schemas import Farmer, FarmerListItem, Plot

# Intake form fixtures, trimmed from the dev registry and Master Data (28 Oct 2026).
MOCK_INTAKE_FORM_ID = "mock-intake-form"
MOCK_TABS = {
	"farmer": "farmer_farmer_tab",
	"location": "farmer_location_tab",
	"household": "farmer_household_link_tab",
	"land": "farmer_land_tab",
	"crop": "farmer_crop_tab",
	"livestock": "farmer_livestock_tab",
}
# Change requests as /change-requests/get_change_requests returns them (DA-272)
MOCK_CHANGE_REQUESTS = {
	"farmer-uuid-001": [
		{
			"change_request_id": "cr-001-approved",
			"record_name": "Abebe Kebede",
			"register_id": "reg-farmer",
			"register_mnemonic": "Farmer",
			"tab_id": "farmer_farmer_tab",
			"section_id": "farmer_name",
			"source_partner_id": "Registry Staff Portal UI",
			"created_by": "Nina Patel",
			"created_at": "2026-10-20T09:00:00",
			"no_of_verifications_required": 0,
			"no_of_verifications_done": 0,
			"approval_status": "APPROVED",
			"approved_by": "Nina Patel",
			"approved_at": "2026-10-20T10:00:00",
		},
		{
			"change_request_id": "cr-001-pending",
			"record_name": "Abebe Kebede",
			"register_id": "reg-farmer",
			"register_mnemonic": "Farmer",
			"tab_id": "farmer_location_tab",
			"section_id": "farmer_location",
			"source_partner_id": "DA_SERVICES",
			"created_by": "da.demo@da.local",
			"created_at": "2026-10-28T14:30:00",
			"no_of_verifications_required": 1,
			"no_of_verifications_done": 0,
			"approval_status": "PENDING",
			"approved_by": None,
			"approved_at": None,
		},
	]
}
MOCK_INTAKE_REGISTERS = {
	"Farmer": "reg-farmer",
	"FarmerPhone": "reg-phone",
	"Household": "reg-household",
	"HouseholdMember": "reg-member",
	"Land": "reg-land",
	"Crop": "reg-crop",
	"FarmInputs": "reg-inputs",
	"Livestock": "reg-livestock",
	"MembershipDetails": "reg-membership",
}
MOCK_CODE_LISTS = {
	"GENDER": [("FEMALE", "Female"), ("MALE", "Male"), ("OTHER", "Other")],
	"MARITAL_STATUS": [
		("SINGLE", "Single"),
		("MARRIED", "Married"),
		("WIDOWED", "Widowed"),
		("DIVORCED", "Divorced"),
	],
	"EDUCATION_LEVEL": [
		("ILLITERATE", "Illiterate"),
		("CAN_READ_AND_WRITE", "Can read and write"),
		("PRIMARY", "Primary"),
		("HIGHER_EDUCATION", "Higher education"),
	],
	"SOURCE_OF_INCOME": [
		("SOI_CROP_PRODUCTION", "Crop Production"),
		("SOI_LIVESTOCK_PRODUCTION", "Livestock Production"),
		("SOI_GOVERNMENT_NGO_SUPPORT", "Government / NGO Support"),
		("SOI_OTHERS", "Others"),
	],
	"RELATIONSHIP_TO_HEAD": [("SELF", "Self (head)"), ("SPOUSE", "Spouse"), ("CHILD", "Child")],
	"IDENTITY_DOCUMENT_TYPE": [("IDDOC_FAYDA", "Fayda credential"), ("IDDOC_PASSPORT", "Ethiopian passport")],
	"LAND_OWNERSHIP_TYPE": [("OWNER", "Owner"), ("TENANT", "Tenant"), ("CROP_SHARE", "Crop share")],
	"CROP_COMMODITY": [("CROP_TEFF", "Teff for testing"), ("CROP_MAIZE", "Maize")],
	"CROP_SEASON": [("SEASON_MEHER", "Meher (main rains)"), ("SEASON_BELG", "Belg (short rains)")],
	"LIVESTOCK_TYPE": [("LSTK_CATTLE", "CATTLE"), ("LSTK_GOAT", "GOAT")],
	"LIVESTOCK_SYSTEM": [("MIXED", "Mixed"), ("INDUSTRIAL", "Industrial")],
	"FARMER_CLUSTER_ROLE": [("LEAD", "Lead"), ("MEMBER", "Member")],
	"DISABILITY_DOMAIN": [("VISION", "Vision"), ("MOBILITY", "Mobility")],
	"DISABILITY_SEVERITY": [("SOME_DIFFICULTY", "Some difficulty"), ("CANNOT_DO_AT_ALL", "Cannot do at all")],
}
MOCK_GEO_LEVELS = ("country", "region", "zone", "woreda", "kebele")
# level_value_id -> (level_id, name, parent)
MOCK_PLACES = {
	"ET04": ("l1", "Oromia", None),
	"ET0408": ("l2", "Arsi", "ET04"),
	"ET040801": ("l3", "Merti", "ET0408"),
	"ET04080101": ("l4", "Abomsa_01", "ET040801"),
	"ET04080102": ("l4", "Abomsa_02", "ET040801"),
}

# Mock farmer data matching Farmer Registry schema
MOCK_FARMERS = {
	"farmer-uuid-001": Farmer(
		farmer_id="farmer-uuid-001",
		reg_id="FR-000101",
		given_name="Abebe",
		family_name="Kebede",
		father_first_name="Kebede",
		record_name="Abebe Kebede",
		phone="911234567",
		email="abebe@example.com",
		has_personal_phone=True,
		region_name="ET04",
		zone_name="ET04-Z01",
		woreda_name="ET04-W01",
		kebele_name="Bako 01",
		birth_date=date(1985, 3, 15),
		gender="male",
		education_level="Primary",
		is_household_head=True,
		household_size=5,
		total_land_area=2.5,
		land_ownership="OWNER",
		state="APPROVED",
		registration_date=datetime(2024, 6, 1, 10, 30),
		source_of_income="Crop production",
		is_psnp_user=False,
	),
	"farmer-uuid-002": Farmer(
		farmer_id="farmer-uuid-002",
		reg_id="FR-000102",
		given_name="Almaz",
		family_name="Tesfaye",
		father_first_name="Tesfaye",
		record_name="Almaz Tesfaye",
		phone="922345678",
		email=None,
		has_personal_phone=True,
		region_name="ET04",
		zone_name="ET04-Z01",
		woreda_name="ET04-W01",
		kebele_name="Bako 02",
		birth_date=date(1990, 7, 22),
		gender="female",
		education_level="Secondary",
		is_household_head=False,
		household_size=4,
		total_land_area=1.8,
		land_ownership="TENANT",
		state="APPROVED",
		registration_date=datetime(2024, 6, 15, 14, 20),
		source_of_income="Mixed farming",
		is_psnp_user=True,
	),
	"farmer-uuid-003": Farmer(
		farmer_id="farmer-uuid-003",
		reg_id="FR-000103",
		given_name="Chaltu",
		family_name="Deressa",
		father_first_name="Deressa",
		record_name="Chaltu Deressa",
		phone="933456789",
		email="chaltu@example.com",
		has_personal_phone=True,
		region_name="ET03",
		zone_name="ET03-Z01",
		woreda_name="ET03-W02",
		kebele_name="Kebele 03",
		birth_date=date(1988, 11, 5),
		gender="female",
		education_level="Tertiary",
		is_household_head=True,
		household_size=6,
		total_land_area=3.2,
		land_ownership="HYBRID",
		state="APPROVED",
		registration_date=datetime(2024, 5, 20, 9, 15),
		source_of_income="Livestock and crops",
		is_psnp_user=False,
	),
}

# Record photos (the registry's signed MinIO link and document id)
MOCK_PHOTOS = {
	"farmer-uuid-001": {
		"url": "https://minio.example.com/documents/mock-photo-001?X-Amz-Expires=3600",
		"document_id": "mock-photo-001",
	},
}

# Mock plots
MOCK_PLOTS = {
	"farmer-uuid-001": [
		Plot(
			plot_id="plot-001-1",
			land_kebele="Bako 01",
			area_in_hectare=2.5,
			land_ownership_type="OWNER",
			soil_fertility="Good",
			current_land_use="CROP_PRODUCTION",
			certificate_provided=True,
		),
	],
	"farmer-uuid-002": [
		Plot(
			plot_id="plot-002-1",
			land_kebele="Bako 02",
			area_in_hectare=1.8,
			land_ownership_type="TENANT",
			soil_fertility="Medium",
			current_land_use="CROP_PRODUCTION",
			certificate_provided=False,
		),
	],
	"farmer-uuid-003": [
		Plot(
			plot_id="plot-003-1",
			land_kebele="Bako 01",
			area_in_hectare=2.0,
			land_ownership_type="OWNER",
			soil_fertility="Good",
			current_land_use="CROP_PRODUCTION",
			certificate_provided=True,
		),
		Plot(
			plot_id="plot-003-2",
			land_kebele="Bako 01",
			area_in_hectare=1.2,
			land_ownership_type="TENANT",
			soil_fertility="Medium",
			current_land_use="GRAZING",
			certificate_provided=False,
		),
	],
}


class MockFarmerRegistryClient(FarmerRegistryClient):
	"""Mock implementation for development without live Farmer Registry."""

	def get_farmer(self, farmer_id: str) -> Farmer | None:
		"""Get farmer from mock fixtures."""
		return MOCK_FARMERS.get(farmer_id)

	def list_farmers(
		self,
		*,
		woreda: str | None = None,
		kebele: str | None = None,
		state: str | None = None,
		limit: int = 50,
		offset: int = 0,
	) -> tuple[list[FarmerListItem], int]:
		"""List mock farmers with filters."""
		farmers = list(MOCK_FARMERS.values())

		# Apply filters
		if woreda:
			farmers = [f for f in farmers if f.woreda_name == woreda]
		if kebele:
			farmers = [f for f in farmers if f.kebele_name == kebele]
		if state:
			farmers = [f for f in farmers if f.state == state]

		total = len(farmers)

		# Pagination
		farmers = farmers[offset : offset + limit]

		# Convert to list items
		items = [
			FarmerListItem(
				farmer_id=f.farmer_id,
				reg_id=f.reg_id,
				name=f.record_name or f"{f.given_name} {f.family_name}",
				phone=f.phone,
				kebele=f.kebele_name,
				woreda=f.woreda_name,
				region=f.region_name,
				total_land_area=f.total_land_area,
				state=f.state,
				registration_date=f.registration_date,
			)
			for f in farmers
		]

		return items, total

	def search_farmers(
		self,
		*,
		search_text: str = "",
		filter_by: dict | None = None,
		page: int = 1,
		page_size: int = 50,
	) -> dict:
		"""Search mock farmers by name / ids with the same filters as list_farmers."""
		page = max(1, int(page or 1))
		page_size = max(1, int(page_size or 50))
		filters = filter_by or {}
		farmers = list(MOCK_FARMERS.values())
		if filters.get("woreda"):
			farmers = [f for f in farmers if f.woreda_name == filters["woreda"]]
		if filters.get("kebele"):
			farmers = [f for f in farmers if f.kebele_name == filters["kebele"]]
		if filters.get("state"):
			farmers = [f for f in farmers if f.state == filters["state"]]
		needle = (search_text or "").strip().lower()
		if needle:
			farmers = [
				f
				for f in farmers
				if needle in " ".join(filter(None, (f.record_name, f.reg_id, f.farmer_id, f.phone))).lower()
			]
		total = len(farmers)
		start = (page - 1) * page_size
		items = [
			FarmerListItem(
				farmer_id=f.farmer_id,
				reg_id=f.reg_id,
				name=f.record_name or f"{f.given_name} {f.family_name}",
				phone=f.phone,
				kebele=f.kebele_name,
				woreda=f.woreda_name,
				region=f.region_name,
				total_land_area=f.total_land_area,
				state=f.state,
				registration_date=f.registration_date,
			)
			for f in farmers[start : start + page_size]
		]
		return {
			"items": items,
			"page": page,
			"page_size": page_size,
			"total": total,
			"number_of_pages": (total + page_size - 1) // page_size,
		}

	def get_farmer_plots(self, farmer_id: str) -> list[Plot]:
		"""Get mock plots for a farmer."""
		return MOCK_PLOTS.get(farmer_id, [])

	def get_farmer_photo(self, farmer_id: str) -> dict | None:
		"""A fixed photo link for the farmers that have one in the fixtures."""
		return MOCK_PHOTOS.get(farmer_id)

	def get_change_requests(
		self, farmer_id: str, *, tab: str | None = None, status: str | None = None
	) -> list[dict]:
		"""Change requests from fixtures, newest first."""
		rows = list(MOCK_CHANGE_REQUESTS.get(farmer_id, []))
		if tab:
			rows = [r for r in rows if r.get("tab_id") == MOCK_TABS.get(tab)]
		if status:
			rows = [r for r in rows if r.get("approval_status") == status.strip().upper()]
		return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)

	def update_farmer(self, farmer_id: str, updates: dict, expected_version: int | None = None) -> dict:
		"""Mock update with version conflict simulation (for DA-41).

		Simulates version conflict if expected_version is provided but doesn't match.
		In mock mode, we simulate versions but don't actually persist changes.

		Args:
		    farmer_id: Farmer Registry internal_record_id
		    updates: Dict of field:value pairs to update
		    expected_version: Expected current version (for testing conflicts)

		Returns:
		    Update result with version info or conflict flag
		"""
		# Get current farmer data (from mock or DA Farmer Reference cache)
		current_farmer = self.get_farmer(farmer_id)
		if not current_farmer:
			raise FarmerRegistryError(f"Farmer {farmer_id} not found")

		# Get version (default to 1 if not present)
		current_version = getattr(current_farmer, "version", 1)

		# Simulate version conflict detection
		if expected_version is not None and expected_version != current_version:
			# Version mismatch - return conflict
			# Convert Pydantic model to dict if needed
			current_data = (
				current_farmer.model_dump() if hasattr(current_farmer, "model_dump") else current_farmer
			)
			return {
				"success": False,
				"conflict": True,
				"farmer_id": farmer_id,
				"expected_version": expected_version,
				"current_version": current_version,
				"current_data": current_data,
				"message": f"Version conflict: expected v{expected_version}, server has v{current_version}",
			}

		# Success - return new version
		new_version = current_version + 1
		return {
			"success": True,
			"conflict": False,
			"farmer_id": farmer_id,
			"version": new_version,
			"message": f"Mock update successful - version updated to v{new_version}",
		}

	def register_farmer(self, payload: dict) -> dict:
		"""Mock farmer registration (ingest pipeline).

		Does not persist; returns a deterministic fake correlation_id so callers
		and tests can exercise the register flow without a live registry.
		"""
		if not isinstance(payload, dict) or not payload.get("message"):
			raise FarmerRegistryError("Invalid ingest payload: missing 'message'")

		message_id = (payload.get("header") or {}).get("message_id") or "mock-msg"
		correlation_id = f"mock-corr-{message_id}"
		return {
			"success": True,
			"correlation_id": correlation_id,
			"status": "SUCCESS",
			"error_code": None,
			"error_message": None,
		}

	# -- intake form (shape of the dev farmer_ingestion_intake form, trimmed) ----

	def render_intake_form(self) -> dict:
		def section(section_id, register_id, order, is_list=False, ui=None):
			return {
				"section_id": section_id,
				"section_register_id": register_id,
				"section_order": order,
				"is_list": is_list,
				"section_ui_schema": ui or {},
			}

		farmer = MOCK_INTAKE_REGISTERS["Farmer"]
		documents_ui = {"widgets": [{"widget-data-path": f"{farmer}.identity_document_1"}]}

		def select(register_id, field, attribute):
			# Same shape as the dev registry's dropdown widgets.
			return {
				"widget": "select",
				"widget-data-path": f"{register_id}.{field}",
				"widget-data-source": {"type": "api", "params": {"attribute_id": attribute}},
			}

		name_ui = {
			"widgets": [
				select(farmer, "gender", "GENDER"),
				select(farmer, "marital_status", "MARITAL_STATUS"),
			]
		}
		land_ui = {
			"widgets": [select(MOCK_INTAKE_REGISTERS["Land"], "land_ownership_type", "LAND_OWNERSHIP_TYPE")]
		}
		return {
			"form_id": MOCK_INTAKE_FORM_ID,
			"register_id": farmer,
			"tabs": [
				{
					"tab_id": "intake",
					"sections": [
						section("oan_farmer_name", farmer, 21, ui=name_ui),
						section("oan_farmer_phones", MOCK_INTAKE_REGISTERS["FarmerPhone"], 27, True),
						section("oan_household_information", MOCK_INTAKE_REGISTERS["Household"], 33),
						section(
							"household_member_section", MOCK_INTAKE_REGISTERS["HouseholdMember"], 35, True
						),
						section("oan_farmer_location", farmer, 40),
						section("oan_farmer_lands", MOCK_INTAKE_REGISTERS["Land"], 50, True, ui=land_ui),
						section("oan_farmer_crops_intake", MOCK_INTAKE_REGISTERS["Crop"], 60, True),
						section(
							"oan_farmer_farm_inputs_intake", MOCK_INTAKE_REGISTERS["FarmInputs"], 70, True
						),
						section("oan_farmer_livestock_intake", MOCK_INTAKE_REGISTERS["Livestock"], 80, True),
						section("oan_farmer_membership", MOCK_INTAKE_REGISTERS["MembershipDetails"], 90),
						section("oan_farmer_documents", farmer, 100, ui=documents_ui),
					],
				}
			],
		}

	def get_registers(self) -> list[dict]:
		return [
			{"register_id": rid, "register_mnemonic": name} for name, rid in MOCK_INTAKE_REGISTERS.items()
		]

	def get_intake_forms(self) -> list[dict]:
		return [
			{
				"form_id": MOCK_INTAKE_FORM_ID,
				"form_mnemonic": "farmer_ingestion_intake",
				"register_id": MOCK_INTAKE_REGISTERS["Farmer"],
				"used_only_in_ingestion_pipeline": True,
			}
		]

	def get_attribute_values(self, attribute_id: str) -> list[dict]:
		return [
			{"value_id": code, "value_code": code, "value_display": display}
			for code, display in MOCK_CODE_LISTS.get(attribute_id, [])
		]

	def get_geo_levels(self) -> list[dict]:
		return [{"level_id": f"l{i}", "level_mnemonic": name} for i, name in enumerate(MOCK_GEO_LEVELS)]

	def get_geo_level_values(self, level_id: str, parent_level_value_id: str | None = None) -> list[dict]:
		return [
			{
				"level_id": level_id,
				"level_value_id": vid,
				"level_value_mnemonic": name,
				"parent_level_value_id": parent,
			}
			for vid, (lid, name, parent) in MOCK_PLACES.items()
			if lid == level_id and parent == parent_level_value_id
		]

	def upload_document(self, filename: str, content: bytes, content_type: str) -> str:
		if not content:
			raise FarmerRegistryError("Empty document")
		return f"mock-doc-{uuid.uuid4()}"

	def save_intake_section(self, section: dict) -> dict:
		if not section.get("section_payload"):
			return {
				"success": False,
				"submission_id": section.get("submission_id"),
				"status": "ERROR",
				"error_code": "MOCK-ERR",
				"error_message": f"{section.get('section_id')}: no records",
			}
		return {
			"success": True,
			"submission_id": section.get("submission_id") or f"mock-sub-{uuid.uuid4()}",
			"status": "SUCCESS",
			"error_code": None,
			"error_message": None,
		}

	def finalize_intake_submission(self, submission_id: str) -> dict:
		return {
			"success": True,
			"submission_id": submission_id,
			"draft_status": "FINAL",
			"approval_status": "PENDING",
			"status": "SUCCESS",
			"error_code": None,
			"error_message": None,
		}

	def get_intake_submission(self, submission_id: str) -> dict | None:
		if not submission_id.startswith("mock-sub-") or submission_id in self.deleted_submissions:
			return None
		return {
			"submission_id": submission_id,
			"application_reference": "2026OCT29-000001",
			"record_name": "Abebe Tesfaye",
			"form_id": MOCK_INTAKE_FORM_ID,
			"register_id": MOCK_INTAKE_REGISTERS["Farmer"],
			"draft_status": "FINAL",
			"approval_status": "PENDING",
			"finalized_at": "2026-10-29T15:04:59",
			"approved_by": None,
			"approved_at": None,
			"section_payloads": [
				{
					"section_id": "oan_farmer_name",
					"section_register_id": MOCK_INTAKE_REGISTERS["Farmer"],
					"is_list": False,
					"records": [
						{
							"internal_record_id": "mock-farmer-row",
							"functional_record_id": None,
							"record_name": "Abebe Tesfaye",
							"first_name": "Abebe",
							"middle_name": "Kebede",
							"last_name": "Tesfaye",
							"search_text": "Abebe Kebede Tesfaye",
							"created_by": "Admin User",
						}
					],
					"documents": [],
				},
				{
					"section_id": "oan_farmer_phones",
					"section_register_id": MOCK_INTAKE_REGISTERS["FarmerPhone"],
					"is_list": True,
					"records": [{"internal_record_id": "mock-phone-row", "phone_number": "911234567"}],
					"documents": [],
				},
			],
		}

	@property
	def deleted_submissions(self) -> set:
		if not hasattr(self, "_deleted_submissions"):
			self._deleted_submissions = set()
		return self._deleted_submissions

	def delete_intake_submission(self, submission_id: str) -> bool:
		if not submission_id.startswith("mock-sub-") or submission_id in self.deleted_submissions:
			return False
		self.deleted_submissions.add(submission_id)
		return True

	def get_ingest_status(self, correlation_id: str) -> list[dict]:
		"""Mock ingestion status: report a single PROCESSED row."""
		return [
			{
				"ingest_correlation_id": correlation_id,
				"ingest_id": f"mock-ingest-{correlation_id}",
				"classification_status": "PROCESSED",
				"transformation_status": "PROCESSED",
				"ingestion_status": "PROCESSED",
			}
		]
