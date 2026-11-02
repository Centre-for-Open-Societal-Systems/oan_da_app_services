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


def _section(section_id: str, register_id: str, description: str, order: int, panels: list, *, is_list=False):
	return {
		"register_id": REGISTER_ID,
		"section_id": section_id,
		"section_register_id": register_id,
		"section_mnemonic": section_id,
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
					"species": "CATTLE",
					"gender": "FEMALE",
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
		return [
			{
				"kind": "submission",
				"submission_id": FIXTURE_SUBMISSION_ID,
				"record_name": FIXTURE_FARMER_ID,
				"draft_status": "FINAL",
				"approval_status": "PENDING",
			}
		]
