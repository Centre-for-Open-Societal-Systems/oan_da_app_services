"""Validate a Crop Sown section against the live intake form and field metadata.

Field names, types, required flags, and choices come from the registry. This
module does not name crop columns. A field that appears in metadata is
accepted; a field that disappears is rejected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from .client import CropRegistryClientError

# Columns the staff save route fills itself. A new business field is not in this set.
_MANAGED = re.compile(
	r"^(?:"
	r"created_.*|last_approved_.*|sync_.*|"
	r"search_text|record_name|record_image_url|functional_record_id|"
	r"actual_score|ideal_score|completion_score_required|record_status|"
	r"is_duplicated|state|state_date|source_system|import_source|"
	r"internal_record_id|link_.*"
	r")$"
)

# Submit Crop Report section ids. The value is the Crop Sown section mnemonic.
_SCREEN_SECTIONS = {
	"identity": "cs_cropsown_record",
	"address": "cs_cropsown_location",
	"planning": "cs_planning_details",
	"planning-cluster": "cs_cluster_details",
	"cultivation": "cs_cultivation_details",
	"cultivation-cluster": "cs_cultivation_cluster_details",
	"sowing": "cs_sowing_details",
	"infestation": "cs_infestation_details",
	"harvest": "cs_harvest_details",
}
_MNEMONIC_SECTIONS = {mnemonic: screen for screen, mnemonic in _SCREEN_SECTIONS.items()}

# Screen tab names. The value is the substring of the staff form mnemonic.
# Crop Sown identity and location are sections of the planning form.
_SCREEN_FORMS = {
	"crop-sown": "planning",
	"cropsown": "planning",
	"planning": "planning",
	"cultivation": "cultivation",
	"sowing": "sowing",
	"harvesting": "harvesting",
	"harvest": "harvesting",
}

_TYPE_OK = {
	"string": lambda value: isinstance(value, str),
	"text": lambda value: isinstance(value, str),
	"boolean": lambda value: isinstance(value, bool),
	"integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
	"number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
	"float": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
	"date": lambda value: isinstance(value, str) and bool(value),
	"datetime": lambda value: isinstance(value, str) and bool(value),
	"json": lambda value: isinstance(value, (dict, list, str)),
	"array": lambda value: isinstance(value, list),
	"list": lambda value: isinstance(value, list),
}


class IntakeContractError(CropRegistryClientError):
	"""The submitted records do not match the current intake metadata."""


def section_index(form: dict) -> list[dict]:
	"""Sections of a rendered intake form, in tab order, one row per section_id."""
	found: list[dict] = []
	seen: set[str] = set()
	for tab in form.get("tabs") or []:
		if not isinstance(tab, dict):
			continue
		for section in tab.get("sections") or []:
			if not isinstance(section, dict) or not section.get("section_id"):
				continue
			section_id = str(section["section_id"])
			if section_id in seen:
				continue
			seen.add(section_id)
			found.append(
				{
					"section_id": section_id,
					"section_register_id": str(section.get("section_register_id") or ""),
					"section_mnemonic": section.get("section_mnemonic"),
					"is_list": bool(section.get("is_list")),
					"widgets": widgets_by_field(section.get("section_ui_schema")),
				}
			)
	if not found:
		raise IntakeContractError("Rendered Crop Sown form has no sections")
	return found


def widgets_by_field(node) -> dict[str, dict]:
	"""Map a UI-schema field name to its widget. Nested layouts are walked."""
	found: dict[str, dict] = {}

	def walk(value) -> None:
		if isinstance(value, list):
			for item in value:
				walk(item)
			return
		if not isinstance(value, dict):
			return
		key = value.get("column-key") or value.get("widget-data-path")
		if isinstance(key, str) and key:
			found[key.split(".")[-1]] = value
		for child in value.values():
			if isinstance(child, (dict, list)):
				walk(child)

	walk(node)
	return found


def form_selector(form_name: str | None, default: str) -> str:
	"""Staff mnemonic fragment for one screen tab. An unknown name is used as given."""
	text = (form_name or "").strip().lower().replace("_", "-").replace(" ", "-")
	if not text:
		text = (default or "planning").strip().lower()
	return _SCREEN_FORMS.get(text, text)


def describe_form(
	form: dict,
	fields_by_register: dict[str, list[dict]],
	catalogs: dict[str, list] | None = None,
) -> dict:
	"""Intake form the frontend can render. Field lists are the register columns.

	``required`` is the widget flag. ``get_register_fields.required`` is true for
	managed columns on the planning server and is not a caller input. ``catalogs``
	maps ``attribute_id`` to the live rows already loaded. A missing key means
	that list was not loaded.
	"""
	catalogs = catalogs or {}
	sections = []
	for section in section_index(form):
		fields = []
		for field in fields_by_register.get(section["section_register_id"]) or []:
			if not isinstance(field, dict) or not field.get("field_name"):
				continue
			name = str(field["field_name"])
			widget = section["widgets"].get(name) or {}
			source = (
				widget.get("widget-data-source") if isinstance(widget.get("widget-data-source"), dict) else {}
			)
			params = source.get("params") if isinstance(source.get("params"), dict) else {}
			readonly = bool(widget.get("widget-readonly")) or bool(_MANAGED.match(name))
			attribute_id = params.get("attribute_id")
			described = {
				"field_name": name,
				"label": widget.get("widget-label"),
				"data_type": field.get("data_type"),
				"required": bool(widget.get("widget-required")) and not readonly,
				"readonly": readonly,
				"options": _option_values(source.get("options")),
				"attribute_id": attribute_id,
			}
			pattern = widget.get("widget-pattern")
			if pattern:
				described["pattern"] = pattern
			if attribute_id:
				key = str(attribute_id)
				if key in catalogs:
					described["choices"] = _catalog_choices(catalogs[key])
					described["catalog_loaded"] = True
				else:
					described["choices"] = None
					described["catalog_loaded"] = False
			geo = _geo_source(source)
			if geo:
				described["geo"] = geo
			fields.append(described)
		sections.append(
			{
				"section_id": section["section_id"],
				"section_register_id": section["section_register_id"],
				"section_mnemonic": section["section_mnemonic"],
				"screen_section": _MNEMONIC_SECTIONS.get(str(section.get("section_mnemonic") or "")),
				"is_list": section["is_list"],
				"fields": fields,
			}
		)
	return {
		"form_id": form.get("form_id"),
		"register_id": form.get("register_id"),
		"form_mnemonic": form.get("form_mnemonic"),
		"sections": sections,
	}


def submit_with_metadata(
	*,
	form: dict,
	fields_for: Callable[[str], list[dict]],
	catalogs: dict[str, list],
	requests: list[dict],
	submission_id: str | None,
	subject_record_id: str | None,
	save: Callable[..., dict],
) -> dict:
	"""Validate each requested section and save it, chaining submission_id."""
	if not isinstance(requests, list) or not requests:
		raise IntakeContractError("At least one section is required")
	indexed = section_index(form)
	form_register_id = str(form.get("register_id") or "")
	current = submission_id or None
	subject = subject_record_id or None
	saved: dict | None = None
	prepared: list[tuple[dict, dict]] = []
	for request in requests:
		if not isinstance(request, dict):
			raise IntakeContractError("Each section must be an object")
		prepared.append((resolve_section(indexed, request), request))
	prepared.sort(key=lambda item: item[0]["section_register_id"] != form_register_id)
	for section, request in prepared:
		records = request.get("records")
		if records is None:
			records = request.get("section_payload")
		payload = build_section_records(
			section=section,
			fields=fields_for(section["section_register_id"]),
			records=records,
			catalogs=catalogs,
			subject_record_id=subject,
			form_register_id=form_register_id,
		)
		saved = save(
			section_id=section["section_id"],
			section_register_id=section["section_register_id"],
			section_payload=payload,
			submission_id=current,
			form_id=form.get("form_id"),
			register_id=form_register_id or None,
		)
		current = str(saved.get("submission_id") or "")
		if not current:
			raise IntakeContractError("Crop Sown save did not return a submission_id")
		found = subject_from_save(saved, form_register_id)
		if found:
			subject = found
	if not saved:
		return {}
	result = dict(saved)
	if subject:
		result.setdefault("subject_record_id", subject)
	return result


def resolve_section(sections: list[dict], request: dict) -> dict:
	"""Find the one form section named by the caller.

	``section_id`` is the intake section, or a Submit Crop Report id such as
	``planning``. ``section_register_id`` is accepted on its own, and also when
	it was sent in ``section_id``, because those two ids are different values
	on the same section.
	"""
	section_id = str(request.get("section_id") or "").strip()
	mnemonic = str(request.get("section_mnemonic") or "").strip() or _SCREEN_SECTIONS.get(section_id, "")
	register_id = str(request.get("section_register_id") or "").strip()
	by_id = [section for section in sections if section_id and section["section_id"] == section_id]
	by_register = [
		section for section in sections if register_id and section["section_register_id"] == register_id
	]
	by_id_as_register = [
		section for section in sections if section_id and section["section_register_id"] == section_id
	]
	by_mnemonic = [
		section for section in sections if mnemonic and str(section.get("section_mnemonic") or "") == mnemonic
	]
	if section_id and by_id:
		matches = by_id
	elif register_id:
		matches = by_register
	elif section_id in _SCREEN_SECTIONS:
		matches = by_mnemonic
	elif section_id:
		matches = by_id_as_register
	elif mnemonic:
		matches = by_mnemonic
	else:
		raise IntakeContractError("section_id, section_mnemonic, or section_register_id is required")
	if len(matches) != 1:
		raise IntakeContractError("Crop Sown section is missing or ambiguous")
	section = matches[0]
	if register_id and register_id != section["section_register_id"]:
		raise IntakeContractError("section_register_id does not match the intake form")
	if not section["section_register_id"]:
		raise IntakeContractError("Crop Sown section has no register")
	return section


def build_section_records(
	*,
	section: dict,
	fields: list[dict],
	records,
	catalogs: dict[str, list] | None = None,
	subject_record_id: str | None = None,
	form_register_id: str | None = None,
) -> list[dict]:
	"""Copy caller records into the register field set and reject anything else."""
	if not isinstance(records, list) or not records:
		raise IntakeContractError("records must be a non-empty list")
	if not section.get("is_list") and len(records) != 1:
		raise IntakeContractError("This Crop Sown section accepts one record")
	by_name = {
		str(field["field_name"]): field
		for field in fields
		if isinstance(field, dict) and field.get("field_name")
	}
	if not by_name:
		raise IntakeContractError("Crop Sown section has no field definitions")
	catalogs = catalogs or {}
	built: list[dict] = []
	for record in records:
		if not isinstance(record, dict):
			raise IntakeContractError("Each record must be an object")
		row: dict = {}
		for key, value in record.items():
			name = str(key)
			if _MANAGED.match(name):
				raise IntakeContractError(f"{name} is managed by the registry")
			field = by_name.get(name)
			if field is None:
				raise IntakeContractError(f"Unknown Crop Sown field {name}")
			if _blank(value):
				continue
			widget = section["widgets"].get(name) or {}
			if widget.get("widget-readonly"):
				raise IntakeContractError(f"{name} is read-only")
			value = _coerce(field.get("data_type"), value)
			value = _canonicalize_choice(name, widget, value, catalogs)
			_check_type(name, field.get("data_type"), value)
			row[name] = value
		for name in by_name:
			widget = section["widgets"].get(name) or {}
			if not _caller_required(name, widget) or not _blank(row.get(name)):
				continue
			raise IntakeContractError(f"{name} is required")
		if form_register_id and section["section_register_id"] != form_register_id:
			if not subject_record_id:
				raise IntakeContractError("Save the main Crop Sown section before this section")
			row["link_internal_record_id"] = subject_record_id
		built.append(row)
	return built


def subject_from_save(saved: dict, register_id: str) -> str | None:
	"""Internal id of the form's own register row, once the staff save echoes it."""
	if not isinstance(saved, dict) or not register_id:
		return None
	for section in saved.get("section_payloads") or []:
		if not isinstance(section, dict) or str(section.get("section_register_id") or "") != register_id:
			continue
		for row in section.get("records") or []:
			if isinstance(row, dict) and row.get("internal_record_id"):
				return str(row["internal_record_id"])
	return None


