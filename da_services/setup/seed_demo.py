"""Demo data for a development site: two users and a filled DA dashboard (DA-198).

    bench --site da-services.localhost execute da_services.setup.seed_demo.run

Refuses to run unless the site has ``developer_mode`` on (or ``force=1`` is passed), and is
idempotent: every row carries a ``seed-*`` client_op_id or a fixed title, so running it
again changes nothing. The DA-IDs match the mock DA Registry fixtures (DA-000001 in
Woreda ET04-W01); with a real registry the posting lookup on insert decides the scope.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, today

from da_services.integrations.da_registry.mock import FIXTURE_GEO
from da_services.services import assignments, notifications, tasks, visits
from da_services.services import constants as C
from da_services.utils.permissions import get_scope_context

DEMO_PASSWORD = "Demo#2026"  # local demo only; the guard below keeps this off real sites

USERS = [
	# (email, first name, role, grant kwargs)
	(
		"da.demo@da.local",
		"Abebe",
		C.ROLE_DA,
		{"da_id": "DA-000001", "region_scope": "ET04", "woreda_scope": "ET04-W01"},
	),
	("sup.demo@da.local", "Almaz", C.ROLE_SUPERVISOR, {"region_scope": "ET04", "woreda_scope": "ET04-W01"}),
]

DA_ID = "DA-000001"
FARMERS = ["FRM-000101", "FRM-000102", "FRM-000103", "FRM-000104"]


def _geo_masters() -> None:
	"""DA Country / Region / Zone / Woreda / Kebele rows for the demo geography (missing ones only)."""
	if not frappe.db.exists("DA Country", DEMO_COUNTRY[0]):
		frappe.get_doc(
			{"doctype": "DA Country", "country_code": DEMO_COUNTRY[0], "country_name": DEMO_COUNTRY[1]}
		).insert(ignore_permissions=True)
	for code, level, parent, name in FIXTURE_GEO:  # parents come before their children
		doctype, parent_field = GEO_DOCTYPES[level]
		if frappe.db.exists(doctype, code):
			continue
		frappe.get_doc(
			{
				"doctype": doctype,
				f"{level}_code": code,
				f"{level}_name": name,
				parent_field: parent or DEMO_COUNTRY[0],
			}
		).insert(ignore_permissions=True)


def _user(email: str, first_name: str, role: str, grant: dict) -> None:
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = first_name
		u.send_welcome_email = 0
		u.new_password = DEMO_PASSWORD
		u.insert(ignore_permissions=True)
	u = frappe.get_doc("User", email)
	if role not in frappe.get_roles(email):
		u.add_roles(role)
	if grant.get("da_id") and u.get("da_registry_id") != grant["da_id"]:
		# The grant's DA-ID is fetched from the user's DA Registry ID (unique): the demo
		# user takes it over from any other user on this developer site.
		frappe.db.sql(
			"update `tabUser` set da_registry_id = null where da_registry_id = %s and name != %s",
			(grant["da_id"], email),
		)
		frappe.db.set_value("User", email, "da_registry_id", grant["da_id"])
	if not frappe.db.exists("DA RBAC Assignment", {"user": email, "role": role, "active": 1}):
		doc = frappe.new_doc("DA RBAC Assignment")
		doc.user = email
		doc.role = role
		doc.effective_from = today()
		doc.update(grant)
		doc.insert(ignore_permissions=True)


def _visits(ctx) -> None:
	t = today()
	plan = [
		# (client_op_id, farmer, scheduled_at, visit type, complete?)
		("seed-v1", FARMERS[0], f"{add_days(t, -20)} 09:00:00", "Crop Survey", True),
		("seed-v2", FARMERS[1], f"{add_days(t, -12)} 10:30:00", "Advisory", True),
		("seed-v3", FARMERS[2], f"{add_days(t, -6)} 14:00:00", "Livestock Survey", True),
		("seed-v4", FARMERS[3], f"{add_days(t, -2)} 09:00:00", "Services", True),
		("seed-v5", FARMERS[0], f"{t} 10:00:00", "Follow-up", False),
		("seed-v6", FARMERS[1], f"{t} 15:00:00", "Crop Survey", False),
	]
	for op, farmer, when, vtype, complete in plan:
		out = visits.plan_visit(
			farmer_id=farmer,
			scheduled_at=when,
			visit_type=vtype,
			purpose=f"{vtype} at {farmer}",
			da_id=DA_ID,
			client_op_id=op,
			ctx=ctx,
		)
		if complete and out["physical_status"] in ("Planned", "Confirmed"):
			visits.submit_outcome(
				out["name"],
				observed="Seeded demo visit: crop stand normal, no pest pressure.",
				advice_given="Continue current practice; next check in two weeks.",
				farmer_response="Agreed",
				ctx=ctx,
			)


def _tasks(ctx) -> None:
	t = today()
	plan = [
		# (client_op_id, title, type, due_at, priority, done?)
		("seed-t1", "Submit weekly crop report", "Report due", f"{t} 13:00:00", "Normal", False),
		("seed-t2", "Collect soil sample at FRM-000102", "Survey due", f"{t} 16:00:00", "Normal", False),
		(
			"seed-t3",
			"Follow-up on fertiliser voucher",
			"Follow-up",
			f"{add_days(t, 2)} 09:00:00",
			"Urgent",
			False,
		),
		("seed-t4", "Prepare kebele training list", "Custom", f"{add_days(t, 5)} 09:00:00", "Normal", False),
		("seed-t5", "Monthly livestock count", "Report due", f"{add_days(t, -9)} 09:00:00", "Normal", True),
		("seed-t6", "Visit summary FRM-000101", "Follow-up", f"{add_days(t, -15)} 09:00:00", "Normal", True),
		("seed-t7", "Seed distribution check", "Survey due", f"{add_days(t, -25)} 09:00:00", "Normal", True),
	]
	for op, title, ttype, due, priority, done in plan:
		out = tasks.create_task(
			title=title, da_id=DA_ID, task_type=ttype, due_at=due, priority=priority, client_op_id=op, ctx=ctx
		)
		if done and out["status"] == "Open":
			tasks.complete_task(out["name"], ctx=ctx)


# Region > Zone > Woreda > Kebele codes match the mock DA Registry fixtures (ET04-W01-K03 ...).
# Names are placeholders until the administrative master is synced.
# The demo geography is the mock registry's Master Data (one list, two readers).
AREAS = [(code, level.capitalize(), parent, name) for code, level, parent, name in FIXTURE_GEO]
# The same geography in the geo masters, which RBAC scopes and Kebele postings link to:
# level -> (doctype, parent link field). The Regions sit under DEMO_COUNTRY.
DEMO_COUNTRY = ("ET", "Ethiopia")
GEO_DOCTYPES = {
	"region": ("DA Region", "country"),
	"zone": ("DA Zone", "region"),
	"woreda": ("DA Woreda", "zone"),
	"kebele": ("DA Kebele", "woreda"),
}
# One demo boundary so the geofence check can be exercised: a box around Dambi Gobu.
DEMO_BOUNDARY = {
	"ET04-W01-K03": {
		"type": "Polygon",
		"coordinates": [[[37.50, 9.08], [37.62, 9.08], [37.62, 9.17], [37.50, 9.17], [37.50, 9.08]]],
	}
}


def _areas() -> None:
	for code, level, parent, name_en in AREAS:
		if frappe.db.exists("DA Administrative Area", code):
			continue
		doc = frappe.new_doc("DA Administrative Area")
		doc.code = code
		doc.level = level
		doc.parent_area = parent
		doc.name_en = name_en
		doc.source = "seed_demo"
		if code in DEMO_BOUNDARY:
			doc.boundary_geojson = json.dumps(DEMO_BOUNDARY[code])
			doc.boundary_ref = "demo-box-v1"
		doc.insert(ignore_permissions=True)


def _assignments(ctx) -> None:
	assignments.assign(
		da_id=DA_ID,
		kebele="ET04-W01-K03",
		effective_date=add_days(today(), -400),
		reason="Seeded demo posting",
		mode="Automatic",
		da_location={"lat": 9.1236, "lng": 37.5671},  # inside the demo boundary
		client_op_id="seed-kas-1",
		ctx=ctx,
	)


def _notifications() -> None:
	user = "da.demo@da.local"
	rows = [
		("Sync", "sync_completed", "All data synced", "6 operations accepted by the server."),
		("Broadcast", "broadcast", "Rust alert for ET04-W01", "Check wheat plots for stem rust this week."),
	]
	for kind, event, title, body in rows:
		if not frappe.db.exists("DA Notification", {"user": user, "event": event, "title": title}):
			notifications.notify(user, kind=kind, title=title, body=body, event=event, da_id=DA_ID)


def run(force: str | int | bool = False) -> dict:
	if not (frappe.conf.get("developer_mode") or str(force).lower() in ("1", "true", "yes")):
		frappe.throw("seed_demo only runs on a developer_mode site (or with force=1).")
	frappe.set_user("Administrator")
	_geo_masters()  # before the grants: their scopes link to the geo masters
	for email, first_name, role, grant in USERS:
		_user(email, first_name, role, grant)
	ctx = get_scope_context("Administrator")
	_areas()
	_assignments(ctx)
	_visits(ctx)
	_tasks(ctx)
	_notifications()
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	counts = {
		"visits": frappe.db.count("DA Visit", {"da_id": DA_ID}),
		"tasks": frappe.db.count("DA Task", {"da_id": DA_ID}),
		"notifications": frappe.db.count("DA Notification", {"user": "da.demo@da.local"}),
		"areas": frappe.db.count("DA Administrative Area"),
		"assignments": frappe.db.count("DA Kebele Assignment", {"da_id": DA_ID}),
	}
	print(f"seed_demo: {counts}")
	return counts
