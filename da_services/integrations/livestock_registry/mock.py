"""In-memory Livestock registry for local development and tests.

The form is a trimmed copy of the dev "Livestock Intake" form (2 Nov 2026):
farmer lookup, location, livestock details and health events, with the same
widget kinds, required flags and show / require rules.
"""

from __future__ import annotations

from .client import LivestockRegistryClient, LivestockRegistryClientError
from .intake import describe_form, submit_with_metadata
from .mapper import submission_to_livestock
from .schemas import LivestockSubmission

REGISTER_ID = "reg-livestock"
ANIMAL_REGISTER_ID = "reg-animal"
HEALTH_REGISTER_ID = "reg-health-event"
VACCINATION_REGISTER_ID = "reg-vaccination"
VITAL_REGISTER_ID = "reg-vital-event"
BREEDING_REGISTER_ID = "reg-breeding"
RETAG_REGISTER_ID = "reg-retagging"

# Staff section mnemonic for each intake section id. Screen ids in intake.py
# resolve to these mnemonics.
_MNEMONICS = {
	"livestock_farmer_lookup": "ls_farmer_lookup",
	"livestock_survey_personnel_section_02": "ls_survey_personnel",
	"livestock_livestock_location_section_03": "ls_livestock_location",
	"livestock_animal_details_section_01": "ls_animal_details",
	"livestock_retagging_details_section_01": "ls_retagging_details",
	"livestock_health_event_details_section_01": "ls_health_event_details",
	"livestock_vaccination_details_section_01": "ls_vaccination_details",
	"livestock_vital_event_details_section_01": "ls_vital_event_details",
	"livestock_breeding_details_section_01": "ls_breeding_details",
}
FIXTURE_SUBMISSION_ID = "livestock-sub-001"
FIXTURE_SUBJECT_ID = "livestock-rec-001"
FIXTURE_FARMER_ID = "FR-512145929324"


def _select(column: str, label: str, attribute_id: str, **extra) -> dict:
	return {
		"widget": "select",
		"column-key": column,
		"widget-type": "input",
		"widget-label": label,
		"widget-readonly": False,
		"widget-data-path": column,
		"widget-data-source": {
			"type": "api",
			"params": {"page_size": 500, "attribute_id": attribute_id},
			"service": "attributes",
			"endpoint": "values",
			"labelKey": "value_display",
			"valueKey": "value_id",
		},
		**extra,
	}


def _text(column: str, label: str, **extra) -> dict:
	return {
		"widget": "text",
		"column-key": column,
		"widget-label": label,
		"widget-readonly": False,
		**extra,
	}


def _table(widget_id: str, label: str, register_id: str, columns: list[dict]) -> dict:
	return {
		"widgets": [
			{
				"widget": "dialog-table",
				"widget-id": widget_id,
				"widget-label": label,
				"widget-data-path": f"{register_id}.records",
				"widget-data-operations": {"add": True, "edit": True, "remove": True},
				"widget-data-columns": columns,
			}
		]
	}


def _section(section_id: str, register_id: str, description: str, order: int, panels: list, *, is_list=False):
	return {
		"register_id": REGISTER_ID,
		"section_id": section_id,
		"section_register_id": register_id,
		"section_mnemonic": _MNEMONICS.get(section_id, section_id),
		"section_description": description,
		"is_list": is_list,
		"section_order": order,
		"section_ui_schema": {"panels": panels, "section-id": section_id, "section-title": description},
	}