def _caller_required(name: str, widget: dict) -> bool:
	"""True when the rendered widget requires a caller value."""
	if _MANAGED.match(name) or widget.get("widget-readonly"):
		return False
	return bool(widget.get("widget-required"))


def _option_values(options) -> list | None:
	"""Static widget options. Both the label and the stored value are kept."""
	if not isinstance(options, list) or not options:
		return None
	values = []
	for option in options:
		if isinstance(option, dict) and "value" in option:
			values.append({"label": option.get("label"), "value": option.get("value")})
		else:
			values.append({"label": None, "value": option})
	return values


def _catalog_choices(rows) -> list[dict]:
	"""Live catalog rows the screen stores. ``value_id`` is the stored value."""
	if not isinstance(rows, list):
		return []
	choices = []
	for row in rows:
		if not isinstance(row, dict) or not row.get("value_id"):
			continue
		choices.append({"value_id": row.get("value_id"), "value_display": row.get("value_display")})
	return choices


def _geo_source(source: dict) -> dict | None:
	"""Geography widget settings copied from the rendered form. Levels are not listed here."""
	params = source.get("params") if isinstance(source.get("params"), dict) else {}
	endpoint = source.get("endpoint") or params.get("endpoint")
	kind = str(source.get("type") or "")
	if endpoint != "geo-level-values" and "geo-level-values" not in kind:
		return None
	geo = {"endpoint": "geo-level-values"}
	if params.get("valueKey"):
		geo["value_key"] = params["valueKey"]
	if params.get("labelKey"):
		geo["label_key"] = params["labelKey"]
	if params.get("level_id"):
		geo["level_id"] = params["level_id"]
	return geo


