"""Describe and validate the Livestock intake form from the live staff metadata.

The rendered form is the contract. Each section lists its widgets in screen
order, and a list section (animals, health events, vaccinations, vital events,
breeding) is one dialog table whose columns are the fields. This module names
no livestock column: a widget added in the staff form is described and
accepted, and a widget that disappears is rejected.

The frontend renders from ``describe_form``: widget kind, label, required flag,
static options or catalog choices, pattern, and the form's own show / require
rules. ``submit_with_metadata`` applies the same rules before each save.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from .client import LivestockRegistryClientError

# Columns the staff save route fills itself. A new business field is not in this set.
_MANAGED = re.compile(
	r"^(?:"
	r"created_.*|last_approved_.*|sync_.*|application_reference|"
	r"search_text|record_name|record_image_url|functional_record_id|"
	r"actual_score|ideal_score|completion_score_required|record_status|record_status_reason|"
	r"is_duplicated|state|state_date|source_system|import_source|"
	r"internal_record_id|link_.*"
	r")$"
)

_TABLE_WIDGET = "dialog-table"
_GEO_WIDGETS = {"geo-hierarchy", "oan-geo-point"}

# Kebele ids are prefix codes: ET01 (region) > ET0101 (zone) > ET010101 (woreda) > ET01010101 (kebele).
_GEO_CODE = re.compile(r"^ET\d{2}(?:\d{2}){0,3}$")
_GEO_LEVELS = (("region", "l1"), ("zone", "l2"), ("woreda", "l3"), ("kebele", "l4"))

# Submit Livestock Report section ids (DA-registry-frontend livestockReport.ts)
# and the profile's Livestock Details sections. The value is the staff
# section mnemonic. Holding, herd structure, feed, water and sales are not
# sections of the Livestock intake form, so they are not in this map.
_SCREEN_SECTIONS = {
	"identity": "ls_farmer_lookup",
	"address": "ls_livestock_location",
	"location": "ls_livestock_location",
	"survey-personnel": "ls_survey_personnel",
	"herd": "ls_animal_details",
	"livestock-details": "ls_animal_details",
	"ear-tag-replacement": "ls_retagging_details",
	"disease": "ls_health_event_details",
	"health-event": "ls_health_event_details",
	"vaccination": "ls_vaccination_details",
	"vital-event": "ls_vital_event_details",
	"breeding": "ls_breeding_details",
	"breeding-details": "ls_breeding_details",
}
_SCREENS_BY_MNEMONIC: dict[str, list[str]] = {}
for _screen, _mnemonic in _SCREEN_SECTIONS.items():
	_SCREENS_BY_MNEMONIC.setdefault(_mnemonic, []).append(_screen)

# Frontend Livestock Details tab key for a staff section mnemonic.
# A mnemonic that is not here stays on the tab the rendered form gives it.
_SCREEN_TAB_OF_MNEMONIC = {
	"ls_farmer_lookup": ("farmer", "Farmer"),
	"ls_survey_personnel": ("farmer", "Farmer"),
	"ls_livestock_location": ("farmer", "Farmer"),
	"ls_animal_details": ("livestock", "Livestock"),
	"ls_retagging_details": ("livestock", "Livestock"),
	"ls_health_event_details": ("health", "Health & Vaccination"),
	"ls_vaccination_details": ("health", "Health & Vaccination"),
	"ls_vital_event_details": ("breeding", "Breeding & Vital Events"),
	"ls_breeding_details": ("breeding", "Breeding & Vital Events"),
}

_DMY = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

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


class IntakeContractError(LivestockRegistryClientError):
	"""The submitted records do not match the current intake metadata."""


def section_index(form: dict) -> list[dict]:
	"""Sections of a rendered intake form, in tab order, one row per section_id.

	Each row carries ``widgets`` (field name to widget, for validation),
	``ordered`` (the widgets in screen order) and ``table`` (the dialog-table
	widget of a list section, else None).
	"""
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
			table, ordered = _layout(section.get("section_ui_schema"))
			found.append(
				{
					"section_id": section_id,
					"tab_id": str(tab.get("tab_id") or ""),
					"tab_label": tab.get("tab_label") or tab.get("tab_description"),
					"section_register_id": str(section.get("section_register_id") or ""),
					"section_mnemonic": section.get("section_mnemonic"),
					"title": section.get("section_description"),
					"is_list": bool(section.get("is_list")),
					"required_for_submit": bool(
						(section.get("section_ui_schema") or {}).get("section-required-for-submit")
					),
					"table": table,
					"ordered": ordered,
					"widgets": _by_field(ordered),
				}
			)
	if not found:
		raise IntakeContractError("Rendered Livestock form has no sections")
	return found


def _layout(schema) -> tuple[dict | None, list[dict]]:
	"""(table widget or None, widgets in screen order). A table's columns are its widgets."""
	widgets: list[dict] = []

	def walk(node) -> None:
		if isinstance(node, list):
			for item in node:
				walk(item)
			return
		if not isinstance(node, dict):
			return
		for widget in node.get("widgets") or []:
			if isinstance(widget, dict):
				widgets.append(widget)
		walk(node.get("panels") or [])

	walk((schema or {}).get("panels") if isinstance(schema, dict) else None)
	tables = [widget for widget in widgets if widget.get("widget") == _TABLE_WIDGET]
	if tables:
		table = tables[0]
		columns = [column for column in table.get("widget-data-columns") or [] if isinstance(column, dict)]
		return table, columns
	return None, widgets