MOCK_FORM = {
	"form_id": "form-livestock",
	"register_id": REGISTER_ID,
	"form_mnemonic": "Livestock Intake",
	"tabs": [
		{
			"tab_id": "tab-livestock",
			"tab_label": "Livestock Intake",
			"sections": [
				_section(
					"livestock_farmer_lookup",
					REGISTER_ID,
					"Farmer lookup",
					1,
					[
						{
							"widgets": [
								{
									"widget": "text",
									"widget-id": "farmer_id",
									"widget-label": "Farmer ID",
									"widget-required": True,
									"widget-data-path": f"{REGISTER_ID}.farmer_id",
									"widget-data-validation": {"pattern": r"^\s*FR-[0-9]{12}\s*$"},
								},
								{
									"widget": "text",
									"widget-id": "fayda_id",
									"widget-label": "Fayda ID (FAN or FIN)",
									"widget-required": True,
									"widget-data-path": f"{REGISTER_ID}.fayda_id",
									"widget-data-validation": {
										"pattern": r"^\s*(?:[0-9]{12}|[0-9](?:\s*[0-9]){15})\s*$"
									},
								},
							]
						}
					],
				),
				_section(
					"livestock_survey_personnel_section_02",
					REGISTER_ID,
					"Survey Personnel",
					2,
					[
						{
							"widgets": [
								_text("surveyor_name", "DA Name"),
								_text("surveyor_mobile_number", "DA Mobile Number"),
								_text("supervisor_name", "Supervisor Name"),
								_text("supervisor_mobile_number", "Supervisor Mobile Number"),
							]
						}
					],
				),
				_section(
					"livestock_livestock_location_section_03",
					REGISTER_ID,
					"Location",
					3,
					[
						{
							"widgets": [
								{
									"widget": "geo-hierarchy",
									"widget-id": "livestock_geo_hierarchy",
									"widget-label": "Administrative hierarchy",
									"widget-required": True,
									"widget-data-path": {
										"value": f"{REGISTER_ID}.geo_lowest_level_value_id",
										"hierarchy": f"{REGISTER_ID}.geo_code_hierarchy_json",
									},
								},
								{
									"widget": "oan-geo-point",
									"widget-id": "livestock_geo_point",
									"widget-label": "Holding location",
									"widget-data-path": {
										"latitude": f"{REGISTER_ID}.latitude",
										"longitude": f"{REGISTER_ID}.longitude",
									},
								},
							]
						}
					],
				),
				_section(
					"livestock_animal_details_section_01",
					ANIMAL_REGISTER_ID,
					"Livestock details",
					4,
					[
						{
							"widgets": [
								{
									"widget": "dialog-table",
									"widget-id": "ls_animal_details_table",
									"widget-label": "Livestock details",
									"widget-required": True,
									"widget-data-path": f"{ANIMAL_REGISTER_ID}.records",
									"widget-data-operations": {"add": True, "edit": True, "remove": True},
									"widget-data-columns": [
										{
											"widget": "text",
											"column-key": "ear_tag_id",
											"widget-label": "Ear tag",
										},
										_select(
											"species",
											"Species",
											"LIVESTOCK_SPECIES",
											**{"widget-required": True},
										),
										{
											"widget": "select",
											"column-key": "gender",
											"widget-label": "Sex",
											"widget-required": True,
											"widget-data-source": {
												"type": "static",
												"options": [
													{"label": "Male", "value": "MALE"},
													{"label": "Female", "value": "FEMALE"},
												],
											},
										},
										{
											"widget": "number",
											"column-key": "quantity",
											"widget-label": "Head count",
										},
										{
											"widget": "date",
											"column-key": "date_of_birth",
											"widget-label": "Date of birth",
										},
										{
											"widget": "text",
											"column-key": "age",
											"widget-label": "Age",
											"widget-readonly": True,
											"widget-age-from-date": {"baseField": "date_of_birth"},
										},
									],
								}
							]
						}
					],
					is_list=True,
				),
				_section(
					"livestock_health_event_details_section_01",
					HEALTH_REGISTER_ID,
					"Health event details",
					5,
					[
						{
							"widgets": [
								{
									"widget": "dialog-table",
									"widget-id": "ls_health_event_details_table",
									"widget-label": "Health event details",
									"widget-data-path": f"{HEALTH_REGISTER_ID}.records",
									"widget-data-columns": [
										{
											"widget": "text",
											"column-key": "ear_tag_id",
											"widget-label": "Ear tag",
											"widget-autofill": {
												"fields": {"age": "age", "species": "species"},
												"matchField": "ear_tag_id",
												"sourceWidgetId": "ls_animal_details_table",
											},
											"widget-data-options": {
												"actions": [
													{
														"action": "require",
														"condition": {
															"field": "ear_tag_id",
															"operator": "empty",
														},
													}
												]
											},
										},
										_select(
											"species",
											"Species",
											"LIVESTOCK_SPECIES",
											**{"widget-readonly": True},
										),
										{
											"widget": "text",
											"column-key": "age",
											"widget-label": "Age",
											"widget-readonly": True,
										},
										_select(
											"event_type",
											"Event type",
											"LIVESTOCK_HEALTH_EVENT_TYPE",
											**{
												"widget-data-options": {
													"actions": [
														{
															"action": "require",
															"condition": {
																"field": "event_type",
																"operator": "empty",
															},
														}
													]
												}
											},
										),
										_select(
											"disease_type",
											"Disease",
											"LIVESTOCK_DISEASE",
											**{
												"widget-data-options": {
													"actions": [
														{
															"action": "show",
															"condition": {
																"field": "event_type",
																"value": "DISEASE",
																"operator": "equals",
															},
														},
														{
															"action": "require",
															"condition": {
																"field": "event_type",
																"value": "DISEASE",
																"operator": "equals",
															},
														},
													]
												}
											},
										),
										{
											"widget": "radio",
											"column-key": "is_notifiable",
											"widget-label": "Notifiable disease",
											"widget-data-source": {
												"type": "static",
												"options": [
													{"label": "Yes", "value": True},
													{"label": "No", "value": False},
												],
											},
										},
									],
								}
							]
						}
					],
					is_list=True,
				),
				_section(
					"livestock_retagging_details_section_01",
					RETAG_REGISTER_ID,
					"Ear Tag Replacement",
					6,
					[
						_table(
							"ls_retagging_details_table",
							"Ear Tag Replacement",
							RETAG_REGISTER_ID,
							[
								_text("ear_tag_id", "Old Ear Tag"),
								_select("species", "Species", "LIVESTOCK_SPECIES"),
								_text("new_ear_tag_id", "New Ear Tag"),
								_text("reason", "Reason for Replacement"),
								_text("retag_date", "Date of Retagging"),
								_text("approving_officer", "Approving Officer"),
								_text("justification", "Justification Notes"),
							],
						)
					],
					is_list=True,
				),
				_section(
					"livestock_vaccination_details_section_01",
					VACCINATION_REGISTER_ID,
					"Vaccination Details",
					7,
					[
						_table(
							"ls_vaccination_details_table",
							"Vaccination Details",
							VACCINATION_REGISTER_ID,
							[
								_text(
									"ear_tag_id",
									"Ear Tag",
									**{
										"widget-autofill": {
											"fields": {"age": "age", "species": "species"},
											"matchField": "ear_tag_id",
											"sourceWidgetId": "ls_animal_details_table",
										}
									},
								),
								_select(
									"species", "Species", "LIVESTOCK_SPECIES", **{"widget-readonly": True}
								),
								_text("age", "Age", **{"widget-readonly": True}),
								_text("vaccine_type", "Vaccine"),
								_text("vaccination_date", "Vaccination Date"),
								_text("next_due_date", "Next Due Date"),
							],
						)
					],
					is_list=True,
				),
				_section(
					"livestock_vital_event_details_section_01",
					VITAL_REGISTER_ID,
					"Vital Event Details",
					8,
					[
						_table(
							"ls_vital_event_details_table",
							"Vital Event Details",
							VITAL_REGISTER_ID,
							[
								_text("ear_tag_id", "Ear Tag"),
								_select(
									"species", "Species", "LIVESTOCK_SPECIES", **{"widget-readonly": True}
								),
								_text("event_type", "Event Type"),
								_text("event_date", "Event Date"),
								_text("offspring_count", "Number of Offspring"),
							],
						)
					],
					is_list=True,
				),
				_section(
					"livestock_breeding_details_section_01",
					BREEDING_REGISTER_ID,
					"Breeding Details",
					9,
					[
						_table(
							"ls_breeding_details_table",
							"Breeding Details",
							BREEDING_REGISTER_ID,
							[
								_text("ear_tag_id", "Ear Tag"),
								_select(
									"species", "Species", "LIVESTOCK_SPECIES", **{"widget-readonly": True}
								),
								_text("event_type", "Breeding Type"),
								_text("breeding_date", "Breeding Date"),
								_text("outcome", "Outcome"),
							],
						)
					],
					is_list=True,
				),
			],
		}
	],
}

