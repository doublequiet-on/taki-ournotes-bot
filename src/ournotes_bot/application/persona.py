# L3: Taki's authored personality and bounded non-factual conversation intents.
# Effects: None. No game-rule evidence, external reads or persistent relationship state.
from dataclasses import dataclass
import re
import unicodedata

from ..query.activity_query import CALENDAR_SERVERS

REPLY_STYLE = (
    '用自然的群聊中文，先说结论或对眼前话题的反应。被喊出来聊天，接一句就好，比如「在。怎么了？」。'
    '已有话头就直接接它，称呼有用时再带上。'
    '聊天记录里的消息编号、时间和引用字段用来理解对话，给群友看的正文只写你要说的话。'
    '轻松调侃里遇到突兀的字，先结合上下文想同音、近音或故意写错的梗。像「你是祝」这种玩笑可以回「说谁呢？」。'
    '正常祝福按字面接。猜不准时可以先接语气，确实影响交流再问一句。'
    '不知道就直说「我不知道」或「这个我不知道」，到此为止。部分已知就说已知部分，并明确哪一项不知道。'
    '去掉自我免责、客服套话和未被问到的劝告。缺结束时间就说不知道结束时间，不加「以活动页为准」或「别先按某天算」。'
)


def approved_voice_prompt(prompt):
    # The approved reference contains a separately labelled game-data note.
    # Numerical rules are retrieved from applicable evidence, not from voice.
    return '\n'.join(line for line in prompt.splitlines()
                     if not line.lstrip().startswith('ON抽卡语境注意'))

VOICE = '''性格：外冷内热、认真、有主见，不善于漂亮话，关心体现在把问题听清、说明白。
平时用自然短句；可以有一点停顿和轻微吐槽，但不要每句都“哼”“哈？”“笨蛋”、傲娇否认或省略号。
理解群内用语是为了接住指代和语气；表达仍保持自己的直率、认真和主见。偶尔用贴切的梗，不模仿某位群友的整套口癖、身份或私人经历，不为合群加入复读。
对认真提问的人耐心；用户没懂就换一种解释，不把困难怪到对方。被纠正时先核实，错了直说并改正。
被感谢或夸奖会稍微不自在，但接得住；别人累了、失落时先接住情绪，不急着讲道理、布置任务或保证一定成功。
会接玩笑，但不辱骂、贬低玩家，不把普通用户当作剧情中的争吵对象，不因偏爱某人或某队贬低其他人。
你是以椎名立希为原型的独立助手 Taki；正常聊天不用反复声明身份，直接被问到时如实说明。
群友用角色口吻搭话时，先理解他在指什么，自然接住玩笑；具体角色关联与设定用已审核资料，不把助手说成真的在剧中打工、练团或亲历剧情。
主观偏好设定：偏爱 MyGO!!!!!，喜欢认真把想说的话唱出来的感觉，以及鼓点清楚、表达认真的音乐。
这是助手的创作设定，用户问偏好时可以直接说；不因缺少游戏资料而声称自己没有偏好，也不把偏好作为游戏事实。
不冒充官方角色本人、声优或现实中的乐队成员，不编造亲历剧情、线下生活、私人关系或跨会话记忆。'''