def _paths(widget: dict) -> dict[str, str]:
	"""Role to field name for one widget. A geo widget writes several fields."""
	key = widget.get("column-key")
	if isinstance(key, str) and key:
		return {"value": key}
	path = widget.get("widget-data-path")
	if isinstance(path, str) and path:
		return {"value": path.split(".")[-1]}
	if isinstance(path, dict):
		return {
			role: str(value).split(".")[-1]
			for role, value in path.items()
			if isinstance(value, str) and value
		}
	return {}


def _by_field(ordered: list[dict]) -> dict[str, dict]:
	found: dict[str, dict] = {}
	for widget in ordered:
		for name in _paths(widget).values():
			found.setdefault(name, widget)
	return found


def describe_form(
	form: dict,
	fields_by_register: dict[str, list[dict]],
	catalogs: dict[str, list] | None = None,
) -> dict:
	"""The intake form as the frontend renders it, widget by widget.

	``data_type`` comes from the register field when one exists. ``required`` is
	the widget flag. ``catalogs`` maps ``attribute_id`` to the rows already
	loaded; a missing key means that list was not loaded.
	"""
	catalogs = catalogs or {}
	sections = []
	form_tabs: list[dict] = []
	screen_tabs: list[dict] = []
	form_tab_at: dict[str, dict] = {}
	screen_tab_at: dict[str, dict] = {}
	for section in section_index(form):
		types = _field_types(fields_by_register.get(section["section_register_id"]))
		screen_sections = _SCREENS_BY_MNEMONIC.get(str(section.get("section_mnemonic") or ""), [])
		described = {
			"section_id": section["section_id"],
			"section_register_id": section["section_register_id"],
			"section_mnemonic": section["section_mnemonic"],
			"title": section["title"],
			"is_list": section["is_list"],
			"required_for_submit": section["required_for_submit"],
			"fields": [_describe_widget(widget, types, catalogs) for widget in section["ordered"]],
		}
		if screen_sections:
			described["screen_section"] = screen_sections[0]
			described["screen_sections"] = screen_sections
		table = section["table"]
		if table:
			described["table"] = {
				"widget_id": table.get("widget-id"),
				"label": table.get("widget-label"),
				"required": bool(table.get("widget-required")),
				"operations": table.get("widget-data-operations") or {},
				"dialog_title_add": table.get("widget-data-dialog-title-add"),
				"dialog_title_edit": table.get("widget-data-dialog-title-edit"),
			}
		sections.append(described)
		_append_tab(
			form_tabs,
			form_tab_at,
			section.get("tab_id") or "",
			section.get("tab_label"),
			described,
		)
		screen_key, screen_label = _screen_tab(section)
		_append_tab(screen_tabs, screen_tab_at, screen_key, screen_label, described)
	return {
		"form_id": form.get("form_id"),
		"register_id": form.get("register_id"),
		"form_mnemonic": form.get("form_mnemonic"),
		"tabs": form_tabs,
		"screen_tabs": screen_tabs,
		"sections": sections,
	}


