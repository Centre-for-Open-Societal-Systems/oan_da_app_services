# Livestock parameterized collection run, 2026-11-02

Source: Postman run `58813371-735e89bb-7e63-4b30-bd9e-97d1ec9fcb95`, collection **OAN Registries - Fully Parameterized Integration** (Pushkar's Fork), environment **OAN Registry - Livestock - New Dev [Pushkar's Fork]**. Ran today at 06:56 PM. One iteration, 34s, 84 tests, 83 passed, 1 failed, 0 errors.

Response bodies are not stored on this run. Postman shows "Response is no longer available." The section list, `get_register_fields` rows, and catalog rows from this run are therefore not in this file. What follows is what the runner and the collection scripts establish. Field names in the generator are the names that script would send. They were not sent on this run.

## Result

Discovery passed: register, intake form, widgets, field definitions, code lists, and a four-level geography path.

The write did not start. Request **01 Generate and save all supported fields** failed its test `Generation prerequisites available` and was not sent:

> New Dev Master Data is missing registry-specific LIVESTOCK_SPECIES_* values. Restore the Livestock catalog before running this intake; generic CATTLE/GOATS values are rejected by the deployed registry.

The script throws that when `catalogs.LIVESTOCK_SPECIES` has no `value_id` starting with `LIVESTOCK_SPECIES_`. Request 05 had already returned SUCCESS, so the catalog response for this run did not contain those ids. No draft was saved. Finalize and read-back did not run.

## Environment keys used

From the Livestock New Dev environment. Secrets are not copied here.

| Key | Value on this run |
| --- | --- |
| `registry_key` | `livestock` |
| `register_mnemonic` | `Livestock` |
| `form_name_contains` | `Livestock Intake` |
| `registry_client_id` | `livestock-registry-staff-portal` |
| `staff_base_url` | `https://staff-livestock.development.oanstaging.com` |
| `registry_ui_url` | `https://livestock-registry.development.oanstaging.com` |
| `master_data_url` | `https://master-data.development.oanstaging.com` |
| `iam_base_url` | `https://staff-iam.development.oanstaging.com` |
| `kc_realm_url` | `https://keycloak.development.oanstaging.com/realms/staff` |
| `login_provider_id` | `1` |
| `test_document_id` | empty |
| `kc_username`, `kc_password`, `access_token` | set; not copied |

Collection variables: `auth_mode=existing_user`, `enable_writes=true`, `enable_finalize=true`.

`farmer_staff_base_url` is not in this environment. Requests 08 and 09 only run when `registry_key` is `cropsown`, so they were skipped for Livestock. Request 10 only runs when `registry_key` is `farmer`, so the identity-document upload was skipped.

## Calls that ran

Staff and master-data calls use the envelope `request_header.sender_app_mnemonic = OAN_REGISTRY_API_TEST` and `request_body.request_payload`. A passing call is HTTP 200 and `response_header.response_status = SUCCESS`. The payload is `response_body.response_payload`.

| # | Call | Host path | Keys read or stored |
| --- | --- | --- | --- |
| 01 | Discover target register | `POST /register-metadata/get_all_registers` | Match `register_mnemonic`. Store `register_id` |
| 02 | Discover intake workflow | `POST /intake-form-metadata/get_all_intake_forms` | Match `register_id` and `form_mnemonic` (spaces, `_`, `-` ignored) containing `form_name_contains`. Store `intake_form_id` from `form_id` |
| 03 | Render intake form | `POST /intake-form-metadata/render_intake_form` | Body `{form_id}`. Require `tabs.length > 0`. Store the form as `registry_form`. Queue each distinct `tabs[].sections[].section_register_id` as `field_queue`. Walk every `widget-data-source.params.attribute_id` into `catalog_queue` |
| 04 | Field definitions, one call per queued register | `POST /register-metadata/get_register_fields` | Body `{register_id}`. Require `fields` to be an array. Store the payload under `field_schemas[register_id]` |
| 05 | Code lists, one call per queued attribute | `POST /attributes/get_attribute_values` on master data | Body `{attribute_id}`. Store `attribute_values` under `catalogs[attribute_id]` |
| 06 | Administrative levels | `POST /geo/get_all_geo_levels` | Store the payload as `geo_levels` |
| 07 | Geography, four loops | `POST /geo/get_geo_level_values` | Levels in order: `region`, `zone`, `woreda`, `kebele`, matched on `level_mnemonic`. Child calls send `parent_level_value_id` from the previous node's `level_value_id`. `geo_path` defaults to `[]`, so each level takes the first returned node. Stored node keys: `level_value_id`, `level_value_mnemonic`, `display_name`, plus `level_id` set to `level-region`, `level-zone`, `level-woreda`, `level-kebele` |

