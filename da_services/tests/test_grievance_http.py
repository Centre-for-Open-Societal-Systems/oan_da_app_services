"""HTTP grievance client: Development Agent login chain and collection mapping.

These tests do not call the live service. They check that each step's response
is what the next step sends.
"""

import json
import unittest
from unittest.mock import patch

from da_services.integrations.grievance.auth import (
	AgentTokenProvider,
	access_token_from_cookies,
	login_agent,
	login_form_action,
	login_local,
)
from da_services.integrations.grievance.client import HttpGrievanceClient
from da_services.integrations.grievance.mapper import (
	ticket_from_duplicate,
	to_upstream_submission,
)
from da_services.integrations.grievance.mock import MockGrievanceClient
from da_services.integrations.grievance.schemas import GrievanceSubmission

_LOGIN_HTML = """
<html><body>
<form id="kc-form-login" action="https://keycloak.example/login?client_id=iam&amp;tab=1">
</form>
</body></html>
"""


class _Response:
	def __init__(self, status, body=None, text="", headers=None, url="", set_cookies=None):
		self.status_code = status
		self._body = body if body is not None else {}
		self.text = text if text else json.dumps(self._body)
		self.headers = headers or {}
		self.url = url
		self.set_cookies = set_cookies or []

	def json(self):
		if isinstance(self._body, Exception):
			raise self._body
		return self._body


class _Session:
	def __init__(self, responses):
		self._responses = list(responses)
		self.calls = []

	def post(self, url, **kwargs):
		self.calls.append(("POST", url, kwargs))
		return self._responses.pop(0)

	def get(self, url, **kwargs):
		self.calls.append(("GET", url, kwargs))
		return self._responses.pop(0)


def _submission(**overrides) -> GrievanceSubmission:
	fields = dict(
		complainant_id="DA-000001",
		complainant_name="Abebe Kebede",
		complainant_role="Development Agent",
		subject="Fertilizer voucher",
		description="The fertilizer voucher never arrived at the kebele office.",
		category="Inputs",
		subcategory="Fertilizer Shortage",
		client_submission_uuid="uuid-1",
		submitter_name="Agent Walk-in Farmer",
		contact_mobile="+251911887766",
		administrative_area="kebele-ET140108101008",
		administrative_unit="Kebele 01 Center",
		desired_outcome="Reissue the fertilizer voucher.",
	)
	fields.update(overrides)
	return GrievanceSubmission(**fields)


class TestLoginChain(unittest.TestCase):
	def test_form_action_unescapes_and_joins(self):
		action = login_form_action(_LOGIN_HTML, "https://keycloak.example/start")
		self.assertEqual(action, "https://keycloak.example/login?client_id=iam&tab=1")

	def test_prefixed_cookie_wins(self):
		headers = [
			"other=1",
			"agent-X-Access-Token=agent-token; Path=/; HttpOnly",
		]
		self.assertEqual(access_token_from_cookies(headers, "agent-"), "agent-token")

	def test_k1_through_k5_pass_values_forward(self):
		session = _Session(
			[
				_Response(
					200,
					{
						"status": "success",
						"data": {
							"redirect_url": "https://kc.example/auth",
							"cookie_prefix": "agent-",
							"iam": "agent",
						},
					},
				),
				_Response(200, text=_LOGIN_HTML, url="https://kc.example/auth"),
				_Response(302, headers={"Location": "https://grievance.example/iam/callback?code=abc"}),
				_Response(302, set_cookies=["agent-X-Access-Token=live-token; Path=/; HttpOnly"]),
				_Response(200, {"data": {"roles": ["Grievance Assisted Submitter"]}}),
			]
		)
		token = login_agent(
			session,
			base_url="https://grievance.example/api",
			timeout=5,
			login_key="agent",
			return_url="http://localhost:3000",
			username="da-agent@services.com",
			password="secret",
		)
		self.assertEqual(token, "live-token")
		self.assertEqual(session.calls[0][0], "POST")
		self.assertEqual(session.calls[0][1], "https://grievance.example/api/v1/auth/iam/login")
		self.assertEqual(session.calls[0][2]["json"]["key"], "agent")
		self.assertEqual(session.calls[0][2]["json"]["return_url"], "http://localhost:3000")
		self.assertNotIn("password", json.dumps(session.calls[0][2]))
		self.assertEqual(session.calls[1][1], "https://kc.example/auth")
		self.assertTrue(session.calls[1][2]["allow_redirects"])
		self.assertEqual(session.calls[2][1], "https://keycloak.example/login?client_id=iam&tab=1")
		self.assertEqual(session.calls[2][2]["data"]["username"], "da-agent@services.com")
		self.assertFalse(session.calls[2][2]["allow_redirects"])
		self.assertEqual(session.calls[3][1], "https://grievance.example/iam/callback?code=abc")
		self.assertFalse(session.calls[3][2]["allow_redirects"])
		self.assertEqual(session.calls[4][2]["headers"]["Authorization"], "Bearer live-token")

	def test_local_login_posts_credentials_and_checks_the_role(self):
		session = _Session(
			[
				_Response(200, {"status": "success", "data": {"access_token": "local-token"}}),
				_Response(200, {"data": {"roles": ["Grievance Assisted Submitter"]}}),
			]
		)
		token = login_local(
			session,
			base_url="https://grievance.example/api",
			timeout=5,
			username="da-agent@services.com",
			password="secret",
		)
		self.assertEqual(token, "local-token")
		self.assertEqual(session.calls[0][1], "https://grievance.example/api/v1/auth/login")
		self.assertEqual(session.calls[0][2]["json"], {"usr": "da-agent@services.com", "pwd": "secret"})
		self.assertEqual(session.calls[1][2]["headers"]["Authorization"], "Bearer local-token")


