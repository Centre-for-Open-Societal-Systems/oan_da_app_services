"""The fields a DA profile change request may carry, their checks, and the registry payload.

One request changes one section of the DA register's ``da_details_tab``:

- Contact (da_contact_section_04): mobile, telegram, alternate_mobile, email
- Qualifications (da_service_section_02): education_tier, specialisation, position, joined_date
- Identity (da_identity_section_01): prefix, first_name, father_name, grandfather_name,
  fayda_id, gender, marital_status, birth_month_ec, birth_year_ec
- Location (da_location_section_03): region, zone, woreda, kebele (Master Data codes)

Anything else is refused here, before a row is stored. The registry checks formats,
Master Data codes and duplicate Fayda IDs again, but never who asked or for which DA, so
this allow-list and the approval in services/profile.py are the guard.

Coded fields are checked against the Master Data list and stored as codes. A location
change is sent as the Kebele's id plus the whole geo chain from the country down, built
from Master Data (never hard-coded); the registry derives the names and flat codes.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import frappe
from frappe import _

from da_services.integrations.da_registry.client import (
	DARegistryClient,
	DARegistryError,
	DARegistryUnavailable,
)
from da_services.integrations.da_registry.schemas import DAReference

SECTIONS = ("Contact", "Qualifications", "Identity", "Location")
NOTE_MIN_LENGTH = 10  # a change of name or Fayda ID needs a reason this long
GEO_LEVELS = ("region", "zone", "woreda", "kebele")

# Ethiopian mobile number, as the registry takes it: 0911234567 or +251911234567
_PHONE_RE = re.compile(r"^(?:\+251|0)[79]\d{8}$")
_FAYDA_RE = re.compile(r"^\d{16}$")
# Letters of any script (Ge'ez included), single spaces, apostrophe, hyphen or dot between them
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-.][^\W\d_]+)*\.?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ProfileField:
	section: str
	kind: str  # phone, email, code, date, name, fayda, month, year, geo
	key: str | None  # ProfileUpdate.changes key (integrations/da_registry/mapper.py PROFILE_FIELDS)
	current: Callable[[DAReference], object]
	attribute: str | None = None  # Master Data list of a coded field
	note_required: bool = False


FIELDS: dict[str, ProfileField] = {
	"mobile": ProfileField("Contact", "phone", "phone", lambda r: r.phone),
	"telegram": ProfileField("Contact", "phone", "telegram_number", lambda r: r.telegram_number),
	"alternate_mobile": ProfileField("Contact", "phone", "alternate_number", lambda r: r.alternate_number),
	"email": ProfileField("Contact", "email", "email", lambda r: r.email),
	"education_tier": ProfileField(
		"Qualifications", "code", "education_tier", lambda r: r.education_tier, "DA_EDUCATION_LEVEL"
	),
	"specialisation": ProfileField(
		"Qualifications", "code", "specialisation", lambda r: r.specialisation, "DA_SPECIALISATION"
	),
	"position": ProfileField("Qualifications", "code", "position", lambda r: r.position, "DA_POSITION"),
	"joined_date": ProfileField("Qualifications", "date", "joined_date", lambda r: r.joined_at),
	"prefix": ProfileField("Identity", "code", "prefix", lambda r: r.salutation, "PREFIX"),
	"first_name": ProfileField("Identity", "name", "first_name", lambda r: r.first_name, note_required=True),
	"father_name": ProfileField(
		"Identity", "name", "father_name", lambda r: r.father_name, note_required=True
	),
	"grandfather_name": ProfileField(
		"Identity", "name", "grandfather_name", lambda r: r.grandfather_name, note_required=True
	),
	"fayda_id": ProfileField("Identity", "fayda", "fayda_id", lambda r: r.fayda_id, note_required=True),
	"gender": ProfileField("Identity", "code", "gender", lambda r: r.gender, "GENDER"),
	"marital_status": ProfileField(
		"Identity", "code", "marital_status", lambda r: r.marital_status, "MARITAL_STATUS"
	),
	"birth_month_ec": ProfileField("Identity", "month", "birth_month_ec", lambda r: r.birth_month_ec),
	"birth_year_ec": ProfileField("Identity", "year", "birth_year_ec", lambda r: r.birth_year_ec),
	# The four levels travel together; only the Kebele and the chain reach the registry.
	"region": ProfileField("Location", "geo", None, lambda r: r.region),
	"zone": ProfileField("Location", "geo", None, lambda r: r.zone),
	"woreda": ProfileField("Location", "geo", None, lambda r: r.woreda),
	"kebele": ProfileField("Location", "geo", "kebele", lambda r: r.kebele),
}


# Where the profile view (services/profile.py) shows a field under another name
PROFILE_KEYS = {
	"telegram": "telegram_number",
	"alternate_mobile": "alternate_number",
	"joined_date": "joined_at",
	"prefix": "salutation",
}


def catalog(ref: DAReference | None = None, client: DARegistryClient | None = None) -> dict[str, dict]:
	"""What the portal may offer, per field: section, kind, Master Data list, note rule.

	With ``client``, a coded field also carries ``options`` ([{code, label}], the Master
	Data list), or None when the list cannot be read now: the value is still checked
	when the request is sent. With ``ref``, each field carries ``current``, the DA's
	value as a change request would send it (the code of a coded field), and
	``current_label`` where the value has a display name.
	"""
	out = {}
	for name, f in FIELDS.items():
		entry = {
			"section": f.section,
			"kind": f.kind,
			"master_data_list": f.attribute,
			"note_required": f.note_required,
			"profile_key": PROFILE_KEYS.get(name, name),
		}
		values = _values(client, f.attribute) if client and f.kind == "code" else None
		if client and f.kind == "code":
			entry["options"] = (
				None
				if values is None
				else [
					{"code": code, "label": label}
					for code, label in sorted(values.items(), key=lambda item: str(item[1]).casefold())
				]
			)
		if ref is not None:
			current = f.current(ref)
			if f.kind == "code":
				entry["current"] = _code_of(values, current) if values is not None else None
				entry["current_label"] = current or None
			else:
				entry["current"] = current
				if f.kind == "geo":
					entry["current_label"] = getattr(ref, f"{name}_name", None)
		out[name] = entry
	return out


class InvalidProfileChange(frappe.ValidationError):
	"""The requested change is not acceptable (answered as 400 with this message).

	``field`` names the field the refusal is about, when it is about one.
	"""

	api_code = "VALIDATION_ERROR"

	def __init__(self, message: str, field: str | None = None):
		super().__init__(message)
		self.field = field


class NothingToChange(InvalidProfileChange):
	"""Every requested value is already on the DA's record."""

	api_code = "NOTHING_TO_CHANGE"


