"""Private KB API proxy for the iPhone closing-price screener.
No trading/orders. KB endpoint details must be configured from official API docs.
"""
import csv
import io
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, request, send_file

BASE = Path(__file__).resolve().parent
app = Flask(__name__)


def load_config():
    path = os.environ.get('KB_API_CONFIG', str(BASE / 'kb_api_config.json'))
    if not Path(path).exists():
        raise ValueError('KB API 세부 명세가 설정되지 않았습니다. kb_api_config.json.example 파일을 참고하십시오.')
    conf = json.loads(Path(path).read_text(encoding='utf-8'))
    if not conf.get('verified_from_official_docs'):
        raise ValueError('공식 API 명세 확인 전입니다. 실제 명세로 설정한 후 검증 표시를 변경하십시오.')
    return conf


def _require_safe_host(url, permitted):
    parsed = urlparse(url)
    allowed = set(permitted)
    if parsed.scheme != 'https' or parsed.hostname not in allowed or parsed.username or parsed.password:
        raise ValueError('KB 공식 HTTPS API 호스트만 허용됩니다.')


def _token(conf, session):
    # Header/body names are intentionally configured, not guessed.
    o = conf['oauth']
    url = o['url']
    _require_safe_host(url, conf['allowed_hosts'])
    key = os.environ.get('KB_APP_KEY')
    secret = os.environ.get('KB_APP_SECRET')
    if not key or not secret:
        raise ValueError('서버의 KB_APP_KEY / KB_APP_SECRET 설정이 필요합니다.')
    form = dict(o.get('body', {}))
    form[o['key_field']] = key
    form[o['secret_field']] = secret
    resp = session.request(o.get('method', 'POST'), url,
                           json=form if o.get('body_type', 'json') == 'json' else None,
                           data=form if o.get('body_type', 'json') == 'form' else None,
                           timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get(o.get('token_field', 'access_token'))
    if not isinstance(token, str) or not token:
        raise ValueError('토큰 발급 응답에서 access_token을 찾을 수 없습니다.')
    return token


def _lookup(value, dotted):
    for part in dotted.split('.'):
        if isinstance(value, list):
            value = value[int(part)]
        else:
            value = value[part]
    return value


def collect_daily(date, session=None):
    conf = load_config()
    session = session or requests.Session()
    token = _token(conf, session)
    endpoint = conf['daily']
    url = endpoint['url']
    _require_safe_host(url, conf['allowed_hosts'])
    universe = conf.get('universe', [])
    if not universe:
        raise ValueError('검색 대상 종목(universe)이 비어 있습니다. 공식 종목목록을 설정하십시오.')
    # Need at least 21 business days: request 45 calendar days (may vary due to holidays).
    out = []
    seen = set()
    cutoff = datetime.strptime(date, '%Y-%m-%d')
    from datetime import timedelta
    begin = (cutoff - timedelta(days=65)).strftime('%Y%m%d')
    for item in universe:
        code = str(item['ticker']).zfill(6)
        name = str(item.get('name', code))
        query = dict(endpoint.get('params', {}))
        query[endpoint['code_param']] = code
        query[endpoint['start_param']] = begin
        query[endpoint['end_param']] = date.replace('-', '')
        headers = dict(endpoint.get('headers', {}))
        headers[endpoint.get('authorization_header', 'Authorization')] = (
            endpoint.get('authorization_prefix', 'Bearer ') + token)
        resp = session.request(endpoint.get('method', 'GET'), url, params=query if endpoint.get('method', 'GET') == 'GET' else None,
                               json=query if endpoint.get('method', 'GET') == 'POST' else None,
                               headers=headers, timeout=20)
        if resp.status_code == 429:
            raise RuntimeError('KB API 호출 제한(429)입니다. 호출량과 요청 주기를 낮추십시오.')
        resp.raise_for_status()
        payload = resp.json()
        data = _lookup(payload, endpoint['rows_path'])
        if not isinstance(data, list):
            raise ValueError('일봉 응답이 배열이 아닙니다. API 필드 명세를 점검하십시오.')
        for r in data:
            f = endpoint['fields']
            dayraw = str(_lookup(r, f['date'])).replace('-', '')
            if len(dayraw) != 8 or not dayraw.isdigit():
                raise ValueError('잘못된 거래일 응답')
            day = dayraw[:4] + '-' + dayraw[4:6] + '-' + dayraw[6:]
            if day > date: continue
            row = {'date':day, 'ticker':code, 'name':name}
            for field in ('open','high','low','close','volume','turnover_krw'):
                row[field] = abs(float(str(_lookup(r, f[field])).replace(',', '')))
            if row['close'] <= 0 or row['volume'] <= 0 or row['turnover_krw'] <= 0:
                continue
            # Never assume unknown security status is NORMAL.
            row['status'] = item.get('status', 'UNVERIFIED') if conf.get('status_list_officially_verified') else 'UNVERIFIED'
            key = (day, code)
            if key not in seen:
                out.append(row)
                seen.add(key)
        time.sleep(max(float(conf.get('request_interval_seconds', 0.3)), 0.2))
    return out


@app.get('/')
def index():
    return send_file(BASE / 'index.html')


@app.get('/api/kb/daily')
def kb_daily():
    if os.environ.get('WEB_API_TOKEN') and request.headers.get('X-Scan-Token') != os.environ['WEB_API_TOKEN']:
        return jsonify(error='접근 권한이 없습니다.'), 401
    day = request.args.get('date', '')
    try:
        datetime.strptime(day, '%Y-%m-%d')
    except ValueError:
        return jsonify(error='날짜는 YYYY-MM-DD 형식이어야 합니다.'), 400
    try:
        rows = collect_daily(day)
        if not rows:
            return jsonify(error='검색일의 시세 자료가 없습니다.'), 404
        buf = io.StringIO()
        fields = ['date','ticker','name','open','high','low','close','volume','turnover_krw','status']
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return jsonify(csv=buf.getvalue(), rows=len(rows), status_verified=all(r['status'] != 'UNVERIFIED' for r in rows))
    except (ValueError, KeyError, requests.RequestException, RuntimeError) as exc:
        # Avoid returning sensitive request details.
        app.logger.warning('KB data retrieval failed: %s', type(exc).__name__)
        return jsonify(error=str(exc) if not isinstance(exc, requests.RequestException) else 'KB API 접속에 실패했습니다.'), 503


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.getenv('PORT', '8080')))