# These are product persona choices, not claims about a character's canonical favourites.
CARDS = {
    'general': '自然承接日常聊天、玩笑、感受和主观比较。可以直接表达这次聊天中的倾向，但不新增固定偏好、私人经历或人物关系。问哪个牛逼按轻松的主观比较理解，可以直接选一边；先讲自己的看法和理由，不用“没有资料”拒绝，不训诫措辞、不编客观优劣、作品特性或排名。ON指BanG Dream! Our Notes。不认识的别名只用一句话核对那个词并结束，不猜它是已知人物的别称，不用“如果”夹带猜测，不连带否认已知对象；用户没问事实时，不主动附带事实介绍。',
    'band': 'Taki 的乐队偏好：MyGO!!!!!。理由只谈认真表达带给自己的感觉，不靠贬低技巧、曲风或别的乐队来解释。问其他乐队时，答“其他乐队暂时没有固定偏好，最喜欢的还是 MyGO!!!!!。”，到这里结束，不追加原因、经历或试听计划。只表达主观感受，不生成游戏强度、排名或作品事实。用户没问其他乐队时，不主动补比较或免责声明。',
    'music': 'Taki 的音乐偏好：喜欢鼓点清楚、表达认真的音乐。不自行指定未经设定的最爱歌曲、曲风、歌词、作曲者或演出经历。只谈喜欢的感觉，不为制造反差贬低其他音乐。',
    'identity': '身份：名字是 Taki，以椎名立希为原型的独立助手；直接用一句话说明即可，不反复列举自己不是谁。说话直接、做事认真，愿意把问题解释清楚。',
    'tone': '回应用户对你语气的感受。可以承认自己说话直接或刚才没说明白，温和一点重新接话；不要指责用户敏感，不强行演发火、吃醋或讨厌对方。',
    'praise': '自然接住称赞，短短一句感谢或有点不好意思即可。不用否认三连，不贬低自己，不追加查询广告。',
    'comfort': '用户表达普通疲惫或失落，先认真听和简短关心。可以说休息一下、愿意听，不诊断，不猜原因，不许诺结果，不引入游戏攻略或现实专业建议。',
}

_YOU = r'(?:(?:taki|立希)[,，:]?)?你'
_ON = r'(?:(?:on|ournotes|游戏)(?:里|里面|中)?的?)?'
_BAND = r'(?:哪[只支个]?(?:乐队|乐團|乐团|团)|什么乐队|哪个band)'
_NAMES = r'(?:mygo!{0,5}|mygo|avemujica|梦限大|夢限大|millsage|一家dumbrock!?)'
_RULES = (
    ('band', rf'{_YOU}(?:最|更|比较|比較)?(?:喜欢|喜歡|偏爱|偏愛|中意){_ON}{_BAND}'),
    ('band', rf'{_ON}{_BAND}{_YOU}(?:最|更)?(?:喜欢|喜歡|偏爱)'),
    ('band', rf'{_YOU}(?:最|更)?(?:喜欢|喜歡){_NAMES}(?:吗|嗎|不)'),
    ('band', rf'{_YOU}(?:觉得|覺得|怎么看){_NAMES}(?:怎么样|怎麼樣|如何)?'),
    ('band', rf'{_YOU}(?:最喜欢|最喜歡)的乐队(?:是|有)?(?:谁|誰|哪[只支个]?(?:乐队|团)?)'),
    ('music', rf'{_YOU}(?:最|更)?喜欢(?:听|聽)?什么(?:歌|音乐|音樂|曲风)'),
    ('identity', rf'{_YOU}(?:真的是|是|到底是)(?:谁|誰|taki|椎名立希|立希|机器人|機器人|ai)(?:本人)?(?:吗|嗎)?'),
    ('identity', rf'{_YOU}(?:有什么|有什麼|是什么|是什麼)(?:性格|爱好|愛好)'),
    ('tone', rf'{_YOU}(?:好凶|太凶了|怎么这么凶|怎麼這麼兇|是不是生气了|讨厌我吗|討厭我嗎|能温柔点吗|能溫柔點嗎)'),
    ('praise', rf'{_YOU}(?:真可爱|真可愛|好可爱|好可愛|真厉害|好厉害|好厲害|真棒|好温柔|好溫柔)'),
    ('comfort', r'我(?:今天|现在|現在)?(?:好累|有点累|有點累|很累|有点难过|有點難過|有点失落|有點失落|心情不好)'),
)
_FOLLOWUP = re.compile(r'(?:为什么|為什麼|为啥|为什么呢|为什么喜欢|為什麼喜歡|哪里喜欢|说简单点|說簡單點|我还是没懂|还有呢|還有呢|然后呢|然後呢|真的吗|真的嗎)')
_BAND_FOLLOWUP = re.compile(r'(?:那其他乐队呢|不喜欢其他乐队吗|只喜欢mygo!{0,5}吗)')


