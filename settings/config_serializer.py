"""
/***************************************************************************
Name                 : ConfigurationWriter
Description          : Reads/writes configuration object from/to file.
Date                 : 15/February/2016
copyright            : (C) 2015 by UN-Habitat and implementing partners.
                       See the accompanying file CONTRIBUTORS.txt in the root
email                : stdm@unhabitat.org
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
import logging
from collections import OrderedDict
from datetime import (
    date,
    datetime
)

from PyQt4.QtCore import (
    QFile,
    QFileInfo,
    QIODevice
)
from PyQt4.QtXml import (
    QDomDocument,
    QDomElement,
    QDomNode
)

from stdm.data.configuration.stdm_configuration import StdmConfiguration
from stdm.data.configuration.exception import ConfigurationException
from stdm.data.configuration.supporting_document import SupportingDocument
from stdm.data.configuration.entity import Entity
from stdm.data.configuration.entity_relation import EntityRelation
from stdm.data.configuration.profile import Profile
from stdm.data.configuration.value_list import ValueList
from stdm.data.configuration.association_entity import AssociationEntity
from stdm.data.configuration.social_tenure import SocialTenure
from stdm.data.configuration.columns import (
    BaseColumn,
    ForeignKeyColumn
)
from stdm.utils.util import (
    date_from_string,
    datetime_from_string
)

LOGGER = logging.getLogger('stdm')


class ConfigurationFileSerializer(object):
    """
    (De)serializes configuration object from/to a specified file object.
    """
    def __init__(self, path):
        """
        :param path: File location where the configuration will be saved.
        :type path: str
        """
        self.path = path
        self.config = StdmConfiguration.instance()

    def save(self):
        """
        Serialize configuration object to the given file location.
        """
        if self.config.is_null:
            raise ConfigurationException('StdmConfiguration object is null')

        if not self.path:
            raise IOError('File path for saving the configuration is empty.')

        save_file_info = QFileInfo(self.path)

        #Check if the suffix is in the file name
        #TODO: Remove str function
        if not unicode(save_file_info.suffix()).lower != 'stc':
            self.path = u'{0}.{1}'.format(self.path, 'stc')
            save_file_info = QFileInfo(self.path)

        #Test if the file is writeable
        save_file = QFile(self.path)
        if not save_file.open(QIODevice.WriteOnly):
            raise IOError(u'The file cannot be saved in '
                          u'{0}'.format(self.path))

        #Create DOM document and populate it with STDM config properties
        config_document = QDomDocument()
        self.write_xml(config_document)

        if save_file.write(config_document.toByteArray()) == -1:
            raise IOError('The configuration could not be written to file.')

    def write_xml(self, document):
        """
        Populate the DOM document with the configuration properties.
        :param document: DOM document to be updated.
        :type document: QDomDocument
        """
        config_element = document.createElement('Configuration')
        config_element.setAttribute('version', str(self.config.VERSION))

        #Append main element
        document.appendChild(config_element)

        #Append profile information
        for p in self.config.profiles.values():
            ProfileSerializer.write_xml(p, config_element, document)

    def load(self):
        """
        Loads the contents of the configuration file to the corresponding
        instance object.
        """
        if not QFile.exists(self.path):
            raise IOError(u'{0} does not exist. Configuration file cannot be '
                          u'loaded.')

        config_file = QFile(self.path)

        if not config_file.open(QIODevice.ReadOnly):
            raise IOError('Cannot read configuration file.')

        config_doc = QDomDocument()

        status, msg, line, col = config_doc.setContent(config_file)
        if not status:
            raise ConfigurationException(u'Configuration file cannot be '
                                         u'loaded.\n{0}'.format(msg))

        #Load configuration items
        self.read_xml(config_doc)

    def update(self, document):
        """
        Tries to upgrade the configuration file specified in the DOM document
        to the current version.
        :param document: Older version of STDM config.
        :type document: QDomDocument
        :return: True if the upgrade succeeded including the updated document
        object, else False with None document object.
        :rtype: tuple(bool, QDomDocument)
        """
        #TODO: Need to plugin the updater object
        return False, None

    def read_xml(self, document):
        """
        Reads configuration file and loads contents into a configuration
        instance.
        :param document: Main document object containing config information.
        :type document: QDomDocument
        """
        #Reset items in the config file
        self.config._clear()

        #Load items afresh
        #Check tag and version attribute first
        doc_element = document.documentElement()

        if doc_element.isNull():
            #Its an older config file hence, try upgrade
            document = self._update_status(document)

        if not doc_element.hasAttribute('version'):
            #Again, an older version
            document = self._update_status(document)

        #Check version
        config_version = doc_element.attribute('version')
        if config_version:
            config_version = float(config_version)

        else:
            #Fatal error
            raise ConfigurationException('Error extracting version '
                                         'number from the '
                                         'configuration file.')

        if config_version < StdmConfiguration.instance().VERSION:
            #Upgrade configuration
            document = self._update_status(document)

        doc_element = document.documentElement()

        #All should be well at this point so start parsing the items
        self._load_config_items(doc_element)

    def _load_config_items(self, element):
        #Load profiles
        profile_elements = element.elementsByTagName('Profile')

        p_count = profile_elements.count()

        for i in range(p_count):
            profile_element = profile_elements.item(i).toElement()
            profile = ProfileSerializer.read_xml(profile_element, element,
                                                 self.config)

            if not profile is None:
                self.config.add_profile(profile)

            else:
                LOGGER.debug('Empty profile name in the configuration file. '
                             'Profile cannot be loaded.')

    def _update_status(self, document):
        status, doc = self.update(document)

        if not status:
            raise ConfigurationException('Configuration could not be updated. '
                                         'Please contact your system '
                                         'administrator.')

        return doc


def _populate_collections_from_element(element, tag_name, collection):
    group_el = element.firstChildElement(tag_name)

    if not group_el.isNull():
        er_collection = group_el.childNodes()

        for i in range(er_collection.count()):
            er_el = er_collection.item(i).toElement()

            if er_el.hasAttribute('name'):
                name = unicode(er_el.attribute('name'))

                collection[name] = er_el


class ProfileSerializer(object):
    """
    (De)serialize profile information.
    """
    @staticmethod
    def _populate_entity_relations(element, collection):
        #Populate collection
        _populate_collections_from_element(
            element,
            'Relations',
            collection
        )

    @staticmethod
    def _populate_associations(element, collection):
        #Populate collection
        _populate_collections_from_element(
            element,
            AssociationEntitySerializer.GROUP_TAG,
            collection
        )

    @staticmethod
    def read_xml(element, config_element, configuration):
        """
        :param element: Element containing profile information.
        :type element: QDomElement
        :param config_element: Parent configuration element.
        :type config_element: QDomElement
        :param configuration: Current configuration instance.
        :type configuration: StdmConfiguration
        :return: Returns a Profile object using information contained in the
        profile element.
        :rtype: Profile
        """
        profile_name = element.attribute('name', '')
        if not profile_name:
            LOGGER.debug('Empty profile name. Profile will not be loaded.')

            return None

        #TODO: Remove unicode
        profile = Profile(unicode(profile_name), configuration)

        '''
        Now populate the entity relations and associations for use by child
        elements.
        '''
        association_elements = {}
        entity_relation_elements = {}
        ProfileSerializer._populate_associations(element,
                                                 association_elements)
        ProfileSerializer._populate_entity_relations(element,
                                                     entity_relation_elements)

        '''
        We resort to manually loading the entities (and subclasses) which
        have no dependencies to any parents. Start with value lists.
        '''
        value_lists_el = element.firstChildElement(ValueListSerializer.GROUP_TAG)
        if not value_lists_el.isNull():
            ValueListSerializer.read_xml(value_lists_el, profile,
                                         association_elements,
                                         entity_relation_elements)

        deferred_elements = []

        #Process entity elements with no dependency first
        child_nodes = element.childNodes()
        for i in range(child_nodes.count()):
            child_element = child_nodes.item(i).toElement()
            child_tag_name = child_element.tagName()
            item_serializer = EntitySerializerCollection.handler_by_tag_name(
                child_tag_name
            )

            #Hack: Process only entity elements.
            if child_element.tagName() == 'Entity':
                if not item_serializer is None:
                    #Check if element has dependency
                    if not item_serializer.has_dependency(child_element):
                        item_serializer.read_xml(child_element, profile,
                                                 association_elements,
                                                 entity_relation_elements)

                    else:
                        #Queue the item - tuple containing element and serializer
                        deferred_elements.append((child_element, item_serializer))

        #Process deferred items
        for c in deferred_elements:
            el, serializer = c[0], c[1]
            serializer.read_xml(el, profile, association_elements,
                        entity_relation_elements)

        #Set social tenure entities
        str_el = element.firstChildElement('SocialTenure')
        if not str_el.isNull():
            SocialTenureSerializer.read_xml(str_el, profile,
                                            association_elements,
                                            entity_relation_elements)

        return profile

    @staticmethod
    def write_xml(profile, parent_node, document):
        """
        Appends profile information to the parent node.
        :param profile: Profile object
        :type profile: Profile
        :param parent_node: Parent element.
        :type parent_node: QDomNode
        :param document: Represents main document object.
        :type document: QDomDocument
        """
        profile_element = document.createElement('Profile')

        profile_element.setAttribute('name', profile.name)

        #Append entity information
        for e in profile.entities.values():
            item_serializer = EntitySerializerCollection.handler(e.TYPE_INFO)

            if item_serializer:
                item_serializer.write_xml(e, profile_element, document)

        #Append entity relation information
        er_parent_element = document.createElement('Relations')
        for er in profile.relations.values():
            EntityRelationSerializer.write_xml(er, er_parent_element, document)

        profile_element.appendChild(er_parent_element)

        #Append social tenure information
        SocialTenureSerializer.write_xml(profile.social_tenure,
                                         profile_element, document)

        parent_node.appendChild(profile_element)


class SocialTenureSerializer(object):
    """
    (De)serializes social tenure information.
    """
    PARTY = 'party'
    SPATIAL_UNIT = 'spatialUnit'
    TENURE_TYPE = 'tenureTypeList'

    @staticmethod
    def read_xml(child_element, profile, association_elements,
                 entity_relation_elements):
        """
        Reads the social tenure attributes in the child element and set them
        in the profile.
        :param child_element: Element containing social tenure information.
        :type child_element: QDomElement
        :param profile: Profile object whose STR attributes are to be set.
        :type profile: Profile
        """
        party = unicode(child_element.attribute(
            SocialTenureSerializer.PARTY, '')
        )
        spatial_unit = unicode(child_element.attribute(
            SocialTenureSerializer.SPATIAL_UNIT, '')
        )

        #Set STR attributes
        if party:
            profile.set_social_tenure_attr(SocialTenure.PARTY, party)

        if spatial_unit:
            profile.set_social_tenure_attr(SocialTenure.SPATIAL_UNIT,
                                       spatial_unit)

    @staticmethod
    def write_xml(social_tenure, parent_node, document):
        """
        Appends social tenure information to the profile node.
        :param social_tenure: Social tenure object
        :type social_tenure: SocialTenure
        :param parent_node: Parent element.
        :type parent_node: QDomNode
        :param document: Represents main document object.
        :type document: QDomDocument
        """
        social_tenure_element = document.createElement('SocialTenure')

        social_tenure_element.setAttribute(SocialTenureSerializer.PARTY,
                                           social_tenure.party.short_name)
        social_tenure_element.setAttribute(SocialTenureSerializer.SPATIAL_UNIT,
                                        social_tenure.spatial_unit.short_name)
        social_tenure_element.setAttribute(SocialTenureSerializer.TENURE_TYPE,
                                    social_tenure.tenure_type_collection.short_name)

        parent_node.appendChild(social_tenure_element)


class EntitySerializerCollection(object):
    """
    Container for entity-based serializers which are registered using the
    type info of the Entity subclass.
    """
    _registry = OrderedDict()

    @classmethod
    def register(cls):
        if not hasattr(cls, 'ENTITY_TYPE_INFO'):
            return

        EntitySerializerCollection._registry[cls.ENTITY_TYPE_INFO] = cls

    @staticmethod
    def handler(type_info):
        return EntitySerializerCollection._registry.get(type_info, None)

    @classmethod
    def entry_tag_name(cls):
        if hasattr(cls, 'GROUP_TAG'):
            return cls.GROUP_TAG

        return cls.TAG_NAME

    @staticmethod
    def handler_by_tag_name(tag_name):
        handler = [s for s in EntitySerializerCollection._registry.values()
                   if s.entry_tag_name() == tag_name]

        if len(handler) == 0:
            return None

        return handler[0]

    @classmethod
    def has_dependency(cls, element):
        """
        :param element: Element containing entity information.
        :type element: QDomElement
        :return: Return True if the entity element has columns that are
        dependent on other entities such as foreign key columns.Default is
        False.
        :rtype: bool
        """
        return False

    @classmethod
    def group_element(cls, parent_node, document):
        """
        Creates a parent/group element which is then used as the parent node
        for this serializer. If no 'GROUP_TAG' class attribute is specified
        then the profile node is returned.
        :param parent_node: Parent node corresponding to the profile node,
        :type parent_node: QDomNode
        :param document: main document object.
        :type document: QDomDocument
        :return: Prent/group node fpr appending the child node created by
        this serializer.
        :rtype: QDomNode
        """
        if not hasattr(cls, 'GROUP_TAG'):
            return parent_node

        group_tag = getattr(cls, 'GROUP_TAG')

        #Search for group element and create if it does not exist
        group_element = parent_node.firstChildElement(group_tag)

        if group_element.isNull():
            group_element = document.createElement(group_tag)
            parent_node.appendChild(group_element)

        return group_element


class EntitySerializer(EntitySerializerCollection):
    """
    (De)serializes entity information.
    """
    TAG_NAME = 'Entity'

    #Specify attribute names
    GLOBAL = 'global'
    SHORT_NAME = 'shortName'
    NAME = 'name'
    DESCRIPTION = 'description'
    ASSOCIATIVE = 'associative'
    EDITABLE = 'editable'
    CREATE_ID = 'createId'
    PROXY = 'proxy'
    SUPPORTS_DOCUMENTS = 'supportsDocuments'
    ENTITY_TYPE_INFO = 'ENTITY'
    DEPENDENCY_FLAGS = [ForeignKeyColumn.TYPE_INFO]

    @staticmethod
    def read_xml(child_element, profile, association_elements,
                 entity_relation_elements):
        """
        Reads entity information in the entity element and add to the profile.
        :param child_element: Element containing entity information.
        :type child_element: QDomElement
        :param profile: Profile object to be populated with the entity
        information.
        :type profile: Profile
        """
        short_name = unicode(child_element.attribute(
            EntitySerializer.SHORT_NAME, '')
        )
        if short_name:
            optional_args = {}

            #Check global
            is_global = unicode(child_element.attribute(
                EntitySerializer.GLOBAL, '')
            )
            if is_global:
                is_global = _str_to_bool(is_global)
                optional_args['is_global'] = is_global

            #Proxy
            proxy = unicode(child_element.attribute(
                EntitySerializer.PROXY, '')
            )
            if proxy:
                proxy = _str_to_bool(proxy)
                optional_args['is_proxy'] = proxy

            #Create ID
            create_id = unicode(child_element.attribute(
                EntitySerializer.CREATE_ID, '')
            )
            if create_id:
                create_id = _str_to_bool(create_id)
                optional_args['is_proxy'] = create_id

            #Supports documents
            supports_docs = unicode(child_element.attribute(
                EntitySerializer.SUPPORTS_DOCUMENTS, '')
            )
            if supports_docs:
                supports_docs = _str_to_bool(supports_docs)
                optional_args['supports_documents'] = supports_docs

            ent = Entity(short_name, profile, **optional_args)

            #Associative
            associative = unicode(child_element.attribute(
                EntitySerializer.ASSOCIATIVE, '')
            )
            if associative:
                associative = _str_to_bool(associative)
                ent.is_associative = associative

            #Editable
            editable = unicode(child_element.attribute(
                EntitySerializer.EDITABLE, '')
            )
            if editable:
                editable = _str_to_bool(editable)
                ent.user_editable = editable

            #Description
            description = unicode(child_element.attribute(
                EntitySerializer.DESCRIPTION, '')
            )
            ent.description = description

            column_elements = EntitySerializer.column_elements(child_element)

            for ce in column_elements:
                #Just validate that it is a 'Column' element
                if str(ce.tagName()) == 'Column':
                    '''
                    Read element and load the corresponding column object
                    into the entity.
                    '''
                    ColumnSerializerCollection.read_xml(ce, ent,
                                                        association_elements,
                                                        entity_relation_elements)

            profile.add_entity(ent)

    @staticmethod
    def column_elements(entity_element):
        """
        Parses the entity element and returns a list of column elements.
        :param entity_element: Element containing entity information.
        :type entity_element: QDomElement
        :return: A list of elements containing column information.
        :rtype: list
        """
        col_els = []

        cols_group_el = entity_element.firstChildElement('Columns')

        if not cols_group_el.isNull():
            #Populate columns in the entity
            column_elements = cols_group_el.childNodes()

            for i in range(column_elements.count()):
                column_el = column_elements.item(i).toElement()

                col_els.append(column_el)

        return col_els

    @classmethod
    def has_dependency(cls, element):
        """
        :param element: Element containing entity information.
        :type element: QDomElement
        :return: Return True if the entity element has columns that are
        dependent on other entities such as foreign key columns.Default is
        False.
        :rtype: bool
        """
        dependency = False

        column_elements = EntitySerializer.column_elements(element)

        for ce in column_elements:
            if ce.hasAttribute('TYPE_INFO'):
                type_info = unicode(ce.attribute('TYPE_INFO'))

                #Check if the type info is in the flags' list
                if type_info in cls.DEPENDENCY_FLAGS:
                    dependency = True

                    break

        return dependency

    @staticmethod
    def write_xml(entity, parent_node, document):
        """
        ""
        Appends entity information to the profile node.
        :param entity: Social tenure object
        :type entity: SocialTenure
        :param parent_node: Parent element.
        :type parent_node: QDomNode
        :param document: Represents main document object.
        :type document: QDomDocument
        """
        entity_element = document.createElement(EntitySerializer.TAG_NAME)

        #Set entity attributes
        entity_element.setAttribute(EntitySerializer.GLOBAL,
                                    str(entity.is_global))
        entity_element.setAttribute(EntitySerializer.SHORT_NAME,
                                    entity.short_name)
        #Name will be ignored when the deserializing the entity object
        entity_element.setAttribute(EntitySerializer.NAME,
                                    entity.name)
        entity_element.setAttribute(EntitySerializer.DESCRIPTION,
                                    entity.description)
        entity_element.setAttribute(EntitySerializer.ASSOCIATIVE,
                                    str(entity.is_associative))
        entity_element.setAttribute(EntitySerializer.EDITABLE,
                                    str(entity.user_editable))
        entity_element.setAttribute(EntitySerializer.CREATE_ID,
                                    str(entity.create_id_column))
        entity_element.setAttribute(EntitySerializer.PROXY,
                                    str(entity.is_proxy))
        entity_element.setAttribute(EntitySerializer.SUPPORTS_DOCUMENTS,
                                    str(entity.supports_documents))

        #Root columns element
        columns_element = document.createElement('Columns')

        #Append column information
        for c in entity.columns.values():
            column_serializer = ColumnSerializerCollection.handler(c.TYPE_INFO)

            if column_serializer:
                column_serializer.write_xml(c, columns_element, document)

        entity_element.appendChild(columns_element)

        parent_node.appendChild(entity_element)

EntitySerializer.register()


class AssociationEntitySerializer(EntitySerializerCollection):
    """
    (De)serializes association entity information.
    """
    GROUP_TAG = 'Associations'
    TAG_NAME = 'Association'

    #Attribute names
    FIRST_PARENT = 'firstParent'
    SECOND_PARENT = 'secondParent'

    #Corresponding type info to (de)serialize
    ENTITY_TYPE_INFO = 'ASSOCIATION_ENTITY'

    @staticmethod
    def read_xml(element, profile, association_elements,
                 entity_relation_elements):
        """
        Reads association information from the element.
        :param child_element: Element containing association entity
        information.
        :type child_element: QDomElement
        :param profile: Profile object to be populated with the association
        entity information.
        :type profile: Profile
        :return: Association entity object.
        :rtype: AssociationEntity
        """
        ae = None

        short_name = element.attribute(EntitySerializer.SHORT_NAME, '')
        if short_name:
            ae = AssociationEntity(unicode(short_name), profile)

            first_parent = element.attribute(
                AssociationEntitySerializer.FIRST_PARENT, '')
            second_parent = element.attribute(
                AssociationEntitySerializer.SECOND_PARENT, '')

            ae.first_parent = unicode(first_parent)
            ae.second_parent = unicode(second_parent)

        return ae

    @staticmethod
    def write_xml(association_entity, parent_node, document):
        """
        ""
        Appends association entity information to the profile node.
        :param value_list: Association entity object
        :type value_list: AssociationEntity
        :param parent_node: Parent element.
        :type parent_node: QDomNode
        :param document: Represents main document object.
        :type document: QDomDocument
        """
        assoc_entity_element = document.createElement(AssociationEntitySerializer.TAG_NAME)

        assoc_entity_element.setAttribute(EntitySerializer.NAME,
                                          association_entity.name)
        assoc_entity_element.setAttribute(EntitySerializer.SHORT_NAME,
                                          association_entity.short_name)
        assoc_entity_element.setAttribute(AssociationEntitySerializer.FIRST_PARENT,
                                          association_entity.first_parent.short_name)
        assoc_entity_element.setAttribute(AssociationEntitySerializer.SECOND_PARENT,
                                          association_entity.second_parent.short_name)

        group_node = AssociationEntitySerializer.group_element(parent_node, document)

        group_node.appendChild(assoc_entity_element)

AssociationEntitySerializer.register()


class ValueListSerializer(EntitySerializerCollection):
    """
    (De)serializes ValueList information.
    """
    GROUP_TAG = 'ValueLists'
    TAG_NAME = 'ValueList'
    CODE_VALUE_TAG = 'CodeValue'

    #Attribute names
    NAME = 'name'
    CV_CODE = 'code'
    CV_VALUE = 'value'

    #Corresponding type info to (de)serialize
    ENTITY_TYPE_INFO = 'VALUE_LIST'

    @staticmethod
    def read_xml(child_element, profile, association_elements,
                 entity_relation_elements):
        """
        Reads the items in the child list element and add to the profile.
        If child element is a group element then children nodes are also
        extracted.
        :param child_element: Element containing value list information.
        :type child_element: QDomElement
        :param profile: Profile object to be populated with the value list
        information.
        :type profile: Profile
        """
        value_list_elements = child_element.elementsByTagName(
            ValueListSerializer.TAG_NAME
        )

        for i in range(value_list_elements.count()):
            value_list_el = value_list_elements.item(i).toElement()
            name = value_list_el.attribute('name', '')
            if name:
                value_list = ValueList(unicode(name), profile)

                #Get code values
                cd_elements = value_list_el.elementsByTagName(
                    ValueListSerializer.CODE_VALUE_TAG
                )

                for c in range(cd_elements.count()):
                    cd_el = cd_elements.item(c).toElement()
                    code = cd_el.attribute(ValueListSerializer.CV_CODE, '')
                    value = cd_el.attribute(ValueListSerializer.CV_VALUE, '')

                    #Add lookup items only when value is not empty
                    if value:
                        value_list.add_value(value, code)

                #Add value list to the profile
                profile.add_entity(value_list)

    #Specify attribute names
    @staticmethod
    def write_xml(value_list, parent_node, document):
        """
        ""
        Appends value list information to the profile node.
        :param value_list: Value list object
        :type value_list: ValueList
        :param parent_node: Parent element.
        :type parent_node: QDomNode
        :param document: Represents main document object.
        :type document: QDomDocument
        """
        value_list_element = document.createElement(ValueListSerializer.TAG_NAME)

        value_list_element.setAttribute(ValueListSerializer.NAME,
                                        value_list.short_name)

        #Add code value elements
        for cv in value_list.values.values():
            cd_element = document.createElement(ValueListSerializer.CODE_VALUE_TAG)

            cd_element.setAttribute(ValueListSerializer.CV_VALUE, cv.value)
            cd_element.setAttribute(ValueListSerializer.CV_CODE, cv.code)

            value_list_element.appendChild(cd_element)

        group_node = ValueListSerializer.group_element(parent_node, document)

        group_node.appendChild(value_list_element)

ValueListSerializer.register()


class EntityRelationSerializer(object):
    """
    (De)serializes EntityRelation information.
    """
    TAG_NAME = 'EntityRelation'

    NAME = 'name'
    PARENT = 'parent'
    PARENT_COLUMN = 'parentColumn'
    CHILD = 'child'
    CHILD_COLUMN = 'childColumn'

    @staticmethod
    def read_xml(element, profile, association_elements,
                 entity_relation_elements):
        """
        Reads entity relation information from the element object.
        :param element: Element object containing entity relation information.
        :type element: QDomElement
        :param profile: Profile object that the entity relations belongs to.
        :type profile: Profile
        :param association_elements: Collection of QDomElements containing
        association entity information.
        :type association_elements: dict
        :param entity_relation_elements: Collection of QDomElements
        containing entity relation information.
        :type entity_relation_elements: dict
        :return: Returns an EntityRelation object constructed from the
        information contained in the element.
        :rtype: EntityRelation
        """
        args = {}
        args['parent'] = unicode(element.attribute('parent', ''))
        args['child'] = unicode(element.attribute('child', ''))
        args['parent_column'] = unicode(element.attribute('parentColumn', ''))
        args['child_column'] = unicode(element.attribute('childColumn', ''))

        er = EntityRelation(profile, **args)

        return er

    @staticmethod
    def write_xml(entity_relation, parent_node, document):
        """
        Appends entity relation information to the parent node.
        :param entity_relation: Enrity relation object.
        :type entity_relation: EntityRelation
        :param parent_node: Parent node.
        :type parent_node: QDomNode
        :param document: Main document object
        :type document: QDomDocument
        """
        er_element = document.createElement(EntityRelationSerializer.TAG_NAME)

        #Set attributes
        er_element.setAttribute(EntityRelationSerializer.NAME,
                                entity_relation.name)
        er_element.setAttribute(EntityRelationSerializer.PARENT,
                                entity_relation.parent.short_name)
        er_element.setAttribute(EntityRelationSerializer.PARENT_COLUMN,
                                entity_relation.parent_column)
        er_element.setAttribute(EntityRelationSerializer.CHILD,
                                entity_relation.child.short_name)
        er_element.setAttribute(EntityRelationSerializer.CHILD_COLUMN,
                                entity_relation.child_column)

        parent_node.appendChild(er_element)


class ColumnSerializerCollection(object):
    """
    Container for column-based serializers which are registered using the
    type info of the column subclass.
    """
    _registry = {}
    TAG_NAME = 'Column'

    #Attribute names
    DESCRIPTION = 'description'
    NAME = 'name'
    INDEX = 'index'
    MANDATORY = 'mandatory'
    SEARCHABLE = 'searchable'
    UNIQUE = 'unique'
    USER_TIP = 'tip'
    MINIMUM = 'minimum'
    MAXIMUM = 'maximum'

    @classmethod
    def register(cls):
        if not hasattr(cls, 'COLUMN_TYPE_INFO'):
            return

        ColumnSerializerCollection._registry[cls.COLUMN_TYPE_INFO] = cls

    @staticmethod
    def handler_by_element(element):
        t_info = str(ColumnSerializerCollection.type_info(element))

        if not t_info:
            return None

        return ColumnSerializerCollection.handler(t_info)

    @staticmethod
    def type_info(element):
        return element.attribute('TYPE_INFO', '')

    @staticmethod
    def read_xml(element, entity, association_elements,
                 entity_relation_elements):
        column_handler = ColumnSerializerCollection.handler_by_element(
            element
        )

        if not column_handler is None:
            column_handler.read(element, entity, association_elements,
                 entity_relation_elements)

    @classmethod
    def read(cls, element, entity, association_elements,
             entity_relation_elements):
        col_type_info = str(ColumnSerializerCollection.type_info(element))
        if not col_type_info:
            return

        #Get column attributes
        name = unicode(element.attribute(ColumnSerializerCollection.NAME, ''))
        if not name:
            return

        kwargs = {}

        #Description
        description = unicode(
            element.attribute(ColumnSerializerCollection.DESCRIPTION, '')
            )
        kwargs['description'] = description

        #Index
        index = unicode(
            element.attribute(ColumnSerializerCollection.INDEX, 'False')
        )
        kwargs['index'] = _str_to_bool(index)

        #Mandatory
        mandatory = unicode(
            element.attribute(ColumnSerializerCollection.MANDATORY, 'False')
        )
        kwargs['mandatory'] = _str_to_bool(mandatory)

        #Searchable
        searchable = unicode(
            element.attribute(ColumnSerializerCollection.SEARCHABLE, 'False')
        )
        kwargs['searchable'] = _str_to_bool(searchable)

        #Unique
        unique = unicode(
            element.attribute(ColumnSerializerCollection.UNIQUE, 'False')
        )
        kwargs['unique'] = _str_to_bool(unique)

        #User tip
        user_tip = unicode(
            element.attribute(ColumnSerializerCollection.USER_TIP, '')
        )
        kwargs['user_tip'] = user_tip

        #Minimum
        if element.hasAttribute(ColumnSerializerCollection.MINIMUM):
            minimum = element.attribute(ColumnSerializerCollection.MINIMUM)
            '''
            The value is not set if an exception is raised. Type will
            use defaults.
            '''
            try:
                kwargs['minimum'] = cls._convert_bounds_type(minimum)
            except ValueError:
                pass

        #Maximum
        if element.hasAttribute(ColumnSerializerCollection.MAXIMUM):
            maximum = element.attribute(ColumnSerializerCollection.MAXIMUM)

            try:
                kwargs['maximum'] = cls._convert_bounds_type(maximum)
            except ValueError:
                pass

        #Mandatory arguments
        args = [name, entity]

        #Custom arguments provided by subclasses
        custom_args, custom_kwargs = cls._obj_args(args, kwargs, element,
                                                   association_elements,
                                                   entity_relation_elements)

        #Get column type based on type info
        column_cls = BaseColumn.column_type(col_type_info)

        if not column_cls is None:
            column = column_cls(*custom_args, **custom_kwargs)

            #Append column to the entity
            entity.add_column(column)

    @classmethod
    def _obj_args(cls, args, kwargs, element, associations, entity_relations):
        """
        To be implemented by subclasses if they want to pass additional
        or modify existing arguments in the class constructor of the given
        column type.
        Default implementation returns the default arguments that were
        specified in the function.
        """
        return args, kwargs

    @classmethod
    def _convert_bounds_type(cls, value):
        """
        Converts string value of the minimum/maximum value to the correct
        type e.g. string to date, string to int etc.
        Default implementation returns the original value as a string.
        """
        return value

    @classmethod
    def write_xml(cls, column, parent_node, document):
        col_element = document.createElement(cls.TAG_NAME)

        #Append general column information
        col_element.setAttribute('TYPE_INFO', cls.COLUMN_TYPE_INFO)
        col_element.setAttribute(ColumnSerializerCollection.DESCRIPTION,
                                 column.description)
        col_element.setAttribute(ColumnSerializerCollection.NAME, column.name)
        col_element.setAttribute(ColumnSerializerCollection.INDEX,
                                 str(column.index))
        col_element.setAttribute(ColumnSerializerCollection.MANDATORY,
                                 str(column.mandatory))
        col_element.setAttribute(ColumnSerializerCollection.SEARCHABLE,
                                 str(column.searchable))
        col_element.setAttribute(ColumnSerializerCollection.UNIQUE,
                                 str(column.unique))
        col_element.setAttribute(ColumnSerializerCollection.USER_TIP,
                                 column.user_tip)

        if hasattr(column, 'minimum'):
            col_element.setAttribute(ColumnSerializerCollection.MINIMUM,
                                     str(column.minimum))

        if hasattr(column, 'maximum'):
            col_element.setAttribute(ColumnSerializerCollection.MAXIMUM,
                                     str(column.maximum))

        #Append any additional information defined by subclasses.
        cls._write_xml(column, col_element, document)

        parent_node.appendChild(col_element)

    @classmethod
    def _write_xml(cls, column, column_element, document):
        """
        To be implemented by subclasses if they want to append additional
        information to the column element. Base implementation does nothing.
        """
        pass

    @staticmethod
    def handler(type_info):
        return ColumnSerializerCollection._registry.get(type_info, None)


class TextColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes text column type.
    """
    COLUMN_TYPE_INFO = 'TEXT'

