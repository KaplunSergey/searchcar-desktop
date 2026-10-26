"""Manual auction messages from owned stored lots; no network or sold inference."""
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .external_links import validated_source_image_url
from .sources.registry import source_catalog

CALENDAR_PAGE = 'https://searchcar-auction-calendar-preview.pages.dev/'

def utf16(value):
    return len(value.encode('utf-16-le')) // 2

def clipped(value, limit):
    if utf16(value) <= limit:
        return value
    result = []
    for char in value:
        if utf16(''.join(result)) + utf16(char) > limit - 1:
            break
        result.append(char)
    return ''.join(result) + '…'

def clean(value, limit=120):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069\ud800-\udfff]', ' ', value)
    return clipped(' '.join(value.split()), limit)

def listing_link(lot):
    try:
        raw = lot['url']
        if not isinstance(raw, str) or re.search(r'[\x00-\x20\x7f]', raw):
            return None
        url = urlsplit(raw)
        source = next(item for item in source_catalog() if item['key'] == lot['source_key'])
        if url.scheme != 'https' or url.hostname not in source['domains'] or url.username or url.password or url.port not in (None,443):
            return None
        if lot['source_key'] == 'copart':
            match = re.fullmatch(r'/(?:[a-z]{2}/)?lot/(\d+)(?:/[^/]+)?/?', url.path)
            valid = bool(match and match[1] == lot['source_listing_id'])
        else:
            valid = lot['source_key'] == 'iaa' and bool(re.fullmatch(r'/VehicleDetail/\d+~US/?', url.path, re.I))
        link = urlunsplit((url.scheme, url.netloc, url.path, '', ''))
        return link if valid and len(link) <= 512 else None
    except (ValueError, TypeError, KeyError, StopIteration):
        return None