def _fail(message: str, field: str | None = None) -> None:
	raise InvalidProfileChange(message, field)


def ethiopian_year(day: date) -> int:
	"""The Ethiopian calendar year a Gregorian date falls in.

	The Ethiopian new year is 11 September, or 12 September in the Gregorian year
	before a leap year; the count runs 7 years behind from then and 8 before.
	"""
	new_year = date(day.year, 9, 12 if calendar.isleap(day.year + 1) else 11)
	return day.year - (7 if day >= new_year else 8)


def _text(value) -> str:
	return (str(value) if value is not None else "").strip()


def _registry_down() -> None:
	frappe.throw(_("The DA Registry is unavailable. Try again shortly."), frappe.ValidationError)


def _code(client: DARegistryClient, attribute: str, value: str, field: str) -> tuple[str, str]:
	"""(code, label) of a Master Data value given as its code or its label."""
	try:
		values = client.attribute_values(attribute)
	except DARegistryUnavailable:
		_registry_down()
	except DARegistryError:
		_fail(_("The {0} list cannot be read from Master Data.").format(field))
	if value in values:
		return value, values[value]
	wanted = value.casefold()
	for code, label in values.items():
		if str(label).casefold() == wanted or code.casefold() == wanted:
			return code, label
	_fail(_("{0} must be one of {1}.").format(field, ", ".join(sorted(values.values()))))


def _values(client: DARegistryClient, attribute: str) -> dict[str, str] | None:
	"""{code: label} of a Master Data list, or None when it cannot be read now."""
	try:
		return client.attribute_values(attribute)
	except DARegistryError:
		return None


def _code_of(values: dict[str, str], current) -> str | None:
	"""The code behind the label the profile shows (DAReference holds labels)."""
	current = _text(current)
	if not current:
		return None
	if current in values:
		return current
	for code, label in values.items():
		if str(label).casefold() == current.casefold():
			return code
	return current