class TestUpstreamMapping(unittest.TestCase):
	def test_k6_body(self):
		body = to_upstream_submission(_submission())
		self.assertEqual(body["submission_channel"], "Development Agent Assisted")
		self.assertEqual(body["client_submission_uuid"], "uuid-1")
		self.assertEqual(body["submitter_type"], "Individual Farmer")
		self.assertEqual(body["submitter_name"], "Agent Walk-in Farmer")
		self.assertEqual(body["contact_mobile"], "+251911887766")
		self.assertEqual(body["administrative_area"], "kebele-ET140108101008")
		self.assertEqual(body["administrative_unit"], "Kebele 01 Center")
		self.assertEqual(body["service_category"], "Inputs")
		self.assertEqual(body["grievance_type"], "Fertilizer Shortage")
		self.assertIn("Fertilizer voucher", body["description"])
		self.assertIn("never arrived", body["description"])
		self.assertEqual(body["desired_outcome"], "Reissue the fertilizer voucher.")
		self.assertEqual(body["is_anonymous"], 0)
		self.assertEqual(body["consent_given"], 1)
		self.assertNotIn("complainant_id", body)
		self.assertNotIn("priority", body)

	def test_duplicate_message_names_the_original_ticket(self):
		text = "This submission has already been filed as B-001-0012-0."
		self.assertEqual(ticket_from_duplicate(text), "B-001-0012-0")

	def test_mock_replay_returns_the_same_ticket(self):
		client = MockGrievanceClient()
		first = client.submit_grievance(_submission())
		second = client.submit_grievance(_submission(subject="changed"))
		self.assertEqual(second.ticket_id, first.ticket_id)
		self.assertEqual(len([key for key in client._records if key.startswith("GRV-")]), 4)


