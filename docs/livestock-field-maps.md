# Livestock field and option map

Source: `GET /api/v1/da-services/livestock/intake-form` on 2026-11-02, the farmer livestock run against the development hosts. `value_key` is recorded only where the staff render of this form was read the same day: species, breed, sex, health status, vaccination status, and vaccine type. If a later response disagrees with this snapshot, use the response.

Form `Livestock Intake` `e92e8be1-207f-518e-99c2-8bc21cc1f112`. Register `997676d3-7008-59f9-b23e-613ad79bbb08`.

A control is a field the intake form returned. Draw those. Do not send a read-only field.
Store the column named by `value_key` on a catalog field. Show `value_display`.

## Screen tabs

| Tab key | Label | Sections |
| --- | --- | --- |
| `0ebdc221-187d-5df6-9dc3-c6f4c4ee160e` | livestock_intake_tab | `livestock_farmer_lookup` |
| `farmer` | Farmer | `livestock_survey_personnel_section_02`, `livestock_livestock_location_section_03` |
| `livestock` | Livestock | `livestock_animal_details_section_01` |
| `health` | Health & Vaccination | `livestock_health_event_details_section_01`, `livestock_vaccination_details_section_01` |
| `breeding` | Breeding & Vital Events | `livestock_vital_event_details_section_01`, `livestock_breeding_details_section_01` |

## `livestock_farmer_lookup`

Farmer lookup. Mnemonic `livestock_farmer_lookup`. Screen alias none. one row. Register `997676d3-7008-59f9-b23e-613ad79bbb08`.
Required before submit.

| Field | Label | Type | Required | Read-only | Control |
| --- | --- | --- | --- | --- | --- |
| `farmer_id` | Farmer ID | string | yes | no | Text. Pattern `^\s*FR-[0-9]{12}\s*$`. |
| `fayda_id` | Fayda ID (FAN or FIN) |  | yes | no | Text. Pattern `^\s*(?:[0-9]{12}|[0-9](?:\s*[0-9]){15})\s*$`. |

## `livestock_survey_personnel_section_02`

Survey personnel. Mnemonic `ls_survey_personnel`. Screen alias `survey-personnel`. one row. Register `997676d3-7008-59f9-b23e-613ad79bbb08`.

| Field | Label | Type | Required | Read-only | Control |
| --- | --- | --- | --- | --- | --- |
| `surveyor_name` | DA name | string | yes | no | Text. A person name, 1–100 characters. |
| `surveyor_mobile_number` | DA mobile number | string | no | no | text |
| `supervisor_name` | Supervisor name | string | no | no | Text. A person name, 1–100 characters. |
| `supervisor_mobile_number` | Supervisor mobile number | string | no | no | text |

## `livestock_livestock_location_section_03`

Location. Mnemonic `ls_livestock_location`. Screen alias `address`, `location`. one row. Register `997676d3-7008-59f9-b23e-613ad79bbb08`.

| Field | Label | Type | Required | Read-only | Control |
| --- | --- | --- | --- | --- | --- |
| `geo_lowest_level_value_id` | Administrative hierarchy | string | yes | no | Geography `geo-level-values`. Writes `hierarchy` → `geo_code_hierarchy_json`, `value` → `geo_lowest_level_value_id`. |
| `latitude` | Holding location | number | no | no | Geography `geo-level-values`. Writes `hierarchy` → `geo_code_hierarchy_json`, `latitude` → `latitude`, `location` → `geo_lowest_level_value_id`, `longitude` → `longitude`. |

## `livestock_animal_details_section_01`

Livestock details. Mnemonic `ls_animal_details`. Screen alias `herd`, `livestock-details`. table. Register `041a9f79-2142-548a-a15b-a4c76fc9f6f7`.