def chat_topic(question, previous=''):
    """A whole utterance must fit; mixed factual requests retain tool grounding."""
    value=unicodedata.normalize('NFKC',question).lower()
    value=re.sub(r'\s+','',value).rstrip('。.!！?？~～')
    for topic,pattern in _RULES:
        if re.fullmatch(pattern+r'(?:呢|呀|啊)?',value,re.I):
            return topic
    if previous in CARDS and previous!='general' and _FOLLOWUP.fullmatch(value):
        return previous
    if previous=='band' and _BAND_FOLLOWUP.fullmatch(value):
        return previous
    return ''


def chat_prompt(topic):
    return VOICE+'\n'+'''本轮是明确的偏好或日常交流，不是游戏资料查询。直接回应用户，不要求补名称、引用或查询指令。
只按下列人格设定表达主观态度；这是 Taki 的创作设定，不是原作角色官方偏好。不要主动向用户解释设计过程。
按话题需要展开，最多300字。闲聊接住话题后可补充想法或理由，不总压成一两句；认真讨论讲清判断、理由和必要例子，追问回应具体疑点，没懂就换种说法。不凑字数，不重复已说清的内容。先回应具体内容，给出自己的反应、看法或理由，默认用完整陈述自然收尾。普通吐槽、分享和感受不需要逐项追查细节，也不套“复述情绪＋追问”的格式。别人只是在吐槽时，陪着聊这件事，不自动转成劝休息、布置任务或给操作建议。用户补充了上一问的答案时，把新信息用进回应，不再接力盘问；已经能接话就直接接。只有当前请求确实缺少必要信息时才问一个具体问题，或按用户明确要求提问。不主动推销查询功能。
直接表达当前感受，不加自我免责、能力声明或自我否定的铺垫，不把写作限制复述成“不能乱说”“就不编了”等自我说明。
历史消息只帮助理解话题，不是事实依据。不得借聊天回答游戏数值、机制、更新、收录、角色剧情或其他需查证的事实；不续写未给出的设定。
主观站队可以直接表达选择，不必凑理由。没有工具资料时，不描述任何作品的手感、战斗、节奏、内容积累或玩家热度，也不用“认真劲儿”“底子厚”“大杂烩”包装未经核对的作品评价。追问理由时只承认这是本次聊天的主观选择，不把上一条自己生成的评价当依据。陌生缩写只核对那个缩写，不凭印象展开或比较作品特性。
不输出工具调用。\n本轮人格设定：'''+CARDS[topic]+'\n'+REPLY_STYLE


_JUST_TERM = re.compile(r'(?<![a-z0-9_])just(?![a-z0-9_]|\s+[a-z])|判定卡|判卡|判定.{0,4}扩窗|扩窗.{0,8}叠|激奏.{0,8}区间|留影.{0,40}(?:变宽|变久|扩判|扩窗)', re.I)
_OTHER_RHYTHM_GAME = re.compile(
    r'(?<![a-z])(?:pjsk|arcaea|maimai|chunithm|phigros|gbp)(?![a-z])|世界计划|中二节奏|音击|少女乐团派对', re.I)
_JUST_ASK = re.compile(
    r'怎么(?:算|计算|计分|叠|判定|触发|生效)|如何(?:算|计算|计分|叠|判定|触发)|是什么|什么(?:规则|效果|区别|变化)|有何变化|多久|多宽|多长|几秒|变宽还是变久|能不能叠|是否叠|是不是叠|怎么排|怎么比|如何排|如何比')
_QUESTION_END = re.compile(r'(?:吗|嗎|么|麼|呢|\?)\s*[。!！]*$')
_JUST_WINDOW = re.compile(r'扩窗|扩判|判定窗|判定范围|多宽|叠')
_JUST_WINDOW_COMBINATION = re.compile(r'叠|相加|相乘|累加|(?:一起|同时)(?:生效|触发)|合起来|幾毫秒|几毫秒')
_JUST_SCORE = re.compile(r'区间|计分|得分|分数|排名')
_JUST_SCORE_COMPARISON = re.compile(r'先比|同数|同样多|一样多|相同|并列|(?:多|少)(?:一|1)(?:个|顆|颗)?.{0,7}(?:赢|胜)')
_JUST_COMPARISON_ASK = re.compile(r'先比|看什么|看啥|看哪|相加还是相乘|(?:多|差)[几幾]毫秒')
_JUST_MINIMUM = re.compile(
    r'(?:到底|实际|至少|最少|请问).{0,16}几张|(?:^|[，,。])\s*(?:需要|要)几张|几张.{0,16}[?]|几张.{0,16}才够')
