"""Visits API: plan, list, detail timeline, reschedule, cancel (DA-43); check-in, outcome,
evidence and weekly activity report (DA-44)."""

import hashlib
from io import BytesIO

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today
from PIL import Image
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from da_services.api.v1 import visits as api
from da_services.services import constants as C
from da_services.services import visits as svc
from da_services.tests.fixtures import ensure_kebele_assignment
from da_services.utils.permissions import get_scope_context

DA1 = "vapi-da1@da.local"  # DA-000001, ET04 / ET04-W01
SUP = "vapi-sup@da.local"  # Supervisor ET04-W01
EXEC = "vapi-exec@da.local"  # Executive ET04
COMMS = "vapi-comms@da.local"


def _user(email, roles):
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	u = frappe.get_doc("User", email)
	for r in roles:
		if r not in frappe.get_roles(email):
			u.add_roles(r)
	return email


def _png(color: str) -> bytes:
	"""A real (tiny) image: Frappe's File doctype opens image uploads to strip EXIF."""
	buf = BytesIO()
	Image.new("RGB", (2, 2), color).save(buf, "PNG")
	return buf.getvalue()


def _pdf() -> bytes:
	"""A minimal but valid one-page PDF with a correct xref (Frappe parses PDFs with pypdf)."""
	objs = [
		b"<</Type/Catalog/Pages 2 0 R>>",
		b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
		b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 10 10]>>",
	]
	out = bytearray(b"%PDF-1.4\n")
	offsets = []
	for i, body in enumerate(objs, start=1):
		offsets.append(len(out))
		out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
	xref = len(out)
	out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
	for off in offsets:
		out += b"%010d 00000 n \n" % off
	out += b"trailer\n<</Root 1 0 R/Size %d>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
	return bytes(out)


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestVisitAPI(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for dt in ("DA Notification", "DA Visit", "DA Task", "DA RBAC Assignment"):
			frappe.db.delete(dt)
		_user(DA1, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")
		_grant(COMMS, C.ROLE_COMMS)
		ensure_kebele_assignment("DA-000001", "ET04-W01-K03")
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		frappe.local.request = None  # API helpers then read kwargs, as the form would send them

	def tearDown(self):
		frappe.set_user("Administrator")
		for dt in ("DA Notification", "DA Visit", "DA Task", "DA RBAC Assignment"):
			frappe.db.delete(dt)
		frappe.db.delete("DA Kebele Assignment", {"da_id": "DA-000001"})
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def _plan(self, **kw):
		base = {"farmer_id": "FRM-000101", "date": add_days(today(), 1), "time": "10:30"}
		return api.plan(**{**base, **kw})

	# -- plan ---------------------------------------------------------------------

	def test_da_plans_from_the_form_fields(self):
		frappe.set_user(DA1)
		out = self._plan(
			visit_type="Advisory",
			purpose="Input advisory",
			duration_min="60",
			priority="High",
			priority_reason="Overdue by 12 days",
			location={"lat": 9.1234, "lng": 37.5678},
			notify_farmer_sms="true",
			advisory_qa=[{"question": "Fertiliser timing?", "answer": ""}],
		)
		self.assertEqual(out["status"], "success", out)
		v = out["data"]
		self.assertEqual(v["da_id"], "DA-000001")
		self.assertEqual(v["scheduled_at"], f"{add_days(today(), 1)}T10:30:00")
		self.assertEqual((v["visit_type"], v["duration_min"], v["priority"]), ("Advisory", 60, "High"))
		self.assertEqual((v["location_lat"], v["location_lng"]), (9.1234, 37.5678))
		self.assertEqual(v["advisory_qa"], [{"question": "Fertiliser timing?", "answer": ""}])
		self.assertEqual(v["physical_status"], "Planned")
		self.assertEqual(frappe.db.get_value("DA Visit", v["name"], "notify_farmer_sms"), 1)
		self.assertIsNone(v["assigned_by"])

	def test_plan_validates_required_fields(self):
		frappe.set_user(DA1)
		out = api.plan(date=add_days(today(), 1))
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 400)
		out = api.plan(farmer_id="FRM-000101")
		self.assertEqual(out["status"], "error")

	def test_plan_is_idempotent_on_client_op_id(self):
		frappe.set_user(DA1)
		a = self._plan(client_op_id="op-43-1")["data"]
		b = self._plan(client_op_id="op-43-1", purpose="changed")["data"]
		self.assertEqual(a["name"], b["name"])
		self.assertEqual(frappe.db.count("DA Visit"), 1)

	def test_supervisor_plans_for_a_da_in_woreda_only(self):
		frappe.set_user(SUP)
		out = self._plan(da_id="DA-000001")
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["assigned_by"], SUP)
		out = self._plan(da_id="DA-000003")  # ET03, outside the Supervisor's Woreda
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)
		out = self._plan()  # officer must name the DA
		self.assertEqual(out["status"], "error")

	def test_executive_and_comms_cannot_plan(self):
		frappe.set_user(EXEC)
		self.assertEqual(self._plan(da_id="DA-000001")["status"], "error")
		frappe.set_user(COMMS)
		self.assertEqual(self._plan()["status"], "error")
		self.assertEqual(api.list_visits()["status"], "error")

	# -- list / detail -------------------------------------------------------------

	def test_list_filters_and_pagination(self):
		frappe.set_user(DA1)
		self._plan(date=today(), time="08:00")
		self._plan(date=today(), time="14:00", farmer_id="FRM-000102")
		self._plan(date=add_days(today(), 2), farmer_id="FRM-000103")
		out = api.list_visits(date=today())
		self.assertEqual(out["meta"]["total"], 2)
		self.assertEqual([r["farmer_id"] for r in out["data"]], ["FRM-000101", "FRM-000102"])
		out = api.list_visits(date=today(), limit=1, start=1)
		self.assertEqual((len(out["data"]), out["meta"]["total"]), (1, 2))
		out = api.list_visits(**{"from": today(), "to": add_days(today(), 2), "status": "Planned"})
		self.assertEqual(out["meta"]["total"], 3)
		out = api.list_visits(farmer_id="FRM-000102")
		self.assertEqual(out["meta"]["total"], 1)

	def test_executive_reads_region_but_not_other_region(self):
		frappe.set_user(DA1)
		mine = self._plan()["data"]
		frappe.set_user("Administrator")
		self._plan(da_id="DA-000003", farmer_id="FRM-000199")
		frappe.set_user(EXEC)
		out = api.list_visits()
		self.assertEqual([r["da_id"] for r in out["data"]], ["DA-000001"])
		self.assertEqual(api.get_visit(mine["name"])["status"], "success")

	def test_detail_has_a_timeline(self):
		frappe.set_user(DA1)
		v = self._plan()["data"]
		out = api.get_visit(v["name"])
		self.assertEqual(out["status"], "success", out)
		tl = out["data"]["timeline"]
		self.assertEqual([e["label"] for e in tl], ["Planned"])
		self.assertEqual(tl[0]["by"], DA1)
		self.assertTrue(tl[0]["when"])
		# a Supervisor-planned visit names the Supervisor as the planner
		frappe.set_user(SUP)
		v2 = self._plan(da_id="DA-000001", farmer_id="FRM-000102")["data"]
		self.assertEqual(api.get_visit(v2["name"])["data"]["timeline"][0]["by"], SUP)

	# -- reschedule / cancel ---------------------------------------------------------

	def test_reschedule_supersedes_and_shows_in_timeline(self):
		frappe.set_user(DA1)
		v = self._plan()["data"]
		out = api.reschedule(v["name"], date=add_days(today(), 3), time="11:00", reason="Farmer unavailable")
		self.assertEqual(out["status"], "success", out)
		new = out["data"]
		self.assertEqual(new["rescheduled_from"], v["name"])
		self.assertEqual(new["scheduled_at"], f"{add_days(today(), 3)}T11:00:00")
		old = api.get_visit(v["name"])["data"]
		self.assertEqual(old["physical_status"], "Rescheduled")
		self.assertIn("Rescheduled", [e["label"] for e in old["timeline"]])
		# a superseded visit cannot be moved again
		out = api.reschedule(v["name"], date=add_days(today(), 4))
		self.assertEqual(out["status"], "error")
		out = api.reschedule(new["name"])  # no time given
		self.assertEqual(out["status"], "error")

	def test_cancel_and_scope(self):
		frappe.set_user(DA1)
		v = self._plan()["data"]
		frappe.set_user("Administrator")
		other = self._plan(da_id="DA-000003", farmer_id="FRM-000199")["data"]
		frappe.db.commit()  # nosemgrep: an API error below rolls back uncommitted rows
		frappe.set_user(DA1)
		self.assertEqual(api.cancel(other["name"], reason="not mine")["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)
		out = api.cancel(v["name"], reason="Farmer moved")
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["physical_status"], "Cancelled")

	# -- DA-44: start / submit -----------------------------------------------------

	def test_start_then_submit_creates_follow_ups_and_timeline(self):
		frappe.set_user(DA1)
		v = self._plan(date=today(), time="08:00")["data"]
		out = api.start(v["name"], gps={"lat": 9.1236, "lng": 37.5671, "accuracy_m": 8}, client_op_id="st-1")
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["physical_status"], "In progress")
		self.assertEqual(frappe.db.get_value("DA Visit", v["name"], "gps_lat"), 9.1236)
		# replay of the same check-in is accepted, not refused
		self.assertEqual(api.start(v["name"], client_op_id="st-1")["status"], "success")

		out = api.submit(
			v["name"],
			observed="Rust pustules on ~15% of the plot",
			advice_given="Propiconazole within 3 days",
			farmer_response="Will apply",
			next_action={
				"type": "Follow-up visit",
				"note": "Check spray result",
				"date": add_days(today(), 14),
			},
			client_op_id="sub-1",
		)
		self.assertEqual(out["status"], "success", out)
		d = out["data"]
		self.assertEqual(d["physical_status"], "Physical Visit Complete")
		self.assertTrue(d["follow_up_task"] and d["follow_up_visit"])
		self.assertEqual(frappe.db.count("DA Notification", {"user": DA1, "event": "task_created"}), 1)
		labels = [e["label"] for e in api.get_visit(v["name"])["data"]["timeline"]]
		self.assertEqual(labels, ["Planned", "In progress", "Physical Visit Complete"])

	def test_submit_is_idempotent_on_client_op_id(self):
		frappe.set_user(DA1)
		v = self._plan(date=today(), time="08:00")["data"]
		first = api.submit(
			v["name"], observed="ok", next_action={"type": "Task", "note": "Bring seed"}, client_op_id="sub-9"
		)
		self.assertEqual(first["status"], "success", first)
		again = api.submit(
			v["name"], observed="ok", next_action={"type": "Task", "note": "Bring seed"}, client_op_id="sub-9"
		)
		self.assertEqual(again["status"], "success", again)
		self.assertEqual(again["data"]["follow_up_task"], first["data"]["follow_up_task"])
		self.assertEqual(frappe.db.count("DA Task", {"visit": v["name"]}), 1)
		frappe.db.commit()  # nosemgrep: the error call below rolls back uncommitted rows
		# a different operation on a closed visit is refused
		self.assertEqual(api.submit(v["name"], observed="again", client_op_id="sub-10")["status"], "error")

	def test_start_respects_scope(self):
		frappe.set_user("Administrator")
		other = self._plan(da_id="DA-000003")["data"]
		frappe.db.commit()  # nosemgrep: the error call below rolls back uncommitted rows
		frappe.set_user(DA1)
		self.assertEqual(api.start(other["name"])["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)

	# -- DA-44: evidence -------------------------------------------------------------

	def test_attachment_dedup_and_hash_check(self):
		frappe.set_user(DA1)
		v = self._plan(date=today(), time="08:00")["data"]
		ctx = get_scope_context(DA1)
		content = _png("red")
		digest = hashlib.sha256(content).hexdigest()
		row = svc.add_attachment(
			v["name"],
			content=content,
			filename="plot.png",
			kind="Crop",
			gps={"lat": 9.1, "lng": 37.5},
			ctx=ctx,
		)
		self.assertEqual((row["kind"], row["content_hash"], row["gps"]["lat"]), ("Crop", digest, 9.1))
		self.assertTrue(row["file"].startswith("/private/files/"))
		again = svc.add_attachment(v["name"], content=content, filename="plot-retry.png", ctx=ctx)
		self.assertEqual(again["name"], row["name"])  # same bytes: same row
		self.assertEqual(len(frappe.get_doc("DA Visit", v["name"]).attachments), 1)
		with self.assertRaises(frappe.ValidationError):
			svc.add_attachment(
				v["name"], content=b"other", filename="x.jpg", content_hash="deadbeef", ctx=ctx
			)
		# evidence may still be added after the outcome is in
		api.submit(v["name"], observed="done")
		svc.add_attachment(v["name"], content=_png("green"), filename="after.png", kind="Land", ctx=ctx)
		self.assertEqual(len(api.get_visit(v["name"])["data"]["attachments"]), 2)

	def test_attachment_upload_over_multipart(self):
		frappe.set_user(DA1)
		v = self._plan(date=today(), time="08:00")["data"]
		builder = EnvironBuilder(
			method="POST",
			path=f"/api/v1/da-services/visits/{v['name']}/attachments",
			data={
				"file": (BytesIO(_png("blue")), "farm.png"),
				"kind": "Animal",
				"lat": "9.2",
				"lng": "37.6",
			},
		)
		frappe.local.request = Request(builder.get_environ())
		try:
			out = api.add_attachment(v["name"])
		finally:
			frappe.local.request = None
		self.assertEqual(out["status"], "success", out)
		self.assertEqual((out["data"]["kind"], out["data"]["gps"]), ("Animal", {"lat": 9.2, "lng": 37.6}))
		self.assertEqual(api.add_attachment(v["name"])["status"], "error")  # no multipart body

	# -- DA-44: weekly activity report -----------------------------------------------

	def test_evidence_is_validated_before_storage(self):
		frappe.set_user(DA1)
		v = self._plan(date=today(), time="08:00")["data"]
		ctx = get_scope_context(DA1)
		with self.assertRaises(frappe.ValidationError):  # renamed binary is not an image
			svc.add_attachment(v["name"], content=b"MZ\x90\x00not-an-image", filename="photo.png", ctx=ctx)
		with self.assertRaises(frappe.ValidationError):  # extension not accepted
			svc.add_attachment(v["name"], content=_png("red"), filename="script.exe", ctx=ctx)
		with self.assertRaises(frappe.ValidationError):  # not a PDF inside
			svc.add_attachment(v["name"], content=b"hello", filename="doc.pdf", ctx=ctx)
		frappe.conf.da_evidence_max_mb = 1
		try:
			with self.assertRaises(frappe.ValidationError):  # over the size limit
				svc.add_attachment(
					v["name"], content=b"\x00" * (1024 * 1024 + 1), filename="big.pdf", ctx=ctx
				)
		finally:
			frappe.conf.da_evidence_max_mb = None
		with self.assertRaises(frappe.ValidationError):  # PDF header but no PDF inside
			svc.add_attachment(v["name"], content=b"%PDF-1.4 minimal", filename="cert.pdf", ctx=ctx)
		row = svc.add_attachment(v["name"], content=_pdf(), filename="cert.pdf", kind="Document", ctx=ctx)
		self.assertEqual(row["kind"], "Document")

	def test_rate_limit_is_per_user_and_per_endpoint(self):
		from da_services.utils.ratelimit import rate_limited

		calls = []

		@rate_limited(limit=2, seconds=60)
		def ping():
			calls.append(1)
			return "ok"

		frappe.set_user(DA1)
		self.assertEqual(ping(), "ok")  # no HTTP request: never limited
		frappe.local.request = Request(EnvironBuilder(path="/api/v1/da-services/x").get_environ())
		try:
			# Both users' buckets: counters live 60 s in Redis, so back-to-back suite runs
			# would otherwise start the second user already near the limit.
			# The limiter stores the already-prefixed key, so delete it as-is (make_keys=False);
			# delete_value would otherwise prefix it a second time and miss.
			for who in (DA1, SUP):
				frappe.cache.delete_value(
					frappe.cache.make_key(f"rl:da:{ping.__module__}.{ping.__name__}:{who}:60"),
					make_keys=False,
				)
			self.assertEqual(ping(), "ok")
			self.assertEqual(ping(), "ok")
			with self.assertRaises(frappe.RateLimitExceededError):
				ping()
			frappe.set_user(SUP)  # another user has their own bucket
			self.assertEqual(ping(), "ok")
		finally:
			frappe.local.request = None
			frappe.set_user(DA1)
		self.assertEqual(len(calls), 4)

	def test_activity_report_rejects_bad_weeks(self):
		frappe.set_user(DA1)
		# 2025 has 52 ISO weeks (1 Jan is a Wednesday), so W53 is invalid there; 2026 and 2020 have 53
		for bad in ("bad", "2026-W0", "2026-W54", "2025-W53", "2026W42", "2026-W42-1"):
			self.assertEqual(api.activity_report(week=bad)["status"], "error", bad)
			self.assertEqual(frappe.response.get("http_status_code"), 400, bad)
		self.assertEqual(api.activity_report(week="2020-W53")["status"], "success")  # 2020 has 53 weeks

	def test_activity_report_counts_the_week(self):
		frappe.set_user(DA1)
		a = self._plan(date=today(), time="08:00", visit_type="Advisory")["data"]
		b = self._plan(date=today(), time="09:00", farmer_id="FRM-000102")["data"]
		self._plan(date=today(), time="16:00", farmer_id="FRM-000103")  # planned, not done
		api.submit(a["name"], observed="advice given", advice_given="Split urea")
		api.submit(
			b["name"],
			observed="pests",
			next_action={"type": "Task", "note": "Sample", "date": add_days(today(), 2)},
		)
		out = api.activity_report()
		self.assertEqual(out["status"], "success", out)
		r = out["data"]
		self.assertEqual(r["da_id"], "DA-000001")
		self.assertRegex(r["week"], r"^\d{4}-W\d{2}$")
		self.assertEqual((r["visits_completed"], r["visits_planned"]), (2, 3))
		self.assertEqual((r["farmers_reached"], r["advisories_issued"], r["follow_ups"]), (2, 1, 1))
		self.assertEqual({o["farmer_id"] for o in r["outcomes"]}, {"FRM-000101", "FRM-000102"})
		# explicit week and scope
		self.assertEqual(api.activity_report(week=r["week"])["data"]["visits_completed"], 2)
		self.assertEqual(api.activity_report(week="2020-W01")["data"]["visits_completed"], 0)
		frappe.db.commit()  # nosemgrep: the error calls below roll back uncommitted rows
		self.assertEqual(api.activity_report(week="bad")["status"], "error")
		frappe.set_user(SUP)
		self.assertEqual(api.activity_report(da_id="DA-000001")["data"]["visits_completed"], 2)
		self.assertEqual(api.activity_report()["status"], "error")  # officer must name the DA
		self.assertEqual(api.activity_report(da_id="DA-000003")["status"], "error")  # outside Woreda
