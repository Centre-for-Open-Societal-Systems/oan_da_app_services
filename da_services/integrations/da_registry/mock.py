"""In-memory DA Registry for development and tests.

Fixture DAs cover the states Part 2 has to handle: an active published DA, one on
leave, one separated, and one still awaiting Woreda review (not yet Active, so no
lifecycle action should be allowed on it).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime

from da_services.services import constants as C

from .client import DANotFound, DARegistryClient
from .mapper import to_da_reference
from .schemas import DAReference, IntegrationResult, LifecycleOutcome, ProfileUpdate, SectionChange

FIXTURE_DAS: dict[str, dict] = {
	"DA-000001": {
		"da_id": "DA-000001",
		"full_name": "Abebe Kebede",
		"active_status": C.ACTIVE_STATUS_ACTIVE,
		"approval_status": "Published",
		"region": "ET04",
		"zone": "ET04-Z01",
		"woreda": "ET04-W01",
		"assigned_kebele": "ET04-W01-K03",
		"education_tier": "Diploma",
		"fayda_status": "Verified",
		"phone": "+251911000001",
		"specialisation": "Crop production",
		"position": "Development agent",
		"position_code": "DEVELOPMENT_AGENT",
		"passout_year": 2011,
		"joined_at": "2019-09-01",
		"farmer_count": 128,
		"version": 3,
		# DA Registry #66: the photo's document, Education & Professional records with
		# certificates (main record first), family members.
		"photo_document_id": "doc-photo-000001",
		"qualifications": [
			{
				"id": "qual-000001-2",
				"education_level": "DIPLOMA",
				"education_level_label": "Diploma",
				"passout_year": 2011,
				"certificates": [{"document_id": "doc-cert-000001-a", "name": "diploma.pdf"}],
				"is_main": True,
			},
			{
				"id": "qual-000001-1",
				"education_level": "CERTIFICATE",
				"education_level_label": "Certificate",
				"passout_year": 2008,
				"certificates": [],
				"is_main": False,
			},
		],
		"dependents": [
			{
				"id": "dep-000001-1",
				"name": "Almaz Abebe",
				"relationship": "REL_DAUGHTER",
				"relationship_label": "Daughter",
				"date_of_birth": "2015-03-02",
			}
		],
	},
	"DA-000002": {
		"da_id": "DA-000002",
		"full_name": "Marta Tesfaye",
		"active_status": C.ACTIVE_STATUS_ON_LEAVE,
		"approval_status": "Published",
		"region": "ET04",
		"zone": "ET04-Z01",
		"woreda": "ET04-W01",
		"assigned_kebele": "ET04-W01-K07",
		"education_tier": "Degree",
		"fayda_status": "Verified",
		"phone": "+251911000002",
		"specialisation": "Livestock",
		"position": "Senior DA",
		"position_code": "SENIOR_DA",
		"joined_at": "2017-03-15",
		"farmer_count": 96,
		"version": 5,
	},
	"DA-000003": {
		"da_id": "DA-000003",
		"full_name": "Yonas Alemu",
		"active_status": C.ACTIVE_STATUS_SEPARATED,
		"approval_status": "Published",
		"region": "ET03",
		"zone": "ET03-Z02",
		"woreda": "ET03-W02",
		"assigned_kebele": None,
		"education_tier": "Diploma",
		"fayda_status": "Verified",
		"phone": "+251911000003",
		"specialisation": "Natural resources",
		"joined_at": "2012-01-10",
		"farmer_count": 0,
		"version": 9,
	},
	"DA-000004": {
		"da_id": "DA-000004",
		"full_name": "Hana Girma",
		"active_status": C.ACTIVE_STATUS_ACTIVE,
		"approval_status": "Awaiting review",
		"region": "ET03",
		"zone": "ET03-Z02",
		"woreda": "ET03-W02",
		"assigned_kebele": "ET03-W02-K01",
		"education_tier": "Degree",
		"fayda_status": "Pending",
		"phone": "+251911000004",
		"specialisation": "Crop production",
		"joined_at": "2026-07-01",
		"farmer_count": 0,
		"version": 1,
	},
}


# Master Data geography of the demo site; setup/seed_demo.py seeds the same list: (code, level, parent, name)
GEO_LEVELS = ("region", "zone", "woreda", "kebele")
FIXTURE_GEO = (
	("ET04", "region", None, "Oromia"),
	("ET04-Z01", "zone", "ET04", "West Shewa"),
	("ET04-W01", "woreda", "ET04-Z01", "Bako Tibe"),
	("ET04-W01-K01", "kebele", "ET04-W01", "Bako 01"),
	("ET04-W01-K02", "kebele", "ET04-W01", "Bako 02"),
	("ET04-W01-K03", "kebele", "ET04-W01", "Dambi Gobu"),
	("ET04-W01-K04", "kebele", "ET04-W01", "Koye Feche"),
	("ET04-W01-K05", "kebele", "ET04-W01", "Amarti Gibe"),
	("ET04-W01-K07", "kebele", "ET04-W01", "Gombo"),
	("ET03", "region", None, "Amhara"),
	("ET03-Z02", "zone", "ET03", "North Shewa"),
	("ET03-W02", "woreda", "ET03-Z02", "Ankober"),
	("ET03-W02-K01", "kebele", "ET03-W02", "Ankober 01"),
)


# Master Data code lists of the demo site, {attribute_id: {value_id: label}}
FIXTURE_ATTRIBUTES: dict[str, dict[str, str]] = {
	"DA_EDUCATION_LEVEL": {
		"CERTIFICATE": "Certificate",
		"DIPLOMA": "Diploma",
		"DEGREE": "Degree",
		"MASTERS": "Masters",
	},
	"DA_SPECIALISATION": {
		"CROP_PRODUCTION": "Crop production",
		"LIVESTOCK": "Livestock",
		"NATURAL_RESOURCES": "Natural resources",
	},
	"DA_POSITION": {
		"DEVELOPMENT_AGENT": "Development agent",
		"SENIOR_DA": "Senior DA",
		"SUPERVISOR": "Supervisor",
		"COMMUNICATION_OFFICER": "Communication officer",
		"EXECUTIVE_OFFICER": "Executive officer",
		"ADMIN": "Admin",
	},
	"DA_RELATIONSHIP": {
		"REL_SPOUSE": "Spouse",
		"REL_SON": "Son",
		"REL_DAUGHTER": "Daughter",
		"REL_OTHER": "Other",
	},
	"PREFIX": {"ATO": "Ato", "WROS": "W/ro", "DR": "Dr"},
	"GENDER": {"MALE": "Male", "FEMALE": "Female"},
	"MARITAL_STATUS": {
		"SINGLE": "Single",
		"MARRIED": "Married",
		"DIVORCED": "Divorced",
		"WIDOWED": "Widowed",
	},
}
COUNTRY = ("ET", "Ethiopia")

# ProfileUpdate.changes key -> (fixture key, Master Data list whose label the fixture holds)
_PROFILE_KEYS = {
	"phone": ("phone", None),
	"telegram_number": ("telegram_number", None),
	"alternate_number": ("alternate_number", None),
	"email": ("email", None),
	"home_address": ("home_address", None),
	"specialisation": ("specialisation", "DA_SPECIALISATION"),
	"position": ("position", "DA_POSITION"),
	"joined_date": ("joined_at", None),
	"reports_to": ("reports_to", None),
	"prefix": ("salutation", "PREFIX"),
	"first_name": ("first_name", None),
	"father_name": ("father_name", None),
	"grandfather_name": ("grandfather_name", None),
	"fayda_id": ("fayda_id", None),
	"gender": ("gender", "GENDER"),
	"marital_status": ("marital_status", "MARITAL_STATUS"),
	"birth_month_ec": ("birth_month_ec", None),
	"birth_year_ec": ("birth_year_ec", None),
	"language": ("language", None),
	"photo_url": ("photo_url", None),
}


class MockDARegistryClient(DARegistryClient):
	_instance: MockDARegistryClient | None = None

	def __init__(self, records: dict[str, dict] | None = None):
		# Copy so tests that mutate never leak into each other.
		self._records = copy.deepcopy(records or FIXTURE_DAS)
		self._outcomes: dict[str, IntegrationResult] = {}
		self.imports: list[tuple[str, bytes]] = []
		self.import_exceptions: list[dict] = []
		self.pushed: list[ProfileUpdate] = []  # every profile change request created
		self.section_changes: list[SectionChange] = []  # list / photo change requests created
		self.documents: list[tuple[str, bytes, str]] = []  # files uploaded

	@classmethod
	def shared(cls) -> MockDARegistryClient:
		"""One instance per process so pushed outcomes are visible across calls."""
		if cls._instance is None:
			cls._instance = cls()
		return cls._instance

	@classmethod
	def reset(cls) -> None:
		cls._instance = None

	def get_da(self, da_id: str) -> DAReference:
		payload = self._records.get(da_id)
		if not payload:
			raise DANotFound(da_id)
		return to_da_reference(payload)

	def list_das(self, *, woreda: str | None = None, region: str | None = None) -> list[DAReference]:
		rows = []
		for payload in self._records.values():
			if woreda and payload.get("woreda") != woreda:
				continue
			if region and payload.get("region") != region:
				continue
			rows.append(to_da_reference(payload))
		return sorted(rows, key=lambda r: r.da_id)

	def push_profile_update(self, update: ProfileUpdate) -> IntegrationResult:
		if update.reference in self._outcomes:
			return self._outcomes[update.reference]  # idempotent replay
		payload = self._records.get(update.da_id)
		if not payload:
			raise DANotFound(update.da_id)
		if "education_level" in update.changes:
			# The registry refuses education on Position and reporting (18 Nov 2026).
			return IntegrationResult(
				accepted=False,
				message="Change payload contains fields not allowed by section 'da_service_section_02': "
				"row 0: education_level",
				retryable=False,
				error_code="REQ-VAL-001",
			)
		self._apply_profile_changes(payload, update.changes)
		payload["version"] = int(payload.get("version") or 0) + 1
		self.pushed.append(update)
		result = IntegrationResult(
			accepted=True,
			external_reference=f"MOCK-{update.reference}",
			message="accepted by mock registry",
			raw={"applied_at": datetime.now(UTC).isoformat(), "version": payload["version"]},
		)
		self._outcomes[update.reference] = result
		return result

	@staticmethod
	def _apply_profile_changes(payload: dict, changes: dict) -> None:
		"""Write the registry-side change onto the fixture, as the registry would."""
		for key, value in changes.items():
			if key == "kebele":
				payload["assigned_kebele"] = value
			elif key == "geo_code_hierarchy_json":
				for row in (value or {}).get("hierarchy") or []:
					if row.get("level") in ("region", "zone", "woreda"):
						payload[row["level"]] = row.get("level_value_id")
			elif key in _PROFILE_KEYS:
				if key == "position":
					payload["position_code"] = value
					if value == "ADMIN":
						payload["reports_to"] = None  # hidden and emptied for ADMIN
				field, attribute = _PROFILE_KEYS[key]
				labels = FIXTURE_ATTRIBUTES.get(attribute) or {}
				payload[field] = labels.get(value, value)
			else:
				payload[key] = value
		names = [payload.get(k) for k in ("first_name", "father_name", "grandfather_name")]
		if any(k in changes for k in ("first_name", "father_name", "grandfather_name")) and any(names):
			payload["full_name"] = " ".join(n for n in names if n)

	def push_lifecycle_outcome(self, outcome: LifecycleOutcome) -> IntegrationResult:
		if outcome.reference in self._outcomes:
			return self._outcomes[outcome.reference]  # idempotent replay
		payload = self._records.get(outcome.da_id)
		if not payload:
			raise DANotFound(outcome.da_id)
		if outcome.new_active_status:
			payload["active_status"] = outcome.new_active_status
		if outcome.target_kebele:
			payload["assigned_kebele"] = outcome.target_kebele
		payload["version"] = int(payload.get("version") or 0) + 1
		result = IntegrationResult(
			accepted=True,
			external_reference=f"MOCK-{outcome.reference}",
			message="accepted by mock registry",
			raw={"applied_at": datetime.now(UTC).isoformat(), "version": payload["version"]},
		)
		self._outcomes[outcome.reference] = result
		return result

	def import_agents_file(self, filename: str, content: bytes) -> IntegrationResult:
		self.imports.append((filename, content))
		return IntegrationResult(
			accepted=True,
			external_reference=f"MOCK-IMPORT-{len(self.imports)}",
			message="queued by mock registry",
		)

	def list_import_exceptions(self, *, page: int = 1, page_size: int = 50) -> dict:
		start = (page - 1) * page_size
		return {
			"items": self.import_exceptions[start : start + page_size],
			"total": len(self.import_exceptions),
		}

	def upload_document(self, filename: str, content: bytes, content_type: str) -> str:
		self.documents.append((filename, content, content_type))
		return f"doc-up-{len(self.documents)}"

	def push_section_change(self, change: SectionChange) -> IntegrationResult:
		if change.reference in self._outcomes:
			return self._outcomes[change.reference]  # idempotent replay
		payload = self._records.get(change.da_id)
		if not payload:
			raise DANotFound(change.da_id)
		error = self._apply_section_change(payload, change)
		self.section_changes.append(change)
		if error:
			return IntegrationResult(accepted=False, message=error, retryable=False, error_code="REQ-VAL-000")
		result = IntegrationResult(
			accepted=True, external_reference=f"MOCK-{change.reference}", message="accepted by mock registry"
		)
		self._outcomes[change.reference] = result
		return result

	@staticmethod
	def _apply_section_change(payload: dict, change: SectionChange) -> str | None:
		"""Apply list rows / the photo as the registry would; a refusal message or None."""
		from .mapper import DEPENDENT_REGISTER, QUALIFICATION_REGISTER, dependent_rows, qualification_rows

		if change.section_register_id is None:  # the photo on the identity section
			for row in change.rows:
				if "record_image_document_id" in row:
					payload["photo_document_id"] = row["record_image_document_id"]
					payload["photo_url"] = None
			return None
		if change.section_register_id == QUALIFICATION_REGISTER:
			key, fields = (
				"qualifications",
				("education_level", "passout_year", "certificates"),
			)
		elif change.section_register_id == DEPENDENT_REGISTER:
			key, fields = "dependents", ("member_name", "relationship", "date_of_birth")
		else:
			return f"Unknown section register {change.section_register_id}"
		# Back to the registry's row shape, change, then shaped again as get_da answers.
		current = []
		for r in payload.get(key) or []:
			raw = {"internal_record_id": r["id"]}
			if key == "qualifications":
				raw.update({f: r.get(f) for f in fields})
			else:
				raw.update(
					{
						"member_name": r.get("name"),
						"relationship": r.get("relationship"),
						"date_of_birth": r.get("date_of_birth"),
					}
				)
			current.append(raw)
		for row in change.rows:
			action = row.get("edit_action")
			values = {f: row[f] for f in fields if f in row}
			if action == "ADD":
				current.append({"internal_record_id": f"{key[:4]}-{len(current) + 1:03d}-new", **values})
			elif action in ("UPDATE", "DELETE"):
				target = next(
					(r for r in current if r["internal_record_id"] == row.get("internal_record_id")), None
				)
				if not target:
					return f"No {key[:-1]} {row.get('internal_record_id')} on this DA"
				if action == "DELETE":
					current.remove(target)
				else:
					target.update(values)
			if len(values.get("certificates") or []) > 3:
				return "Certificates & documents: up to 3 files can be attached to one record. Remove one."
		if (
			key == "qualifications"
			and not current
			and payload.get("position_code")
			in (
				"DEVELOPMENT_AGENT",
				"SENIOR_DA",
			)
		):
			return "Education & Professional: add at least one row"
		shaped = (
			qualification_rows(current, FIXTURE_ATTRIBUTES)
			if key == "qualifications"
			else dependent_rows(current, FIXTURE_ATTRIBUTES)
		)
		payload[key] = shaped
		return None

	def change_request_status(self, reference: str) -> str:
		# The mock applies a profile update at once, so it is already approved. Its own
		# references are known by prefix: the scheduler runs in another process, whose
		# fresh mock never saw the push.
		if not str(reference or "").startswith("MOCK-"):
			raise DANotFound(reference)
		return "Approved"

	def attribute_values(self, attribute_id: str) -> dict[str, str]:
		return dict(FIXTURE_ATTRIBUTES.get(attribute_id) or {})

	def document_links(self, document_ids: list[str]) -> dict[str, str]:
		return {d: f"https://minio.example.com/documents/{d}?X-Amz-Expires=3600" for d in document_ids if d}

	def geo_levels(self) -> list[dict]:
		return [{"level_id": "l0", "mnemonic": "country", "parent_level_id": None}] + [
			{"level_id": f"l{i + 1}", "mnemonic": mnemonic, "parent_level_id": f"l{i}"}
			for i, mnemonic in enumerate(GEO_LEVELS)
		]

	def geo_level_values(self, level_id: str, parent: str | None = None) -> list[dict]:
		index = int(str(level_id).lstrip("l")) - 1
		if index == -1:
			return [{"code": COUNTRY[0], "name": COUNTRY[1], "parent": None}]
		if not 0 <= index < len(GEO_LEVELS):
			return []
		return [
			{"code": code, "name": name, "parent": parent_code}
			for code, level, parent_code, name in FIXTURE_GEO
			if level == GEO_LEVELS[index] and (parent is None or parent_code == parent)
		]
