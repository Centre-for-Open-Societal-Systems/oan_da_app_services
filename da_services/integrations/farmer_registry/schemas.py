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

	# Completeness: register tabs the client could not fetch (e.g. "location",
	# "household"). Empty means every section was read. Callers should treat a
	# record with entries here as partial rather than as "no phone / no household".
	incomplete_sections: list[str] = Field(default_factory=list)


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
	system: str = Field(
		"mixed", description="mixed | semi_nomadic | sedentary_pastoral | nomadic_pastoral | industrial"
	)


class MembershipInfo(BaseModel):
	"""Cooperative / union / cluster membership. The registry requires the three
	yes/no answers before it finalizes a registration; they default to no."""

	is_primary_cooperative_member: bool = False
	primary_cooperative_name: str | None = Field(None, max_length=140)
	is_cooperative_union_member: bool = False
	cooperative_union_name: str | None = Field(None, max_length=140)
	is_farmer_cluster_member: bool = False
	farmer_cluster_role: str | None = Field(
		None, max_length=80, description="lead | deputy | secretary | accountant | member"
	)
	primary_commodity: str | None = Field(None, max_length=80, description="crop name, e.g. teff")


def _check_person_name(v):
	"""The registry's name rule: letters only, with spaces, apostrophes and hyphens.

	Checked here so a digit in a name is a 400 with the field named, instead of
	a 502 after the registry has already created a draft.
	"""
	if v is None:
		return None
	s = str(v).strip()
	if s == "":
		return None
	if not all(ch.isalpha() or ch in " '-" for ch in s):
		raise ValueError("use letters only (spaces, apostrophes and hyphens allowed)")
	return s


def _norm_gender_value(v):
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


class HouseholdMemberItem(BaseModel):
	"""A household member other than the farmer (the farmer is always the head)."""

	first_name: str = Field(..., min_length=1, max_length=100)
	father_name: str | None = Field(None, max_length=100)
	grandfather_name: str = Field(..., min_length=1, max_length=100)
	gender: str = Field(..., description="male | female | other")
	date_of_birth: date
	relationship: str = Field(
		..., description="spouse | child | parent | sibling | other_relative | non_relative"
	)

	@field_validator("first_name", "father_name", "grandfather_name", mode="before")
	@classmethod
	def _check_names(cls, v):
		return _check_person_name(v)

	@field_validator("gender", mode="before")
	@classmethod
	def _norm_gender(cls, v):
		return _norm_gender_value(v)


class HouseholdInfo(BaseModel):
	"""The farmer's household. size counts the farmer, so size == 1 + len(members)."""

	size: int | None = Field(None, ge=1, le=100)
	number_of_children: int | None = Field(None, ge=0, le=100)
	other_land_owner: bool = False
	members: list[HouseholdMemberItem] = Field(default_factory=list, max_length=99)


class FarmerRegistrationForm(BaseModel):
	"""Body of POST /farmers.

	Mirrors the fields the DA Register Farmer form collects. Unknown keys are
	ignored so the frontend can send UI-only fields; known keys are typed and
	range-checked here so the service and mapper never see malformed input.

	The intake form path (farmer_registry_register_mode = "intake") needs more
	than ingest did; intake.py checks those fields and names what is missing.
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
	kebele: str | None = Field(
		None,
		pattern=r"^(ET)?\d{1,15}$",
		description="Kebele code: Master Data id (ET04080101) for intake, numeric P-code for ingest",
	)
	national_id: str | None = Field(None, pattern=r"^\d{10,20}$", description="Fayda FAN (16) or FIN (12)")
	fayda_rid: str | None = Field(None, pattern=r"^\d{29}$", description="Fayda registration slip id")
	location: str | None = Field(None, description='"lat lon [alt [accuracy]]"')
	marital_status: str | None = Field(None, max_length=40, description="single | married | widowed | ...")
	education_level: str | None = Field(None, max_length=40, description="illiterate | primary | ...")
	income_sources: list[str] = Field(default_factory=list, max_length=10)
	income_source_other: str | None = Field(
		None, max_length=140, description='What "other" is; required when income_sources includes other'
	)
	disabled: bool = False
	disability_type: str | None = Field(
		None, max_length=40, description="vision | hearing | mobility | cognition | self_care | communication"
	)
	disability_severity: str | None = Field(
		None,
		max_length=40,
		description="no_difficulty | some_difficulty | a_lot_of_difficulty | cannot_do_at_all",
	)
	is_psnp_user: bool | None = None
	household: HouseholdInfo | None = None
	membership: MembershipInfo | None = None
	identity_document_id: str | None = Field(
		None,
		pattern=r"^[A-Za-z0-9-]{8,100}$",
		description="document_id from POST /farmers/registrations/documents (a registry uuid)",
	)
	identity_document_type: str = Field(
		"fayda", max_length=60, description="fayda | kebele_woreda_id | passport | ..."
	)
	lands: list[LandItem] = Field(default_factory=list, max_length=50)
	crops: list[CropItem] = Field(default_factory=list, max_length=50)
	livestock: list[LivestockItem] = Field(default_factory=list, max_length=50)
	da_id: str | None = Field(None, max_length=40)
	farmer_reference_id: str | None = Field(None, max_length=64)
	client_op_id: str | None = Field(
		None, pattern=r"^[A-Za-z0-9_-]{8,64}$", description="Idempotency key chosen by the client"
	)

	@field_validator("first_name", "father_name", "grandfather_name", mode="before")
	@classmethod
	def _check_names(cls, v):
		return _check_person_name(v)

	@field_validator("gender", mode="before")
	@classmethod
	def _norm_gender(cls, v):
		return _norm_gender_value(v)

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
