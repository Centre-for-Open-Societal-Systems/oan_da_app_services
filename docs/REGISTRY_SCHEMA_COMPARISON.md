# Registry Schema vs DA-40 Implementation Comparison

## 📊 **1. FARMER REGISTRY - Actual Database Schema**

### **Table:** `g2p_register_farmers` (OpenG2P + Farmer Extension)

#### **Identity & Personal Info**
| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `internal_record_id` | String (UUID) | No | ✅ `farmer_id` | ✅ MAPPED |
| `reg_id` | String | Yes | ✅ `reg_id` | ✅ MAPPED |
| `given_name` | String | Yes | ✅ `name` (composed) | ✅ MAPPED |
| `family_name` | String | Yes | ✅ `name` (composed) | ✅ MAPPED |
| `father_first_name` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `record_name` | String (computed) | Yes | ✅ `name` | ✅ MAPPED |
| `first_name_amh` | String (Amharic) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `first_name_om` | String (Oromo) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `record_name_local` | String (hybrid) | Computed | ❌ Missing | ⚠️ NOT RETURNED |
| `gender` | String | Yes | ✅ `gender` | ✅ MAPPED |
| `birth_date` | Date | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `birth_date_ec` | String (Ethiopian) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `estimated_age` | Integer | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `national_id_masked` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |

#### **Contact Information**
| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `phone` | String | Yes | ✅ `phone` | ✅ MAPPED |
| `email` | String | Yes | ✅ `email` | ✅ MAPPED |
| `has_personal_phone` | Boolean | Yes | ❌ Missing | ⚠️ NOT RETURNED |

#### **Geographic Location**
| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `region_name` | String | Yes | ✅ `region` | ✅ MAPPED |
| `zone_name` | String | Yes | ✅ `zone` | ✅ MAPPED |
| `woreda_name` | String | Yes | ✅ `woreda` | ✅ MAPPED |
| `kebele_name` | String | Yes | ✅ `kebele` | ✅ MAPPED |
| `woreda_level_value_id` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |

#### **Household & Demographics**
| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `household_size` | Integer | Yes | ✅ `household_size` | ✅ MAPPED |
| `is_household_head` | Boolean | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `education_level` | Enum | Yes | ✅ `education_level` | ✅ MAPPED |
| `language_spoken` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `local_language` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |

#### **Land & Agriculture**
| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `total_land_area` | Numeric(16,6) | Yes | ✅ `total_land_area` | ✅ MAPPED |
| `total_land_owned_area` | Numeric(16,6) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `total_land_rent_area` | Numeric(16,6) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `total_land_crop_sharing_area` | Numeric(16,6) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `land_ownership` | Enum | Yes | ✅ `land_ownership` | ✅ MAPPED |

#### **Social & Economic**
| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `source_of_income` | Enum | Yes | ✅ `source_of_income` | ✅ MAPPED |
| `source_of_income_other` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `is_psnp_user` | Boolean (Safety Net) | Yes | ✅ `is_psnp_beneficiary` | ✅ MAPPED |
| `disabled` | Boolean | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `disability_type` | Enum | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `disability_severity` | Enum | Yes | ❌ Missing | ⚠️ NOT RETURNED |

#### **Workflow & Metadata**
| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `state` | Enum (DRAFT/APPROVED/etc) | Yes | ✅ `registration_state` | ✅ MAPPED |
| `import_source` | Enum | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `is_duplicated` | Boolean | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `registration_date` | Date | Yes | ✅ `registered_at` | ✅ MAPPED |

#### **Enumerator Data**
| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `enumerator_name` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `enumerator_user_id` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `data_collection_date` | Date | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `enumerator_latitude` | Numeric(10,7) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `enumerator_longitude` | Numeric(10,7) | Yes | ❌ Missing | ⚠️ NOT RETURNED |

---

## 📊 **2. LAND REGISTRY - Actual Database Schema**

### **Table:** `g2p_register_lands`

| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `land_ownership_type` | Enum | Yes | ✅ `land_ownership_type` | ✅ MAPPED |
| `area_in_hectare` | Numeric(16,6) | Yes | ✅ `area_in_hectare` | ✅ MAPPED |
| `land_kebele` | String | Yes | ✅ `land_kebele` | ✅ MAPPED |
| `certificate_provided` | Boolean | Yes | ✅ `certificate_provided` | ✅ MAPPED |
| `certificate_storage_id` | Text | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `land_size` | Numeric(16,6) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `unit` | Enum (ha/sqm) | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `soil_fertility` | String | Yes | ✅ `soil_fertility` | ✅ MAPPED |
| `current_land_use` | Enum | Yes | ✅ `current_land_use` | ✅ MAPPED |
| `farming_type` | Enum | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `year_of_acquisition` | Integer | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `means_of_acquisition` | String | Yes | ❌ Missing | ⚠️ NOT RETURNED |
| `land_id` | String | Yes | ✅ `plot_id` | ✅ MAPPED |
| `remark` | Text | Yes | ❌ Missing | ⚠️ NOT RETURNED |

---

## 📊 **3. CROP REGISTRY - Actual Database Schema**

### **Table:** `g2p_register_crops`

