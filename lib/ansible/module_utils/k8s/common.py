#
#  Copyright 2018 Red Hat | Ansible
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import, division, print_function

import os
import copy

from dictdiffer import diff

from ansible.module_utils.six import iteritems
from ansible.module_utils.basic import AnsibleModule

try:
    import kubernetes
    from openshift.dynamic import DynamicClient
    HAS_K8S_MODULE_HELPER = True
except ImportError:
    HAS_K8S_MODULE_HELPER = False

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

ARG_ATTRIBUTES_BLACKLIST = ('property_path',)

COMMON_ARG_SPEC = {
    'state': {
        'default': 'present',
        'choices': ['present', 'absent'],
    },
    'force': {
        'type': 'bool',
        'default': False,
    },
    'resource_definition': {
        'type': 'dict',
        'aliases': ['definition', 'inline']
    },
    'src': {
        'type': 'path',
    },
    'kind': {},
    'name': {},
    'namespace': {},
    'api_version': {
        'default': 'v1',
        'aliases': ['api', 'version'],
    },
}

AUTH_ARG_SPEC = {
    'kubeconfig': {
        'type': 'path',
    },
    'context': {},
    'host': {},
    'api_key': {
        'no_log': True,
    },
    'username': {},
    'password': {
        'no_log': True,
    },
    'verify_ssl': {
        'type': 'bool',
    },
    'ssl_ca_cert': {
        'type': 'path',
    },
    'cert_file': {
        'type': 'path',
    },
    'key_file': {
        'type': 'path',
    },
}

class K8sAnsibleMixin(object):
    _argspec_cache = None

    @property
    def argspec(self):
        """
        Introspect the model properties, and return an Ansible module arg_spec dict.
        :return: dict
        """
        if self._argspec_cache:
            return self._argspec_cache
        argument_spec = copy.deepcopy(COMMON_ARG_SPEC)
        argument_spec.update(copy.deepcopy(AUTH_ARG_SPEC))
        self._argspec_cache = argument_spec
        return self._argspec_cache

    def get_api_client(self, **auth):
        auth_args = AUTH_ARG_SPEC.keys()

        auth = auth or getattr(self, 'params', {})

        configuration = kubernetes.client.Configuration()
        for key, value in iteritems(auth):
            if key in auth_args and value is not None:
                if key == 'api_key':
                    setattr(configuration, key, {'authorization': "Bearer {}".format(value)})
                else:
                    setattr(configuration, key, value)
            elif key in auth_args and value is None:
                env_value = os.getenv('K8S_AUTH_{}'.format(key.upper()), None)
                if env_value is not None:
                    setattr(configuration, key, env_value)

        kubernetes.client.Configuration.set_default(configuration)

        if auth.get('username') and auth.get('password') and auth.get('host'):
            auth_method = 'params'
        elif auth.get('api_key') and auth.get('host'):
            auth_method = 'params'
        elif auth.get('kubeconfig') or auth.get('context'):
            auth_method = 'file'
        else:
            auth_method = 'default'

        # First try to do incluster config, then kubeconfig
        if auth_method == 'default':
            try:
                kubernetes.config.load_incluster_config()
                return DynamicClient(kubernetes.client.ApiClient())
            except kubernetes.config.ConfigException:
                return DynamicClient(self.client_from_kubeconfig(auth.get('kubeconfig'), auth.get('context')))

        if auth_method == 'file':
            return DynamicClient(self.client_from_kubeconfig(auth.get('kubeconfig'), auth.get('context')))

        if auth_method == 'params':
            return DynamicClient(kubernetes.client.ApiClient(configuration))

    def client_from_kubeconfig(self, config_file, context):
        try:
            return kubernetes.config.new_client_from_config(config_file, context)
        except (IOError, kubernetes.config.ConfigException):
            # If we failed to load the default config file then we'll return
            # an empty configuration
            # If one was specified, we will crash
            if not config_file:
                return kubernetes.client.ApiClient()
            raise

    def remove_aliases(self):
        """
        The helper doesn't know what to do with aliased keys
        """
        for k, v in iteritems(self.argspec):
            if 'aliases' in v:
                for alias in v['aliases']:
                    if alias in self.params:
                        self.params.pop(alias)

    def load_resource_definitions(self, src):
        """ Load the requested src path """
        result = None
        path = os.path.normpath(src)
        if not os.path.exists(path):
            self.fail_json(msg="Error accessing {0}. Does the file exist?".format(path))
        try:
            with open(path, 'r') as f:
                result = list(yaml.safe_load_all(f))
        except (IOError, yaml.YAMLError) as exc:
            self.fail_json(msg="Error loading resource_definition: {0}".format(exc))
        return result

    @staticmethod
    def diff_objects(existing, new):

        def get_shared_attrs(o1, o2):
            shared_attrs = {}
            for k, v in o2.items():
                if isinstance(v, dict):
                    shared_attrs[k] = get_shared_attrs(o1.get(k, {}), v)
                else:
                    shared_attrs[k] = o1.get(k)
            return shared_attrs

        diffs = list(diff(new, get_shared_attrs(existing, new)))
        match = len(diffs) == 0
        return match, diffs


class KubernetesAnsibleModule(AnsibleModule, K8sAnsibleMixin):
    resource_definition = None
    api_version = None
    kind = None

    def __init__(self, *args, **kwargs):

        kwargs['argument_spec'] = self.argspec
        AnsibleModule.__init__(self, *args, **kwargs)

        if not HAS_K8S_MODULE_HELPER:
            self.fail_json(msg="This module requires the OpenShift Python client. Try `pip install openshift`")

        if not HAS_YAML:
            self.fail_json(msg="This module requires PyYAML. Try `pip install PyYAML`")

    def execute_module(self):
        raise NotImplementedError()
