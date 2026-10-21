"""The gate's eyes inside the sandbox comms container. Read-only on the database; the key comes from the container's own
environment and is never printed.

  probe.py api METHOD PATH            one /api/v1 call, the JSON body on stdin; prints "<status> <body>"
  probe.py watch AGENT FILE           appends a snapshot to FILE whenever what it sees changes, every 0.5 s, for ever
  probe.py snapshot AGENT             prints one snapshot

A snapshot is stamped with this container's clock (`t`, ms), the clock comms' own timestamps come from, so the gate
orders comms' rows and its own observations on one clock. A comms restart stops `watch`; the gate starts it again.
"""
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

DB = os.path.join(os.environ.get('DATA_DIR', '/data'), 'aify.db')


def api(method, path, body=None):
    request = urllib.request.Request(
        'http://127.0.0.1:8800/api/v1' + path, method=method, data=body.encode() if body else None,
        headers={'X-API-Key': os.environ['API_KEY'], 'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def rows(db, sql, *args):
    return [dict(row) for row in db.execute(sql, args)]


def snapshot(agent):
    db = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        publishers = rows(db, 'select machine_id, instance, generation, publication, kind, reason, data_publication, '
                              'data_applied_at from agent_state_shadow_publishers order by instance')
        records = []
        for row in rows(db, 'select instance, lifetime, record_json, row_applied_at from agent_state_shadow_rows '
                            'where agent_id = ? order by instance', agent):
            record = json.loads(row['record_json'])
            records.append({'instance': row['instance'], 'lifetime': row['lifetime'], 'busy': record.get('busy'),
                            'state': record.get('state'), 'stateCause': record.get('stateCause'),
                            'appliedAt': row['row_applied_at']})
        runs = rows(db, 'select id, subject, status, queue_if_busy, steer_if_busy, requested_at, claimed_at, '
                        'claim_bridge_id from dispatch_runs where target_agent = ? order by requested_at, id', agent)
        controls = rows(db, 'select c.id, c.action, c.status, c.run_id, c.requested_at, c.claimed_at from '
                            'dispatch_controls c join dispatch_runs r on r.id = c.run_id where r.target_agent = ? '
                            'order by c.requested_at, c.id', agent)
        spawns = rows(db, 'select id, status, initial_message, created_at from spawn_requests where agent_id = ? '
                          'order by created_at, id', agent)
        legacy = rows(db, 'select turn_busy, turn_updated_at from agent_turn_state where agent_id = ?', agent)
    finally:
        db.close()
    status, text = api('GET', f'/agents/{agent}')
    word = None
    if status == 200:
        payload = json.loads(text).get('agent') or {}
        word = {'status': payload.get('status'), 'statusNote': payload.get('statusNote', ''),
                'lastKnown': payload.get('lastKnown')}
    return {'publishers': publishers, 'records': records, 'runs': runs, 'controls': controls, 'spawns': spawns,
            'legacy': legacy, 'word': word, 'wordStatus': status}


def watch(agent, path):
    last = None
    while True:
        try:
            seen = snapshot(agent)
        except Exception as error:  # a restarting or locked database is an observation, not a crash
            seen = {'error': f'{type(error).__name__}: {error}'}
        encoded = json.dumps(seen, sort_keys=True)
        if encoded != last:
            with open(path, 'a') as out:
                out.write(json.dumps({'t': int(time.time() * 1000), **seen}) + '\n')
            last = encoded
        time.sleep(0.5)


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'api':
        body = sys.stdin.read()
        code, text = api(sys.argv[2], sys.argv[3], body or None)
        print(code, text)
    elif command == 'watch':
        watch(sys.argv[2], sys.argv[3])
    elif command == 'snapshot':
        print(json.dumps({'t': int(time.time() * 1000), **snapshot(sys.argv[2])}))
    else:
        sys.exit(f'unknown command {command}')