TextColumnSerializer.register()


class VarCharColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes VarChar column type.
    """
    COLUMN_TYPE_INFO = 'VARCHAR'

    @classmethod
    def _convert_bounds_type(cls, value):
        return int(value)

VarCharColumnSerializer.register()


class TextColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes VarChar column type.
    """
    COLUMN_TYPE_INFO = 'TEXT'

    @classmethod
    def _convert_bounds_type(cls, value):
        return int(value)

TextColumnSerializer.register()


class IntegerColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes integer column type.
    """
    COLUMN_TYPE_INFO = 'BIGINT'

    @classmethod
    def _convert_bounds_type(cls, value):
        return int(value)

IntegerColumnSerializer.register()


class DoubleColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes double column type.
    """
    COLUMN_TYPE_INFO = 'DOUBLE'

    @classmethod
    def _convert_bounds_type(cls, value):
        return float(value)

DoubleColumnSerializer.register()


class SerialColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes serial/auto-increment column type.
    """
    COLUMN_TYPE_INFO = 'SERIAL'

SerialColumnSerializer.register()


class DateColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes date column type.
    """
    COLUMN_TYPE_INFO = 'DATE'

    @classmethod
    def _convert_bounds_type(cls, value):
        return date_from_string(value)

DateColumnSerializer.register()


class DateTimeColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes date time column type.
    """
    COLUMN_TYPE_INFO = 'DATETIME'

    @classmethod
    def _convert_bounds_type(cls, value):
        return datetime_from_string(value)

DateTimeColumnSerializer.register()


class YesNoColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes yes/no column type.
    """
    COLUMN_TYPE_INFO = 'YES_NO'

YesNoColumnSerializer.register()


class GeometryColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes geometry column type.
    """
    COLUMN_TYPE_INFO = 'GEOMETRY'
    GEOM_TAG = 'Geometry'

    #Attribute names
    SRID = 'srid'
    GEOMETRY_TYPE = 'type'

    @classmethod
    def _obj_args(cls, args, kwargs, element, assoc_elements,
                  entity_relation_elements):
        #Include the geometry type and SRID in the arguments.
        geom_el = element.firstChildElement(GeometryColumnSerializer.GEOM_TAG)
        if not geom_el.isNull():
            geom_type = int(geom_el.attribute(
                GeometryColumnSerializer.GEOMETRY_TYPE,
                '2'
            ))

            srid = int(geom_el.attribute(
                GeometryColumnSerializer.SRID,
                '4326'
            ))

            #Append additional geometry information
            args.append(geom_type)
            kwargs['srid'] = srid

        return args, kwargs

    @classmethod
    def _write_xml(cls, column, column_element, document):
        #Append custom geometry information
        geom_element = \
            document.createElement(GeometryColumnSerializer.GEOM_TAG)
        geom_element.setAttribute(GeometryColumnSerializer.SRID,
                                    str(column.srid))
        geom_element.setAttribute(GeometryColumnSerializer.GEOMETRY_TYPE,
                                    column.geom_type)

        column_element.appendChild(geom_element)

GeometryColumnSerializer.register()


class ForeignKeyColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes foreign key column type.
    """
    COLUMN_TYPE_INFO = 'FOREIGN_KEY'
    RELATION_TAG = 'Relation'

    @classmethod
    def _obj_args(cls, args, kwargs, element, assoc_elements,
                  entity_relation_elements):
        #Include entity relation information.
        relation_el = element.firstChildElement(
            ForeignKeyColumnSerializer.RELATION_TAG
        )

        if not relation_el.isNull():
            relation_name = unicode(relation_el.attribute('name', ''))
            er_element = entity_relation_elements.get(relation_name, None)

            if not er_element is None:
                profile = args[1].profile
                er = EntityRelationSerializer.read_xml(er_element, profile,
                                                       assoc_elements,
                                                       entity_relation_elements)

                status, msg = er.valid()
                if status:
                    #Append entity relation information
                    kwargs['entity_relation'] = er

        return args, kwargs

    @classmethod
    def _write_xml(cls, column, column_element, document):
        #Append entity relation name
        fk_element = \
            document.createElement(ForeignKeyColumnSerializer.RELATION_TAG)
        fk_element.setAttribute('name', column.entity_relation.name)

        column_element.appendChild(fk_element)

