# Copyright 2017, Rackspace US, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# (c) 2017, Kevin Carter <kevin.carter@rackspace.com>

import datetime
import re
import json

import cassandra
from cassandra.cqlengine import connection
from cassandra.auth import PlainTextAuthProvider

from oslo_config import cfg
from oslo_log import log as logging

import models

from cruton import exceptions as exps

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class Exceptions(object):
    """General exceptions class.

    This class puulls in the execptions from the driver in a method allowing
    it to be universally accessed.
    """
    InvalidRequest = cassandra.InvalidRequest


def _auth_provider(conf):
    """Return an authentication object."""
    username = conf.get('username')
    password = conf.get('password')
    if username and password:
        return PlainTextAuthProvider(
            username=conf['username'],
            password=conf['password']
        )


def close(conn):
    """Close any open Session."""
    conn.shutdown()


def setup():
    """Run the connection setup."""
    cassandra_conf = CONF['data_store']
    cluster_connect = dict(
        hosts=cassandra_conf['cluster_node'],
        port=cassandra_conf['port'],
        default_keyspace=cassandra_conf['keyspace'],
        executor_threads=6,
        retry_connect=True,
        lazy_connect=True
    )
    auth_provider = _auth_provider(conf=cassandra_conf)
    if auth_provider:
        cluster_connect['auth_provider'] = auth_provider
    connection.setup(**cluster_connect)
    return connection


def convert_from_json(q_got):
    """Return a dict from a JSON variable.

    :param q_got: retrieved query
    :type q_got: ``dict``
    :return: dict
    """
    for k, v in q_got.get('vars', {}).items():
        try:
            q_got['vars'][k] = json.loads(v)
        except ValueError:
            pass
    else:
        return q_got


def deep_search(data_structure, criteria, fuzzy=False):
    """Recursively merge new_items into base_items.

    This search function will dive through most data structures and look
    hte provided criteria. If the criteria is found the function will
    return True otherwise it will return False.

    :param data_structure: Data structure to search through.
    :param criteria: Item to search for.
    :param fuzzy: Enables or disables a fuzzy search.
    :returns: ``bool``
    """
    def _criteria_in_value(c, v):
        if fuzzy:
            try:
                c = c.lower()
                v = v.lower()
                return c == v or c in v
            except (TypeError, AttributeError):
                pass
        else:
            return c == v

    if isinstance(data_structure, (list, set, tuple)):
        for item in data_structure:
            if deep_search(data_structure=item, criteria=criteria, fuzzy=fuzzy):
                return True
    elif isinstance(data_structure, dict):
        for key, value in data_structure.items():
            if isinstance(value, (dict, list, set, tuple)):
                if deep_search(data_structure=value, criteria=criteria, fuzzy=fuzzy):
                    return True
            elif not isinstance(value, int) and (',' in value or '\n' in value):
                values = [i.lower() for i in re.split(',|\n', value) if i]
                if deep_search(data_structure=values, criteria=criteria, fuzzy=fuzzy):
                    return True
            elif isinstance(value, int):
                if criteria == value:
                    return True
            else:
                if _criteria_in_value(c=criteria, v=value):
                    return True
    else:
        if _criteria_in_value(c=criteria, v=data_structure):
            return True
    return False


def _search(self, q, search_items, lookup_params, fuzzy):
    """Search query results."""
    q_list = list()
    for k, v in search_items:
        print('SHIT', k, v)
        if v:
            lookup_params[k] = v

    all_list = [convert_from_json(q_got=dict(i)) for i in q.all()]
    for item in all_list:
        if not lookup_params:
            q_list.append(self._friendly_return(item))
            continue

        for k, v in lookup_params.items():
            q_item = item.get(k)
            if q_item:
                if deep_search(data_structure=q_item, criteria=v, fuzzy=fuzzy):
                    q_list.append(self._friendly_return(item))
                    break
    else:
        return q_list


