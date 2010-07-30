# -*- coding: utf-8 -*-
#
# Copyright(c) 2010 poweredsites.org
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import hashlib
from datetime import datetime, timedelta
from decorator import decorator
from tornado.escape import utf8
from tornado.options import options

from poweredsites.libs.handler import BaseHandler
from poweredsites.db.caches import Page, Cache
from poweredsites.db import conn
from poweredsites.libs.utils import NoDefault

__all__ = ("cache", "page")

_cache_condition = {}
_mem_caches = {}

def cache(expire=7200, condition=None, key=None, anonymous=False):
    """Decorator which caches the value of a method in a handler or a module.    
    
    expire: Cache will be expired time from now in seconds.
    condition: If the result of sql condition has changed, then cache expired. 
    key: The unique key of the cache identify in the DB 
    
    """
    def wrapper(func, self, *args, **kwargs):
        now = datetime.now()
        if key is None:
            c = self.__class__.__name__ + func.__name__
        else:
            c = key
        k = key_gen(c, *args, **kwargs)

        value = Cache().findby_key(k)

        if _valid_cache(self, k, value, condition, anonymous, now):
            return value["value"]
        else:
            val = func(self, *args, **kwargs)

            c = Cache()
            c.key = k
            c.value = val
            c.expire = now + timedelta(seconds=expire)

            if value:
                c.save(value["_id"])
            else:
                c.insert()

            return val

    return decorator(wrapper)

def page(expire=7200, condition=None, key=None, anonymous=False):
    """Decorator which caches a whole page(headers + html) in a handler
    
    expire: Cache will be expired time from now in seconds.   
    condition: If the result of sql condition has changed, then cache expired.
    key: The unique key of the cache identify in the DB 
    
    """
    def wrapper(func, self, *args, **kwargs):
        now = datetime.now()
        if key is None:
            c = self.__class__.__name__ + func.__name__
        else:
            c = key
        k = key_gen(c, *args, **kwargs)
        value = Cache().findby_key(k)

        if _valid_cache(self, k, value, condition, anonymous, now) and value["status"] in (200, 304):
            # finish request with cache chunk and headers         
            self.set_status(value["status"])
            self.set_header("Content-Type", utf8(value["headers"]["Content-Type"]))
            self.write(utf8("".join(value["chunk"])))
        else:
            func(self, *args, **kwargs)

            c = Page()
            c.key = k
            c.status = self._status_code
            c.headers = self._headers
            c.chunk = self._write_buffer
            c.expire = now + timedelta(seconds=expire)
            if value:
                c.save(value["_id"])
            else:
                c.insert()

    return decorator(wrapper)


def mem(expire=7200, key=None):
    """Mem cache to python dict"""
    def wrapper(func, self, *args, **kwargs):
        now = datetime.now()
        if key is None:
            c = self.__class__.__name__ + func.__name__
        else:
            c = key
        k = key_gen(c, *args, **kwargs)
        value = _mem_caches.get(k, None)

        if _valid_cache(self, k, value, None, False, now):
            return value["value"]
        else:
            val = func(self, *args, **kwargs)
            _mem_caches[k] = {"value":val, "expire":now}

            return val

    return decorator(wrapper)


def key_gen(key, *args, **kwargs):
    code = hashlib.md5()

    code.update(str(key))

    # copy args to avoid sort original args
    c = list(args[:])
    # sort c to avoid generate different key when args is the same
    # but sequence is different
    c.sort()
    c = [str(v) for v in c]
    code.update("".join(c))

    c = ["%s=%s" % (k, v) for k, v in kwargs]
    c.sort()
    code.update("".join(c))

    return code.hexdigest()

def remove(key, *args, **kwargs):
    """Remove a cache's value."""
    k = key_gen(key, *args, **kwargs)
    try:
        del _cache_condition[k]
    except KeyError:
        pass

    c = Cache()
    v = c.findby_key(k)
    if v:
        c.remove(v["_id"])

def _valid_cache(self, k, value, condition, anonymous, now):
    if not options.cache_enabled:
        return False

    if isinstance(self, BaseHandler):
        handler = self
    else:
        handler = getattr(self, "handler", None)

    if anonymous and handler.current_user:
        return False

    if condition is None and handler is not None:
        condition = getattr(handler, "cache_condition", None)

    if value:
        if condition is not None:
            cond = _cache_condition.get(k, NoDefault)
            if cond is NoDefault:
                # update condition result                
                _cache_condition[k] = conn.mysql.query(condition)
            else:
                new_cond = conn.mysql.query(condition)
                if cond != new_cond:
                    _cache_condition[k] = new_cond
                    return False

        if value["expire"] > now:
            return True
        else:
            return False
    else:
        return False
