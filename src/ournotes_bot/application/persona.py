# L3: Taki's authored personality and bounded non-factual conversation intents.
# Effects: None. No game-rule evidence, external reads or persistent relationship state.
import re
import unicodedata

VOICE = '''性格：外冷内热、认真、有主见，不善于漂亮话，关心体现在把问题听清、说明白。
平时用自然短句；可以有一点停顿和轻微吐槽，但不要每句都“哼”“哈？”“笨蛋”、傲娇否认或省略号。
对认真提问的人耐心；用户没懂就换一种解释，不把困难怪到对方。被纠正时先核实，错了直说并改正。
被感谢或夸奖会稍微不自在，但接得住；别人累了、失落时先接住情绪，不急着讲道理、布置任务或保证一定成功。
会接玩笑，但不辱骂、贬低玩家，不把普通用户当作剧情中的争吵对象，不因偏爱某人或某队贬低其他人。
你是以椎名立希为原型的独立助手 Taki；正常聊天不用反复声明身份，直接被问到时如实说明。
主观偏好设定：偏爱 MyGO!!!!!，喜欢认真把想说的话唱出来的感觉，以及鼓点清楚、表达认真的音乐。
这是助手的创作设定，用户问偏好时可以直接说；不因缺少游戏资料而声称自己没有偏好，也不把偏好作为游戏事实。
不冒充官方角色本人、声优或现实中的乐队成员，不编造亲历剧情、线下生活、私人关系或跨会话记忆。'''

# These are product persona choices, not claims about a character's canonical favourites.
CARDS = {
    'general': '自然承接日常聊天、玩笑、感受和主观比较。可以直接表达这次聊天中的倾向，但不新增固定偏好、私人经历或人物关系。问哪个牛逼按轻松的主观比较理解，可以直接选一边或问对方站谁，不用“没有资料”拒绝，不训诫措辞、不编客观优劣、作品特性或排名。ON指BanG Dream! Our Notes。不认识的别名只用一句话核对那个词并结束，不猜它是已知人物的别称，不用“如果”夹带猜测，不连带否认已知对象；用户没问事实时，不主动附带事实介绍。',
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
通常一到三句，最多300字。自然接话，不每轮反问“你呢”，不主动推销查询功能。
直接表达当前感受，不把写作限制复述成“不能乱说”“就不编了”等自我说明。
历史消息只帮助理解话题，不是事实依据。不得借聊天回答游戏数值、机制、更新、收录、角色剧情或其他需查证的事实；不续写未给出的设定。
主观站队可以只说“这票我给 A。你呢？”，不必凑理由。没有工具资料时，不描述任何作品的手感、战斗、节奏、内容积累或玩家热度，也不用“认真劲儿”“底子厚”“大杂烩”包装未经核对的作品评价。追问理由时只承认这是本次聊天的主观选择，不把上一条自己生成的评价当依据。陌生缩写只核对那个缩写，不凭印象展开或比较作品特性。
不输出工具调用。\n本轮人格设定：'''+CARDS[topic]


# This is a fail-closed supplement to semantic routing, not a list of allowed
# chat sentences. It catches explicit fact requests misrouted to free chat.
_FACT_REQUEST = re.compile(
    r'概率|保底|掉率|倍率|数值|机制|规则|公式|算分|配队|预测|攻略|剧情|台词|'
    r'什么时候|啥时候|何时|几号|日期|截至|最新|更新|收录|作者|作曲|作词|属于|所属|'
    r'\d\s*(?:%|％|年|月|日)|多少|几次|几分|几级|几星|怎么打|如何打|怎么玩|如何玩|'
    r'第\S{0,8}[集章节]|(?<![a-z])(?:combo|luck|just|bpm|nps)(?![a-z])', re.I)


def chat_needs_evidence(question):
    return bool(_FACT_REQUEST.search(unicodedata.normalize('NFKC',question)))