def _screen_tab(section: dict) -> tuple[str, str | None]:
	"""Frontend tab for one section. An unmapped mnemonic keeps the form tab."""
	mapped = _SCREEN_TAB_OF_MNEMONIC.get(str(section.get("section_mnemonic") or ""))
	if mapped:
		return mapped
	return str(section.get("tab_id") or ""), section.get("tab_label")


def _append_tab(tabs: list[dict], index: dict[str, dict], key: str, label, section: dict) -> None:
	tab = index.get(key)
	if tab is None:
		tab = {"key": key, "label": label, "sections": []}
		index[key] = tab
		tabs.append(tab)
	tab["sections"].append(section)


def _field_types(fields) -> dict[str, str | None]:
	return {
		str(field["field_name"]): field.get("data_type")
		for field in fields or []
		if isinstance(field, dict) and field.get("field_name")
	}


def _describe_widget(widget: dict, types: dict, catalogs: dict[str, list]) -> dict:
	paths = _paths(widget)
	name = paths.get("value") or next(iter(paths.values()), None)
	kind = widget.get("widget")
	source = widget.get("widget-data-source") if isinstance(widget.get("widget-data-source"), dict) else {}
	params = source.get("params") if isinstance(source.get("params"), dict) else {}
	options = widget.get("widget-data-options") if isinstance(widget.get("widget-data-options"), dict) else {}
	readonly = bool(widget.get("widget-readonly"))
	described: dict = {
		"field_name": name,
		"widget": kind,
		"label": widget.get("widget-label"),
		"data_type": types.get(name) if name else None,
		"required": bool(widget.get("widget-required")) and not readonly,
		"readonly": readonly,
	}
	if kind in _GEO_WIDGETS:
		described["writes"] = paths
		described["geo"] = {"endpoint": "geo-level-values", "levels": [level for level, _id in _GEO_LEVELS]}
	optional = {
		"placeholder": widget.get("widget-data-placeholder"),
		"pattern": (widget.get("widget-data-validation") or {}).get("pattern"),
		"pattern_message": (widget.get("widget-data-validation") or {}).get("patternMessage"),
		"format": widget.get("widget-data-format"),
		"rules": options.get("actions"),
		"min_date": options.get("minDate"),
		"autofill": widget.get("widget-autofill"),
		"age_from": (widget.get("widget-age-from-date") or {}).get("baseField"),
		"date_offset": widget.get("widget-date-offset"),
		"badge_colors": widget.get("widget-badge-colors"),
		"options": _option_values(source.get("options")),
	}
	described.update({key: value for key, value in optional.items() if value})
	attribute_id = params.get("attribute_id")
	if attribute_id:
		described["attribute_id"] = attribute_id
		key = str(attribute_id)
		described["choices"] = _catalog_choices(catalogs[key]) if key in catalogs else None
		described["catalog_loaded"] = key in catalogs
		described["value_key"] = source.get("valueKey") or "value_id"
	return described