ForeignKeyColumnSerializer.register()


class LookupColumnSerializer(ForeignKeyColumnSerializer):
    """
    (De)serializes lookup column type.
    """
    COLUMN_TYPE_INFO = 'LOOKUP'

LookupColumnSerializer.register()


class AdminSpatialUnitColumnSerializer(ForeignKeyColumnSerializer):
    """
    (De)serializes administrative spatial unit column type.
    """
    COLUMN_TYPE_INFO = 'ADMIN_SPATIAL_UNIT'

    @classmethod
    def _obj_args(cls, args, kwargs, element, assoc_elements,
                  entity_relation_elements):
        #We need to remove the name of the entity since it is already preset.
        col_name = args.pop(0)

        return args, kwargs

AdminSpatialUnitColumnSerializer.register()


class MultipleSelectColumnSerializer(ColumnSerializerCollection):
    """
    (De)serializes multiple select column type information.
    """
    COLUMN_TYPE_INFO = 'MULTIPLE_SELECT'

    ASSOCIATION_TAG = 'associationEntity'

    @classmethod
    def _obj_args(cls, args, kwargs, element, associations, entity_relations):
        #Include entity relation information.
        assoc_el = element.firstChildElement(
            MultipleSelectColumnSerializer.ASSOCIATION_TAG
        )

        if not assoc_el.isNull():
            assoc_name = unicode(assoc_el.attribute('name', ''))
            association_element = associations.get(assoc_name, None)

            if not association_element is None:
                first_parent = unicode(association_element.attribute(
                    AssociationEntitySerializer.FIRST_PARENT, '')
                )

                if first_parent:
                    #Include the name of the first_parent table in kwargs
                    kwargs['first_parent'] = first_parent

        return args, kwargs

    @classmethod
    def _write_xml(cls, column, column_element, document):
        #Append association entity short name
        association_entity_element = \
            document.createElement(MultipleSelectColumnSerializer.ASSOCIATION_TAG)
        association_entity_element.setAttribute('name',
                                                column.association.name)

        column_element.appendChild(association_entity_element)

MultipleSelectColumnSerializer.register()


def _str_to_bool(bool_str):
    if len(bool_str) > 1:
        bool_str = bool_str[0]
    return unicode(bool_str).upper() == 'T'







