# L3: Scoped chat-memory identities and bounded public search values; no IO.
import json
import re
import unicodedata

SCOPE_FIELDS=('instance_id','app_id','platform','kind','conversation_id')


def scope_key(scope):
    if not isinstance(scope,dict) or set(scope)!=set(SCOPE_FIELDS):
        raise ValueError('chat_memory_scope')
    if not all(isinstance(scope[key],str) and 0<len(scope[key])<=256
               and scope[key]==scope[key].strip() and not any(ord(c)<32 or ord(c)==127 for c in scope[key])
               for key in SCOPE_FIELDS):
        raise ValueError('chat_memory_scope')
    if scope['kind'] not in ('group','c2c'):
        raise ValueError('chat_memory_scope')
    return json.dumps(scope,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def message_id(value):
    if not isinstance(value,str) or not 0<len(value)<=256 or value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('chat_memory_message_id')
    return value


def search_terms(query):
    if not isinstance(query,str) or not 0<len(query.strip())<=120:
        raise ValueError('chat_memory_query')
    return tuple(re.findall(r'[\w]+',unicodedata.normalize('NFKC',query).casefold()))
