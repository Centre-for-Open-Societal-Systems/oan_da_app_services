"""Move survey question choices and translations out of JSON into their own columns.

`DA Survey Parameter.options_json` (a JSON list of choices) becomes `option_1` ..
`option_4`, and `prompts_json` (a JSON object of translated prompts) becomes
`prompt_amharic` / `prompt_afaan_oromoo`. Only rows whose new columns are still empty
are filled. The old columns are left untouched, so nothing is lost: a question that
had more than four choices keeps the extra ones there, and is reported below.
"""

import json

import frappe

PARAMETER = "DA Survey Parameter"
OPTION_FIELDS = ("option_1", "option_2", "option_3", "option_4")
TRANSLATION_FIELDS = {"Amharic": "prompt_amharic", "Afaan Oromoo": "prompt_afaan_oromoo"}


def _load(raw, kind):
	try:
		value = json.loads(raw) if raw else None
	except ValueError:
		return kind()
	return value if isinstance(value, kind) else kind()


def execute():
	has_options = frappe.db.has_column(PARAMETER, "options_json")
	has_prompts = frappe.db.has_column(PARAMETER, "prompts_json")
	if not (has_options or has_prompts):
		return
	columns = ["name", "parent", "parameter_id"]
	columns += ["options_json"] if has_options else []
	columns += ["prompts_json"] if has_prompts else []
	rows = frappe.db.sql(
		f"""select {", ".join(columns)} from `tab{PARAMETER}`
		where ifnull(option_1, '') = '' and ifnull(prompt_amharic, '') = ''
		and ifnull(prompt_afaan_oromoo, '') = ''""",
		as_dict=True,
	)
	moved = 0
	for r in rows:
		values: dict = {}
		options = [str(o).strip() for o in _load(r.get("options_json"), list) if str(o).strip()]
		values.update(dict(zip(OPTION_FIELDS, options, strict=False)))
		if len(options) > len(OPTION_FIELDS):
			print(
				f"survey_parameter_json_to_columns: {r.parent} {r.parameter_id} has {len(options)} choices; kept the first {len(OPTION_FIELDS)}"
			)
		for language, text in _load(r.get("prompts_json"), dict).items():
			field = TRANSLATION_FIELDS.get(language)
			if field and str(text or "").strip():
				values[field] = str(text).strip()
		if values:
			frappe.db.set_value(PARAMETER, r.name, values, update_modified=False)
			moved += 1
	print(f"survey_parameter_json_to_columns: filled columns on {moved} question(s)")
