"""Поиск реальных публикаций для исследовательского кабинета (ТЗ, п. 16): OpenAlex, Crossref, Semantic Scholar.

Все три источника открытые и работают без ключа. Ответы приводятся к одному виду:
title, authors, year, venue, doi, url, cited, abstract, source.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from ..lab_settings import conf

SOURCES = ('openalex', 'crossref', 'semantic_scholar')
_TAG_RE = re.compile(r'<[^>]+>')


class LiteratureError(Exception):
    pass


def _get(url):
    contact = conf('CONTACT_EMAIL')
    agent = 'AI-edu-virtual-labs' + (f' (mailto:{contact})' if contact else '')
    req = urllib.request.Request(url, headers={'User-Agent': agent, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=conf('LITERATURE_TIMEOUT_SECONDS')) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise LiteratureError(f'Сервис вернул ошибку {exc.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise LiteratureError(f'Сервис недоступен: {getattr(exc, "reason", exc)}') from None


def _clean_doi(value):
    if not value:
        return None
    return re.sub(r'^https?://(dx\.)?doi\.org/', '', str(value)).strip() or None


def _abstract_from_index(index):
    if not isinstance(index, dict):
        return ''
    positions = sorted((pos, word) for word, items in index.items() for pos in items)
    return ' '.join(word for _, word in positions)[:1200]


def openalex(query, limit):
    q = urllib.parse.urlencode({'search': query, 'per-page': limit, 'sort': 'relevance_score:desc'})
    j = _get(f'https://api.openalex.org/works?{q}')
    out = []
    for w in j.get('results') or []:
        doi = _clean_doi(w.get('doi'))
        out.append({
            'title': w.get('display_name') or w.get('title') or '',
            'authors': ', '.join(a.get('author', {}).get('display_name', '') for a in (w.get('authorships') or [])[:3] if a.get('author')),
            'year': w.get('publication_year'),
            'venue': ((w.get('primary_location') or {}).get('source') or {}).get('display_name') or '',
            'doi': doi, 'url': f'https://doi.org/{doi}' if doi else (w.get('id') or ''),
            'cited': w.get('cited_by_count') or 0,
            'abstract': _abstract_from_index(w.get('abstract_inverted_index')),
            'source': 'openalex',
        })
    return out


def crossref(query, limit):
    q = urllib.parse.urlencode({'query': query, 'rows': limit,
                                'select': 'DOI,title,author,issued,container-title,is-referenced-by-count,URL,abstract'})
    j = _get(f'https://api.crossref.org/works?{q}')
    out = []
    for w in (j.get('message') or {}).get('items') or []:
        parts = ((w.get('issued') or {}).get('date-parts') or [[None]])[0]
        authors = [' '.join(x for x in (a.get('given'), a.get('family')) if x) for a in (w.get('author') or [])[:3]]
        doi = _clean_doi(w.get('DOI'))
        out.append({
            'title': ' '.join(w.get('title') or []),
            'authors': ', '.join(a for a in authors if a),
            'year': parts[0] if parts else None,
            'venue': ' '.join((w.get('container-title') or [])[:1]),
            'doi': doi, 'url': w.get('URL') or (f'https://doi.org/{doi}' if doi else ''),
            'cited': w.get('is-referenced-by-count') or 0,
            'abstract': _TAG_RE.sub('', w.get('abstract') or '')[:1200],
            'source': 'crossref',
        })
    return out


def semantic_scholar(query, limit):
    q = urllib.parse.urlencode({'query': query, 'limit': limit,
                                'fields': 'title,authors,year,venue,externalIds,citationCount,url,abstract'})
    j = _get(f'https://api.semanticscholar.org/graph/v1/paper/search?{q}')
    out = []
    for w in j.get('data') or []:
        doi = _clean_doi((w.get('externalIds') or {}).get('DOI'))
        out.append({
            'title': w.get('title') or '',
            'authors': ', '.join(a.get('name', '') for a in (w.get('authors') or [])[:3]),
            'year': w.get('year'),
            'venue': w.get('venue') or '',
            'doi': doi, 'url': w.get('url') or (f'https://doi.org/{doi}' if doi else ''),
            'cited': w.get('citationCount') or 0,
            'abstract': (w.get('abstract') or '')[:1200],
            'source': 'semantic_scholar',
        })
    return out


SEARCHERS = {'openalex': openalex, 'crossref': crossref, 'semantic_scholar': semantic_scholar}


def search(query, sources=SOURCES, limit=8):
    """Ищет во всех выбранных источниках, объединяет результаты без дублей по DOI.
    Ошибка одного источника не мешает остальным: она возвращается в errors."""
    results, errors, seen = [], {}, set()
    for name in sources:
        try:
            items = SEARCHERS[name](query, limit)
        except LiteratureError as exc:
            errors[name] = str(exc)
            continue
        for item in items:
            key = (item['doi'] or '').lower() or re.sub(r'\W+', '', item['title'].lower())[:80]
            if not item['title'] or key in seen:
                continue
            seen.add(key)
            results.append(item)
    return {'results': results, 'errors': errors}