| Field | Label | Type | Required | Read-only | Control |
| --- | --- | --- | --- | --- | --- |
| `species` | Species | string | yes | no | Catalog [LIVESTOCK_SPECIES](#livestock_species). Store `value_id`. |
| `breed` | Breed | string | yes | no | Catalog [LIVESTOCK_BREED](#livestock_breed). Store `value_id`. |
| `ear_tag_id` | Ear tag | string | no | no | Text. Pattern `^ET\d{10}$`. |
| `secondary_identifier` | Secondary identifier | string | no | no | text |
| `quantity` | Number of animals (head count) | integer | no | no | number |
| `weight` | Weight (kg) | number | no | no | number |
| `date_of_birth` | Date of birth | date | no | no | date |
| `age` | Age | string | no | yes | Do not send age from `date_of_birth`. |
| `gender` | Sex | string | yes | no | Catalog [LIVESTOCK_SEX](#livestock_sex). Store `value_code`. |
| `health_status` | Health status | string | yes | no | Catalog [LIVESTOCK_HEALTH_STATUS](#livestock_health_status). Store `value_code`. |
| `vaccination_status` | Vaccination status | string | yes | no | Catalog [LIVESTOCK_VACCINATION_STATUS](#livestock_vaccination_status). Store `value_code`. |
| `registration_date` | Registration date | date | yes | no | date |

## `livestock_health_event_details_section_01`

Health event details. Mnemonic `ls_health_event_details`. Screen alias `disease`, `health-event`. table. Register `a40e4a02-1b82-5b31-89df-71624bd96545`.

| Field | Label | Type | Required | Read-only | Control |
| --- | --- | --- | --- | --- | --- |
| `ear_tag_id` | Ear tag / secondary identifier | string | no | no | oan-animal-picker has show/require rules, autofilled. |
| `species` | Species | string | no | yes | Do not send |
| `age` | Age | string | no | yes | Do not send |
| `event_type` | Event type | string | no | no | Catalog [LIVESTOCK_HEALTH_EVENT_TYPE](#livestock_health_event_type). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `disease_type` | Disease | string | no | no | Catalog [LIVESTOCK_DISEASE](#livestock_disease). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `date_onset` | Date of onset | date | no | no | date has show/require rules. |
| `date_resolution` | Date of resolution | date | no | no | date has show/require rules. |
| `is_notifiable` | Notifiable disease | boolean | no | no | Static. Yes = `True`, No = `False` has show/require rules. |
| `treatment` | Treatment administered | string | no | no | text has show/require rules. |
| `veterinarian_name` | Veterinarian / officer | string | no | no | Text. A person name, 1–100 characters. has show/require rules. |
| `location` | Location | string | no | no | Catalog [LIVESTOCK_EVENT_LOCATION](#livestock_event_location). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `location_details` | Location details | string | no | no | text has show/require rules. |
| `notes` | Notes | string | no | no | text has show/require rules. |

## `livestock_vaccination_details_section_01`

Vaccination details. Mnemonic `ls_vaccination_details`. Screen alias `vaccination`. table. Register `51c1f6d6-856a-5e2f-84e9-ff5abdc4fb75`.

| Field | Label | Type | Required | Read-only | Control |
| --- | --- | --- | --- | --- | --- |
| `ear_tag_id` | Ear tag / secondary identifier | string | no | no | oan-animal-picker has show/require rules, autofilled. |
| `species` | Species | string | no | yes | Do not send |
| `age` | Age | string | no | yes | Do not send |
| `vaccine_type` | Vaccine | string | no | no | Catalog [VACCINE_TYPE](#vaccine_type). Store `value_id`. has show/require rules. |
| `vaccination_date` | Vaccination date | date | no | no | date has show/require rules. |
| `next_due_date` | Next due date | date | no | no | oan-next-due-date |
| `batch_number` | Batch / lot number | string | no | no | text |
| `administered_by` | Administered by | string | no | no | text |
| `notes` | Notes | string | no | no | text |

## `livestock_vital_event_details_section_01`

Vital event details. Mnemonic `ls_vital_event_details`. Screen alias `vital-event`. table. Register `76811bf6-07df-5ed4-9466-13b782f627fe`.

| Field | Label | Type | Required | Read-only | Control |
| --- | --- | --- | --- | --- | --- |
| `ear_tag_id` | Ear tag / secondary identifier | string | no | no | oan-animal-picker has show/require rules, autofilled. |
| `species` | Species | string | no | yes | Do not send |
| `age` | Age | string | no | yes | Do not send |
| `event_type` | Event type | string | no | no | Catalog [LIVESTOCK_VITAL_EVENT_TYPE](#livestock_vital_event_type). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `event_date` | Event date | date | no | no | date has show/require rules. |
| `cause` | Cause | string | no | no | Catalog [LIVESTOCK_CAUSE_OF_DEATH](#livestock_cause_of_death). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `location` | Location | string | no | no | Catalog [LIVESTOCK_EVENT_LOCATION](#livestock_event_location). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `location_details` | Location details | string | no | no | text has show/require rules. |
| `disease_type` | Disease / diagnosis | string | no | no | Catalog [LIVESTOCK_DISEASE](#livestock_disease). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `date_onset` | Date of onset | date | no | no | date has show/require rules. |
| `date_resolution` | Date of resolution | date | no | no | date has show/require rules. |
| `treatment` | Treatment administered | string | no | no | text has show/require rules. |
| `veterinarian_name` | Attending veterinarian | string | no | no | Text. A person name, 1–100 characters. has show/require rules. |
| `is_notifiable` | Notifiable disease | boolean | no | no | Static. Yes = `True`, No = `False` has show/require rules. |
| `offspring_count` | Number of offspring | integer | no | no | number has show/require rules. |
| `offspring_gender` | Sex of offspring | string | no | no | Catalog [LIVESTOCK_SEX](#livestock_sex). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `offspring_breed` | Breed of Offspring | string | no | no | Catalog [LIVESTOCK_BREED](#livestock_breed). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `offspring_weight` | Birth Weight (kg) | number | no | no | number has show/require rules. |
| `reporting_officer` | Reporting officer | string | no | no | text has show/require rules. |
| `notes` | Notes | string | no | no | text has show/require rules. |

## `livestock_breeding_details_section_01`

Breeding details. Mnemonic `ls_breeding_details`. Screen alias `breeding`, `breeding-details`. table. Register `192c08b0-5403-5167-968a-2676d4af2879`.

| Field | Label | Type | Required | Read-only | Control |
| --- | --- | --- | --- | --- | --- |
| `ear_tag_id` | Ear tag / secondary identifier | string | no | no | oan-animal-picker has show/require rules, autofilled. |
| `species` | Species | string | no | yes | Do not send |
| `age` | Age | string | no | yes | Do not send |
| `event_type` | Breeding type | string | no | no | Catalog [LIVESTOCK_BREEDING_METHOD](#livestock_breeding_method). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `breeding_date` | Breeding date | date | no | no | date has show/require rules. |
| `sire_or_semen_id` | Sire / semen source | string | no | no | text has show/require rules. |
| `location` | Location | string | no | no | Catalog [LIVESTOCK_EVENT_LOCATION](#livestock_event_location). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `location_details` | Location details | string | no | no | text has show/require rules. |
| `ai_technician_name` | AI technician | string | no | no | Text. A person name, 1–100 characters. has show/require rules. |
| `ai_technique` | Technique used | string | no | no | text has show/require rules. |
| `semen_batch_number` | Semen batch number | string | no | no | text has show/require rules. |
| `expected_calving_date` | Expected calving date | date | no | no | date has show/require rules. |
| `pregnancy_confirmed` | Pregnancy confirmed | boolean | no | no | Static. Yes = `True`, No = `False` has show/require rules. |
| `pregnancy_confirmation_date` | Pregnancy confirmation date | date | no | no | date has show/require rules. |
| `outcome` | Outcome | string | no | no | Catalog [LIVESTOCK_BREEDING_OUTCOME](#livestock_breeding_outcome). Store the `value_key` on this field. It was not in this snapshot. has show/require rules. |
| `notes` | Notes | string | no | no | text has show/require rules. |

## Catalogs

Same rows for every section that names the attribute. Store the column in the section table, not the label. `value_code` for sex, health status, and vaccination status was read from master data the same day. The intake-form body returned `value_id` and `value_display` only.

### `LIVESTOCK_SPECIES`

Store `value_id`. 7 rows.

| value_id | value_display |
| --- | --- |
| `CATTLE` | Cattle |
| `GOATS` | Goats |
| `SHEEP` | Sheep |
| `POULTRY` | Poultry |
| `CAMELS` | Camels |
| `EQUINES` | Equines |
| `OTHER` | Other |

### `LIVESTOCK_BREED`

Store `value_id`. 3 rows.

| value_id | value_display |
| --- | --- |
| `BREED_IMPROVED` | Improved |
| `BREED_LOCAL` | Local |
| `BREED_HYBRID` | Hybrid |

### `LIVESTOCK_SEX`

Store `value_code`. 5 rows.

| value_id | value_code | value_display |
| --- | --- | --- |
| `SEX_MALE` | `SEX_MALE` | Male |
| `SEX_FEMALE` | `SEX_FEMALE` | Female |
| `SEX_MALE_NEUTER` | `SEX_MALE_NEUTER` | Castrated male |
| `SEX_FEMALE_NEUTER` | `SEX_FEMALE_NEUTER` | Female neuter |
| `SEX_MIXED` | `SEX_MIXED` | Mixed / Not Applicable |

### `LIVESTOCK_HEALTH_STATUS`

Store `value_code`. 4 rows.

| value_id | value_code | value_display |
| --- | --- | --- |
| `LIVESTOCK_HEALTH_STATUS_HEALTHY` | `HEALTHY` | Healthy |
| `LIVESTOCK_HEALTH_STATUS_SICK` | `SICK` | Sick |
| `LIVESTOCK_HEALTH_STATUS_QUARANTINED` | `QUARANTINED` | Quarantined |
| `LIVESTOCK_HEALTH_STATUS_DECEASED` | `DECEASED` | Deceased |

### `LIVESTOCK_VACCINATION_STATUS`

Store `value_code`. 3 rows.

| value_id | value_code | value_display |
| --- | --- | --- |
| `LIVESTOCK_VACCINATION_STATUS_UP_TO_DATE` | `UP_TO_DATE` | Up to date |
| `LIVESTOCK_VACCINATION_STATUS_OVERDUE` | `OVERDUE` | Overdue |
| `LIVESTOCK_VACCINATION_STATUS_NONE` | `NONE` | None |

### `LIVESTOCK_HEALTH_EVENT_TYPE`

Stored column was not read for this attribute on this snapshot. 4 rows.

| value_id | value_display |
| --- | --- |
| `LIVESTOCK_HEALTH_EVENT_TYPE_DISEASE` | Disease |
| `LIVESTOCK_HEALTH_EVENT_TYPE_INJURY` | Injury |
| `LIVESTOCK_HEALTH_EVENT_TYPE_TREATMENT` | Treatment |
| `LIVESTOCK_HEALTH_EVENT_TYPE_RECOVERY` | Recovery |

### `LIVESTOCK_DISEASE`

Stored column was not read for this attribute on this snapshot. 29 rows.

| value_id | value_display |
| --- | --- |
| `LIVESTOCK_DISEASE_ANTHRAX` | Anthrax |
| `LIVESTOCK_DISEASE_BLACKLEG` | Blackleg |
| `LIVESTOCK_DISEASE_CBPP` | Contagious bovine pleuropneumonia |
| `LIVESTOCK_DISEASE_CCPP` | Contagious caprine pleuropneumonia |
| `LIVESTOCK_DISEASE_FMD` | Foot and mouth disease |
| `LIVESTOCK_DISEASE_LSD` | Lumpy skin disease |
| `LIVESTOCK_DISEASE_PPR` | Peste des petits ruminants |
| `LIVESTOCK_DISEASE_SHEEP_GOAT_POX` | Sheep and goat pox |
| `LIVESTOCK_DISEASE_NEWCASTLE_DISEASE` | Newcastle disease |
| `LIVESTOCK_DISEASE_GUMBORO_DISEASE` | Gumboro |
| `LIVESTOCK_DISEASE_FOWL_TYPHOID_DISEASE` | Fowl typhoid |
| `LIVESTOCK_DISEASE_FOWL_POX_DISEASE` | Fowl pox |
| `LIVESTOCK_DISEASE_RABIES` | Rabies |
| `LIVESTOCK_DISEASE_BRUCELLOSIS` | Brucellosis |
| `LIVESTOCK_DISEASE_TRYPANOSOMIASIS` | Trypanosomiasis |
| `LIVESTOCK_DISEASE_MASTITIS` | Mastitis |
| `LIVESTOCK_DISEASE_INTERNAL_PARASITES` | Internal parasites |
| `LIVESTOCK_DISEASE_EXTERNAL_PARASITES` | External parasites |
| `LIVESTOCK_DISEASE_PASTEURELLOSIS` | Pasteurellosis |
| `LIVESTOCK_DISEASE_BOVINE_TB` | Bovine tuberculosis |
| `LIVESTOCK_DISEASE_CAMEL_POX` | Camel pox |
| `LIVESTOCK_DISEASE_AFRICAN_HORSE_SICKNESS` | African horse sickness |
| `LIVESTOCK_DISEASE_ENTEROTOXAEMIA_DISEASE` | Enterotoxaemia |
| `LIVESTOCK_DISEASE_COCCIDIOSIS` | Coccidiosis |
| `LIVESTOCK_DISEASE_FOOT_ROT` | Foot rot |
| `LIVESTOCK_DISEASE_BLOAT` | Bloat |
| `LIVESTOCK_DISEASE_RETAINED_PLACENTA` | Retained placenta |
| `LIVESTOCK_DISEASE_DIARRHOEA` | Diarrhoea |
| `LIVESTOCK_DISEASE_OTHER_DISEASE` | Other |

### `LIVESTOCK_EVENT_LOCATION`

Stored column was not read for this attribute on this snapshot. 6 rows.

| value_id | value_display |
| --- | --- |
| `LIVESTOCK_EVENT_LOCATION_HOME` | Home |
| `LIVESTOCK_EVENT_LOCATION_VETERINARY` | Veterinary clinic |
| `LIVESTOCK_EVENT_LOCATION_MARKET` | Market |
| `LIVESTOCK_EVENT_LOCATION_FIELD` | Field |
| `LIVESTOCK_EVENT_LOCATION_QUARANTINE_CENTER` | Quarantine center |
| `LIVESTOCK_EVENT_LOCATION_OTHER` | Other |

### `VACCINE_TYPE`

Store `value_id`. 30 rows.

| value_id | value_display |
| --- | --- |
| `VACCINE_TYPE_ANTHRAX_CATTLE` | Anthrax (cattle) |
| `VACCINE_TYPE_BLACKLEG` | Blackleg |
| `VACCINE_TYPE_CBPP` | Contagious bovine pleuropneumonia |
| `VACCINE_TYPE_FMD_CATTLE` | Foot and mouth disease |
| `VACCINE_TYPE_LSD` | Lumpy skin disease |
| `VACCINE_TYPE_PASTEURELLOSIS_CATTLE` | Bovine pasteurellosis |
| `VACCINE_TYPE_BRUCELLOSIS_S19` | Brucellosis (S19) |
| `VACCINE_TYPE_RABIES_CATTLE` | Rabies (cattle) |
| `VACCINE_TYPE_PPR_SHEEP` | Peste des petits ruminants (sheep) |
| `VACCINE_TYPE_SGP_SHEEP` | Sheep and goat pox (sheep) |
| `VACCINE_TYPE_ANTHRAX_SHEEP` | Anthrax (sheep) |
| `VACCINE_TYPE_PASTEURELLOSIS_SHEEP` | Ovine pasteurellosis |
| `VACCINE_TYPE_ENTEROTOXAEMIA` | Enterotoxaemia |
| `VACCINE_TYPE_PPR_GOAT` | Peste des petits ruminants (goats) |
| `VACCINE_TYPE_SGP_GOAT` | Sheep and goat pox (goats) |
| `VACCINE_TYPE_CCPP` | Contagious caprine pleuropneumonia |
| `VACCINE_TYPE_ANTHRAX_GOAT` | Anthrax (goats) |
| `VACCINE_TYPE_PASTEURELLOSIS_GOAT` | Caprine pasteurellosis |
| `VACCINE_TYPE_ANTHRAX_CAMEL` | Anthrax (camels) |
| `VACCINE_TYPE_CAMEL_POX_VACCINE` | Camel pox |
| `VACCINE_TYPE_PASTEURELLOSIS_CAMEL` | Camel pasteurellosis |
| `VACCINE_TYPE_NEWCASTLE` | Newcastle disease |
| `VACCINE_TYPE_GUMBORO` | Gumboro (infectious bursal disease) |
| `VACCINE_TYPE_FOWL_TYPHOID` | Fowl typhoid |
| `VACCINE_TYPE_FOWL_POX` | Fowl pox |
| `VACCINE_TYPE_MAREK` | Marek disease |
| `VACCINE_TYPE_AHS` | African horse sickness (horse) |
| `VACCINE_TYPE_TETANUS_HORSE` | Tetanus |
| `VACCINE_TYPE_AHS_DONKEY` | African horse sickness (donkey) |
| `VACCINE_TYPE_RABIES_DONKEY` | Rabies (donkeys) |

### `LIVESTOCK_VITAL_EVENT_TYPE`

Stored column was not read for this attribute on this snapshot. 3 rows.

| value_id | value_display |
| --- | --- |
| `LIVESTOCK_VITAL_EVENT_TYPE_BIRTH` | Birth |
| `LIVESTOCK_VITAL_EVENT_TYPE_MORTALITY` | Mortality |
| `LIVESTOCK_VITAL_EVENT_TYPE_DISEASE` | Disease |

### `LIVESTOCK_CAUSE_OF_DEATH`

Stored column was not read for this attribute on this snapshot. 5 rows.

| value_id | value_display |
| --- | --- |
| `LIVESTOCK_CAUSE_OF_DEATH_DISEASE` | Disease |
| `LIVESTOCK_CAUSE_OF_DEATH_ACCIDENT` | Accident |
| `LIVESTOCK_CAUSE_OF_DEATH_PREDATION` | Predation |
| `LIVESTOCK_CAUSE_OF_DEATH_SLAUGHTER` | Slaughter |
| `LIVESTOCK_CAUSE_OF_DEATH_UNKNOWN` | Unknown |

### `LIVESTOCK_BREEDING_METHOD`

Stored column was not read for this attribute on this snapshot. 2 rows.

| value_id | value_display |
| --- | --- |
| `LIVESTOCK_BREEDING_METHOD_NATURAL` | Natural breeding |
| `LIVESTOCK_BREEDING_METHOD_AI` | Artificial insemination |

### `LIVESTOCK_BREEDING_OUTCOME`

Stored column was not read for this attribute on this snapshot. 3 rows.

| value_id | value_display |
| --- | --- |
| `LIVESTOCK_BREEDING_OUTCOME_PENDING` | Pending |
| `LIVESTOCK_BREEDING_OUTCOME_SUCCESSFUL` | Successful |
| `LIVESTOCK_BREEDING_OUTCOME_FAILED` | Failed |