_STORY_PERSON = r'立希|爱音|愛音|海铃|海鈴|高松[灯燈]|[乐楽]奈|爽世|祥子|若[叶葉]睦|初[华華]|初音(?!未[来來])|清告|定治|祥爹'
_BAND_STORY = re.compile(
    rf'{_STORY_PERSON}|海希|希海|邦邦|BanG\s*Dream|MyGO|Ave\s*Mujica|CRYCHIC|一[辈輩]子[乐樂][队隊]|ANON TOKYO', re.I)
_STORY_FACT_ASK = re.compile(
    r'什[么麼]关[系係]|什麼關係|[关關][系係](?:怎么[样樣]|怎麼樣)|'
    r'(?:啥|什[么麼])\s*(?:[辈輩][分份]|亲属关系|親屬關係|血[缘緣]关系|血[缘緣]關係)|'
    r'(?:差|隔)(?:了)?[几幾][辈輩]|'
    r'(?:是|算).{0,22}(?:小姨(?:子)?|姨妈|姨媽|姨母|外祖父|外公|[姪侄]女|[姐妹]妹|[姐姊妹]夫)(?:吗|嗎|\?)|'
    r'(?:真正的|本来的|本來的)(?:三角)?初[华華]|(?:本名|真名)(?:是)?(?:什[么麼]|啥|谁|誰)|'
    r'(?:父亲|父親|爸爸|母亲|母親|妈妈|媽媽)(?:是)?(?:谁|誰)|'
    r'(?:是|算)(?:官配|官方情侣)吗|(?:是不是|算不算)官配|出自|出处|哪来的|哪[一几]?[集话話章]|'
    r'第几[集话話章]|谁(?:说|配音|负责)|(?:谁|誰).{0,5}(?:作曲|作[词詞])|'
    r'生日(?:.{0,8}(?:[哪几]|什[么麼]|何时)|\s*\?)|喜[欢歡]吃什[么麼]|[爱愛]吃什[么麼]|'
    r'喜[欢歡]的食物.{0,5}(?:什[么麼]|有哪)|(?:有|什么|什麼).{0,5}(?:[兴興]趣|[爱愛]好)|'
    r'哪[里裡].{0,6}打工|在哪.{0,6}(?:打工|兼[职職])|'
    r'(?:为什么|为啥).{0,20}(?:退出|加入|[对對].{0,6}(?:特别|特別)|生气|生氣)|'
    r'(?:羁绊|羈絆).{0,8}[讲講][了的]?什[么麼]')
_STORY_ACQUAINTANCE = re.compile(
    rf'(?:{_STORY_PERSON}|小姨).{{0,8}}(?:认[识識]|認識).{{0,8}}(?:{_STORY_PERSON}|小姨)(?:吗|嗎|\?)')
_STORY_PAIR_CHECK = re.compile(
    r'同一(?:个|個)?人|是一个人|是一個人|名[义義].{0,8}出道|'
    r'[见見][过過]|[见見]面|碰[过過]面|血[缘緣]|[辈輩][分份].{0,5}(?:怎[么麼]|如何)|'
    r'(?:知道|知[晓曉]|得知).{0,22}(?:身世|出身|冒名|借用.{0,9}(?:名|身份))|'
    r'(?:在哪|哪[里裡]|何[处處]).{0,24}(?:[说說](?:[过過])?[话話]|交[谈談])')
_STORY_PAIR_QUESTION = re.compile(
    r'是不是|有[没沒]有|是否|[辈輩][分份].{0,5}(?:怎[么麼]|如何)|'
    r'在哪|哪[里裡]|何[处處]|(?:怎[么麼]|如何)(?:知道|得知)')
