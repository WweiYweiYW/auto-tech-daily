"""Self-contained, offline-friendly reading page for the public digest."""
from datetime import datetime
from html import escape
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


def _safe_link(value):
    if not isinstance(value, str) or re.search(r'[\x00-\x20\x7f\\]', value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme.lower() not in ('http', 'https') or not url.hostname or url.username is not None or url.password is not None:
            return None
        url.port  # Reject malformed ports as well as malformed hosts.
        return value
    except ValueError:
        return None


def _text(value):
    return ' '.join(str(value or '').split())


def _excerpt(value, limit=240):
    if len(value) <= limit:
        return value
    prefix = value[:limit - 1]
    ends = list(re.finditer(r'[。！？!?](?:[”’」』])?', prefix))
    if ends and ends[-1].end() >= limit // 2:
        prefix = prefix[:ends[-1].end()]
    return prefix.rstrip() + '…'


def _json(value):
    # Safe even if a future client configuration contains article-controlled text.
    return (json.dumps(value, ensure_ascii=False).replace('&', '\\u0026')
            .replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def _clock(value, zone, fmt):
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            return ''
        return moment.astimezone(zone).strftime(fmt)
    except (ValueError, TypeError, AttributeError):
        return ''


def render_page(payload: dict) -> str:
    """Render public articles without fetching data or modifying the payload.

    Dates are supplied by the backend in Asia/Shanghai. All articles remain
    readable without JavaScript; filters progressively enhance the same markup.
    """
    zone = ZoneInfo('Asia/Shanghai')
    updated = _clock(payload.get('updated_at'), zone, '%Y年%m月%d日 %H:%M')
    groups = {}
    for item in payload.get('articles', []):
        day = str(item.get('date') or '')
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise ValueError
            datetime.strptime(day, '%Y-%m-%d')
        except ValueError:
            day = '日期待确认'
        groups.setdefault(day, []).append(item)
    dates = sorted((day for day in groups if day != '日期待确认'), reverse=True)
    if '日期待确认' in groups:
        dates.append('日期待确认')
    total = sum(map(len, groups.values()))
    options = ''.join(f'<option value="{escape(day, quote=True)}">{escape(day)}</option>' for day in dates)
    sections = []
    for day in dates:
        articles = []
        for item in groups[day]:
            title = _text(item.get('title')) or '未命名文章'
            summary = _text(item.get('summary'))
            source = _text(item.get('source')) or '来源未注明'
            category = _text(item.get('category')) or '未分类'
            link = _safe_link(item.get('link'))
            stamp = _clock(item.get('published'), zone, '%H:%M')
            time_label = ('发布于 ' + stamp) if stamp else ('采集于 ' + _clock(item.get('seen_at'), zone, '%m-%d %H:%M'))
            if time_label == '采集于 ':
                time_label = '发布时间未提供'
            search = escape(' '.join((title, summary, source, category)), quote=True)
            safe_title = escape(title)
            heading = f'<a href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{safe_title}</a>' if link else safe_title
            entry = (f'<a class="original" href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer" aria-label="{escape("阅读全文：" + title, quote=True)}">阅读全文 <span aria-hidden="true">↗</span></a>'
                     if link else '<span class="invalid-link">原文链接不可用</span>')
            excerpt = f'<p class="summary">{escape(_excerpt(summary))}</p>' if summary else '<p class="summary missing">此条未提供摘要，可前往原文阅读。</p>'
            if not summary and not link:
                excerpt = '<p class="summary missing">此条未提供摘要。</p>'
            articles.append(
                f'<article data-date="{escape(day, quote=True)}" data-category="{escape(category, quote=True)}" data-search="{search}">'
                f'<div class="article-meta"><span class="category">{escape(category)}</span><span>{escape(source)}</span></div>'
                f'<h3>{heading}</h3>{excerpt}<div class="article-bottom"><span>{escape(time_label)}</span>{entry}</div></article>'
            )
        label = day if day == '日期待确认' else f'{day[5:7]}月{day[8:10]}日'
        sections.append(f'<section class="day-group" aria-labelledby="day-{len(sections)}"><div class="day-heading"><h2 id="day-{len(sections)}">{escape(label)}</h2><span>{escape(day[:4]) if day != "日期待确认" else ""}</span><span class="day-count">{len(articles)} 篇</span></div>{"".join(articles)}</section>')
    html = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><meta name="color-scheme" content="light">
<title>汽车技术日报</title>
<style>
:root{--paper:#faf9f5;--ink:#213b35;--muted:#64716b;--line:#dce1d7;--accent:#236354;--wash:#edf0e8}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;-webkit-text-size-adjust:100%;line-height:1.7}a{color:inherit;text-underline-offset:5px}button,input,select{font:inherit}button,a,input,select{-webkit-tap-highlight-color:transparent}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #986320;outline-offset:4px}button{cursor:pointer}button:hover{background:#dfe8dd}a:hover{color:#387d63}main{max-width:1080px;padding:0 48px 64px;margin:auto}.skip{position:absolute;top:-100px;left:16px;background:white;padding:10px;z-index:9}.skip:focus{top:12px}.masthead{padding:48px 0 30px;border-top:5px solid var(--ink)}.eyebrow{display:flex;gap:16px;align-items:center;font-size:12px;letter-spacing:2px;font-weight:700;color:var(--accent)}.eyebrow:before{content:"";width:26px;height:2px;background:var(--accent)}h1{font-family:"Songti SC","STSong",SimSun,serif;font-size:clamp(34px,5vw,52px);letter-spacing:2px;line-height:1.35;margin:16px 0 12px;font-weight:700}.intro{margin:0;color:var(--muted);font-size:15px}.updated{display:flex;flex-wrap:wrap;gap:4px 16px;margin:24px 0 0;font-size:12px;color:var(--muted)}.updated strong{font-weight:500;color:var(--ink)}.workspace{display:grid;grid-template-columns:210px minmax(0,1fr);gap:52px;border-top:1px solid var(--line);padding-top:28px}.filters{align-self:start;position:sticky;top:24px}.filters h2{font-size:13px;letter-spacing:1px;margin:0 0 20px}label,legend{font-size:13px;font-weight:600}label{display:block;margin-bottom:7px}select,input[type=search]{width:100%;min-height:46px;border:1px solid #b6c1b5;border-radius:4px;background:#fffefa;color:var(--ink);padding:9px 10px;font-size:14px;min-width:0}select{padding-right:24px}.field{margin-bottom:20px}fieldset{padding:0;margin:0 0 20px;border:0;min-width:0}legend{padding:0;margin-bottom:8px}.radio{display:flex;align-items:center;gap:9px;min-height:44px;margin:0;font-size:14px;font-weight:400;cursor:pointer}.radio input{accent-color:var(--accent);width:17px;height:17px;flex-shrink:0}.reset{border:1px solid #b6c1b5;border-radius:4px;padding:9px 14px;min-height:44px;color:var(--accent);background:transparent;font-size:13px}.filter-note{font-size:12px;color:var(--muted);margin-top:16px}.results{min-width:0}.results-top{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:0 0 20px;font-size:12px;color:var(--muted)}.results-top p{margin:0}.results-top a{padding:10px 0}.day-heading{display:flex;gap:12px;align-items:baseline;border-bottom:1px solid var(--line);padding-bottom:12px}.day-heading h2{font-family:"Songti SC",SimSun,serif;font-size:24px;margin:0}.day-heading>span{color:var(--muted);font-size:12px}.day-heading .day-count{margin-left:auto}.day-group+ .day-group{margin-top:42px}article{padding:26px 0 24px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.article-meta{display:flex;flex-wrap:wrap;gap:5px 12px;color:var(--muted);font-size:12px;line-height:1.8}.category{color:var(--accent);font-weight:600}h3{font-size:22px;line-height:1.55;letter-spacing:.2px;margin:10px 0 12px;font-weight:650}h3 a{text-decoration:none}h3 a:hover{text-decoration:underline}.summary{font-size:16px;line-height:1.95;margin:0;color:#46564e}.missing{color:var(--muted);font-size:14px}.article-bottom{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:14px;font-size:12px;color:var(--muted)}.original{display:inline-flex;align-items:center;gap:12px;min-height:44px;padding:8px 0 8px 12px;flex-shrink:0;font-size:14px;color:var(--accent);font-weight:600;text-decoration:none}.original span{font-size:18px}.original:hover{text-decoration:underline}.invalid-link{font-size:13px}.empty{padding:48px 0 64px;border-top:1px solid var(--line)}.empty h2{font-size:21px;margin:0 0 10px}.empty p{color:var(--muted);font-size:14px;max-width:30em}.empty button{margin-top:8px}footer{margin-top:48px;padding-top:20px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}footer p{margin:4px 0}noscript p{padding:12px 16px;background:var(--wash);font-size:14px}[hidden]{display:none!important}.js-only{display:none}.js .js-only{display:block}
.workspace{display:block}.js .workspace{display:grid}html:not(.js) .results{max-width:720px}
@media(max-width:720px){main{padding:0 22px 40px}.masthead{padding:28px 0 24px}.workspace,.js .workspace{display:block;padding-top:20px}.filters{position:static}.filters h2{display:none}.filter-fields{display:grid;grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr);gap:12px}.field{margin-bottom:14px}fieldset{margin-bottom:8px}.categories{display:flex;flex-wrap:wrap;gap:0 16px}.radio{font-size:13px;gap:6px}.filters .reset{margin:4px 0 16px}.filter-note{display:none}.results-top{padding:4px 0 14px;border-top:1px solid var(--line)}.day-heading h2{font-size:23px}article{padding:22px 0 20px}h3{font-size:21px}.day-group+.day-group{margin-top:32px}.updated{margin-top:20px;gap:2px 10px}.eyebrow{font-size:11px}footer{margin-top:32px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{.filters,.results-top,.original,.skip{display:none!important}.workspace{display:block}article,.day-group{display:block!important}main{max-width:none;padding:0}body{background:white}h3 a:after{content:" (" attr(href) ")";font-size:10px;font-weight:400}article{break-inside:avoid}}
</style></head><body id="top"><a class="skip" href="#reading">跳到文章</a><main>
<header class="masthead"><div class="eyebrow">汽车 · 技术 · 每日阅读</div><h1>汽车技术日报</h1><p class="intro">关注底盘与汽车新技术，按日收录，随时回看。</p>
<p class="updated"><span>最新采集 <strong>__UPDATED__</strong></span><span>北京时间</span></p></header>
<noscript><p>当前显示全部历史文章。启用 JavaScript 后可按日期、分类筛选或搜索；原文链接可直接打开。</p></noscript>
<div class="workspace"><aside class="filters js-only" aria-label="文章筛选"><h2>查找文章</h2>
<div class="filter-fields"><div class="field"><label for="date-filter">阅读日期</label><select id="date-filter"><option value="">全部日期</option>__OPTIONS__</select></div>
<div class="field"><label for="search">搜索文章</label><input id="search" type="search" placeholder="标题、摘要或来源" autocomplete="off" maxlength="200"></div></div>
<fieldset><legend>技术分类</legend><div class="categories"><label class="radio"><input type="radio" name="category" value="" checked>全部</label><label class="radio"><input type="radio" name="category" value="底盘技术">底盘技术</label><label class="radio"><input type="radio" name="category" value="汽车新技术">汽车新技术</label></div></fieldset>
<button type="button" class="reset" id="reset">重置筛选</button><p class="filter-note">日期按文章发布时间归档；<br>未提供时使用采集日期。</p></aside>
<div class="results" id="reading" tabindex="-1"><div class="results-top"><p id="result-count" role="status" aria-live="polite" aria-atomic="true">共 __TOTAL__ 篇文章</p><a href="#top">回到顶部 ↑</a></div>
__SECTIONS__
<div class="empty" id="empty" __EMPTY_HIDDEN__><h2>__EMPTY_TITLE__</h2><p id="empty-description">__EMPTY_DESCRIPTION__</p><button type="button" class="reset js-only" id="empty-reset" __RESET_HIDDEN__>查看全部文章</button></div>
</div></div><footer><p>摘要为 RSS 原文节选，完整内容请阅读原文。</p><p>本页保留历史文章；刷新页面可查看最新采集结果。</p></footer></main>
<script id="page-config" type="application/json">__CONFIG__</script>
<script>
(() => {
 'use strict';
 const config=JSON.parse(document.getElementById('page-config').textContent);
 const date=document.getElementById('date-filter'), search=document.getElementById('search');
 const radios=Array.from(document.querySelectorAll('input[name="category"]'));
 const articles=Array.from(document.querySelectorAll('article'));
 const rows=articles.map(el=>({el,search:el.dataset.search.toLocaleLowerCase('zh-CN')}));
 const groups=Array.from(document.querySelectorAll('.day-group'));
 const empty=document.getElementById('empty');
 function filter(){
  const category=radios.find(r=>r.checked).value;
  const query=search.value.trim().toLocaleLowerCase('zh-CN');
  let count=0;
  rows.forEach(row=>{const el=row.el;const show=(!date.value||el.dataset.date===date.value)&&(!category||el.dataset.category===category)&&(!query||row.search.includes(query));el.hidden=!show;if(show)count++;});
  groups.forEach(group=>{const count=Array.from(group.querySelectorAll('article')).filter(el=>!el.hidden).length;group.hidden=!count;group.querySelector('.day-count').textContent=count+' 篇';});
  document.getElementById('result-count').textContent='显示 '+count+' / '+config.total+' 篇文章';
  empty.hidden=count!==0;
 }
 function reset(){date.value='';search.value='';radios[0].checked=true;filter();}
 date.addEventListener('change',filter);radios.forEach(r=>r.addEventListener('change',filter));
 search.addEventListener('input',filter);
 document.getElementById('reset').addEventListener('click',reset);
 document.getElementById('empty-reset').addEventListener('click',()=>{reset();date.focus();});
 document.documentElement.classList.add('js');
 filter();
})();
</script></body></html>'''
    values = {
        'UPDATED': escape(updated or '暂无采集记录'), 'OPTIONS': options,
        'TOTAL': str(total), 'SECTIONS': ''.join(sections),
        'EMPTY_HIDDEN': 'hidden' if total else '',
        'EMPTY_TITLE': '没有找到匹配的文章' if total else '还没有收录文章',
        'EMPTY_DESCRIPTION': '试试其他日期、分类或关键词，也可以清除筛选查看全部文章。' if total else '下一次采集完成后，文章会显示在这里。稍后刷新页面即可查看。',
        'RESET_HIDDEN': '' if total else 'hidden',
        'CONFIG': _json({'total': total}),
    }
    # One substitution pass: untrusted content cannot create template tokens.
    return re.sub(r'__([A-Z_]+)__', lambda match: values[match[1]], html)