def _get_search(self, model, ent_id=None, env_id=None, dev_id=None):
    """Retrieve a list of entities.

    :param self: Class object
    :type self: object || query
    :param model: DB Model object
    :type model: object || query
    :param ent_id: Entity ID
    :type ent_id: string
    :param env_id: Environment ID
    :type env_id: string
    :param dev_id: Device ID
    :type dev_id: string
    :return: list
    """

    def run_query():
        if lookup_params:
            if fuzzy:
                return model.objects.filter(**lookup_params).allow_filtering()
            else:
                return model.objects.filter(**lookup_params).allow_filtering()
        else:
            return model.objects()

    # This creates a single use search criteria hash which is used to look
    #  inside a list or other hashable type.
    search_dict = {
        'tag': {
            'opt': self.query.pop('tag', None),
            'parent': 'tags'
        },
        'port': {
            'opt': self.query.pop('port', None),
            'parent': 'ports'
        },
        'var': {
            'opt': self.query.pop('var', None),
            'parent': 'vars'
        },
        'link': {
            'opt': self.query.pop('link', None),
            'parent': 'links'
        },
        'contact': {
            'opt': self.query.pop('contact', None),
            'parent': 'contacts'
        }
    }

    lookup_params = dict()
    if ent_id:
        lookup_params['ent_id'] = ent_id
    if env_id:
        lookup_params['env_id'] = env_id
    if dev_id:
        lookup_params['dev_id'] = dev_id

    fuzzy = self.query.pop('fuzzy', False)
    try:
        q = run_query()
        if any([ent_id, env_id, dev_id]) and not fuzzy:
            return [convert_from_json(q_got=dict(q.get()))]
    except Exception as exp:
        LOG.warn(exps.log_exception(exp))
        return list()
    else:
        return _search(
            self=self,
            q=q,
            search_items=[(v['parent'], v['opt']) for k, v in search_dict.items() if v['opt']],
            lookup_params=self.query,
            fuzzy=fuzzy
        )


def get_device(self, ent_id, env_id, dev_id=None):
    """Retrieve a list of entities.

    :param self: Class object
    :type self: object || query
    :param ent_id: Entity ID
    :type ent_id: string
    :param env_id: Environment ID
    :type env_id: string
    :param dev_id: Device ID
    :type dev_id: string
    :return: list
    """
    return _get_search(
        self=self,
        model=models.Devices,
        ent_id=ent_id,
        env_id=env_id,
        dev_id=dev_id
    )


def get_environment(self, ent_id, env_id=None):
    """Retrieve a list of entities.

    :param self: Class object
    :type self: object || query
    :param ent_id: Entity ID
    :type ent_id: string
    :param env_id: Environment ID
    :type env_id: string
    :return: list
    """
    return _get_search(
        self=self,
        model=models.Environments,
        ent_id=ent_id,
        env_id=env_id
    )


def get_entity(self, ent_id):
    """Retrieve a list of entities.

    :param self: Class object
    :type self: object || query
    :param ent_id: Entity ID
    :type ent_id: string
    :return: list
    """
    return _get_search(
        self=self,
        model=models.Entities,
        ent_id=ent_id
    )


def _put_item(args, query, ent_id=None, env_id=None, dev_id=None, update=False):
    """PUT an item.

    :param ent_id: Entity ID
    :type ent_id: string
    :param env_id: Environment ID
    :type env_id: string
    :param dev_id: Device ID
    :type dev_id: string
    :param args: Dictionary arguments
    :type args: dict
    :return: dict
    """
    for k, v in args.get('vars', {}).items():
        if isinstance(v, (dict, list)):
            args['vars'][k] = json.dumps(v)

    args['updated_at'] = datetime.datetime.utcnow()
    if update:
        query.update(**args)
    else:
        args['created_at'] = args['updated_at']
        if ent_id:
            args['ent_id'] = ent_id
        if env_id:
            args['env_id'] = env_id
        if dev_id:
            args['dev_id'] = dev_id
        query.create(**args)
    return args


def _update_tags(query, args):
    """Coalesce tags

    :param query: Class object
    :type query: object || query
    :param args: Dictionary arguments
    :type args: dict
    :return:
    """
    try:
        r_dev = query.get()
        args['tags'] = set(list(r_dev['tags']) + list(args.pop('tags', list())))
    except Exception as exp:
        LOG.warn(exps.log_exception(exp))
        return args, False
    else:
        return args, True