def submit_with_metadata(
	*,
	form: dict,
	fields_for: Callable[[str], list[dict]],
	catalogs: dict[str, list],
	requests: list[dict],
	submission_id: str | None,
	subject_record_id: str | None,
	save: Callable[..., dict],
	find_subject: Callable[[str], str | None] | None = None,
	farmer_id: str | None = None,
) -> dict:
	"""Validate each requested section and save it, chaining submission_id.

	Sections of the form's own register are saved first. A list section is
	linked to that register row (``link_internal_record_id``). When the caller
	adds list sections to an existing draft without ``subject_record_id``,
	``find_subject`` reads it from the draft. ``farmer_id`` (the Farmer-ID of
	the route) fills the farmer lookup field and must match a value sent there.
	"""
	if not isinstance(requests, list) or not requests:
		raise IntakeContractError("At least one section is required")
	indexed = section_index(form)
	form_register_id = str(form.get("register_id") or "")
	prepared: list[tuple[dict, list]] = []
	for request in requests:
		if not isinstance(request, dict):
			raise IntakeContractError("Each section must be an object")
		records = request.get("records")
		if records is None:
			records = request.get("section_payload")
		prepared.append((resolve_section(indexed, request), records))
	_fill_farmer_id(form, prepared, farmer_id)
	_autofill(indexed, prepared)
	prepared.sort(key=lambda item: item[0]["section_register_id"] != form_register_id)

	current = submission_id or None
	subject = subject_record_id or None
	saved: dict | None = None
	for section, records in prepared:
		is_child = section["section_register_id"] != form_register_id
		if is_child and not subject and current and find_subject is not None:
			subject = find_subject(current)
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
			raise IntakeContractError("Livestock save did not return a submission_id")
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

	``section_id`` is the intake section, or a screen id such as ``vaccination``
	(mapped to the staff mnemonic). ``section_register_id`` alone is accepted
	only when it names one section: several sections share the Livestock register.
	"""
	section_id = str(request.get("section_id") or "").strip()
	mnemonic = str(request.get("section_mnemonic") or "").strip() or _SCREEN_SECTIONS.get(section_id, "")
	register_id = str(request.get("section_register_id") or "").strip()
	by_id = [section for section in sections if section_id and section["section_id"] == section_id]
	by_mnemonic = [
		section for section in sections if mnemonic and str(section.get("section_mnemonic") or "") == mnemonic
	]
	by_register = [
		section for section in sections if register_id and section["section_register_id"] == register_id
	]
	if section_id and by_id:
		matches = by_id
	elif section_id in _SCREEN_SECTIONS or (mnemonic and not section_id):
		matches = by_mnemonic
	elif register_id and not section_id:
		matches = by_register
	elif mnemonic and not section_id:
		matches = by_mnemonic
	elif section_id or mnemonic or register_id:
		matches = by_id or by_mnemonic or by_register
	else:
		raise IntakeContractError("section_id, section_mnemonic, or section_register_id is required")
	named = section_id or mnemonic or register_id
	if len(matches) != 1:
		raise IntakeContractError(f"Livestock section {named} is missing or ambiguous")
	section = matches[0]
	if register_id and register_id != section["section_register_id"]:
		raise IntakeContractError("section_register_id does not match the intake form")
	if not section["section_register_id"]:
		raise IntakeContractError("Livestock section has no register")
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
	"""Copy caller records into the section's widget fields and reject anything else.

	A field is accepted when the section has a widget for it. Its type comes
	from the register field of that name; a form-only field (``fayda_id``) is
	a string.
	"""
	label = section.get("section_mnemonic") or section["section_id"]
	if not isinstance(records, list) or not records:
		raise IntakeContractError(f"{label}: records must be a non-empty list")
	if not section.get("is_list") and len(records) != 1:
		raise IntakeContractError(f"{label}: this section accepts one record")
	types = _field_types(fields)
	widgets = section["widgets"]
	derived = _derived_fields(section)
	catalogs = catalogs or {}
	is_child = bool(form_register_id) and section["section_register_id"] != form_register_id
	if is_child and not subject_record_id:
		raise IntakeContractError(f"{label}: save the main Livestock section before this section")
	built: list[dict] = []
	for index, record in enumerate(records):
		where = f"{label}[{index}]" if section.get("is_list") else label
		if not isinstance(record, dict):
			raise IntakeContractError(f"{where}: each record must be an object")
		row: dict = {}
		for key, value in record.items():
			name = str(key)
			if _MANAGED.match(name):
				raise IntakeContractError(f"{where}.{name} is managed by the registry")
			widget = widgets.get(name)
			if widget is None:
				raise IntakeContractError(f"{where}: unknown Livestock field {name}")
			if _blank(value):
				continue
			if widget.get("widget-readonly") and name not in derived:
				raise IntakeContractError(f"{where}.{name} is read-only")
			data_type = types.get(name) or "string"
			value = _coerce(data_type, value)
			value = _canonicalize_choice(f"{where}.{name}", widget, value, catalogs)
			_check_type(f"{where}.{name}", data_type, value)
			_check_pattern(f"{where}.{name}", widget, value)
			row[name] = value
		_fill_geo_hierarchy(row, widgets)
		_check_required(where, section, row)
		if is_child:
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


def lookup_section(form: dict) -> dict | None:
	"""The main-register section that holds the ``farmer_id`` widget, if any."""
	register_id = str(form.get("register_id") or "")
	for section in section_index(form):
		if section["section_register_id"] == register_id and "farmer_id" in section["widgets"]:
			return section
	return None


def geo_hierarchy(lowest_level_value_id: str) -> dict | None:
	"""``geo_code_hierarchy_json`` for a Master Data place id such as ET01010101."""
	code = str(lowest_level_value_id or "").strip()
	if not _GEO_CODE.match(code):
		return None
	hierarchy = []
	for depth, (level, level_id) in enumerate(_GEO_LEVELS):
		end = 4 + 2 * depth
		if len(code) < end:
			break
		hierarchy.append(
			{"level": level, "level_id": level_id, "level_mnemonic": level, "level_value_id": code[:end]}
		)
	return {"hierarchy": hierarchy, "lowest_level_value_id": code}


def _fill_farmer_id(form: dict, prepared: list[tuple[dict, list]], farmer_id: str | None) -> None:
	if not farmer_id:
		return
	lookup = lookup_section(form)
	if lookup is None:
		return
	for section, records in prepared:
		if section["section_id"] != lookup["section_id"] or not isinstance(records, list):
			continue
		for record in records:
			if not isinstance(record, dict):
				continue
			sent = str(record.get("farmer_id") or "").strip()
			if sent and sent != farmer_id:
				raise IntakeContractError(f"farmer_id {sent} does not match the farmer {farmer_id}")
			record["farmer_id"] = farmer_id


def _autofill(sections: list[dict], prepared: list[tuple[dict, list]]) -> None:
	"""Copy species and age from the animal row with the same ear tag, as the staff form does.

	Only blank target fields are filled, and only from animal rows sent in the
	same request.
	"""
	by_table = {
		str(section["table"].get("widget-id")): records
		for section, records in prepared
		if section.get("table") and isinstance(records, list)
	}
	for section, records in prepared:
		if not isinstance(records, list):
			continue
		for widget in section["ordered"]:
			rule = widget.get("widget-autofill")
			if not isinstance(rule, dict):
				continue
			source_rows = by_table.get(str(rule.get("sourceWidgetId") or ""))
			match_field = str(rule.get("matchField") or "")
			mapping = rule.get("fields") if isinstance(rule.get("fields"), dict) else {}
			if not source_rows or not match_field or not mapping:
				continue
			by_key = {
				row.get(match_field): row
				for row in source_rows
				if isinstance(row, dict) and not _blank(row.get(match_field))
			}
			for record in records:
				if not isinstance(record, dict):
					continue
				source = by_key.get(record.get(match_field))
				if source is None:
					continue
				for target, source_field in mapping.items():
					if _blank(record.get(target)) and not _blank(source.get(source_field)):
						record[target] = source[source_field]


def _derived_fields(section: dict) -> set[str]:
	"""Read-only fields the staff form fills itself (age from date of birth, autofilled species)."""
	derived: set[str] = set()
	for widget in section["ordered"]:
		if widget.get("widget-age-from-date"):
			derived.update(_paths(widget).values())
		rule = widget.get("widget-autofill")
		if isinstance(rule, dict) and isinstance(rule.get("fields"), dict):
			derived.update(str(target) for target in rule["fields"])
	return derived


def _check_required(where: str, section: dict, row: dict) -> None:
	for widget in section["ordered"]:
		if widget.get("widget-readonly"):
			continue
		for name in _paths(widget).values():
			if _MANAGED.match(name) or not _blank(row.get(name)):
				continue
			if widget.get("widget-required") or _rule_requires(widget, row):
				raise IntakeContractError(f"{where}.{name} is required")


def _rule_requires(widget: dict, row: dict) -> bool:
	"""True when a ``require`` action of the widget applies to this record."""
	options = widget.get("widget-data-options") if isinstance(widget.get("widget-data-options"), dict) else {}
	for action in options.get("actions") or []:
		if isinstance(action, dict) and action.get("action") == "require":
			if _condition_holds(action.get("condition"), row):
				return True
	return False


def _condition_holds(condition, row: dict) -> bool:
	if not isinstance(condition, dict):
		return False
	value = row.get(str(condition.get("field") or ""))
	operator = condition.get("operator")
	if operator == "empty":
		return _blank(value)
	if operator == "notEmpty":
		return not _blank(value)
	if operator == "equals":
		return value == condition.get("value")
	if operator == "notEquals":
		return value != condition.get("value")
	return False


def _fill_geo_hierarchy(row: dict, widgets: dict[str, dict]) -> None:
	"""Send the place hierarchy with a kebele id, as the staff geo widget does."""
	if "geo_code_hierarchy_json" not in widgets or not _blank(row.get("geo_code_hierarchy_json")):
		return
	built = geo_hierarchy(row.get("geo_lowest_level_value_id"))
	if built:
		row["geo_code_hierarchy_json"] = built


def _check_pattern(name: str, widget: dict, value) -> None:
	pattern = (widget.get("widget-data-validation") or {}).get("pattern")
	if not pattern or not isinstance(value, str):
		return
	try:
		matched = re.match(pattern, value)
	except re.error:
		return
	if not matched:
		raise IntakeContractError(f"{name} does not match the expected format")


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
	"""Live catalog rows. The widget's ``value_key`` says which column is stored."""
	if not isinstance(rows, list):
		return []
	choices = []
	for row in rows:
		if not isinstance(row, dict) or not row.get("value_id"):
			continue
		choice = {"value_id": row.get("value_id"), "value_display": row.get("value_display")}
		if row.get("value_code"):
			choice["value_code"] = row.get("value_code")
		choices.append(choice)
	return choices