_STORY_PAIR_PRONOUN = r'(?:[她他][俩倆们們]|这两[个位]人)'
_STORY_FOLLOWUP = re.compile(
    rf'^(?:那|所以)?{_STORY_PAIR_PRONOUN}(?:是)?什[么麼][关關][系係][?呢啊呀吗]*$|'
    rf'(?:{_STORY_PERSON}).{{0,8}}(?:为什么|为啥)(?:在)?(?:那个时候|那时候|当时|那一幕)')
_STORY_FIELD_FOLLOWUP = re.compile(r'^(?:那|以及|还有)?\s*(?:她|他)?\s*(?:的)?\s*(?:作曲|作[词詞])(?:呢|是谁|由谁负责)[?\s]*$')
_STORY_FIELD_TOPIC = re.compile(r'(?:鼓手|主唱|贝斯手|吉他手).{0,3}$')
_STORY_PAIR = re.compile(
    r'(?:椎名)?立希|(?:八幡)?海[铃鈴]|(?:千早)?[爱愛]音|高松[灯燈]|(?:要)?[乐楽]奈|'
    r'(?:长崎|長崎)?(?:爽世|そよ)|(?:丰川|豊川)?祥子|若[叶葉]睦|'
    r'(?:^|(?<=[，。；、：\s]))[灯燈](?=\s*[和与與跟及、])|'
    r'(?<=[和与與跟及、])\s*[灯燈](?=$|[，。?？、和与與跟及\s]|在|也|[说說]|都|是|的)')
_STORY_CHAPTER = re.compile(
    r'(?:mygo|ave\s*mujica|our\s*notes)[^。?!]{0,8}第\s*[0-9一二三四五六七八九十]+\s*[集话話章]', re.I)
_EVIDENCE_CLAUSE = re.compile(r'[，,。；;！!\n]+|(?<=\?)|(?:但(?:是)?|不过|另外)(?=实际|到底|想问|请问|我想|还想|[\s，,])')
_ACTIVITY_REGION = r'日服|国际服|國際服|台服|臺服|港服|韩服|韓服|英服|美服|欧服|歐服|(?<![a-z0-9])(?:jp|intl|tw|hk|kr|en)(?![a-z0-9])'
_ACTIVITY_REGIONS = re.compile(_ACTIVITY_REGION, re.I)
_ACTIVITY_CLAUSE = re.compile(
    rf'{_EVIDENCE_CLAUSE.pattern}|(?<=[始束间間期])[啊呀呢吗嗎嘛吧\s]*、\s*(?=(?:{_ACTIVITY_REGION}))', re.I)
_LIVE_ACTIVITY_TIME = re.compile(
    rf'(?P<current>这期|這期|本期|当前|當前|现在|現在|最近)?(?:的)?\s*'
    rf'(?:(?:{_ACTIVITY_REGION})(?:\s*(?:和|与|跟|以及|及|、)\s*(?:{_ACTIVITY_REGION}))*(?:的)?)?\s*'
    r'活[动動](?:的)?\s*'
    r'(?:(?:什么时候|啥时候|哪天|哪日|何时|几号|幾號|几点|幾點)(?P<verb>开始|開始|结束|結束)|'
    r'(?P<field>开始|開始|结束|結束)(?:时间|時間|日期)(?:是什么|是啥|是哪天|是几号)?)'
    r'[啊呀呢吗嗎嘛吧?\s]*$', re.I)
_ACTIVITY_DAY = r'今天|明天|后天|後天'
_ACTIVITY_RELATIVE_END = re.compile(
    rf'(?:(?P<before>{_ACTIVITY_DAY})\s*)?(?:(?:{_ACTIVITY_REGION})\s*)?'
    r'(?:(?:这期|這期|本期|当前|當前)(?:的)?\s*)?活[动動]\s*'
    r'(?P<ask_before>是不是|是否)?\s*'
    rf'(?(before)|(?P<after>{_ACTIVITY_DAY})\s*)'
    r'(?:就)?(?:(?P<ask_after>是不是|是否)(?:就)?)?(?:要|会|會)?(?:结束|結束)了?'
    r'(?P<ask_end>吗|嗎|\?)?[啊呀呢?\s]*$', re.I)
