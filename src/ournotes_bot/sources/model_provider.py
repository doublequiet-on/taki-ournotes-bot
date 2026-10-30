# L3: Bounded cancellable Chat Completions / OpenAI Responses and registered prices.
# Effects: HTTPS to fixed provider origins only; no retries, redirects or logging bodies.
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import time
from urllib.parse import urlsplit

import aiohttp

from ..domain.model_calls import AttemptResult, ModelError, Price, ToolCall, Usage, ProviderAction,require
from .json_validation import decode_json

ENDPOINTS = {'deepseek': 'https://api.deepseek.com/chat/completions',
             'openai': 'https://api.openai.com/v1/chat/completions',
             'openrouter': 'https://openrouter.ai/api/v1/chat/completions'}


def endpoint(profile):
    return ('https://api.openai.com/v1/responses' if profile.wire_profile=='openai-responses-v1'
            else ENDPOINTS[profile.provider])


def responses_payload(payload):
    """Normalize only the registered text/function Responses surface for validation."""
    raw=payload.get('usage')
    usage=({'prompt_tokens':raw.get('input_tokens'),'completion_tokens':raw.get('output_tokens'),
            'total_tokens':raw.get('total_tokens'),'prompt_tokens_details':raw.get('input_tokens_details'),
            'completion_tokens_details':raw.get('output_tokens_details')} if isinstance(raw,dict) else None)
    normalized={'usage':usage}
    if payload.get('error'):
        normalized['error']=True
        return normalized
    if payload.get('status')!='completed': return normalized
    output=payload.get('output')
    if not isinstance(output,list): return normalized
    texts=[];calls=[];refusal=False
    for item in output:
        if not isinstance(item,dict): return normalized
        if item.get('type')=='reasoning': continue
        if item.get('type')=='web_search_call':
            if item.get('status')!='completed' or not isinstance(item.get('action'),dict):return normalized
            if item['action'].get('type') not in ('search','open_page','find_in_page'):return normalized
            continue
        if item.get('type')=='function_call':
            if item.get('status')!='completed': return normalized
            calls.append({'type':'function','id':item.get('call_id'),
                          'function':{'name':item.get('name'),'arguments':item.get('arguments')}})
        elif item.get('type')=='message':
            if item.get('role')!='assistant' or item.get('status')!='completed': return normalized
            content=item.get('content')
            if not isinstance(content,list): return normalized
            for part in content:
                if not isinstance(part,dict): return normalized
                if part.get('type')=='refusal': refusal=True
                elif part.get('type')=='output_text' and isinstance(part.get('text'),str):texts.append(part['text'])
                else:return normalized
        else:return normalized
    normalized['choices']=[{'finish_reason':'tool_calls' if calls else 'stop',
        'message':{'role':'assistant','content':''.join(texts) or None,'tool_calls':calls,'refusal':refusal}}]
    return normalized


def bounded_utf8(value,limit):
    if not isinstance(value,str): return False
    try: return len(value.encode('utf-8'))<=limit
    except UnicodeError: return False


def usage_from(raw, profile):
    if not isinstance(raw,dict):
        return Usage()
    def count(value):
        return value if type(value) is int and 0 <= value < 2**53 else None
    inp,out,total = (count(raw.get(k)) for k in ('prompt_tokens','completion_tokens','total_tokens'))
    details = raw.get('prompt_tokens_details')
    details = details if isinstance(details,dict) else {}
    cached = count(raw.get('prompt_cache_hit_tokens') if profile.provider=='deepseek' else details.get('cached_tokens')) if profile.cache_usage else None
    output = raw.get('completion_tokens_details')
    reasoning = count(output.get('reasoning_tokens')) if isinstance(output,dict) and profile.reasoning_usage else None
    status = 'known' if None not in (inp,cached,out) else 'partial' if any(v is not None for v in (inp,out,total)) else 'unknown'
    try:
        if profile.provider == 'deepseek' and inp is not None and cached is not None:
            miss = count(raw.get('prompt_cache_miss_tokens'))
            if miss is not None and miss+cached != inp:
                return Usage()
        written=count(details.get('cache_write_tokens',0))
        if written is None:return Usage()
        return Usage(inp,cached,out,reasoning,total,status,written)
    except ModelError:
        return Usage()


