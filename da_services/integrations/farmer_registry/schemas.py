"""Pydantic schemas for Farmer Registry data.

Models match the Farmer Registry's OpenG2P-based schema (farmer.py models).
Only fields needed for DA-40 profile display are included.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FarmerIdentity(BaseModel):
	"""Farmer identification and basic info."""

	farmer_id: str = Field(..., description="Farmer Registry ID (internal_record_id)")
	reg_id: str | None = Field(None, description="External Farmer-ID (FR-xxxxx)")
	given_name: str | None = Field(None, description="First name (English)")
	family_name: str | None = Field(None, description="Last name (English)")
	father_first_name: str | None = Field(None, description="Father's first name")
	first_name_amh: str | None = Field(None, description="First name (Amharic)")
	first_name_om: str | None = Field(None, description="First name (Afaan Oromo)")
	record_name: str | None = Field(None, description="Display name (from registry)")


class FarmerContact(BaseModel):
	"""Farmer contact information."""

	phone: str | None = Field(None, description="Primary phone number")
	email: str | None = Field(None, description="Email address")
	has_personal_phone: bool = Field(False, description="Has a personal phone")


class FarmerLocation(BaseModel):
	"""Farmer geographic location."""

	region_name: str | None = Field(None, description="Region name")
	zone_name: str | None = Field(None, description="Zone name")
	woreda_name: str | None = Field(None, description="Woreda name")
	kebele_name: str | None = Field(None, description="Kebele name")
	woreda_level_value_id: str | None = Field(None, description="Woreda ID for filtering")


class FarmerDemographics(BaseModel):
	"""Farmer demographic information."""

	birth_date: date | None = Field(None, description="Birth date (Gregorian)")
	birth_date_ec: str | None = Field(None, description="Birth date (Ethiopian calendar)")
	estimated_age: int | None = Field(None, description="Estimated age in years")
	gender: str | None = Field(None, description="Gender (male/female/other)")
	disabled: bool = Field(False, description="Has disability")
	disability_type: str | None = Field(None, description="Type of disability")
	language_spoken: str | None = Field(None, description="Primary language")
	education_level: str | None = Field(None, description="Education level")


class FarmerHousehold(BaseModel):
	"""Farmer household information."""

	is_household_head: bool = Field(False, description="Is head of household")
	size_of_group: int | None = Field(None, description="Household size (from Household table)")
	number_of_children: int | None = Field(None, description="Number of children")


class FarmerSocioeconomic(BaseModel):
	"""Farmer socio-economic data."""

	source_of_income: str | None = Field(None, description="Primary source of income")
	is_psnp_user: bool = Field(False, description="PSNP beneficiary")
	national_id_masked: str | None = Field(None, description="Masked national ID")


class FarmerLand(BaseModel):
	"""Farmer land holdings summary."""

	total_land_area: float | None = Field(None, description="Total land area (hectares)")
	total_land_owned_area: float | None = Field(None, description="Owned land (hectares)")
	total_land_rent_area: float | None = Field(None, description="Rented land (hectares)")
	land_ownership: str | None = Field(None, description="Ownership type (OWNER/TENANT/HYBRID)")


class FarmerRegistration(BaseModel):
	"""Farmer registration metadata."""

	state: str = Field("APPROVED", description="Farmer state (DRAFT/PENDING_APPROVAL/APPROVED)")
	import_source: str | None = Field(None, description="Data source (INTAKE_FORM/PARTNER/etc)")
	registration_date: datetime | None = Field(None, description="When registered")
	is_duplicated: bool = Field(False, description="Flagged as potential duplicate")
	enumerator_name: str | None = Field(None, description="Enumerator who collected data")


class Farmer(BaseModel):
	"""Complete farmer record (DA-40 profile view).

	Combines all farmer data needed for the DA profile screen.
	"""

	# Identity
	farmer_id: str = Field(..., description="Farmer Registry ID")
	reg_id: str | None = Field(None, description="External Farmer-ID")
	given_name: str | None = Field(None, description="First name")
	family_name: str | None = Field(None, description="Last name")
	father_first_name: str | None = Field(None, description="Father's first name")
	record_name: str | None = Field(None, description="Display name")

	# Contact
	phone: str | None = None
	email: str | None = None
	has_personal_phone: bool = False

	# Location
	region_name: str | None = None
	zone_name: str | None = None
	woreda_name: str | None = None
	kebele_name: str | None = None

	# Demographics
	birth_date: date | None = None
	gender: str | None = None
	education_level: str | None = None

	# Household
	is_household_head: bool = False
	household_size: int | None = Field(None, description="From Household table")

	# Land
	total_land_area: float | None = None
	land_ownership: str | None = None

	# Registration
	state: str = "APPROVED"
	registration_date: datetime | None = None

	# Socio-economic
	source_of_income: str | None = None
	is_psnp_user: bool = False


class FarmerListItem(BaseModel):
	"""Farmer list item for /farmers endpoint."""

	farmer_id: str
	reg_id: str | None = None
	name: str = Field(..., description="Display name")
	phone: str | None = None
	kebele: str | None = None
	woreda: str | None = None
	region: str | None = None
	total_land_area: float | None = None
	state: str
	registration_date: datetime | None = None


class Plot(BaseModel):
	"""Land plot information for /farmers/{id}/plots."""

	plot_id: str = Field(..., description="Land record ID")
	land_kebele: str | None = Field(None, description="Plot kebele")
	area_in_hectare: float | None = Field(None, description="Area in hectares")
	land_ownership_type: str | None = Field(None, description="OWNER/TENANT/etc")
	soil_fertility: str | None = None
	current_land_use: str | None = None
	certificate_provided: bool = False


# ---------------------------------------------------------------------------
# Farmer registration (POST /farmers) — validated at the API boundary
# ---------------------------------------------------------------------------


class LandItem(BaseModel):
	land_id: str | None = Field(None, max_length=50)
	ownership: str = Field("owner", description="owner | tenant | share")
	area_hectare: float | None = Field(None, ge=0, le=10000)


class CropItem(BaseModel):
	name: str = Field(..., min_length=1, max_length=80)
	planted_date: date | None = None
	season: str = Field("meher", description="meher | belg | irrigated | perennial")


class LivestockItem(BaseModel):
	animal: str = Field(..., min_length=1, max_length=80)
	count: int = Field(1, ge=1, le=100000)
	system: str = Field("mixed", description="mixed | intensive | extensive")


class FarmerRegistrationForm(BaseModel):
	"""Body of POST /farmers.

	Mirrors the fields the DA Register Farmer form collects. Unknown keys are
	ignored so the frontend can send UI-only fields; known keys are typed and
	range-checked here so the service and mapper never see malformed input.
	"""

	model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

	first_name: str = Field(..., min_length=1, max_length=100)
	father_name: str | None = Field(None, max_length=100)
	grandfather_name: str | None = Field(None, max_length=100)
	gender: str | None = Field(None, description="male | female | other")
	date_of_birth: date | None = None
	phone: str | None = Field(None, pattern=r"^\+?\d{9,15}$")
	region: str | None = Field(None, max_length=20)
	woreda: str | None = Field(None, pattern=r"^\d{1,12}$", description="Master Data woreda code")
	kebele: str | None = Field(None, pattern=r"^\d{1,15}$", description="Master Data kebele code")
	national_id: str | None = Field(None, pattern=r"^\d{10,20}$", description="Fayda / national id")
	location: str | None = Field(None, description='"lat lon [alt [accuracy]]"')
	lands: list[LandItem] = Field(default_factory=list, max_length=50)
	crops: list[CropItem] = Field(default_factory=list, max_length=50)
	livestock: list[LivestockItem] = Field(default_factory=list, max_length=50)
	da_id: str | None = Field(None, max_length=40)
	farmer_reference_id: str | None = Field(None, max_length=64)
	client_op_id: str | None = Field(
		None, pattern=r"^[A-Za-z0-9_-]{8,64}$", description="Idempotency key chosen by the client"
	)

	@field_validator("gender", mode="before")
	@classmethod
	def _norm_gender(cls, v):
		if v is None or v == "":
			return None
		s = str(v).strip().lower()
		if s in ("m", "male"):
			return "male"
		if s in ("f", "female"):
			return "female"
		if s == "other":
			return "other"
		raise ValueError("gender must be male, female or other")

	@field_validator("location")
	@classmethod
	def _check_location(cls, v):
		if v is None or v == "":
			return None
		parts = v.split()
		if not 2 <= len(parts) <= 4:
			raise ValueError('location must be "lat lon [alt [accuracy]]"')
		try:
			nums = [float(p) for p in parts]
		except ValueError as e:
			raise ValueError("location parts must be numbers") from e
		if not (-90 <= nums[0] <= 90 and -180 <= nums[1] <= 180):
			raise ValueError("latitude/longitude out of range")
		return " ".join(parts)