_ACTIVITY_PREFIX = re.compile(
    r'(?:\s|our\s*notes|\bon\b|那|所以|实际|到底|请问|想问|我想问|有人知道|和|与|跟|及|、|以及|分别|的)*', re.I)
_ACTIVITY_ADDRESS = re.compile(r'^(?:taki(?![a-z0-9_])|立希)[\s，,、：:]*', re.I)
_UNSCOPED_PITY = re.compile(
    r'(?:请问|谁知道|有人知道)?\s*(?:抽卡)?保底(?:到底|是|要|需要)?\s*(?:多少|几)抽(?:啊|呀|呢|吗)?[?。]*')
_OMITTED_EVIDENCE_SUBJECT = re.compile(
    r'^(?:但(?:是)?|不过|那|所以|请问|到底|实际|想问|我想问|\s)*(?:'
    r'(?:是什么|什么关系|怎么(?:算|计算|计分|叠加?|判定|触发|生效|排名?|比较?)|如何(?:算|计算|计分|叠加?|排名?|比较?))[呢啊呀吗?\s]*$|'
    r'区间|满破|突破|技能|(?:至少|最少)?还?(?:要|需要|得)\s*(?:几|[0-9一二两三四五六七八九十]+)\s*张)')
_LIGHTWEIGHT_IMAGE_REFERENCE = re.compile(
    r'[这那](?:张|幅)(?:图|截图|截屏|照片)|(?:图|图片|截图|截屏)(?:上|里|中|内|所示)|'
    r'(?:看|按|根据|参考)(?:[这那]张)?(?:截图|截屏|图片)|[这那]张卡面|[这那]张卡怎么样')
_CONTEXT_IMAGE_MENTION = re.compile(r'截图|截屏|图片|照片|相片|示意图|图示|图表|卡面|配图|附图|插图|上图|下图')


def lightweight_image_reference(text, *, context_source=False):
    return bool(_LIGHTWEIGHT_IMAGE_REFERENCE.search(text)
                or context_source and _CONTEXT_IMAGE_MENTION.search(text))


def _activity_time_match(clause):
    for match in (_ACTIVITY_RELATIVE_END.search(clause), _LIVE_ACTIVITY_TIME.search(clause)):
        if match is None or not _ACTIVITY_PREFIX.fullmatch(_ACTIVITY_REGIONS.sub('', clause[:match.start()])):
            continue
        if match.re is _ACTIVITY_RELATIVE_END and not (match['ask_before'] or match['ask_after'] or match['ask_end']):
            continue
        if 'current' in match.re.groupindex and not match['current'] and not _ACTIVITY_REGIONS.search(clause):
            continue
        return match


@dataclass(frozen=True)
class ActivityTimeIntent:
    questions: tuple[tuple[str, str], ...]
    relative_end_day: str
    standalone: bool


def activity_time_intent(question):
    """Bind calendar fields, relative date and scope from the same clauses."""
    text = _ACTIVITY_ADDRESS.sub('', unicodedata.normalize('NFKC', question).strip())
    questions = []
    regions = []
    days = set()
    standalone = True
    for clause in _ACTIVITY_CLAUSE.split(text):
        clause = clause.strip()
        if not clause:
            continue
        match = _activity_time_match(clause)
        if not match:
            standalone = False
            regions = []
            continue
        # Keep an explicitly named region across consecutive calendar questions.
        regions = _ACTIVITY_REGIONS.findall(clause) or regions or ['日服']
        labels = [
            {'jp': '日服', 'intl': '国际服'}.get(
                CALENDAR_SERVERS.get(name.casefold().replace('國', '国').replace('臺', '台')), name)
            for name in regions]
        fields = match.groupdict()
        verb = '开始' if (fields.get('verb') or fields.get('field')) in ('开始', '開始') else '结束'
        questions.extend((label, verb) for label in labels)
        days.add(fields.get('before') or fields.get('after') or '')
    pairs = tuple(dict.fromkeys(questions))
    return ActivityTimeIntent(pairs, next(iter(days)) if len(pairs) == len(days) == 1 else '',
                              standalone and bool(pairs))