def fingerprint(lot):
    # Computed countdown, observation timestamp and project name are not a new vehicle publication.
    value = {key: lot.get(key) for key in ('source_key', 'source_listing_id', 'title', 'url', 'image', 'details', 'project_search_status')}
    value['round'] = (lot.get('round') or {}).get('id')
    value['schedule'] = {key: lot['schedule'].get(key) for key in ('utc_at', 'precision', 'event_kind', 'local_date', 'source_timezone')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()

def calendar_link(lot, now, locale, link):
    if (not link or lot.get('project_search_status') == 'NOT_FOUND_IN_SEARCH'
            or lot['details'].get('auction_phase') in {'LIVE', 'CLOSED', 'CANCELLED'}
            or lot['time']['event_kind'] != 'START' or lot['time']['precision'] != 'EXACT'):
        return None
    try:
        start = datetime.fromisoformat(lot['schedule']['utc_at'].replace('Z', '+00:00')).astimezone(timezone.utc)
        if not start > now or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', lot['source_listing_id']):
            return None
        stamp = start.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        title = list(clean(lot['title'], 240)[:120])
        data = dict(v=1, source=lot['source_key'], id=lot['source_listing_id'], title=''.join(title), url=link,
                    start=stamp, round=(lot.get('round') or {}).get('id'), locale=locale)
        if data['round'] and len(data['round']) > 128:
            return None
        while True:
            url = CALENDAR_PAGE + '#' + quote(json.dumps(data, ensure_ascii=False, separators=(',', ':')), safe="~()*!.'")
            if len(url) <= 2048:
                return url
            if len(title) <= 1:
                fallback_title = list(clean(lot['title'], 120))
                while True:
                    fallback = 'https://calendar.google.com/calendar/render?' + urlencode(dict(action='TEMPLATE', text=''.join(fallback_title),
                        dates=start.strftime('%Y%m%dT%H%M%SZ/')+start.strftime('%Y%m%dT%H%M%SZ'), details=link))
                    if len(fallback) <= 2048:
                        return fallback
                    if len(fallback_title) <= 1:
                        return None
                    fallback_title.pop()
            title.pop(); data['title'] = ''.join(title) + '…'
    except (ValueError, TypeError, KeyError, AttributeError):
        return None

def auction_message(lot, context, locale, *, force_text=False):
    l = lambda ru, uk: uk if locale == 'uk' else ru
    link = listing_link(lot)
    if not link:
        raise ValueError('telegram_listing_link_unavailable')
    now = datetime.fromisoformat(context['generated_at'].replace('Z', '+00:00'))
    photo = None if force_text else validated_source_image_url(lot['source_key'], lot.get('image'))
    # Local preview images are not public Telegram URLs. Never upload local files implicitly.
    time = lot['time']; archived = lot.get('project_search_status') == 'NOT_FOUND_IN_SEARCH'
    mileage = lot['details'].get('mileage') or {}
    value = mileage.get('value') if isinstance(mileage, dict) else None
    distance = l('нет данных', 'немає даних')
    if type(value) in {int, float} and math.isfinite(value) and value >= 0 and mileage.get('unit') in {'mi', 'km'}:
        distance = f'{value:,.3f}'.rstrip('0').rstrip('.').replace(',', ' ').replace('.', ',') + ' ' + mileage['unit']
        if clean(mileage.get('accuracy'), 40):
            distance += ' · ' + clean(mileage['accuracy'], 40)
    name = next(item['name'] for item in source_catalog() if item['key'] == lot['source_key'])
    calendar = calendar_link(lot, now, locale, link)
    label = l('Добавить в календарь', 'Додати до календаря') if calendar else None
    text = ''; entities = []
    def add(value, kind=None, url=None):
        nonlocal text
        offset = utf16(text); text += value
        if kind:
            entities.append(dict(type=kind, offset=offset, length=utf16(value), **({'url': url} if url else {})))
    def field(icon, label, value):
        add(icon+' '); add(label+':', 'bold'); add(' '+value)
    add('🚙 '); add(clean(lot.get('title'), 160) or l('Без названия', 'Без назви'), 'bold'); add('\n\n')
    if archived:
        add(l('Не найден в последнем полном поиске. Последние известные сведения, не подтверждение продажи.', 'Не знайдений в останньому повному пошуку. Останні відомі дані, не підтвердження продажу.')+'\n\n')
    field('🏷', l('Аукцион', 'Аукціон'), f"{name} · № {clean(lot['source_listing_id'], 50)}"); add('\n')
    field('🛠', l('Повреждения', 'Пошкодження'), clean(lot['details'].get('primary_damage')) or l('нет данных', 'немає даних')); add('\n')
    field('🛣', 'Одометр', distance); add('\n\n')
    if time['local_date']:
        months = l('января февраля марта апреля мая июня июля августа сентября октября ноября декабря', 'січня лютого березня квітня травня червня липня серпня вересня жовтня листопада грудня').split()
        date = time['local_date']
        date = f"{int(date[8:10])} {months[int(date[5:7])-1]} {date[:4]}"
        event = l('Начало', 'Початок') if time['event_kind'] == 'START' else l('Окончание', 'Завершення') if time['event_kind'] == 'END' else l('Событие', 'Подія')
        field('🗓', event, (l('тип не подтверждён · ', 'тип не підтверджений · ') if time['event_kind'] == 'UNKNOWN' else '') + date
              + (', '+time['local_datetime'][11:16] if time['local_datetime'] else ' · '+l('точное время не указано', 'точний час не вказано')))
    else:
        add('🗓 '+l('Дата не назначена или не подтверждена', 'Дата не призначена або не підтверджена'))
    add('\n'); field('🌍', l('Часовой пояс', 'Часовий пояс'), context['computer_timezone'])
    if not archived and time['precision'] == 'EXACT' and time['event_kind'] != 'UNKNOWN' and time['remaining_seconds'] is not None:
        minutes = math.ceil(time['remaining_seconds'] / 60); days = minutes // 1440
        add('\n')
        if minutes > 0:
            field('⏳', l('До окончания', 'До завершення') if time['event_kind'] == 'END' else l('До начала', 'До початку'),
                  (f"{days} {l('д', 'дн')} " if days else '') + f"{minutes // 60 % 24} {l('ч', 'год')} {minutes % 60} {l('мин', 'хв')}")
        else:
            add('⏳ '+l('Запланированное время прошло — результат не проверен', 'Запланований час минув — результат не перевірено'))
    listing_label = l(f'Открыть автомобиль на {name}', f'Відкрити автомобіль на {name}')
    footer = '🔗 '+listing_label + ('\n📅 '+label if label else '')
    body = clipped(text, (1024 if photo else 4096) - utf16(footer) - 2)
    end = utf16(body) - (1 if utf16(body) < utf16(text) else 0)
    entities = [{**entity, 'length': min(entity['length'], end-entity['offset'])} for entity in entities if entity['offset'] < end]
    text = body+'\n\n'; add('🔗 '); add(listing_label, 'text_link', link)
    if label:
        add('\n📅 '); add(label, 'text_link', calendar)
    return dict(text=text, photo=photo, entities=entities)
