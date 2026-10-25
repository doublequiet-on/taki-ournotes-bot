# L3: Manual read-only tools; closed schema and grounding failures use finite reason codes.
# Effects: Existing deterministic query/command paths only; no model or platform IO.
from __future__ import annotations

from dataclasses import dataclass
import re

from ..domain.model_calls import ModelError, require
from ..domain.meta_query import MetaError, MetaQuerySpec
from ..domain.knowledge import KnowledgeScope
from ..sources.json_validation import decode_json
from ..structured_query import QuerySpec, QueryResult, answer_for, resolve_query
from ..commands import resolve_command, handle_command
from ..query.song_metrics import parse_meta_question, execute_many
from ..query.field_query import parse_field_question
from ..query.song_conditions import extract_song_conditions
from ..query.event_cutoff_query import parse_natural_cutoff
from .query_capabilities import CAPABILITIES
from .query_validation import validate_capability_action, OutcomeCode, message_for


def obj(properties,required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


def enum(values): return {'type':'string','enum':list(values)}
def string(limit=50): return {'type':'string','maxLength':limit}
def array(item,limit=16): return {'type':'array','items':item,'maxItems':limit}


PARAMETERS={
    'query':string(),'difficulty':enum(('', 'EASY','NORMAL','HARD','EXPERT','ALL')),
    'level_operator':enum(('', 'gte','gt','lte','lt','eq')),
    'level':{'type':['number','null'],'minimum':1,'maximum':40},
    'page':{'type':'integer','minimum':1,'maximum':100},'integer_level':{'type':'boolean'},
    'skill_query':string(30),'skill_kind':enum(('', 'leader','live','gekisou')),
    'rarity':{'type':['integer','null'],'enum':[None,2,3,4]},
    'rarities':array({'type':'integer','enum':[2,3,4,10,20]}),
    'colors':array({'type':'integer','enum':[1,2,3,4,5]}),
    'mission_mode':enum(('', 'contains','all','pure','mixed','exact')),
    'missions':array(enum(('JUST','COMBO','LUCK'))),
    'live_categories':array(enum(('score','life','judgement','duration'))),
    'gekisou_categories':array(enum(('JUST','COMBO','LUCK'))),
    **{key:array(string()) for key in ('tags','characters','bands')},
}
METRIC_FIELDS=('song_id','band_ids','duration_seconds','level','notes','nps','bpm_first','bpm_min','bpm_max')
META_SCHEMA=obj({
    'resource_server':enum(('jp','intl')),'difficulty':enum(('easy','normal','hard','expert')),
    'band_ids':array({'type':'integer','minimum':1},5),
    'filters':array(obj({'field':enum(METRIC_FIELDS),'op':enum(('eq','in','gte','lte')),
                        'value':{'anyOf':[{'type':'number'},{'type':'string'},array({'type':['integer','string']},100)]}},('field','op','value')),8),
    'order_by':array(obj({'field':enum(METRIC_FIELDS),'direction':enum(('asc','desc'))},('field','direction')),3),
    'limit':{'type':'integer','minimum':1,'maximum':50},
})


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    kind: str
    servers: tuple = ('jp',)

    def wire(self):
        return {'type':'function','function':{'name':self.name,'description':self.description,'parameters':self.schema}}


TOOLS={}
for capability in CAPABILITIES.values():
    name=capability.id.replace('.','_')
    TOOLS[name]=Tool(name,capability.description+'；条件须保留原句，缺省 page=1。',
                     obj({k:PARAMETERS[k] for k in capability.allowed_parameters}),capability.id)
TOOLS.update({
    'visible_result':Tool('visible_result','仅解释用户已引用、确实发送成功的结果；不补充未显示的新事实。需要有效引用且用户明确要求解释。',obj({}),'visible'),
    'song_metrics':Tool('song_metrics','歌曲末判定时长、等级、Note、NPS、BPM；默认日服，谱面指标必须明确难度。同类最短和最长最多4组；不可配队、算分。',
                       obj({'queries':{**array(META_SCHEMA,4),'minItems':1}},('queries',)),'meta',('jp','intl')),
    'field_query':Tool('field_query','查询原句明确要求的单个歌曲、卡或角色字段；字段由程序解析。',obj({}),'field'),
    'event_cutoff':Tool('event_cutoff','查询原句中的活动挑战歌曲榜线观测及历史；不预测、不宣称终榜。',obj({}),'cutoff',('jp','tw','kr','en')),
    'catalog_info':Tool('catalog_info','只读人物、活动、卡池、缩写或帮助。query必须取自原句，活动/卡池可用当前、历史、列表。',
        obj({'kind':enum(('character','event','gacha','abbreviation','help','status')),'query':string(80)},('kind','query')),'info'),
    'knowledge_search':Tool('knowledge_search','检索已审核的ON规则；用户纠错仅重新核实。默认日服当前；历史版本/日期必须来自原句。未指定的version/date必须填JSON null，不能填字符串"null"或猜测值。',
        obj({'server':enum(('jp','intl')),'version':{'type':['string','null'],'maxLength':40},
             'date':{'type':['string','null'],'maxLength':10}},('server','version','date')),'knowledge',('jp','intl')),
})


def check_schema(value,schema):
    if 'anyOf' in schema:
        for candidate in schema['anyOf']:
            try: check_schema(value,candidate);return
            except ModelError: pass
        raise ModelError('invalid_argument')
    types=schema['type'];types=types if isinstance(types,list) else [types]
    kind='null' if value is None else 'boolean' if type(value) is bool else 'integer' if type(value) is int else 'number' if type(value) is float else 'string' if type(value) is str else 'array' if type(value) is list else 'object' if type(value) is dict else 'invalid'
    require(kind in types or kind=='integer' and 'number' in types,'invalid_argument')
    require('enum' not in schema or value in schema['enum'],'invalid_argument')
    if kind=='object':
        require(not set(value)-set(schema['properties']) and set(schema['required'])<=set(value),'invalid_argument')
        for key,item in value.items(): check_schema(item,schema['properties'][key])
    elif kind=='array':
        require(schema.get('minItems',0)<=len(value)<=schema['maxItems'],'invalid_argument')
        for item in value: check_schema(item,schema['items'])
    elif kind=='string': require(len(value)<=schema.get('maxLength',100),'invalid_argument')
    elif kind in ('integer','number'):
        require(schema.get('minimum',-10**15)<=value<=schema.get('maximum',10**15),'invalid_argument')


def validate_call(call):
    tool=TOOLS.get(call.name)
    require(tool is not None,'tool_unregistered')
    try:
        args=decode_json(call.arguments)
        check_schema(args,tool.schema)
    except (ValueError,UnicodeError,RecursionError):
        raise ModelError('tool_arguments_invalid') from None
    return tool,args


def scope_for(question,args):
    require(not re.search(r'台服|港服|韩服|美服|欧服|\b(?:tw|hk|kr|en)\b',question,re.I),'knowledge_server_unsupported')
    require(not (re.search(r'日服|\bjp\b',question,re.I) and re.search(r'国际服|國際服|\bintl\b',question,re.I)),'knowledge_server_ambiguous')
    server='intl' if re.search(r'国际服|國際服|\bintl\b',question,re.I) else 'jp'
    require(args['server']==server,'query_server_mismatch')
    for key in ('version','date'):
        if args[key] is not None: require(args[key] in question,'query_'+key+'_mismatch')
    # An explicitly historical question must not quietly become a current one.
    require(not re.search(r'\d{4}-\d{2}-\d{2}',question) or args['date'] is not None,'query_date_mismatch')
    require(not re.search(r'\d+\.\d+(?:\.\d+)?\s*(?:版|版本)',question) or args['version'] is not None,'query_version_mismatch')
    require(not re.search(r'历史|以前|旧版',question) or args['version'] is not None or args['date'] is not None,'knowledge_scope_ambiguous')
    return KnowledgeScope(server=server,version=args['version'],date=args['date'])


def meta_questions(question):
    # One logical call may return two opposite rankings over identical explicit conditions.
    if '最短' in question and '最长' in question:
        return tuple(re.sub(r'和|以及|、',' ',question.replace(other,'')) for other in ('最长','最短'))
    return (question,)


def grounded_meta(question,args,repository):
    questions=meta_questions(question)
    require(len(args['queries'])==len(questions),'invalid_argument')
    try:
        expected=tuple(parse_meta_question(q,repository) for q in questions)
        specs=tuple(MetaQuerySpec.from_dict(value) for value in args['queries'])
    except MetaError as exc: raise ModelError(exc.status) from None
    require(all(expected) and specs==expected,'invalid_argument')
    return specs


@dataclass(frozen=True)
class ToolOutput:
    texts: tuple[str,...]
    results: tuple
    status: str | None = None


def execute(tool,args,question,repository):
    if tool.servers==('jp',):
        require(not re.search(r'国际服|國際服|台服|港服|韩服|美服|欧服|\b(?:intl|tw|hk|kr|en)\b',question,re.I),'query_server_unsupported')
    question=re.sub(r'^(?:请|帮我)?(?:解释|分析)(?:一下)?[\s，,：:]*','',question).strip()
    question=re.sub(r'[，,\s]*(?:并|再|然后)(?:解释|分析)(?:一下)?[。！!]*$','',question).strip()
    if tool.kind in CAPABILITIES:
        capability=CAPABILITIES[tool.kind]
        if tool.kind=='chart.get':
            # The legacy validator grounds entities but does not compare chart
            # difficulty. Native calls must not omit, invent or replace it.
            conditions=question.replace(args.get('query',''),' ') if args.get('query') else question
            evidence=extract_song_conditions(conditions)
            require(not evidence.difficulty_conflict,'query_difficulty_ambiguous')
            require(args.get('difficulty','')==evidence.difficulty,'query_difficulty_mismatch')
        validated=validate_capability_action({'action':'call_tool','capability':tool.kind,'arguments':args},
                                            capability,question,repository,allow_empty_subject=True)
        if validated.spec is None or validated.code!=OutcomeCode.SUCCESS:
            if validated.code not in (OutcomeCode.UNKNOWN_ENTITY,OutcomeCode.AMBIGUOUS):
                raise ModelError('invalid_argument')
            return ToolOutput((message_for(validated.code),),(None,),validated.code.value)
        result=capability.executor(validated.spec,repository)
        return ToolOutput((answer_for(validated.spec,repository,result=result),),(result,))
    if tool.kind=='meta':
        specs=grounded_meta(question,args,repository)
        results=tuple(QueryResult(QuerySpec('song_meta',meta_request=s),meta=r) for s,r in zip(specs,execute_many(specs,repository)))
        return ToolOutput(tuple(answer_for(r.spec,repository,result=r) for r in results),results)
    if tool.kind in ('field','cutoff'):
        spec=parse_field_question(question,repository) if tool.kind=='field' else parse_natural_cutoff(question)
        require(spec is not None and not isinstance(spec,str),'invalid_argument')
        if tool.kind=='cutoff': spec=QuerySpec('event_cutoff',cutoff_request=spec)
        result=resolve_query(spec,repository)
        return ToolOutput((answer_for(spec,repository,result=result),),(result,))
    require(tool.kind=='info','tool_unregistered')
    kind,query=args['kind'],args['query'].strip()
    require(not re.search(r'[\r\n\\/;`]|https?:|<@',query,re.I),'tool_arguments_invalid')
    require(not query or query in question or query in ('当前','历史','列表') and any(w in question for w in ('现在','当前','最近','历史','有哪些','列表')),'query_entity_mismatch')
    heads={'character':'/查角色','event':'/查活动','gacha':'/查卡池','abbreviation':'/查缩写','help':'/帮助','status':'/状态'}
    require(kind not in ('help','status') or not query,'tool_arguments_invalid')
    command=heads[kind]+' '+query
    result=resolve_command(command,repository)
    text=handle_command(command,repository,resolved=result)
    require(bool(text),'invalid_argument')
    return ToolOutput((text,),(result,))
