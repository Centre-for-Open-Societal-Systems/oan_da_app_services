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

	# Socio-economic (same vocabulary as FarmerRegistrationForm, so what a DA
	# registers reads back under the same names)
	source_of_income: str | None = None
	is_psnp_user: bool = False
	marital_status: str | None = None
	disabled: bool | None = None


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


# Registry vocabularies for the Socio-economic and Health section. The codes are
# the registry's attribute values (farmer-extension/meta_data/lookup-data/
# g2p_attribute_values*.sql: MARITAL_STATUS, EDUCATION_LEVEL, SOURCE_OF_INCOME),
# written the way the ODK form sends them; the registry's transform
# (odk/templates/farmer_transform.j2) upper-cases them on intake.
MARITAL_STATUSES = frozenset({"single", "married", "divorced", "widowed", "separated", "unknown"})
EDUCATION_LEVELS = frozenset(
	{"illiterate", "can_read_and_write", "basic", "intermediary", "higher_education"}
)
INCOME_SOURCES = frozenset({"crop_production", "livestock_production", "government_ngo_support", "others"})

_TRUE_WORDS = frozenset({"yes", "y", "true", "t", "1", "on"})
_FALSE_WORDS = frozenset({"no", "n", "false", "f", "0", "off"})


def _coerce_yes_no(v):
	"""bool | 0/1 | 'yes'/'no' | 'true'/'false' -> bool; None/'' -> None (field omitted)."""
	if v is None or v == "":
		return None
	if isinstance(v, bool):
		return v
	if isinstance(v, int | float) and v in (0, 1):
		return bool(v)
	s = str(v).strip().lower()
	if s in _TRUE_WORDS:
		return True
	if s in _FALSE_WORDS:
		return False
	raise ValueError("must be a boolean, yes/no, true/false or 1/0")


def _coerce_code(v, allowed: frozenset[str], label: str):
	"""Normalise a registry code ('Crop Production' -> 'crop_production') and check it."""
	if v is None or v == "":
		return None
	s = str(v).strip().lower().replace("-", "_").replace(" ", "_")
	if s not in allowed:
		raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")
	return s


class FarmerRegistrationForm(BaseModel):
	"""Body of POST /farmers.

	Mirrors the fields the DA Register Farmer form collects. Unknown keys are
	ignored so the frontend can send UI-only fields; known keys are typed and
	range-checked here so the service and mapper never see malformed input.

	Boolean fields (household_head, is_psnp_user, disabled) accept a JSON bool,
	0/1, or the strings yes/no, true/false (any case). Coded fields
	(marital_status, education_level, income_source) accept the registry codes in
	any case, with spaces or hyphens for underscores.
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
	# Household Link + Socio-economic and Health (registry tabs 2 and 1.5)
	household_head: bool | None = Field(
		None, description="Is the farmer the household head? yes = registry creates a Household record"
	)
	is_psnp_user: bool | None = Field(None, description="PSNP beneficiary")
	disabled: bool | None = Field(None, description="Has a disability")
	marital_status: str | None = Field(
		None, max_length=40, description="One of: " + ", ".join(sorted(MARITAL_STATUSES))
	)
	education_level: str | None = Field(
		None, max_length=60, description="One of: " + ", ".join(sorted(EDUCATION_LEVELS))
	)
	income_source: str | None = Field(
		None, max_length=60, description="One of: " + ", ".join(sorted(INCOME_SOURCES))
	)
	farmer_reference_id: str | None = Field(None, max_length=64)
	client_op_id: str | None = Field(
		None, pattern=r"^[A-Za-z0-9_-]{8,64}$", description="Idempotency key chosen by the client"
	)

	@field_validator("household_head", "is_psnp_user", "disabled", mode="before")
	@classmethod
	def _norm_yes_no(cls, v):
		return _coerce_yes_no(v)

	@field_validator("marital_status", mode="before")
	@classmethod
	def _norm_marital_status(cls, v):
		return _coerce_code(v, MARITAL_STATUSES, "marital_status")

	@field_validator("education_level", mode="before")
	@classmethod
	def _norm_education_level(cls, v):
		return _coerce_code(v, EDUCATION_LEVELS, "education_level")

	@field_validator("income_source", mode="before")
	@classmethod
	def _norm_income_source(cls, v):
		return _coerce_code(v, INCOME_SOURCES, "income_source")

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