| Field | Type | Nullable | In DA-40? | Status |
|-------|------|----------|-----------|--------|
| `commodity` | String | Yes | ❌ Missing | ⚠️ NOT USED |
| `planted_date` | Date | Yes | ❌ Missing | ⚠️ NOT USED |
| `planted_date_ec` | String (Ethiopian) | Yes | ❌ Missing | ⚠️ NOT USED |
| `season` | String | Yes | ❌ Missing | ⚠️ NOT USED |
| `end_use` | Enum | Yes | ❌ Missing | ⚠️ NOT USED |

**Note:** Crop data is NOT used in DA-40. `primary_crop` field returns `null`.

---

## 📊 **4. DA-40 JIRA REQUIREMENT vs IMPLEMENTATION**

### **DA-40 Required Fields (from JIRA):**

| JIRA Field | Registry Source | Our Implementation | Match? |
|------------|-----------------|-------------------|--------|
| **Identity** ||||
| Name | `record_name` | ✅ `farmer_name` | ✅ |
| Farmer-ID | `internal_record_id` | ✅ `farmer_id` | ✅ |
| Kebele | `kebele_name` | ✅ `kebele` | ✅ |
| **Verification** ||||
| Fayda verification state | Not in Farmer Registry | ⚠️ `null` | ⚠️ PARTIAL |
| **Work Status** ||||
| Pending-action count | From DA Tasks | ✅ `pending_actions` | ✅ |
| **Contact** ||||
| Phone | `phone` | ✅ `phone` | ✅ |
| Email | `email` | ✅ `email` | ✅ |
| **Household** ||||
| Household size | `household_size` | ✅ `household_size` | ✅ |
| **Land** ||||
| Registered plots | `g2p_register_lands` | ✅ GET /plots | ✅ |
| Total area | `total_land_area` | ✅ `total_land_area` | ✅ |
| Land ownership | `land_ownership` | ✅ `land_ownership` | ✅ |
| **Agriculture** ||||
| Primary crop | `g2p_register_crops` | ⚠️ `null` | ⚠️ PARTIAL |
| **Registration** ||||
| Registration date | `registration_date` | ✅ `registered_at` | ✅ |
| **Field Activity** ||||
| Last visit | From DA Visits | ✅ `last_visit_date` | ✅ |
| Last visit outcome | From DA Visits | ✅ `last_visit_outcome` | ✅ |
| **Additional** ||||
| Region | `region_name` | ✅ `region` | ✅ |
| Woreda | `woreda_name` | ✅ `woreda` | ✅ |
| Gender | `gender` | ✅ `gender` | ✅ |
| Education level | `education_level` | ✅ `education_level` | ✅ |
| Source of income | `source_of_income` | ✅ `source_of_income` | ✅ |

---

## 🚨 **CRITICAL FINDINGS**

### **1. Region/Woreda Field Name Mismatch** ❌ FIXED

**Problem (BEFORE FIX):**
- Registry uses: `region_name`, `woreda_name`, `kebele_name` (human-readable)
- Our mock used: `"Oromia"`, `"Bako Tibe"` (names, not codes)
- DA-services expects: `"ET04"`, `"ET04-W01"` (administrative codes)

**Solution:**
- ✅ Fixed in mock.py: Changed to use administrative codes
- ⚠️ **NEED TO VERIFY:** Does real Farmer Registry API return codes or names?

### **2. Missing Registry Fields in DA-40**

**High Priority (should add):**
- `father_first_name` - Used for identification
- `is_household_head` - Important demographic
- `disabled` / `disability_type` - Accessibility needs
- `birth_date` / `estimated_age` - Age verification
- `total_land_owned_area` - Land breakdown details

**Medium Priority:**
- `has_personal_phone` - Contact preference
- `enumerator_name` - Data provenance
- `is_duplicated` - Data quality flag

**Low Priority (optional):**
- Local language names (Amharic/Oromo)
- Enumerator GPS coordinates
- Land certificate storage ID

### **3. Partial/Null Fields**

| Field | Status | Reason |
|-------|--------|--------|
| `verification_state` (Fayda) | ⚠️ null | Not in Farmer Registry, separate service |
| `primary_crop` | ⚠️ null | Crops table exists but not queried |

---

## ✅ **WHAT'S CORRECT**

1. ✅ All core identity fields mapped correctly
2. ✅ Land plots endpoint returns correct registry data
3. ✅ Visit history comes from DA Services (correct)
4. ✅ Pending actions from DA Tasks (correct)
5. ✅ RBAC scope filtering works (DA/Supervisor/Executive)

---

## 🎯 **RECOMMENDATIONS**

### **Option 1: Ship DA-40 As-Is** ⭐ RECOMMENDED
- Core fields: ✅ Complete
- JIRA requirements: ✅ 90% met
- Missing fields: Not blocking

### **Option 2: Add Missing High-Priority Fields** (30 min)
Add to response:
- `father_first_name`
- `is_household_head`
- `birth_date` / `estimated_age`
- `disabled` / `disability_type`

### **Option 3: Add Primary Crop** (1 hour)
Query `g2p_register_crops` and populate `primary_crop` field.

---

## 🔧 **ACTION ITEMS**

1. ✅ **DONE:** Fix region/woreda codes in mock data
2. ⏳ **TODO:** Verify real Farmer Registry returns codes vs names
3. ⏳ **TODO:** Decide if missing fields are needed
4. ⏳ **TODO:** Implement primary crop query (if needed)

---

Generated: 2026-10-14
