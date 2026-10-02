#!/usr/bin/env python3
"""Public CACS schedule -> Apple-compatible iCalendar, no dependencies."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE = 'https://cacs.ws/s28459'
MOSCOW = ZoneInfo('Europe/Moscow')


def escape(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold(line):
    chunks, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 75:
            chunks.append(current)
            current = ' '
        current += char
    return '\r\n'.join(chunks + [current])


def fetch(week):
    url = f'https://cacs.ws/api/schedules?id=28459&type=student&week={week}'
    result = subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location',
                             '--max-time', '30', '--retry', '2', url],
                            check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    if not isinstance(data.get('schedule'), list):
        raise ValueError(f'Unexpected API response for week {week}')
    return data


def render(rows):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Personal CACS Calendar//RU',
             'CALSCALE:GREGORIAN', 'X-WR-CALNAME:Расписание МГУ — Екатерина',
             'X-WR-TIMEZONE:Europe/Moscow', 'REFRESH-INTERVAL;VALUE=DURATION:PT1H',
             'X-PUBLISHED-TTL:PT1H']
    counts = defaultdict(int)
    for item in sorted(rows, key=lambda x: (x['year'], x['month'], x['day'], x['hours'], x['minutes'], x['name'], x['type'])):
        start = datetime(item['year'], item['month'] + 1, item['day'], item['hours'], item['minutes'], tzinfo=MOSCOW)
        # Time/room/teacher changes keep the same UID. A date change replaces
        # the old event in a complete subscription snapshot.
        key = [SOURCE, start.date().isoformat(), item.get('link') or item['name'], item['type']]
        base = json.dumps(key, ensure_ascii=False)
        counts[base] += 1
        uid = hashlib.sha256(f'{base}:{counts[base]}'.encode()).hexdigest()[:32] + '@cacs-personal'
        teachers = '; '.join(' '.join(t.get(k, '') for k in ('last_name', 'name', 'patronymic')).strip() for t in item.get('teacher', []))
        description = f"{item['type']}\nПреподаватели: {teachers or 'не указаны'}\nИсточник: {SOURCE}\nВремя окончания источник не сообщает."
        lines += ['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{stamp}',
                  'DTSTART:' + start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                  'SUMMARY:' + escape(f"{item['name']} — {item['type']}"),
                  'LOCATION:' + escape(item.get('place', '')),
                  'DESCRIPTION:' + escape(description), 'URL:' + SOURCE, 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return '\r\n'.join(fold(line) for line in lines) + '\r\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=date.fromisoformat)
    parser.add_argument('--end', type=date.fromisoformat)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'public' / 'schedule.ics')
    parser.add_argument('--fixture', type=Path, help='Validate conversion with a saved API response')
    args = parser.parse_args()
    today = datetime.now(MOSCOW).date()
    start = args.start or today - timedelta(days=7)
    end = args.end or today + timedelta(days=84)
    if end < start or (end - start).days > 180:
        raise ValueError('Date range must be 0–180 days')
    rows, snapshots = [], []
    if args.fixture:
        snapshots = [json.loads(args.fixture.read_text())]
    else:
        monday = start - timedelta(days=start.weekday())
        while monday <= end:
            snapshots.append(fetch(monday.isocalendar().week))
            monday += timedelta(days=7)
    seen = set()
    for data in snapshots:
        for item in data['schedule']:
            day = date(item['year'], item['month'] + 1, item['day'])
            fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if start <= day <= end and fingerprint not in seen:
                seen.add(fingerprint)
                rows.append(item)
    if not rows:
        raise ValueError('No events: preserving previous calendar; check source and date range')
    content = render(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=args.output.parent, delete=False) as tmp:
        tmp.write(content)
        temp_path = Path(tmp.name)
    temp_path.replace(args.output)
    print(f'{len(rows)} events, {start} through {end}: {args.output}')


if __name__ == '__main__':
    main()