def _current_code(client: DARegistryClient, attribute: str, current) -> str | None:
	"""The DA's current code of a coded field; the label itself when the list cannot be read."""
	values = _values(client, attribute)
	return _code_of(values, current) if values is not None else (_text(current) or None)


def _check_value(
	field: str, spec: ProfileField, value, client: DARegistryClient
) -> tuple[object, str | None]:
	"""(canonical value, label for display) of one non-location field, or a 400."""
	text = _text(value)
	if not text:
		_fail(_("{0} cannot be empty.").format(field))
	if len(text) > 140:
		_fail(_("{0} is too long.").format(field))
	if spec.kind == "phone":
		if not _PHONE_RE.match(text.replace(" ", "")):
			_fail(
				_("{0} must be an Ethiopian mobile number such as 0911234567 or +251911234567.").format(field)
			)
		return text.replace(" ", ""), None
	if spec.kind == "email":
		if not frappe.utils.validate_email_address(text):
			_fail(_("{0} is not a valid address.").format(field))
		return text, None
	if spec.kind == "code":
		code, label = _code(client, spec.attribute, text, field)
		return code, label
	if spec.kind == "date":
		if not _DATE_RE.match(text):
			_fail(_("{0} must be a date as YYYY-MM-DD.").format(field))
		try:
			day = date.fromisoformat(text)
		except ValueError:
			_fail(_("{0} is not a real date.").format(field))
		if day > date.today():
			_fail(_("{0} cannot be in the future.").format(field))
		return text, None
	if spec.kind == "name":
		if len(text) > 100 or not _NAME_RE.match(text):
			_fail(_("{0} may hold letters, spaces, apostrophes, hyphens and dots only.").format(field))
		return text, None
	if spec.kind == "fayda":
		if not _FAYDA_RE.match(text):
			_fail(_("fayda_id must be exactly 16 digits."))
		return text, None
	if spec.kind == "month":
		if not text.isdigit() or not 1 <= int(text) <= 13:
			_fail(_("birth_month_ec must be a month of the Ethiopian calendar, 1 to 13."))
		return int(text), None
	if spec.kind == "year":
		latest = ethiopian_year(date.today())
		if not text.isdigit() or not 1900 <= int(text) <= latest:
			_fail(_("birth_year_ec must be a year of the Ethiopian calendar, 1900 to {0}.").format(latest))
		return int(text), None
	_fail(_("{0} cannot be changed here.").format(field))


def _same(a, b) -> bool:
	return _text(a).casefold() == _text(b).casefold()


def validate_changes(
	changes: dict, *, ref: DAReference, reason: str | None, client: DARegistryClient
) -> tuple[str, dict]:
	"""Check a requested change against the DA's current record; return (section, diff).

	``diff`` is {field: {"from", "to"[, "from_label", "to_label"]}} with only the fields
	that change; the labels are the display names of coded and location values.
	"""
	if not isinstance(changes, dict) or not changes:
		_fail(_("changes must be a non-empty object."))
	unknown = sorted(set(changes) - set(FIELDS))
	if unknown:
		_fail(_("These fields cannot be changed through a profile request: {0}").format(", ".join(unknown)))
	sections = {FIELDS[f].section for f in changes}
	if len(sections) > 1:
		_fail(_("Send one section per request: {0}.").format(", ".join(sorted(sections))))
	section = sections.pop()

	if section == "Location":
		diff = _location_diff(changes, ref, client)
	else:
		diff = {}
		for field, value in changes.items():
			spec = FIELDS[field]
			try:
				new, label = _check_value(field, spec, value, client)
			except InvalidProfileChange as exc:
				exc.field = exc.field or field  # the screen shows the message under this field
				raise
			current = spec.current(ref)
			if spec.kind == "code":
				# The record shows the label; the request keeps the code, with both labels.
				code_now = _current_code(client, spec.attribute, current)
				if _same(code_now, new):
					continue
				entry = {"from": code_now, "from_label": current or None, "to": new}
			elif _same(current, new):
				continue
			else:
				entry = {"from": current, "to": new}
			if label:
				entry["to_label"] = label
			diff[field] = entry
	if not diff:
		raise NothingToChange(_("Nothing to change: the new values match the current profile."))

	needs_note = sorted(f for f in diff if FIELDS[f].note_required)
	if needs_note and len(_text(reason)) < NOTE_MIN_LENGTH:
		_fail(
			_("A change of {0} needs a reason of at least {1} characters.").format(
				", ".join(needs_note), NOTE_MIN_LENGTH
			)
		)
	return section, diff