def decode_attempt(raw, profile, *, http_status=200, max_tool_calls=None):
    try:
        payload = decode_json(raw)
    except (ValueError,UnicodeError,RecursionError):
        return AttemptResult('error',error_code='model_protocol_error')
    if not isinstance(payload,dict):
        return AttemptResult('error',error_code='model_protocol_error')
    # A completed Responses result can retain a searching attempt ignored after
    # the request's built-in tool limit. It was not a processed/billable action.
    # Use the sent limit, never an unsolicited response limit, as the authority.
    if (profile.wire_profile=='openai-responses-v1' and payload.get('status')=='completed'
            and http_status==200 and not payload.get('error')
            and type(payload.get('max_tool_calls')) is int and type(max_tool_calls) is int and max_tool_calls>0
            and payload.get('max_tool_calls')==max_tool_calls and isinstance(payload.get('output'),list)):
        kept=[];completed=0;ids=set()
        for item in payload['output']:
            if isinstance(item,dict) and item.get('type')=='web_search_call':
                ident=item.get('id');action=item.get('action')
                if (item.get('status')=='searching' and completed==max_tool_calls
                        and isinstance(ident,str) and ident and ident not in ids
                        and isinstance(action,dict) and action.get('type') in ('search','open_page','find_in_page')):
                    ids.add(ident);continue
                if item.get('status')=='completed':completed+=1
                if isinstance(ident,str):ids.add(ident)
            kept.append(item)
        payload={**payload,'output':kept}
    response_items=();citations=();provider_actions=();native_calls=0
    if profile.wire_profile=='openai-responses-v1':
        response_items=tuple(payload.get('output',[])) if isinstance(payload.get('output'),list) else ()
        native_calls=sum(isinstance(item,dict) and item.get('type')=='web_search_call' for item in response_items)
        if payload.get('status')=='incomplete' and http_status==200 and not payload.get('error'):
            return AttemptResult('error',usage_from(responses_payload(payload).get('usage'),profile),
                error_code='model_incomplete',provider_actions=(ProviderAction('web_search',None),) if native_calls else (),native_calls=native_calls)
        if payload.get('status')=='completed' and http_status==200 and not payload.get('error'):
            try:
                searches=0;web_ids=set()
                for item in response_items:
                    if not isinstance(item,dict):raise ValueError('response_item')
                    if item.get('type')!='web_search_call':continue
                    action=item.get('action');ident=item.get('id')
                    if (item.get('status')!='completed' or not isinstance(action,dict)
                            or action.get('type') not in ('search','open_page','find_in_page')
                            or not isinstance(ident,str) or not ident or ident in web_ids):raise ValueError('web_action')
                    web_ids.add(ident)
                    searches+=action['type']=='search'
                provider_actions=(ProviderAction('web_search',searches),)
                citations=tuple(response_citations(response_items))
            except (ValueError,TypeError,KeyError):
                return AttemptResult('error',usage_from(responses_payload(payload).get('usage'),profile),
                                     error_code='model_protocol_error',provider_actions=(ProviderAction('web_search',None),),native_calls=native_calls)
        payload=responses_payload(payload)
    usage = usage_from(payload.get('usage'),profile)  # Always before content/tool validation.
    def error(code='model_protocol_error'):
        return AttemptResult('error',usage,error_code=code,provider_actions=provider_actions,native_calls=native_calls)
    if http_status != 200 or payload.get('error'):
        return error('provider_error')
    choices = payload.get('choices')
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):
        return error()
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message,dict) or message.get('role')!='assistant':
        return error()
    if choice.get('finish_reason') not in ('stop','tool_calls') or message.get('refusal'):
        return error('model_refused' if message.get('refusal') else 'model_incomplete')
    reasoning=None
    if profile.provider=='deepseek' and message.get('reasoning_content') is not None:
        if profile.reasoning_effort in (None,'none') and message.get('reasoning_content'):
            return error('wire_profile_unsupported')
        reasoning=message['reasoning_content']
        if not bounded_utf8(reasoning,131072):return error()
    calls = message.get('tool_calls')
    if calls is None: calls=[]
    if not isinstance(calls,list):
        return error()
    if len(calls)>8: return error('multiple_tools')
    if bool(calls)!=(choice['finish_reason']=='tool_calls'): return error()
    parsed=[]
    for call in calls:
        if not isinstance(call,dict) or call.get('type')!='function': return error()
        function=call.get('function')
        if (not isinstance(function,dict) or not isinstance(call.get('id'),str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',call['id'])
                or not isinstance(function.get('name'),str) or not re.fullmatch(r'[A-Za-z0-9_]{1,64}',function['name'])
                or not bounded_utf8(function.get('arguments'),12000)):
            return error()
        if any(previous.id==call['id'] for previous in parsed): return error()
        # Keep the legacy single-call adapter; new consumers use output_items.
        parsed.append(ToolCall(call['id'],function['name'],function['arguments'],response_items if len(calls)==1 else ()))
    if profile.provider=='deepseek' and profile.reasoning_effort not in (None,'none') and parsed and reasoning is None:
        return error()
    text=message.get('content')
    if text is not None and (not bounded_utf8(text,16000) or len(text)>4000): return error()
    if not parsed and not (text and text.strip()): return error()
    return AttemptResult('ok',usage,tuple(parsed),text.strip() if text else None,output_items=response_items,
                         citations=citations,provider_actions=provider_actions,native_calls=native_calls,reasoning_content=reasoning)


def response_citations(items):
    """Only public HTTP(S) links from typed annotations; titles are display data."""
    seen=set()
    for item in items:
        if not isinstance(item,dict):raise ValueError('response_item')
        if item.get('type')!='message':continue
        for part in item.get('content',[]):
            if not isinstance(part,dict):raise ValueError('response_part')
            for annotation in part.get('annotations',[]):
                if not isinstance(annotation,dict):raise ValueError('annotation')
                if annotation.get('type')!='url_citation':continue
                url=annotation.get('url');title=annotation.get('title')
                if not isinstance(url,str) or len(url)>2048 or any(c.isspace() or c in '<>\\' for c in url):raise ValueError('citation_url')
                parsed=urlsplit(url)
                if '%' in parsed.netloc:raise ValueError('citation_url')
                if parsed.port not in (None,80,443):raise ValueError('citation_url')
                if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('citation_url')
                host=parsed.hostname.rstrip('.')
                if '.' not in host and ':' not in host or host=='localhost' or host.endswith(('.localhost','.local','.internal','.lan','.home','.test','.invalid')):raise ValueError('citation_url')
                try:
                    address=ipaddress.ip_address(host)
                except ValueError:
                    if re.fullmatch(r'(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*',host,re.I):raise ValueError('citation_url')
                    address=None
                if address is not None and not address.is_global:raise ValueError('citation_url')
                if not isinstance(title,str) or len(title)>300:raise ValueError('citation_title')
                start,end=annotation.get('start_index'),annotation.get('end_index')
                if type(start) is not int or type(end) is not int or not 0<=start<=end<=len(part.get('text','')):raise ValueError('citation_span')
                if url not in seen:
                    seen.add(url)
                    yield {'url':url,'title':title,'start_index':annotation.get('start_index'),'end_index':annotation.get('end_index')}


def request_body(profile, messages, tools):
    if any(message.get('images') for message in messages):
        require(profile.wire_profile=='openai-responses-v1','image_capability_unverified')
    if profile.wire_profile=='openai-responses-v1':
        inputs=[]
        for message in messages:
            role=message['role']
            if role=='assistant' and message.get('response_items'):
                inputs.extend(message['response_items'])
                continue
            if role=='tool':
                inputs.append({'type':'function_call_output','call_id':message['tool_call_id'],'output':message['content']})
                continue
            if message.get('content') is not None:
                content=message['content']
                if message.get('images'):
                    require(role=='user','model_protocol_error')
                    import base64
                    content=[{'type':'input_text','text':content},*({'type':'input_image',
                        'image_url':'data:'+image.mime+';base64,'+base64.b64encode(image.data).decode('ascii'),
                        'detail':message['image_detail']} for image in message['images'])]
                inputs.append({'role':'developer' if role=='system' else role,'content':content})
            for call in message.get('tool_calls',[]):
                inputs.append({'type':'function_call','call_id':call['id'],**call['function']})
        body={'model':profile.model,'input':inputs,'store':False,'stream':False,
              'max_output_tokens':profile.max_output_tokens,'include':['reasoning.encrypted_content']}
        if profile.reasoning_effort is not None:body['reasoning']={'effort':profile.reasoning_effort}
        if tools:
            body.update(tools=[{'type':'function',**tool['function'],'strict':False} for tool in tools],
                        tool_choice='auto',parallel_tool_calls=False)
        return body
    body={'model':profile.model,'messages':[{k:v for k,v in message.items() if k!='response_items' and (k!='reasoning_content' or profile.provider=='deepseek' and profile.reasoning_effort not in (None,'none'))}
                                          for message in messages],'stream':False,
          'max_completion_tokens' if profile.provider=='openai' else 'max_tokens':profile.max_output_tokens}
    if tools:
        body['tools']=tools
        body['tool_choice']='auto'
        if profile.parallel_control: body['parallel_tool_calls']=False
    if profile.provider=='openai': body['store']=False
    if profile.provider=='deepseek':
        enabled=profile.reasoning_effort not in (None,'none')
        body['thinking']={'type':'enabled' if enabled else 'disabled'}
        if enabled:
            body['reasoning_effort']=profile.reasoning_effort
            # Historical turns retain visible text only. Same-turn tool results
            # carry their exact returned protocol field; never reconstruct thoughts.
            for message in body['messages']:
                if message.get('role')=='assistant' and message.get('reasoning_content') is None:
                    message['reasoning_content']=''
    if profile.provider=='openrouter':
        body['provider']={'allow_fallbacks':False,'require_parameters':True}
    # No strict/beta, reasoning effort or sampling knobs without an adapter.
    return body


class NativeClient:
    def __init__(self, *, openai_proxy=None):
        self.openai_proxy=openai_proxy

    async def request(self, profile, body, *, timeout):
        timeout=aiohttp.ClientTimeout(total=max(.001,timeout),connect=min(5,max(.001,timeout)))
        try:
            async with aiohttp.ClientSession(timeout=timeout,trust_env=False) as session:
                async with session.post(endpoint(profile),json=body,allow_redirects=False,
                        proxy=self.openai_proxy if profile.provider=='openai' else None,
                        headers={'Authorization':'Bearer '+profile.api_key}) as response:
                    chunks=[];size=0
                    async for chunk in response.content.iter_chunked(8192):
                        size+=len(chunk)
                        if size>100000: return AttemptResult('error',error_code='model_response_too_large')
                        chunks.append(chunk)
                    return decode_attempt(b''.join(chunks),profile,http_status=response.status,max_tool_calls=body.get('max_tool_calls'))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            kind=('disconnected' if isinstance(exc,aiohttp.ServerDisconnectedError) else
                  'timeout' if isinstance(exc,asyncio.TimeoutError) else
                  'connect' if isinstance(exc,aiohttp.ClientConnectorError) else 'transport')
            return AttemptResult('error',error_code='provider_unavailable',transport_error=kind)

    async def price(self, profile):
        if profile.provider!='openrouter': return replace(profile.price,estimated=True)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),trust_env=False) as session:
                async with session.get('https://openrouter.ai/api/v1/models',allow_redirects=False) as response:
                    if response.status!=200: raise ModelError('price_unavailable')
                    chunks=[];size=0
                    async for chunk in response.content.iter_chunked(16384):
                        size+=len(chunk)
                        if size>4_000_000: raise ModelError('price_unavailable')
                        chunks.append(chunk)
                    raw=b''.join(chunks)
            return openrouter_price(decode_json(raw),profile)
        except (aiohttp.ClientError,asyncio.TimeoutError,ModelError,ValueError,KeyError,TypeError):
            return replace(profile.price,estimated=True)


def openrouter_price(payload, profile):
    rows=[r for r in payload['data'] if isinstance(r,dict) and r.get('id')==profile.model]
    if len(rows)!=1 or 'tools' not in rows[0].get('supported_parameters',[]): raise ModelError('price_unavailable')
    pricing=rows[0]['pricing']
    try:
        # API strings are USD/token, contract stores micro-USD/million tokens.
        def rate(key):
            number=Decimal(pricing[key])*10**12
            if not number.is_finite() or number<=0 or number!=number.to_integral_value(): raise ModelError('price_unavailable')
            return int(number)
        if any(Decimal(str(pricing.get(k,'0')))!=0 for k in ('request','image','web_search','internal_reasoning','input_cache_write')):
            raise ModelError('price_dimensions_unsupported')
        if profile.price.currency!='USD': raise ModelError('price_currency')
        rates=(rate('prompt'),rate('input_cache_read'),rate('completion'))
    except (InvalidOperation,KeyError):
        raise ModelError('price_unavailable') from None
    version=hashlib.sha256(json.dumps([profile.model,*rates]).encode()).hexdigest()[:24]
    return Price('USD',*rates,profile.price.reference_rate,'openrouter/'+version,time.time(),False,'openrouter-models')