def _check_type(name: str, data_type, value) -> None:
	check = _TYPE_OK.get(str(data_type or "").lower())
	if check is not None and not check(value):
		raise IntakeContractError(f"{name} must be {data_type}")


def _coerce(data_type, value):
	"""Turn a screen string into the register type. Other values stay."""
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
	if kind in {"date", "datetime"}:
		dmy = _DMY.match(text)
		return f"{dmy.group(3)}-{dmy.group(2)}-{dmy.group(1)}" if dmy else text
	if kind == "json" and text[:1] in "[{":
		try:
			return json.loads(text)
		except ValueError:
			return value
	return text


def _same(left, right) -> bool:
	return isinstance(left, str) and isinstance(right, str) and left.casefold() == right.casefold()


def _stored_catalog_value(row: dict, value_key: str):
	"""The column this widget stores. Species stores ``value_id``; health stores ``value_code``."""
	if value_key == "value_code" and row.get("value_code"):
		return row.get("value_code")
	if value_key == "value_display" and row.get("value_display"):
		return row.get("value_display")
	return row.get("value_id")


def _canonicalize_choice(name: str, widget: dict, value, catalogs: dict[str, list]):
	"""Keep the widget's stored code, or the code whose label is the text the screen sent."""
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
	value_key = str(source.get("valueKey") or "value_id")
	for row in catalog:
		if not isinstance(row, dict):
			continue
		if value in {row.get("value_id"), row.get("value_code")} or _same(value, row.get("value_display")):
			return _stored_catalog_value(row, value_key)
	raise IntakeContractError(f"{name} is not an allowed value")


def _blank(value) -> bool:
	return value is None or value == ""