def _put_links(end_q, endpoint, end_id, args, cluster_keys):
    """PUT Links back.

    :param end_q: Class object
    :type end_q: object || query
    :param endpoint: Environment ID
    :type endpoint: string
    :param end_id: Entity ID
    :type end_id: string
    :param args: Dictionary arguments
    :type args: dict
    """
    # Post back a link within the entity to the new environment
    # links = end_q.get('links')
    links = {}
    if endpoint.endswith(end_id):
        links[end_id] = endpoint
    else:
        links[end_id] = '%s/%s' % (endpoint, end_id)

    end_q(**cluster_keys).update(**{'links': links, 'updated_at': args['updated_at']})


def put_device(self, ent_id, env_id, dev_id, args):
    """PUT an entity.

    :param self: Class object
    :type self: object || query
    :param ent_id: Entity ID
    :type ent_id: string
    :param env_id: Environment ID
    :type env_id: string
    :param dev_id: Device ID
    :type dev_id: string
    :param args: Dictionary arguments
    :type args: dict
    :return: string, int
    """
    try:
        q_env = models.Environments.objects(
            env_id=env_id
        ).limit(1)
        q_env.get()
    except models.Environments.DoesNotExist as exp:
        LOG.warn(exps.log_exception(exp))
        return {'ERROR': 'Environment [%s] was not found' % env_id}, 412

    try:
        q_ent = models.Entities.objects(
            ent_id=ent_id
        ).limit(1)
        q_ent.get()
    except models.Entities.DoesNotExist as exp:
        LOG.critical(exps.log_exception(exp))
        return {'ERROR': 'Entity [%s] was not found' % ent_id}, 412

    q_dev = models.Devices.objects(
        env_id=env_id,
        ent_id=ent_id,
        dev_id=dev_id
    ).limit(1)

    args, update = _update_tags(
        query=q_dev,
        args=args
    )

    try:
        # Write data to the backend
        args = _put_item(
            args=self.convert(args),
            query=q_dev,
            ent_id=ent_id,
            env_id=env_id,
            dev_id=dev_id,
            update=update
        )

        _put_links(
            end_q=q_env,
            endpoint=self.endpoint,
            end_id=dev_id,
            args=args,
            cluster_keys={'ent_id': ent_id}
        )
    except Exception as exp:
        LOG.critical(exps.log_exception(exp))
        return {'ERROR': str(exp)}, 400
    else:
        return self._friendly_return(args), 200


def put_environment(self, ent_id, env_id, args):
    """PUT an entity.

    :param self: Class object
    :type self: object || query
    :param ent_id: Entity ID
    :type ent_id: string
    :param env_id: Environment ID
    :type env_id: string
    :param args: Dictionary arguments
    :type args: dict
    :return: string, int
    """
    try:
        q_ent = models.Entities.objects(
            ent_id=ent_id
        ).limit(1)
        q_ent.get()
    except models.Entities.DoesNotExist as exp:
        LOG.critical(exps.log_exception(exp))
        return {'ERROR': 'Entity [%s] was not found' % ent_id}, 412

    q_env = models.Environments.objects(
        env_id=env_id,
        ent_id=ent_id
    ).limit(1)

    args, update = _update_tags(
        query=q_env,
        args=args
    )

    try:
        # Write data to the backend
        args = _put_item(
            args=args,
            query=q_env,
            ent_id=ent_id,
            env_id=env_id,
            update=update
        )

        _put_links(
            end_q=q_ent,
            endpoint=self.endpoint,
            end_id=env_id,
            args=args,
            cluster_keys={}
        )
    except Exception as exp:
        LOG.critical(exps.log_exception(exp))
        return {'ERROR': str(exp)}, 400
    else:
        return self._friendly_return(args), 200


def put_entity(self, ent_id, args):
    """PUT an entity.

    :param self: object
    :param ent_id: Entity ID
    :type ent_id: string
    :param args: Dictionary arguments
    :type ent_id: dict
    :return: string, int
    """
    q_ent = models.Entities.objects(
        ent_id=ent_id
    ).limit(1)

    args, update = _update_tags(
        query=q_ent,
        args=args
    )

    try:
        # Write data to the backend
        args = _put_item(
            args=args,
            query=q_ent,
            ent_id=ent_id,
            update=update,
        )
    except Exception as exp:
        LOG.critical(exps.log_exception(exp))
        return {'ERROR': str(exp)}, 400
    else:
        return self._friendly_return(args), 200