Login (folder 01) also passed: IAM start, Keycloak form, sign-in (302), IAM callback (307).

## Calls that did not run

| Call | Why |
| --- | --- |
| 08 Discover approved Farmer register | Skipped. Runs only for `registry_key=cropsown` |
| 09 Discover approved Farmer prerequisite | Skipped. Same condition. Would look for a row with `farmer_id` and `fayda_fin` or `foundational_id` |
| 10 Upload synthetic Identity Document | Skipped. Runs only for `registry_key=farmer` |
| 01 Generate and save | Test failed before the HTTP call. No `submission_id` |
| 02 Read draft | Not reached |
| 03 Finalize | Not reached. Would expect `draft_status=FINAL` and `approval_status=PENDING` |
| 04 Read back final state | Not reached |
| 05 Read every intake tab | Not reached. Body would be `{submission_id, tab_id}` to `POST /intake-form-data/get_tab_records` |

## Keys the form and field calls use

Rendered form, request 03:

| Key | Role |
| --- | --- |
| `tabs[].tab_id` | Intake tab. Later read-back sends this as `tab_id` |
| `tabs[].sections[].section_id` | Saved as `section_id` |
| `tabs[].sections[].section_register_id` | Saved as `section_register_id`, and the id passed to `get_register_fields` |
| `tabs[].sections[].section_ui_schema` | Walked for widgets |
| `form_id`, `register_id` | Sent back on every section save |

Widget keys the generator treats as a caller input: `widget-type` is `input`, `widget-readonly` is not set, and the field name is `column-key` or the last segment of `widget-data-path`. Choice widgets use `widget-data-source.options[].value` or `widget-data-source.params.attribute_id`.

Field definition, request 04, per register payload:

| Key | Role |
| --- | --- |
| `register_id` | Map key in `field_schemas` |
| `register_mnemonic` | Used to find Animal, VitalEvent, Breeding, Farmer when those mnemonics exist |
| `fields[].field_name` | Column the generator fills |
| `fields[].data_type` | `boolean`, `number`, `integer`, `date`, `datetime`, `json`, otherwise string |

This collection does not read `fields[].required` or `widget-required`. It fills every field it does not classify as server-managed. A datetime field is omitted because the script says the deployed save route rejects a JSON datetime string.

Catalog row keys, request 05: `value_id`, `value_code`, `value_display`, `parent_value_id`.

The catalog queue always starts with `CROP_SEASON`, `CROP_COMMODITY`, `LIVESTOCK_SPECIES`, `LIVESTOCK_BREED`, `LIVESTOCK_DISEASE`, `VACCINE_TYPE`, then adds every `attribute_id` found on the rendered form.

## Livestock field names the generator knows

These names are hardcoded in the collection's synthetic generator. On this run the generator stopped at the species check, so none of these values were posted. A name is sent only when `get_register_fields` for that section's register includes it. Catalog choices are taken from the live list (`value_id`), not from the fallback strings below, once the `LIVESTOCK_SPECIES_*` check passes.

