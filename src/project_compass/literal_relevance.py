"""Bounded literal matching; selection is not semantic or authority verification."""
import re
import unicodedata


def literal_terms(question):
    query = unicodedata.normalize('NFKC', question[:512]).casefold()
    query = re.sub(r'为什么|为何|如何|怎么|什么|项目|团队|当前|状态|下一步|接下来|'
                   r'方向|决策|变化|改变|未完成|未验证|阻塞|前提|证据|对应|相关|'
                   r'请问|问一下|关于|查看|查询|看看|成员|同事|选择|方案|'
                   r'做了|做过|负责|处于|应该|目前|最近|今天|进展|任务|记录', ' ', query)
    stop = {'why','what','which','how','did','do','does','is','are','was','were',
            'the','and','to','of','for','with','we','it','its','in','this','that','should',
            'next','current','now','state','status','project','team','choose','chosen','change',
            'changed','direction','decision','decisions','task','tasks','work',
            'records','record','evidence','progress','recent','recently','latest','today','about','happen'}
    terms = [('ascii', term.strip('._+-')) for term in re.findall(r'[a-z0-9][a-z0-9_.+-]*', query)
             if len(term.strip('._+-')) >= 2 and term.strip('._+-') not in stop]
    for chunk in re.findall(r'[\u3400-\u9fff]+', query):
        terms.extend(('han', chunk[index:index+size]) for size in (4,3,2)
                     for index in range(len(chunk)-size+1))
    return list(dict.fromkeys(terms))[:256]


def rank_literal(question, pool, fallback, *, text):
    terms = literal_terms(question)
    if not terms or not pool:
        return fallback
    texts = [unicodedata.normalize('NFKC', text(record)).casefold() for record in pool]
    scores = [0.0] * len(pool)
    for kind, term in terms:
        pattern = re.compile(r'(?<![a-z0-9_])' + re.escape(term) + r'(?![a-z0-9_])') if kind == 'ascii' else None
        matches = [bool(pattern.search(value)) if pattern else term in value for value in texts]
        frequency = sum(matches)
        if frequency:
            weight = (len(term)-1) / frequency
            scores = [score + weight if matched else score for score, matched in zip(scores, matches)]
    ranked = [pool[index] for index in sorted(range(len(pool)), key=lambda index: -scores[index]) if scores[index] > 0]
    return ranked or fallback
