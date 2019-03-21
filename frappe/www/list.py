# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals
import frappe, json
from frappe.utils import cint, quoted
from frappe.website.render import resolve_path
from frappe.model.document import get_controller, Document
from frappe import _

no_cache = 1

def get_context(context, **dict_params):
	"""Returns context for a list standard list page.
	Will also update `get_list_context` from the doctype module file"""
	frappe.local.form_dict.update(dict_params)
	doctype = frappe.local.form_dict.doctype
	context.parents = [{"route":"me", "title":_("My Account")}]
	context.meta = frappe.get_meta(doctype)
	context.update(get_list_context(context, doctype) or {})
	context.doctype = doctype
	context.txt = frappe.local.form_dict.txt
	context.update(get(**frappe.local.form_dict))

@frappe.whitelist(allow_guest=True)
def get(doctype, txt=None, limit_start=0, limit=20, pathname=None, **kwargs):
	"""Returns processed HTML page for a standard listing."""
	limit_start = cint(limit_start)
	raw_result = get_list_data(doctype, txt, limit_start, limit=limit + 1, **kwargs)
	show_more = len(raw_result) > limit
	if show_more:
		raw_result = raw_result[:-1]

	meta = frappe.get_meta(doctype)
	list_context = frappe.flags.list_context

	if not raw_result: return {"result": []}

	if txt:
		list_context.default_subtitle = _('Filtered by "{0}"').format(txt)

	result = []
	row_template = list_context.row_template or "templates/includes/list/row_template.html"
	list_view_fields = [df for df in meta.fields if df.in_list_view][:4]

	for doc in raw_result:
		doc.doctype = doctype
		new_context = frappe._dict(doc=doc, meta=meta,
			list_view_fields=list_view_fields)

		if not list_context.get_list and not isinstance(new_context.doc, Document):
			new_context.doc = frappe.get_doc(doc.doctype, doc.name)
			new_context.update(new_context.doc.as_dict())

		if not frappe.flags.in_test:
			pathname = pathname or frappe.local.request.path
			new_context["pathname"] = pathname.strip("/ ")
		new_context.update(list_context)
		set_route(new_context)
		rendered_row = frappe.render_template(row_template, new_context, is_path=True)
		result.append(rendered_row)

	from frappe.utils.response import json_handler
	return {
		"raw_result": json.dumps(raw_result, default=json_handler),
		"result": result,
		"show_more": show_more,
		"next_start": limit_start + limit,
	}

@frappe.whitelist(allow_guest=True)
def get_list_data(doctype, txt=None, limit_start=0, fields=None, cmd=None, limit=20, **kwargs):
	"""Returns processed HTML page for a standard listing."""
	limit_start = cint(limit_start)

	if not txt and frappe.form_dict.search:
		txt = frappe.form_dict.search
		del frappe.form_dict['search']

	controller = get_controller(doctype)
	meta = frappe.get_meta(doctype)

	filters = prepare_filters(doctype, controller, kwargs)
	list_context = get_list_context(frappe._dict(), doctype)
	list_context.title_field = getattr(controller, 'website',
		{}).get('page_title_field', meta.title_field or 'name')

	if list_context.filters:
		filters.update(list_context.filters)

	_get_list = list_context.get_list or get_list

	kwargs = dict(doctype=doctype, txt=txt, filters=filters,
		limit_start=limit_start, limit_page_length=limit,
		order_by = list_context.order_by or 'modified desc')

	# allow guest if flag is set
	if not list_context.get_list and (list_context.allow_guest or meta.allow_guest_to_view):
		kwargs['ignore_permissions'] = True

	raw_result = _get_list(**kwargs)

	# list context to be used if called as rendered list
	frappe.flags.list_context = list_context

	return raw_result

def set_route(context):
	'''Set link for the list item'''
	if context.web_form_name:
		context.route = "{0}?name={1}".format(context.pathname, quoted(context.doc.name))
	elif context.doc and getattr(context.doc, 'route', None):
		context.route = context.doc.route
	else:
		context.route = "{0}/{1}".format(context.pathname or quoted(context.doc.doctype),
			quoted(context.doc.name))

def prepare_filters(doctype, controller, kwargs):
	filters = frappe._dict(kwargs)
	meta = frappe.get_meta(doctype)

	if hasattr(controller, 'website') and controller.website.get('condition_field'):
		filters[controller.website['condition_field']] = 1

	if filters.pathname:
		# resolve additional filters from path
		resolve_path(filters.pathname)
		for key, val in frappe.local.form_dict.items():
			if key not in filters and key != 'flags':
				filters[key] = val

	# filter the filters to include valid fields only
	for fieldname, val in list(filters.items()):
		if not meta.has_field(fieldname):
			del filters[fieldname]

	return filters

def get_list_context(context, doctype):
	from frappe.modules import load_doctype_module
	from frappe.website.doctype.web_form.web_form import get_web_form_list

	list_context = context or frappe._dict()
	meta = frappe.get_meta(doctype)

	if not meta.custom:
		# custom doctypes don't have modules
		module = load_doctype_module(doctype)
		if hasattr(module, "get_list_context"):
			out = frappe._dict(module.get_list_context(list_context) or {})
			if out:
				list_context = out

	# get path from '/templates/' folder of the doctype
	if not list_context.row_template:
		list_context.row_template = meta.get_row_template()

	# is web form, show the default web form filters
	# which is only the owner
	if frappe.form_dict.web_form_name:
		list_context.web_form_name = frappe.form_dict.web_form_name
		if not list_context.get("get_list"):
			list_context.get_list = get_web_form_list

		if not frappe.flags.web_form:
			# update list context from web_form
			frappe.flags.web_form = frappe.get_doc('Web Form', frappe.form_dict.web_form_name)

		if frappe.flags.web_form.is_standard:
			frappe.flags.web_form.update_list_context(list_context)

	return list_context

def get_list(doctype, txt, filters, limit_start, limit_page_length=20, ignore_permissions=False,
	fields=None, order_by=None):
	meta = frappe.get_meta(doctype)
	if not filters:
		filters = []

	if not fields:
		fields = "distinct *"

	or_filters = []

	if txt:
		if meta.search_fields:
			for f in meta.get_search_fields():
				if f == 'name' or meta.get_field(f).fieldtype in ('Data', 'Text', 'Small Text', 'Text Editor'):
					or_filters.append([doctype, f, "like", "%" + txt + "%"])
		else:
			if isinstance(filters, dict):
				filters["name"] = ("like", "%" + txt + "%")
			else:
				filters.append([doctype, "name", "like", "%" + txt + "%"])

	return frappe.get_list(doctype, fields = fields,
		filters=filters, or_filters=or_filters, limit_start=limit_start,
		limit_page_length = limit_page_length, ignore_permissions=ignore_permissions,
		order_by=order_by)