| Field | Generator use |
| --- | --- |
| `farmer_id` | `FR-` plus 12 digits |
| `farmer_name` | synthetic full name |
| `fayda_fan_id` | 16 digits |
| `surveyor_name`, `surveyor_mobile_number` | survey personnel |
| `supervisor_name`, `supervisor_mobile_number` | survey personnel |
| `da_name`, `da_mobile_number` | officer |
| `region_id`, `zone_id`, `woreda_id`, `kebele_id` | `level_value_id` from the geo walk |
| `region`, `zone`, `woreda`, `kebele` | same ids, alternate widget names |
| `geo_lowest_level_value_id` | kebele `level_value_id` |
| `geo_code_hierarchy_json` | `{hierarchy: [{level_mnemonic, level_value_id, level_value_mnemonic}]}` |
| `latitude`, `longitude`, `altitude` | numbers |
| `ear_tag_id` | `ET` plus 10 digits |
| `secondary_identifier` | synthetic id |
| `species` | `LIVESTOCK_SPECIES` value whose id or code is `LIVESTOCK_SPECIES_CATTLE` |
| `breed` | `LIVESTOCK_BREED` value whose `parent_value_id` is that species |
| `quantity`, `weight` | numbers |
| `date_of_birth` | for the Animal register, a date about 800 days before the run |
| `health_status` | `HEALTHY` if that value is allowed by the widget |
| `vaccination_status` | `NONE` if that value is allowed by the widget |
| `animal_name`, `colour` | synthetic |
| `event_type` | VitalEvent forced to `BIRTH`. Breeding forced to `AI`. Other sections use `TREATMENT` only when that value is in the widget options |
| `disease_type` | first live `LIVESTOCK_DISEASE` `value_id` |
| `date_onset`, `date_resolution` | dates |
| `is_notifiable` | boolean |
| `treatment`, `veterinarian_name`, `location`, `location_details`, `notes` | text. `location` uses `HOME` when that option exists |
| `vaccine_type` | `VACCINE_TYPE` value whose parent is the species |
| `vaccination_date`, `next_due_date`, `batch_number`, `administered_by` | vaccination row |
| `event_date`, `cause`, `offspring_count`, `offspring_gender`, `reporting_officer` | vital event. `cause` uses `UNKNOWN` when that option exists |
| `breeding_date`, `sire_or_semen_id`, `ai_technician_name`, `ai_technique`, `semen_batch_number` | breeding row |
| `expected_calving_date`, `pregnancy_confirmed`, `pregnancy_confirmation_date`, `outcome` | breeding row. `outcome` uses `PENDING` when that option exists |
| `registration_date` | run date |

If a Livestock Farmer section exists, its `status` is set to `ACTIVE` when that field is on the register.

Child rows get `link_internal_record_id` of the form's own register row. Each section row also gets a generated `internal_record_id`.

## Fields the generator refuses to send

Matched by name, then omitted and reported rather than posted:

- `internal_record_id` is generated, not copied from an override
- `link_*` is set only as the parent link above
- `created_*`, `last_approved_*`, `sync_*`
- `search_text`, `record_name`, `record_image_url`, `functional_record_id`
- `actual_score`, `ideal_score`, `completion_score_required`, `record_status`
- `is_duplicated`, `state`, `state_date`, `source_system`, `import_source`
- `total_animals`, `total_land_*`, `size_of_group`, `age`
- `farmer_uuid`, `edit_count`, `lifecycle_stage`
- `offspring_generated`, `offspring_ear_tags`, `offspring_ear_tag_prefix`
- any `data_type` of `datetime`

Server-owned identity names, if a register has them: `relationship_to_the_head`, `authentication_token`, `authentication_expiry_notified`, `authentication_expiry_date`, `last_authentication_id`, `last_authentication_status`, `last_authenticated_on`, `farmer_reference_status`, `identity_verification_id`.

## Save body, once the species check passes

`POST /intake-form-data/save_intake_form_submission`, one section per call, same `submission_id` after the first response:

| Key | Source |
| --- | --- |
| `submission_id` | null on the first section, then the id returned |
| `section_id` | rendered section |
| `section_register_id` | rendered section |
| `section_payload` | one record of the filled fields |
| `form_id` | rendered form |
| `register_id` | rendered form's register |

Read-back checks `section_payloads[].section_register_id` and `section_payloads[].records[]` and expects every generated field except `internal_record_id` and `link_*` to be present.

## What this run does not establish

- The live section ids, mnemonics, and which fields are `widget-required`. Those were in the render and field-definition responses, and those bodies were not kept.
- That `LIVESTOCK_SPECIES_CATTLE` exists. The run shows the opposite condition: no `value_id` with the `LIVESTOCK_SPECIES_` prefix was in the catalog the script stored.
- A successful Livestock save or finalize.
