"""Knowledge Base: doctypes (KB Article / KB Video / KB Send Log / KB Category) and the REST handlers."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.da_services.doctype.kb_video.kb_video import parse_youtube_id
from da_services.integrations.knowledge_base import articles as article_api
from da_services.integrations.knowledge_base import categories as category_api
from da_services.integrations.knowledge_base import common
from da_services.integrations.knowledge_base import videos as video_api
from da_services.services import constants as C
from da_services.tests.fixtures import create_administrative_areas
from da_services.utils.permissions import get_scope_context

TAG = "KBTEST"
CATEGORY = f"{TAG} Cat"
CATEGORY_OFF = f"{TAG} Cat Off"  # disabled
SOURCE = f"{TAG} Src"
WRITER = "kbtest-admin@da.local"  # OAN Administrator
SUPERVISOR = "kbtest-sup-w01@da.local"  # Supervisor, Woreda ET04-W01
EXECUTIVE = "kbtest-exec-et04@da.local"  # Executive, Region-only grant ET04
COMMS = "kbtest-comms@da.local"  # Communications Officer: read only
READER_W01 = "kbtest-da-w01@da.local"  # Woreda ET04-W01 (Region ET04)
READER_W02 = "kbtest-da-w02@da.local"  # Woreda ET03-W02 (Region ET03)
USERS = (WRITER, SUPERVISOR, EXECUTIVE, COMMS, READER_W01, READER_W02)

EN = {"language": "EN", "title": "en title", "summary": "en summary", "body": "<p>en</p>"}
AM = {"language": "አማ", "title": "am title", "summary": "am summary", "body": "<p>am</p>"}


def _user(email, role):
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	if role not in frappe.get_roles(email):
		frappe.get_doc("User", email).add_roles(role)


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)


def _code(response: dict):
	"""'ok' for a success envelope, else the error code."""
	return response.get("code") if response.get("status") == "error" else "ok"


def _article(**kw):
	body = {"title": f"{TAG} article", "summary": "s", "category": CATEGORY, "languages": [EN]}
	body.update(kw)
	return article_api.create_article(**body)


def _video(**kw):
	body = {
		"title": f"{TAG} video",
		"youtube_url": "https://youtu.be/dQw4w9WgXcQ",
		"category": CATEGORY,
	}
	body.update(kw)
	return video_api.create_video(**body)


def _commit():
	"""A denied or failed call rolls the request back (handle_api_errors), so commit the
	fixtures first or a later assertion would run against an empty table."""
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit


def _cleanup():
	frappe.set_user("Administrator")
	names = frappe.get_all("KB Article", filters={"title": ("like", f"{TAG}%")}, pluck="name")
	for dt in ("KB Send Log",):
		frappe.db.delete(dt, {"article": ("in", names or ["__none__"])})
	for dt in ("KB Article", "KB Video"):
		for n in frappe.get_all(dt, filters={"title": ("like", f"{TAG}%")}, pluck="name"):
			frappe.delete_doc(dt, n, force=True, ignore_permissions=True)
	frappe.db.delete("DA RBAC Assignment", {"user": ("in", USERS)})
	for dt, name in (("KB Category", CATEGORY), ("KB Category", CATEGORY_OFF), ("KB Source", SOURCE)):
		if frappe.db.exists(dt, name):
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit


class TestKnowledgeBase(IntegrationTestCase):
	def setUp(self):
		_cleanup()
		create_administrative_areas()
		frappe.get_doc({"doctype": "KB Category", "category_name": CATEGORY}).insert()
		frappe.get_doc({"doctype": "KB Category", "category_name": CATEGORY_OFF, "enabled": 0}).insert()
		frappe.get_doc({"doctype": "KB Source", "source_name": SOURCE}).insert()
		_user(WRITER, C.ROLE_ADMIN)
		_user(SUPERVISOR, C.ROLE_SUPERVISOR)
		_user(EXECUTIVE, C.ROLE_EXECUTIVE)
		_user(COMMS, C.ROLE_COMMS)
		_user(READER_W01, C.ROLE_DA)
		_user(READER_W02, C.ROLE_DA)
		_grant(WRITER, C.ROLE_ADMIN)
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(EXECUTIVE, C.ROLE_EXECUTIVE, region_scope="ET04")
		_grant(COMMS, C.ROLE_COMMS)
		_grant(READER_W01, C.ROLE_DA, da_id="DA-KB0001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(READER_W02, C.ROLE_DA, da_id="DA-KB0002", region_scope="ET03", woreda_scope="ET03-W02")
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		frappe.set_user("Administrator")

	def tearDown(self):
		_cleanup()

	# -- YouTube parsing -----------------------------------------------------------

	def test_parse_youtube_id_accepts_common_url_shapes(self):
		vid = "dQw4w9WgXcQ"
		for url in (
			f"https://www.youtube.com/watch?v={vid}",
			f"https://www.youtube.com/watch?feature=share&v={vid}",
			f"https://youtu.be/{vid}",
			f"https://www.youtube.com/embed/{vid}",
			f"https://www.youtube.com/shorts/{vid}",
			f"  {vid}  ",
		):
			self.assertEqual(parse_youtube_id(url), vid, url)

	def test_parse_youtube_id_rejects_garbage(self):
		for url in (None, "", "nope", "https://example.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/short"):
			self.assertIsNone(parse_youtube_id(url), url)

	# -- KB Video doctype ----------------------------------------------------------

	def test_video_derives_id_and_thumbnail_and_published_date(self):
		doc = frappe.get_doc(
			{
				"doctype": "KB Video",
				"title": f"{TAG} v",
				"youtube_url": "https://youtu.be/dQw4w9WgXcQ",
				"category": CATEGORY,
				"status": "Published",
			}
		).insert()
		self.assertEqual(doc.youtube_id, "dQw4w9WgXcQ")
		self.assertEqual(doc.thumbnail, "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg")
		self.assertEqual(str(doc.published_on), today())

	def test_video_keeps_a_custom_thumbnail(self):
		doc = frappe.get_doc(
			{
				"doctype": "KB Video",
				"title": f"{TAG} v",
				"youtube_url": "https://youtu.be/dQw4w9WgXcQ",
				"thumbnail": "https://cdn.example/t.jpg",
				"category": CATEGORY,
			}
		).insert()
		self.assertEqual(doc.thumbnail, "https://cdn.example/t.jpg")

	def test_video_with_bad_url_is_rejected(self):
		doc = frappe.get_doc(
			{"doctype": "KB Video", "title": f"{TAG} v", "youtube_url": "nope", "category": CATEGORY}
		)
		self.assertRaises(frappe.ValidationError, doc.insert)

	# -- KB Article doctype --------------------------------------------------------

	def _article_doc(self, **kw):
		doc = frappe.get_doc(
			{
				"doctype": "KB Article",
				"title": f"{TAG} a",
				"summary": "s",
				"category": CATEGORY,
				"languages": [EN],
				**kw,
			}
		)
		return doc

	def test_article_needs_a_language_version(self):
		doc = self._article_doc(languages=[])
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_article_rejects_duplicate_language(self):
		doc = self._article_doc(languages=[EN, dict(EN)])
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_article_audience_scope_must_match_level(self):
		self.assertRaises(
			frappe.ValidationError,
			self._article_doc(audience_scopes=[{"region": "ET04-W01"}]).insert,  # a Woreda in the Region slot
		)
		self.assertRaises(frappe.ValidationError, self._article_doc(audience_scopes=[{}]).insert)  # empty row

	def test_article_sets_published_date_when_published(self):
		doc = self._article_doc(status="Published").insert()
		self.assertEqual(str(doc.published_on), today())

	def test_send_log_drives_send_count_and_blocks_deletes(self):
		art = self._article_doc().insert()
		self.assertEqual(art.send_count, 0)
		for who in ("0911000001", "0911000002"):
			frappe.get_doc(
				{"doctype": "KB Send Log", "article": art.name, "recipient": who, "channel": "SMS"}
			).insert()
		self.assertEqual(frappe.db.get_value("KB Article", art.name, "send_count"), 2)
		log = frappe.get_all("KB Send Log", filters={"article": art.name}, limit=1, pluck="name")[0]
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, "KB Send Log", log)
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, "KB Article", art.name)

	# -- API: writers --------------------------------------------------------------

	def test_writer_creates_updates_and_reads_an_article(self):
		frappe.set_user(WRITER)
		res = _article(languages=[EN, AM], status="Draft")
		self.assertEqual(_code(res), "ok")
		name = res["data"]["name"]
		self.assertEqual([r["language"] for r in res["data"]["languages"]], ["EN", "አማ"])

		upd = article_api.update_article(name, summary="changed", status="Published")
		self.assertEqual((upd["data"]["summary"], upd["data"]["status"]), ("changed", "Published"))
		self.assertEqual(len(upd["data"]["languages"]), 2)  # untouched table is kept

		upd = article_api.update_article(name, languages=[AM])
		self.assertEqual([r["language"] for r in upd["data"]["languages"]], ["አማ"])  # table replaced

		self.assertEqual(article_api.get_article(name)["data"]["name"], name)

	def test_article_send_count_cannot_be_set_through_the_api(self):
		frappe.set_user(WRITER)
		res = _article(send_count=99)
		self.assertEqual(res["data"]["send_count"], 0)

	def test_article_create_validation_errors_use_the_error_envelope(self):
		frappe.set_user(WRITER)
		self.assertEqual(
			_code(article_api.create_article(title="x", summary="s", category=CATEGORY)), "VALIDATION_ERROR"
		)
		self.assertEqual(_code(_article(audience_scopes=[{"region": "ET04-W01"}])), "VALIDATION_ERROR")

	def test_writer_creates_and_updates_a_video(self):
		frappe.set_user(WRITER)
		res = _video(status="Published", audience_scopes=[{"region": "ET04"}])
		self.assertEqual(_code(res), "ok")
		self.assertEqual(res["data"]["youtube_id"], "dQw4w9WgXcQ")
		upd = video_api.update_video(res["data"]["name"], youtube_url="https://youtu.be/9bZkp7q19f0")
		self.assertEqual(upd["data"]["youtube_id"], "9bZkp7q19f0")

	def test_video_bad_url_is_a_validation_error(self):
		frappe.set_user(WRITER)
		self.assertEqual(_code(_video(youtube_url="nope")), "VALIDATION_ERROR")

	def test_supervisor_and_executive_can_create_and_update(self):
		for user in (SUPERVISOR, EXECUTIVE):
			frappe.set_user(user)
			art = _article(status="Draft")
			self.assertEqual(_code(art), "ok", user)
			upd = article_api.update_article(art["data"]["name"], status="Published")
			self.assertEqual(_code(upd), "ok", user)
			vid = _video()
			self.assertEqual(_code(vid), "ok", user)
			upd = video_api.update_video(vid["data"]["name"], title=f"{TAG} renamed")
			self.assertEqual(_code(upd), "ok", user)

	def test_unknown_item_is_not_found(self):
		frappe.set_user(WRITER)
		self.assertEqual(_code(article_api.get_article("KB-ART-NOPE")), "NOT_FOUND")
		self.assertEqual(_code(video_api.update_video("KB-VID-NOPE", title="x")), "NOT_FOUND")

	# -- API: access control -------------------------------------------------------

	def test_only_writer_roles_can_create_or_update(self):
		frappe.set_user(WRITER)
		name = _article(status="Published")["data"]["name"]
		for user in (READER_W01, COMMS):
			frappe.set_user(user)
			self.assertEqual(_code(_article()), "PERMISSION_DENIED", user)
			self.assertEqual(_code(_video()), "PERMISSION_DENIED", user)
			self.assertEqual(_code(article_api.update_article(name, summary="x")), "PERMISSION_DENIED", user)
		frappe.set_user("Guest")
		self.assertEqual(_code(article_api.list_articles()), "PERMISSION_DENIED")

	def test_reader_sees_only_published_items(self):
		frappe.set_user(WRITER)
		_article(title=f"{TAG} draft", status="Draft")
		_article(title=f"{TAG} live", status="Published")
		_article(title=f"{TAG} old", status="Archived")
		draft = frappe.db.get_value("KB Article", {"title": f"{TAG} draft"}, "name")
		_commit()
		frappe.set_user(READER_W01)
		titles = {r["title"] for r in article_api.list_articles()["data"] if r["title"].startswith(TAG)}
		self.assertEqual(titles, {f"{TAG} live"})
		self.assertEqual(_code(article_api.get_article(draft)), "NOT_FOUND")
		frappe.set_user(WRITER)  # writers see every status and can filter
		titles = {r["title"] for r in article_api.list_articles(status="Archived")["data"]}
		self.assertIn(f"{TAG} old", titles)
		self.assertEqual(_code(article_api.list_articles(status="Bogus")), "VALIDATION_ERROR")

	def test_audience_scope_limits_who_can_read(self):
		frappe.set_user(WRITER)
		everyone = _article(title=f"{TAG} everyone", status="Published")["data"]["name"]
		woreda = _article(
			title=f"{TAG} woreda", status="Published", audience_scopes=[{"woreda": "ET04-W01"}]
		)["data"]["name"]
		region = _article(title=f"{TAG} region", status="Published", audience_scopes=[{"region": "ET04"}])[
			"data"
		]["name"]
		_commit()

		def visible(user):
			frappe.set_user(user)
			return {
				r["title"].removeprefix(f"{TAG} ")
				for r in article_api.list_articles()["data"]
				if r["title"].startswith(TAG)
			}

		self.assertEqual(visible(READER_W01), {"everyone", "woreda", "region"})
		self.assertEqual(visible(READER_W02), {"everyone"})  # another Region and Woreda
		self.assertEqual(visible(COMMS), {"everyone"})  # read-only role with no area grant
		self.assertEqual(visible(EXECUTIVE), {"everyone", "woreda", "region"})  # writers see every audience
		frappe.set_user(READER_W02)
		self.assertEqual(_code(article_api.get_article(everyone)), "ok")
		self.assertEqual(_code(article_api.get_article(woreda)), "NOT_FOUND")  # hidden, not forbidden
		_commit()
		self.assertEqual(_code(article_api.get_article(region)), "NOT_FOUND")

	def test_video_audience_is_enforced_too(self):
		frappe.set_user(WRITER)
		vid = _video(status="Published", audience_scopes=[{"woreda": "ET04-W01"}])["data"]["name"]
		frappe.set_user(READER_W01)
		self.assertEqual(_code(video_api.get_video(vid)), "ok")
		frappe.set_user(READER_W02)
		self.assertEqual(_code(video_api.get_video(vid)), "NOT_FOUND")
		self.assertNotIn(vid, [r["name"] for r in video_api.list_videos()["data"]])

	def test_narrowest_grant_wins_when_reaching_areas(self):
		woreda_user = common._reach(get_scope_context(READER_W01))
		self.assertIn("ET04-W01", woreda_user)
		self.assertIn("ET04-Z01", woreda_user)  # ancestor: content aimed at the Zone reaches the Woreda
		self.assertIn("ET04", woreda_user)
		self.assertNotIn("ET04-W09", woreda_user)  # the Region on the assignment is not a Region grant
		region_user = common._reach(get_scope_context(EXECUTIVE))
		self.assertTrue({"ET04", "ET04-Z01", "ET04-W01"} <= region_user)
		self.assertNotIn("ET03", region_user)

	# -- API: list behaviour -------------------------------------------------------

	def test_list_filters_search_language_and_paging(self):
		frappe.set_user(WRITER)
		for i in range(3):
			_article(title=f"{TAG} teff {i}", status="Published", languages=[EN])
		_article(title=f"{TAG} maize", status="Published", languages=[EN, AM])

		res = article_api.list_articles(category=CATEGORY, search=f"{TAG} teff")
		self.assertEqual(res["meta"]["total"], 3)
		res = article_api.list_articles(category=CATEGORY, language="አማ")
		self.assertEqual([r["title"] for r in res["data"]], ["am title"])  # language wording, not the default
		res = article_api.list_articles(category=CATEGORY, limit=2, start=2)
		self.assertEqual((res["meta"]["count"], res["meta"]["total"], res["meta"]["start"]), (2, 4, 2))
		self.assertEqual(_code(article_api.list_articles(limit="abc")), "VALIDATION_ERROR")

	def test_list_page_size_is_capped(self):
		frappe.set_user(WRITER)
		self.assertEqual(article_api.list_articles(limit=5000)["meta"]["limit"], common.MAX_PAGE_LENGTH)
		self.assertEqual(article_api.list_articles(limit=0)["meta"]["limit"], 1)

	def test_article_list_reports_available_languages(self):
		frappe.set_user(WRITER)
		_article(title=f"{TAG} both", status="Published", languages=[EN, AM])
		row = next(
			r for r in article_api.list_articles(category=CATEGORY)["data"] if r["title"] == f"{TAG} both"
		)
		self.assertEqual(row["languages"], ["EN", "አማ"])

	# -- API: categories -----------------------------------------------------------

	def test_categories_list_only_enabled_in_sort_order(self):
		frappe.set_user("Administrator")
		frappe.db.set_value("KB Category", CATEGORY, "sort_order", -1)  # sorts first
		_commit()
		for user in (READER_W01, COMMS, WRITER):
			frappe.set_user(user)
			res = category_api.list_categories()
			self.assertEqual(_code(res), "ok", user)
			names = [r["name"] for r in res["data"]]
			self.assertEqual(names[0], CATEGORY, user)
			self.assertNotIn(CATEGORY_OFF, names, user)  # disabled is hidden, even for writers
			self.assertEqual(res["meta"]["count"], len(names))
		self.assertEqual(
			set(res["data"][0]), {"name", "category_name", "category_name_am", "color", "sort_order"}
		)

	def test_categories_list_needs_a_kb_role(self):
		frappe.set_user("Guest")
		self.assertEqual(_code(category_api.list_categories()), "PERMISSION_DENIED")
