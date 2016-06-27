#!/usr/bin/env python
# -*- coding: utf-8 -*-

#############################################################################
#  Copyright Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#############################################################################

from cachetools import Cache, hashkey
import pylibmc
import hashlib


def strhash(*args, **kwargs):
    return str(hashkey(*args, **kwargs))


class MemCache(Cache):
    """subclass of cache that uses a memcached store"""

    def __init__(self, missing=None, getsizeof=None):
        super(MemCache, self).__init__(0, missing, getsizeof)
        # name mangling to override 'private variable' __data in cache
        # pylibmc used to connect to memcached client
        self._Cache__data = pylibmc.Client(['127.0.0.1'], binary=True,
                                           behaviors={'tcp_nodelay': True,
                                                      'ketama': True})

    def __repr__(self):
        return 'Memcache doesnt list its keys'

    def __iter__(self):
        print('return fake iter')
        return None

    def __len__(self):
        print('return fake length')
        return -1

    def __contains__(self, key):
        print('cache contains key')
        return None

    def __delitem__(self, key):

        del self._Cache__data[key]

    def __getitem__(self, key):
        assert(isinstance(key, str))

        hashObject = hashlib.sha512(key.encode())
        hexVal = hashObject.hexdigest()

        try:
            return self._Cache__data[hexVal]
        except KeyError:
            return self.__missing__(key)

    def __setitem__(self, key, value):
        assert (isinstance(key, str))

        hashObject = hashlib.sha512(key.encode())
        hexVal = hashObject.hexdigest()

        try:
            self._Cache__data[hexVal] = value
        except KeyError:
            print('Failed to save value %s \nwith key %s' % (value, hexVal))