MOCK_FIELDS = {
	REGISTER_ID: [
		{"field_name": "farmer_id", "data_type": "string"},
		{"field_name": "geo_lowest_level_value_id", "data_type": "string"},
		{"field_name": "geo_code_hierarchy_json", "data_type": "json"},
		{"field_name": "latitude", "data_type": "number"},
		{"field_name": "longitude", "data_type": "number"},
		{"field_name": "surveyor_name", "data_type": "string"},
		{"field_name": "surveyor_mobile_number", "data_type": "string"},
		{"field_name": "supervisor_name", "data_type": "string"},
		{"field_name": "supervisor_mobile_number", "data_type": "string"},
		{"field_name": "internal_record_id", "data_type": "string", "required": True},
	],
	ANIMAL_REGISTER_ID: [
		{"field_name": "ear_tag_id", "data_type": "string"},
		{"field_name": "species", "data_type": "string", "required": True},
		{"field_name": "gender", "data_type": "string", "required": True},
		{"field_name": "quantity", "data_type": "integer"},
		{"field_name": "date_of_birth", "data_type": "date"},
		{"field_name": "age", "data_type": "string"},
	],
	HEALTH_REGISTER_ID: [
		{"field_name": "ear_tag_id", "data_type": "string"},
		{"field_name": "species", "data_type": "string"},
		{"field_name": "age", "data_type": "string"},
		{"field_name": "event_type", "data_type": "string"},
		{"field_name": "disease_type", "data_type": "string"},
		{"field_name": "is_notifiable", "data_type": "boolean"},
	],
	VACCINATION_REGISTER_ID: [
		{"field_name": "ear_tag_id", "data_type": "string"},
		{"field_name": "species", "data_type": "string"},
		{"field_name": "age", "data_type": "string"},
		{"field_name": "vaccine_type", "data_type": "string"},
		{"field_name": "vaccination_date", "data_type": "date"},
		{"field_name": "next_due_date", "data_type": "date"},
	],
	VITAL_REGISTER_ID: [
		{"field_name": "ear_tag_id", "data_type": "string"},
		{"field_name": "species", "data_type": "string"},
		{"field_name": "event_type", "data_type": "string"},
		{"field_name": "event_date", "data_type": "date"},
		{"field_name": "offspring_count", "data_type": "integer"},
	],
	BREEDING_REGISTER_ID: [
		{"field_name": "ear_tag_id", "data_type": "string"},
		{"field_name": "species", "data_type": "string"},
		{"field_name": "event_type", "data_type": "string"},
		{"field_name": "breeding_date", "data_type": "date"},
		{"field_name": "outcome", "data_type": "string"},
	],
	RETAG_REGISTER_ID: [
		{"field_name": "ear_tag_id", "data_type": "string"},
		{"field_name": "species", "data_type": "string"},
		{"field_name": "new_ear_tag_id", "data_type": "string"},
		{"field_name": "reason", "data_type": "string"},
		{"field_name": "retag_date", "data_type": "date"},
		{"field_name": "approving_officer", "data_type": "string"},
		{"field_name": "justification", "data_type": "string"},
	],
}