def _check_type(name: str, data_type, value) -> None:
	if value is None:
		raise IntakeContractError(f"{name} is empty")
	check = _TYPE_OK.get(str(data_type or "").lower())
	if check is not None and not check(value):
		raise IntakeContractError(f"{name} must be {data_type}")


def _coerce(data_type, value):
	"""Turn a Submit Crop Report string into the register type. Other values stay."""
	if not isinstance(value, str):
		return value
	kind = str(data_type or "").lower()
	text = value.strip()
	if kind in {"number", "float", "integer"}:
		try:
			number = float(text)
		except ValueError:
			return value
		if number.is_integer():
			return int(number)
		if kind == "integer":
			return value
		return number
	if kind == "boolean":
		lowered = text.lower()
		if lowered in {"yes", "true", "1"}:
			return True
		if lowered in {"no", "false", "0"}:
			return False
		return value
	if kind in {"json", "array", "list"} and text[:1] in "[{":
		try:
			return json.loads(text)
		except ValueError:
			return value
	if kind in {"array", "list"} and "," in text:
		return [part.strip() for part in text.split(",") if part.strip()]
	return value


def _same(left, right) -> bool:
	return isinstance(left, str) and isinstance(right, str) and left.casefold() == right.casefold()


def _canonicalize_choice(name: str, widget: dict, value, catalogs: dict[str, list]):
	"""Keep a staff code, or the code whose label is the text the screen sent."""
	source = widget.get("widget-data-source") or {}
	options = source.get("options")
	if isinstance(options, list) and options:
		for option in options:
			if isinstance(option, dict):
				if value == option.get("value") or _same(value, option.get("label")):
					return option.get("value", value)
			elif value == option or _same(value, option):
				return option
		raise IntakeContractError(f"{name} is not an allowed value")
	attribute_id = (source.get("params") or {}).get("attribute_id")
	catalog = catalogs.get(attribute_id) if attribute_id else None
	if not catalog:
		return value
	for row in catalog:
		if not isinstance(row, dict):
			continue
		if value in {row.get("value_id"), row.get("value_code")} or _same(value, row.get("value_display")):
			return row.get("value_id")
	raise IntakeContractError(f"{name} is not an allowed value")


def _blank(value) -> bool:
	return value is None or value == ""
