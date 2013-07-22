from operator import attrgetter

from mongoengine import ReferenceField
from mongoengine.base import BaseDocument, DocumentMetaclass

from wtforms import fields, validators
from flask.ext.mongoengine.wtf import orm, fields as mongo_fields

from flask.ext.admin import form
from flask.ext.admin.model.form import FieldPlaceholder
from flask.ext.admin.model.fields import InlineFieldList
from flask.ext.admin.model.widgets import InlineFormWidget
from flask.ext.admin._compat import iteritems

from .fields import ModelFormField, MongoFileField, MongoImageField


class CustomModelConverter(orm.ModelConverter):
    """
        Customized MongoEngine form conversion class.

        Injects various Flask-Admin widgets and handles lists with
        customized InlineFieldList field.
    """

    def __init__(self, view):
        super(CustomModelConverter, self).__init__()

        self.view = view

    def _get_field_override(self, name):
        form_overrides = getattr(self.view, 'form_overrides', None)

        if form_overrides:
            return form_overrides.get(name)

        return None

    def convert(self, model, field, field_args):
        # Check if it is overridden field
        if isinstance(field, FieldPlaceholder):
            return form.recreate_field(field.field)

        kwargs = {
            'label': getattr(field, 'verbose_name', field.name),
            'description': field.help_text or '',
            'validators': [],
            'filters': [],
            'default': field.default
        }

        if field_args:
            kwargs.update(field_args)

        if field.required:
            kwargs['validators'].append(validators.Required())
        else:
            kwargs['validators'].append(validators.Optional())

        ftype = type(field).__name__

        if field.choices:
            kwargs['choices'] = field.choices

            if ftype in self.converters:
                kwargs["coerce"] = self.coerce(ftype)
            if kwargs.pop('multiple', False):
                return fields.SelectMultipleField(**kwargs)
            return fields.SelectField(**kwargs)

        ftype = type(field).__name__

        if hasattr(field, 'to_form_field'):
            return field.to_form_field(model, kwargs)

        override = self._get_field_override(field.name)
        if override:
            return override(**kwargs)

        if ftype in self.converters:
            return self.converters[ftype](model, field, kwargs)

    @orm.converts('DateTimeField')
    def conv_DateTime(self, model, field, kwargs):
        kwargs['widget'] = form.DateTimePickerWidget()
        return orm.ModelConverter.conv_DateTime(self, model, field, kwargs)

    @orm.converts('ListField')
    def conv_List(self, model, field, kwargs):
        if field.field is None:
            raise ValueError('ListField "%s" must have field specified for model %s' % (field.name, model))

        if isinstance(field.field, ReferenceField):
            kwargs['widget'] = form.Select2Widget(multiple=True)

            doc_type = field.field.document_type
            return mongo_fields.ModelSelectMultipleField(model=doc_type, **kwargs)

        if field.field.choices:
            kwargs['multiple'] = True
            return self.convert(model, field.field, kwargs)

        unbound_field = self.convert(model, field.field, {})
        kwargs = {
            'validators': [],
            'filters': [],
        }
        return InlineFieldList(unbound_field, min_entries=0, **kwargs)

    @orm.converts('EmbeddedDocumentField')
    def conv_EmbeddedDocument(self, model, field, kwargs):
        kwargs = {
            'validators': [],
            'filters': [],
            'widget': InlineFormWidget()
        }

        # TODO: Configurable params?
        form_class = get_form(field.document_type_obj, self, field_args={})
        return ModelFormField(field.document_type_obj, form_class, **kwargs)

    @orm.converts('ReferenceField')
    def conv_Reference(self, model, field, kwargs):
        kwargs['widget'] = form.Select2Widget()
        kwargs['allow_blank'] = not field.required

        return orm.ModelConverter.conv_Reference(self, model, field, kwargs)

    @orm.converts('FileField')
    def conv_File(self, model, field, kwargs):
        return MongoFileField(**kwargs)

    @orm.converts('ImageField')
    def conv_image(self, model, field, kwargs):
        return MongoImageField(**kwargs)


def get_form(model, converter,
             base_class=form.BaseForm,
             only=None,
             exclude=None,
             field_args=None,
             extra_fields=None):
    """
    Create a wtforms Form for a given mongoengine Document schema::

        from flask.ext.mongoengine.wtf import model_form
        from myproject.myapp.schemas import Article
        ArticleForm = model_form(Article)

    :param model:
        A mongoengine Document schema class
    :param base_class:
        Base form class to extend from. Must be a ``wtforms.Form`` subclass.
    :param only:
        An optional iterable with the property names that should be included in
        the form. Only these properties will have fields.
    :param exclude:
        An optional iterable with the property names that should be excluded
        from the form. All other properties will have fields.
    :param field_args:
        An optional dictionary of field names mapping to keyword arguments used
        to construct each field object.
    :param converter:
        A converter to generate the fields based on the model properties. If
        not set, ``ModelConverter`` is used.
    """
    if not isinstance(model, (BaseDocument, DocumentMetaclass)):
        raise TypeError('Model must be a mongoengine Document schema')

    field_args = field_args or {}

    # Find properties
    properties = sorted(((k, v) for k, v in iteritems(model._fields)),
                        key=lambda v: v[1].creation_counter)

    if only:
        props = dict(properties)

        def find(name):
            if extra_fields and name in extra_fields:
                return FieldPlaceholder(extra_fields[name])

            p = props.get(name)
            if p is not None:
                return p

            raise ValueError('Invalid model property name %s.%s' % (model, name))

        properties = ((p, find(p)) for p in only)
    elif exclude:
        properties = (p for p in properties in p[0] not in exclude)

    # Create fields
    field_dict = {}
    for name, p in properties:
        field = converter.convert(model, p, field_args.get(name))
        if field is not None:
            field_dict[name] = field

    # Contribute extra fields
    if not only and extra_fields:
        for name, field in iteritems(extra_fields):
            field_dict[name] = form.recreate_field(field)

    field_dict['model_class'] = model
    return type(model.__name__ + 'Form', (base_class,), field_dict)