MOCK_CATALOGS = {
	"LIVESTOCK_SPECIES": [
		{"value_id": "CATTLE", "value_code": "CATTLE", "value_display": "Cattle"},
		{"value_id": "GOATS", "value_code": "GOATS", "value_display": "Goats"},
	],
	"LIVESTOCK_HEALTH_EVENT_TYPE": [
		{"value_id": "DISEASE", "value_code": "DISEASE", "value_display": "Disease"},
		{"value_id": "INJURY", "value_code": "INJURY", "value_display": "Injury"},
	],
	"LIVESTOCK_DISEASE": [
		{
			"value_id": "LIVESTOCK_DISEASE_LSD",
			"value_code": "LIVESTOCK_DISEASE_LSD",
			"value_display": "Lumpy skin disease",
		},
	],
}

_FIXTURE = {
	"submission_id": FIXTURE_SUBMISSION_ID,
	"record_name": FIXTURE_FARMER_ID,
	"application_reference": "2026NOV02-000001",
	"draft_status": "FINAL",
	"approval_status": "PENDING",
	"section_payloads": [
		{
			"section_id": "livestock_farmer_lookup",
			"section_register_id": REGISTER_ID,
			"records": [
				{"internal_record_id": FIXTURE_SUBJECT_ID, "farmer_id": FIXTURE_FARMER_ID, "total_animals": 1}
			],
		},
		{
			"section_id": "livestock_animal_details_section_01",
			"section_register_id": ANIMAL_REGISTER_ID,
			"records": [
				{
					"internal_record_id": "animal-rec-001",
					"link_internal_record_id": FIXTURE_SUBJECT_ID,
					"ear_tag_id": "ET2149721223",
					"secondary_identifier": "NAME-LUCY",
					"species": "CATTLE",
					"breed": "HYBRID",
					"quantity": 1,
					"weight": 326,
					"date_of_birth": "2022-03-01",
					"gender": "FEMALE",
					"health_status": "HEALTHY",
					"vaccination_status": "UP_TO_DATE",
					"registration_date": "2026-11-02",
				}
			],
		},
	],
}


