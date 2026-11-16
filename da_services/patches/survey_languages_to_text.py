"""Copy survey template languages from the old `languages_json` column into `language`.

`DA Survey Template.languages_json` held a JSON list (`["English", "Amharic"]`). The
template now keeps plain text in `language` (`English, Amharic`). Templates whose
`language` is still empty are filled from the old column, which is left untouched.
"""

import json

import frappe

TEMPLATE = "DA Survey Template"


def execute():
	if not frappe.db.has_column(TEMPLATE, "languages_json"):
		return
	rows = frappe.db.sql(
		f"select name, languages_json from `tab{TEMPLATE}` where ifnull(language, '') = ''",
		as_dict=True,
	)
	for r in rows:
		try:
			languages = json.loads(r.languages_json or "[]")
		except ValueError:
			languages = []
		if not isinstance(languages, list):
			languages = []
		text = ", ".join(str(item).strip() for item in languages if str(item).strip()) or "English"
		frappe.db.set_value(TEMPLATE, r.name, "language", text, update_modified=False)
	print(f"survey_languages_to_text: filled language on {len(rows)} template(s)")
