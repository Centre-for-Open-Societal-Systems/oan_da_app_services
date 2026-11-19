"""Move survey answers from the old `answers_json` column into `DA Survey Answer` rows.

`DA Survey Response.answers_json` held every answer of a response as one JSON object.
Answers now live one value per row in the `answers` child table. This copies the old
values across for responses that have no answer rows yet; the old column is left in the
database untouched, so the patch can be re-run and nothing is lost.
"""

import json

import frappe

RESPONSE = "DA Survey Response"
ANSWER = "DA Survey Answer"


def execute():
	if not frappe.db.has_column(RESPONSE, "answers_json"):
		return
	from da_services.services.surveys import _answer_rows

	rows = frappe.db.sql(
		f"""select r.name, r.survey, r.answers_json from `tab{RESPONSE}` r
		where ifnull(r.answers_json, '') not in ('', '{{}}', 'null')
		and not exists (select 1 from `tab{ANSWER}` a where a.parent = r.name)""",
		as_dict=True,
	)
	parameters: dict[str, list] = {}
	moved = 0
	for r in rows:
		try:
			answers = json.loads(r.answers_json)
		except ValueError:
			continue
		if not isinstance(answers, dict) or not answers:
			continue
		if r.survey not in parameters:
			parameters[r.survey] = frappe.get_all(
				"DA Survey Parameter",
				filters={"parent": r.survey, "parenttype": "DA Survey Template"},
				fields=["parameter_id", "prompt", "response_type", "sort_order"],
				order_by="sort_order asc",
			)
		for idx, answer in enumerate(_answer_rows(parameters[r.survey], answers), start=1):
			frappe.get_doc(
				{
					"doctype": ANSWER,
					"parent": r.name,
					"parenttype": RESPONSE,
					"parentfield": "answers",
					"idx": idx,
					**answer,
				}
			).db_insert()
		moved += 1
	print(f"survey_answers_to_rows: moved answers of {moved} response(s)")