class MockLivestockRegistryClient(LivestockRegistryClient):
	"""Fixture form and submission used when livestock_registry_backend is mock."""

	def describe_intake_form(self) -> dict:
		return describe_form(MOCK_FORM, MOCK_FIELDS, MOCK_CATALOGS)

	def geo_level_values(self, level_id: str, parent_level_value_id: str | None = None) -> list[dict]:
		# One synthetic place per level. The parent is accepted and not applied.
		del parent_level_value_id
		if not str(level_id or "").strip():
			raise LivestockRegistryClientError("level_id is required")
		return [{"level_value_id": "ET01", "level_value_mnemonic": "SYNTHETIC", "display_name": "Synthetic"}]

	def submit_sections(
		self,
		requests: list[dict],
		*,
		submission_id: str | None = None,
		subject_record_id: str | None = None,
		farmer_id: str | None = None,
	) -> dict:
		return submit_with_metadata(
			form=MOCK_FORM,
			fields_for=lambda register_id: list(MOCK_FIELDS.get(register_id) or []),
			catalogs=MOCK_CATALOGS,
			requests=requests,
			submission_id=submission_id,
			subject_record_id=subject_record_id,
			save=self.save_section,
			find_subject=lambda _submission_id: FIXTURE_SUBJECT_ID,
			farmer_id=farmer_id,
		)

	def save_section(
		self,
		*,
		section_id: str,
		section_register_id: str,
		section_payload: list[dict],
		submission_id: str | None = None,
		form_id: str | None = None,
		register_id: str | None = None,
	) -> dict:
		if not section_id or not section_register_id or not section_payload:
			raise LivestockRegistryClientError(
				"section_id, section_register_id and section_payload are required"
			)
		records = [dict(row) for row in section_payload]
		if section_register_id == REGISTER_ID:
			for row in records:
				row.setdefault("internal_record_id", FIXTURE_SUBJECT_ID)
		return {
			"submission_id": submission_id or FIXTURE_SUBMISSION_ID,
			"draft_status": "DRAFT",
			"form_id": form_id,
			"register_id": register_id,
			"section_payloads": [
				{"section_id": section_id, "section_register_id": section_register_id, "records": records}
			],
		}

	def finalize_submission(self, submission_id: str) -> dict:
		if not submission_id:
			raise LivestockRegistryClientError("submission_id is required")
		return {"submission_id": submission_id, "draft_status": "FINAL", "approval_status": "PENDING"}

	def get_submission(self, submission_id: str) -> LivestockSubmission | None:
		if submission_id != FIXTURE_SUBMISSION_ID:
			return None
		return submission_to_livestock(_FIXTURE)

	def list_for_farmer(self, farmer_id: str) -> list[dict]:
		if farmer_id != FIXTURE_FARMER_ID:
			return []
		holding = _FIXTURE["section_payloads"][0]["records"][0]
		animal = _FIXTURE["section_payloads"][1]["records"][0]
		sections = [
			{
				"section_id": section["section_id"],
				"section_register_id": section["section_register_id"],
				"records": section["records"],
			}
			for section in _FIXTURE["section_payloads"]
		]
		return [
			{
				"kind": "submission",
				"register_id": REGISTER_ID,
				"submission_id": FIXTURE_SUBMISSION_ID,
				"record_name": FIXTURE_FARMER_ID,
				"draft_status": "FINAL",
				"approval_status": "PENDING",
				"sections": sections,
			},
			{"kind": "record", "register_id": REGISTER_ID, **holding},
			{"kind": "record", "register_id": ANIMAL_REGISTER_ID, **animal},
		]