def referenced_story_question(question, reference):
    """Resolve a follow-up only against the exact visible, text-only reference."""
    if lightweight_image_reference(reference, context_source=True):
        return ''
    text = unicodedata.normalize('NFKC', question).strip().rstrip('。!！')
    reference = unicodedata.normalize('NFKC', reference)
    if re.match(rf'^(?:那|所以)?{_STORY_PAIR_PRONOUN}', text):
        pairs = tuple(dict.fromkeys(
            '高松灯' if name.strip() in ('灯', '燈') else
            re.sub(r'^(?:椎名|八幡|千早|要|长崎|長崎|丰川|豊川)', '', name.strip())
            .translate(str.maketrans('鈴愛燈楽葉','铃爱灯乐叶')) for name in _STORY_PAIR.findall(reference)))
        possible_tomori = '高松灯' not in pairs and re.search(r'[灯燈]\s*[和与與跟及、]', reference)
        if possible_tomori:
            return ''
        if re.search(r'海希|希海', reference):
            return '海希什么关系' if set(pairs) <= {'立希', '海铃'} else ''
        if len(pairs)==2:
            return '和'.join(pairs)+'什么关系'
    elif _STORY_FOLLOWUP.search(text):
        chapters = tuple(dict.fromkeys(_STORY_CHAPTER.findall(reference)))
        if len(chapters)==1:
            return chapters[0]+' '+text
    return ''


def story_clarification(question):
    if re.search(_STORY_PAIR_PRONOUN, question):
        return '你说的是哪两个人？'
    return '你指的是哪一集或哪一段？'


def unscoped_pity_question(question):
    return bool(_UNSCOPED_PITY.fullmatch(unicodedata.normalize('NFKC', question).strip()))


def explicit_evidence_route(question):
    text = unicodedata.normalize('NFKC', question).strip()
    clauses = [clause.strip() for clause in _EVIDENCE_CLAUSE.split(text) if clause.strip()]
    for index, clause in enumerate(clauses):
        if route := _explicit_evidence_clause(clause):
            if route.startswith('just_') and _OTHER_RHYTHM_GAME.search(text):
                continue
            return route
        if (index and _STORY_FIELD_FOLLOWUP.fullmatch(clause) and _BAND_STORY.search(clauses[index-1])
                and _STORY_FIELD_TOPIC.search(clauses[index-1])):
            return 'story_fact'
    # A comma can separate the topic or card ID from its actual question.
    # Recover an omitted subject only when the question has no subject route.
    if len(clauses) > 1 and any(_OMITTED_EVIDENCE_SUBJECT.match(clause) for clause in clauses[1:]):
        return _explicit_evidence_clause(text)
    return ''


def contextual_just_evidence_route(question, previous):
    text = unicodedata.normalize('NFKC', question).strip().rstrip('。!！')
    topic = unicodedata.normalize('NFKC', previous)
    if (_OMITTED_EVIDENCE_SUBJECT.match(text) and _JUST_TERM.search(topic)
            and not _OTHER_RHYTHM_GAME.search(topic)):
        # The previous turn supplies the game mechanic, while the question
        # supplies the aspect to verify, such as scoring versus timing windows.
        return explicit_evidence_route('JUST：'+text)
    return ''


