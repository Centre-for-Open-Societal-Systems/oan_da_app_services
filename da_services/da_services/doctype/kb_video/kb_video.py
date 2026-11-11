# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

from da_services.da_services.doctype.kb_article.kb_article import _check_audience

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_PATTERNS = (
	re.compile(
		r"(?:youtube\.com|youtube-nocookie\.com)/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/|v/)([A-Za-z0-9_-]{11})"
	),
	re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
)


def parse_youtube_id(url: str | None) -> str | None:
	"""Return the 11-character video id from a YouTube URL (or a bare id), else None."""
	url = (url or "").strip()
	if _YT_ID.match(url):
		return url
	for pattern in _YT_PATTERNS:
		if m := pattern.search(url):
			return m.group(1)
	return None


class KBVideo(Document):
	def validate(self):
		video_id = parse_youtube_id(self.youtube_url)
		if not video_id:
			frappe.throw(_("Could not read a YouTube video id from {0}.").format(self.youtube_url))
		self.youtube_id = video_id
		if not self.thumbnail:
			self.thumbnail = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
		if self.status == "Published" and not self.published_on:
			self.published_on = today()
		_check_audience(self)