def _location_diff(changes: dict, ref: DAReference, client: DARegistryClient) -> dict:
	codes = {level: _text(changes.get(level)) for level in GEO_LEVELS}
	missing = [level for level, code in codes.items() if not code]
	if missing:
		_fail(
			_("A location change needs region, zone, woreda and kebele; missing: {0}.").format(
				", ".join(missing)
			)
		)
	if _same(codes["kebele"], ref.kebele):
		return {}
	try:
		chain = build_geo_chain(client, codes)
	except DARegistryUnavailable:
		_registry_down()
	except DARegistryError as exc:
		_fail(_("The location cannot be checked against Master Data: {0}").format(exc))
	names = {row["level"]: row["level_value_mnemonic"] for row in chain["hierarchy"]}
	return {
		level: {
			"from": FIELDS[level].current(ref),
			"from_label": getattr(ref, f"{level}_name", None),
			"to": codes[level],
			"to_label": names.get(level),
		}
		for level in GEO_LEVELS
	}


def build_geo_chain(client: DARegistryClient, codes: dict[str, str]) -> dict:
	"""geo_code_hierarchy_json for a Region / Zone / Woreda / Kebele, from the root down.

	Level ids, mnemonics and names come from Master Data. Each level must be a child of
	the one above, or the request is refused (400). Raises the registry error when
	Master Data cannot be read.
	"""
	levels = {lvl["mnemonic"]: lvl for lvl in client.geo_levels()}
	missing = [m for m in ("country", *GEO_LEVELS) if m not in levels]
	if missing:
		raise DARegistryError(f"Master Data has no {', '.join(missing)} level")

	regions = client.geo_level_values(levels["region"]["level_id"])
	region = next((r for r in regions if r["code"] == codes["region"]), None)
	if not region:
		_fail(_("Region {0} is not in Master Data.").format(codes["region"]))
	countries = client.geo_level_values(levels["country"]["level_id"])
	if region.get("parent"):
		country = next((c for c in countries if c["code"] == region["parent"]), None)
	else:
		country = countries[0] if len(countries) == 1 else None
	if not country:
		raise DARegistryError(f"Master Data names no country above Region {codes['region']}")

	rows = [("country", country), ("region", region)]
	parent = region["code"]
	for level in ("zone", "woreda", "kebele"):
		values = client.geo_level_values(levels[level]["level_id"], parent)
		area = next((v for v in values if v["code"] == codes[level]), None)
		if not area:
			_fail(_("{0} {1} is not in {2}.").format(level.capitalize(), codes[level], parent))
		rows.append((level, area))
		parent = area["code"]
	return {
		"hierarchy": [
			{
				"level": mnemonic,
				"level_id": levels[mnemonic]["level_id"],
				"level_mnemonic": mnemonic,
				"level_value_id": area["code"],
				"level_value_mnemonic": area.get("name") or area["code"],
			}
			for mnemonic, area in rows
		],
		"lowest_level_value_id": codes["kebele"],
	}


def already_applied(section: str, diff: dict, ref: DAReference, client: DARegistryClient) -> bool:
	"""True when the DA's record already holds every value the request asks for."""
	if not diff:
		return False
	if section == "Location":
		return _same(ref.kebele, (diff.get("kebele") or {}).get("to"))
	for field, entry in diff.items():
		spec = FIELDS.get(field)
		if not spec:
			return False
		current = spec.current(ref)
		if spec.kind == "code":
			current = _current_code(client, spec.attribute, current)
		if not _same(current, entry.get("to")):
			return False
	return True


def registry_changes(section: str, diff: dict, client: DARegistryClient) -> dict:
	"""ProfileUpdate.changes for an approved request: only the changed fields.

	A location change is sent as the Kebele id plus the full chain, rebuilt from Master
	Data with the sender's token so it matches Master Data at the moment it is sent.
	"""
	if section == "Location":
		codes = {level: diff[level]["to"] for level in GEO_LEVELS}
		return {"kebele": codes["kebele"], "geo_code_hierarchy_json": build_geo_chain(client, codes)}
	return {FIELDS[field].key: entry["to"] for field, entry in diff.items()}
