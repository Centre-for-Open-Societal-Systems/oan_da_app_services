"""Create the DA Hierarchy root and put the existing parentless nodes under it."""

from da_services.da_services.doctype.da_hierarchy.da_hierarchy import ensure_root


def execute():
	ensure_root()