class TestHttpClient(unittest.TestCase):
	def _client(self):
		tokens = AgentTokenProvider(base_url="https://grievance.example/api", static_token="static-token")
		return HttpGrievanceClient("https://grievance.example/api", timeout=5, tokens=tokens)

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_submit_list_detail_and_timeline_use_the_token_and_ticket(self, request):
		ticket = "B-014-0007-6"
		request.side_effect = [
			_Response(200, {"status": "success", "data": {"ticket_number": ticket, "status": "Submitted"}}),
			_Response(
				200,
				{
					"status": "success",
					"data": {
						"items": [
							{
								"ticket_number": ticket,
								"status": "Submitted",
								"description": "The fertilizer voucher never arrived.",
								"service_category": "Inputs",
								"submitter_name": "Agent Walk-in Farmer",
								"submitted_on": "2026-10-27T10:00:00Z",
							}
						],
						"pagination": {"total_count": 1},
					},
				},
			),
			_Response(404, {"status": "error"}),
			_Response(
				200,
				{
					"status": "success",
					"data": {
						"ticket_number": ticket,
						"status": "Submitted",
						"summary": {
							"description": "The fertilizer voucher never arrived.",
							"service_category": "Inputs",
							"grievance_type_name": "Fertilizer Shortage",
							"administrative_area": "kebele-ET140108101008",
						},
						"submitter": {"name": "Agent Walk-in Farmer", "submitter_type": "Individual Farmer"},
						"sla": {"sla_due_date": "2026-11-10T00:00:00Z", "sla_consumed_percent": 12},
						"timeline": [
							{
								"entry_type": "submission",
								"body": "The fertilizer voucher never arrived.",
								"author_name": "Agent Walk-in Farmer",
								"created_on": "2026-10-27T10:00:00Z",
							}
						],
					},
				},
			),
			_Response(
				200,
				{
					"status": "success",
					"data": {
						"ticket_number": ticket,
						"timeline": [
							{
								"entry_type": "submission",
								"body": "filed",
								"author_role": "Individual Farmer",
								"created_on": "2026-10-27T10:00:00Z",
							}
						],
					},
				},
			),
		]
		client = self._client()
		created = client.submit_grievance(_submission())
		self.assertEqual(created.ticket_number, ticket)
		sent = request.call_args_list[0]
		self.assertEqual(sent.args[0], "POST")
		self.assertEqual(sent.args[1], "https://grievance.example/api/v1/grievances")
		self.assertEqual(sent.kwargs["headers"]["Authorization"], "Bearer static-token")
		self.assertEqual(sent.kwargs["json"]["client_submission_uuid"], "uuid-1")
		self.assertNotIn("X-CSRF-Token", sent.kwargs["headers"])

		items, total = client.list_grievances(complainant_id="DA-000001", page=1, page_size=20)
		self.assertEqual(total, 1)
		self.assertEqual(items[0].ticket_number, ticket)
		self.assertEqual(items[0].category, "Inputs")
		list_call = request.call_args_list[1]
		self.assertNotIn("complainant_id", list_call.kwargs["params"])
		self.assertEqual(list_call.kwargs["params"]["sort_order"], "desc")

		detail = client.get_grievance(ticket)
		self.assertEqual(detail.ticket_number, ticket)
		self.assertEqual(detail.category, "Inputs")
		self.assertEqual(detail.subcategory, "Fertilizer Shortage")
		self.assertEqual(detail.sla_consumed_percent, 12)
		self.assertEqual(
			request.call_args_list[2].args[1], f"https://grievance.example/api/v1/grievances/{ticket}"
		)
		self.assertEqual(
			request.call_args_list[3].args[1],
			f"https://grievance.example/api/v1/grievances/{ticket}/timeline",
		)

		timeline = client.get_timeline(ticket)
		self.assertEqual(timeline[0]["action"], "submission")
		self.assertEqual(timeline[0]["detail"], "filed")

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_repeated_uuid_returns_the_named_ticket(self, request):
		request.return_value = _Response(
			417,
			{"status": "error", "message": "This submission has already been filed as B-014-0007-6."},
		)
		result = self._client().submit_grievance(_submission())
		self.assertTrue(result.accepted)
		self.assertEqual(result.ticket_number, "B-014-0007-6")

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_options_are_public(self, request):
		request.return_value = _Response(
			200,
			{
				"status": "success",
				"data": {
					"service_categories": [{"category_name": "Inputs"}],
					"grievance_types": [{"type_name": "Fertilizer Shortage", "service_category": "Inputs"}],
					"submitter_types": ["Individual Farmer"],
					"submission_types": [{"type_name": "Development Agent Assisted", "code": "DA"}],
				},
			},
		)
		options = self._client().get_options()
		self.assertEqual(options["categories"][0]["value"], "Inputs")
		self.assertEqual(options["categories"][0]["subcategories"], ["Fertilizer Shortage"])
		self.assertNotIn("Authorization", request.call_args.kwargs["headers"])
		self.assertTrue(request.call_args.args[1].endswith("/v1/submitters/options"))


if __name__ == "__main__":
	unittest.main()
