# Crop Sown — frontend mapping

Portal paths under `DA-registry-frontend`: farmer profile Crop Sown tab (`CropSownSection`, `CropHistorySection`), crop report form (`crop-report`).

## Reads

| UI use | API | Notes |
| --- | --- | --- |
| Form Submissions tab | `GET /api/v1/da-services/farmers/{id}/crops?source=submission` | `crops[]` + `source` |
| Registers tab | `GET /api/v1/da-services/farmers/{id}/crops?source=register` | `crops[]` + `source`; rows use `internal_record_id` |
| One submission (stage tabs) | `GET /api/v1/da-services/farmers/{id}/crops?submission_id=` | `sections` / `tabs` as staff returns them |
| One register tab | `GET /api/v1/da-services/farmers/{id}/crops?internal_record_id=&tab_id=` | one staff `get_tab_records`; `tab_id` required |
| Intake form for a tab | `GET /api/v1/da-services/crop-sown/intake-form?form=` | `form` required (`planning` / `cultivation` / `sowing` / `harvesting`); response is that form only |

`source` is required on list. Register detail needs both `internal_record_id` and `tab_id` — one round trip. GPS lives in staff section `cropsown_cropsown_location_section_03` inside `sections`/`tabs` (screen alias `address` via `cropSownLoad`). There is no nested top-level `location` object on these responses.

## FE today

Detail load maps the location section into `address.latitude` / `address.longitude` via `cropSownLoad`. Keep that path; do not expect a synthetic `location` field on list/detail.
