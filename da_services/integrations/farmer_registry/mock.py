"""Mock Farmer Registry client for development and testing.

Returns fixture data matching the OpenG2P Farmer Registry schema. Safe to use
without a live Farmer Registry instance.
"""

from datetime import date, datetime

from .client import FarmerRegistryClient, FarmerRegistryError
from .schemas import Farmer, FarmerListItem, Plot

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

	def get_farmer_plots(self, farmer_id: str) -> list[Plot]:
		"""Get mock plots for a farmer."""
		return MOCK_PLOTS.get(farmer_id, [])

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