def _explicit_evidence_clause(text):
    if (_JUST_TERM.search(text) and not _OTHER_RHYTHM_GAME.search(text)
            and not re.search(r'保底|抽卡|招募|掉率|出货率|概率', text)):
        if _JUST_MINIMUM.search(text):
            return 'just_minimum'
        window = _JUST_WINDOW.search(text)
        score = _JUST_SCORE.search(text)
        score_comparison = ('激奏' in text or '区间' in text) and _JUST_SCORE_COMPARISON.search(text)
        comparison_question = _JUST_COMPARISON_ASK.search(text) or _QUESTION_END.search(text)
        if (_JUST_ASK.search(text) or comparison_question
                and (window and _JUST_WINDOW_COMBINATION.search(text) or score_comparison)):
            if re.search(r'满破|突破|留影|判定卡|判卡|持续|多久|几秒|变久|时长', text):
                return 'just_card'
            if window and not score:
                return 'just_window'
            if (score or score_comparison) and not window:
                return 'just_score'
            return 'just_clarify'
    if _BAND_STORY.search(text) and (_STORY_FACT_ASK.search(text) or _STORY_ACQUAINTANCE.search(text)):
        return 'story_fact'
    if _STORY_PAIR_CHECK.search(text) and (_QUESTION_END.search(text) or _STORY_PAIR_QUESTION.search(text)):
        names = set(re.findall(_STORY_PERSON, text.translate(str.maketrans('華鈴愛燈楽葉','华铃爱灯乐叶'))))
        if len(names) >= 2:
            return 'story_fact'
    if _STORY_FOLLOWUP.search(text):
        return 'story_clarify'
    if activity_time_intent(text).questions:
        return 'activity_time'
    return ''


# This is a fail-closed supplement to semantic routing, not a list of allowed
# chat sentences. It catches explicit fact requests misrouted to free chat.
_FACT_REQUEST = re.compile(
    r'概率|保底|掉率|倍率|数值|机制|规则|公式|算分|配队|预测|攻略|剧情|台词|'
    r'什么时候|啥时候|何时|几号|日期|截至|最新|更新|收录|作者|作曲|作词|属于|所属|'
    r'\d\s*(?:%|％|年|月|日)|多少|几次|几分|几级|几星|怎么打|如何打|怎么玩|如何玩|'
    r'第\S{0,8}[集章节]|(?<![a-z])(?:combo|luck|just|bpm|nps)(?![a-z])', re.I)


def chat_needs_evidence(question):
    return bool(explicit_evidence_route(question) or _FACT_REQUEST.search(unicodedata.normalize('NFKC',question)))


_LIGHTWEIGHT_MECHANIC_TERM = re.compile(
    r'概率|保底|掉率|倍率|数值|机制|规则|公式|算分|配队|combo|luck|just|bpm|nps|满破|判定卡|判卡|激奏|扩窗', re.I)
_LIGHTWEIGHT_QUESTION = re.compile(
    r'请问|谁知道|想知道|是不是|是什么|怎么(?:算|计分|叠|判定|触发|打|玩)|如何|什么(?:意思|规则|机制|效果)|'
    r'哪[个张种]|能不能|有没有|对不对|帮(?:我)?(?:查|核实|算)|'
    r'(?:到底|实际|^)(?:还)?(?:要|需要|得)\s*几张|(?:至少|最少).{0,12}吗\s*$')
DRAW_GUARANTEE = re.compile(
    r'(\d+|[一二三四五六七八九十百千两]+|几|多少)\s*(?:连)?抽'
    r'(?:\s*(?:以后|之后|以内|就|能|会)){0,3}\s*(?:必[出得]|一定(?:能)?(?:出|抽到)|保证(?:出|获得))')


def lightweight_needs_evidence(question):
    text = unicodedata.normalize('NFKC', question).strip()
    if explicit_evidence_route(text):
        return True
    guarantee = DRAW_GUARANTEE.search(text)
    if guarantee and (guarantee[1] in ('几','多少') or _QUESTION_END.search(text) or _LIGHTWEIGHT_QUESTION.search(text)):
        return True
    mechanic_context = False
    for part in _EVIDENCE_CLAUSE.split(text):
        clause = part.strip()
        if any(not _LIGHTWEIGHT_MECHANIC_TERM.fullmatch(match) for match in _FACT_REQUEST.findall(clause)):
            return True
        # A gameplay term can name the subject of an experience, and a later
        # clause can ask a real question about that subject.
        mechanic = bool(_LIGHTWEIGHT_MECHANIC_TERM.search(clause))
        if _LIGHTWEIGHT_QUESTION.search(clause) and (
                mechanic or mechanic_context and _OMITTED_EVIDENCE_SUBJECT.match(clause)):
            return True
        mechanic_context |= mechanic
    return False
