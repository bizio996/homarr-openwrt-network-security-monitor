#!/usr/bin/env python3
"""OpenWrt telemetry collector: read-only ubus, remote syslog, latency history."""
import datetime as dt
import hmac
import http.server
import ipaddress
import json
import os
import re
import socket
import socketserver
import sqlite3
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

BASE = os.environ.get('OPENWRT_URL','http://192.0.2.1').rstrip('/')
USER = os.environ.get('OPENWRT_USER','monitor')
PASSWORD = os.environ.get('OPENWRT_PASSWORD','')
API_KEY = os.environ.get('COLLECTOR_KEY','')
DB = os.environ.get('DB_PATH','/state/monitor.sqlite3')
INTERVAL = max(15,int(os.environ.get('POLL_SECONDS','60')))
RETENTION_DAYS = max(1,min(365,int(os.environ.get('RETENTION_DAYS','30'))))
WEAK_RSSI = int(os.environ.get('WEAK_RSSI_DBM','-70'))
PING_TARGETS = [t.strip() for t in os.environ.get('PING_TARGETS','1.1.1.1,8.8.8.8').split(',') if t.strip()][:4]
DEMO = os.environ.get('DEMO_MODE','false').lower() == 'true'
STATE = {'updated':None,'router':{},'radios':[],'networks':[],'clients':[],'wan':{},'errors':[],'wifiAvailable':False}
LOCK = threading.RLock()

def connect():
    Path(DB).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DB,timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS latency (ts INTEGER, target TEXT, ms REAL, ok INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS firewall (ts INTEGER, action TEXT, src TEXT, dst TEXT, proto TEXT, dpt TEXT, line TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS clients (mac TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, last_ssid TEXT, last_ifname TEXT, seen_count INTEGER, online INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS associations (id INTEGER PRIMARY KEY, mac TEXT, ssid TEXT, ifname TEXT, first_seen INTEGER, last_seen INTEGER, ended INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS device_events (ts INTEGER NOT NULL, mac TEXT NOT NULL, kind TEXT NOT NULL, ssid TEXT, previous_ssid TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_device_events_ts ON device_events(ts)')
    db.execute('CREATE TABLE IF NOT EXISTS wan_events (ts INTEGER, kind TEXT, old_value TEXT, new_value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS wan_state (id INTEGER PRIMARY KEY CHECK(id=1), up INTEGER, ip TEXT, initialized INTEGER)')
    # Additive SQLite migration: existing events and other tables are retained.
    existing={row[1] for row in db.execute('PRAGMA table_info(firewall)')}
    for name in ('zone','direction','interface_in','interface_out','reason','test_event'):
        if name not in existing:
            db.execute('ALTER TABLE firewall ADD COLUMN '+name+(' INTEGER DEFAULT 0' if name=='test_event' else ' TEXT DEFAULT ""'))
    db.execute('CREATE TABLE IF NOT EXISTS syslog_status (id INTEGER PRIMARY KEY CHECK(id=1), received INTEGER DEFAULT 0, last_received INTEGER, last_firewall INTEGER)')
    db.execute('INSERT OR IGNORE INTO syslog_status(id,received) VALUES (1,0)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_firewall_ts ON firewall(ts)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_assocs_mac ON associations(mac,ended)')
    db.commit()
    return db

def rpc(sid, obj, method, args=None):
    body=json.dumps({'jsonrpc':'2.0','id':1,'method':'call','params':[sid,obj,method,args or {}]}).encode()
    req=urllib.request.Request(BASE+'/ubus',data=body,headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=8) as r:
        obj=json.load(r)
    res=obj.get('result',[])
    if len(res)<2 or res[0]!=0: raise RuntimeError(f'ubus {obj.get("error", "call")} status {res[0] if res else "unknown"}')
    return res[1]

def snapshot_openwrt():
    sid=rpc('0'*32,'session','login',{'username':USER,'password':PASSWORD}).get('ubus_rpc_session')
    if not sid: raise RuntimeError('OpenWrt RPC login failed')
    board=rpc(sid,'system','board')
    info=rpc(sid,'system','info')
    wifi=rpc(sid,'network.wireless','status')
    networks=[]; radios=[]; clients=[]
    for radio,entry in wifi.items():
        config=entry.get('config') or {}
        radios.append({'id':radio,'channel':config.get('channel'),'band':config.get('band'),'htmode':config.get('htmode'),'disabled':entry.get('disabled',False),'up':entry.get('up',False)})
        for iface in entry.get('interfaces',[]):
            cfg=iface.get('config') or {}; ifname=(iface.get('ifname') or '')
            item={'id':ifname or cfg.get('ssid') or radio,'ifname':ifname,'radio':radio,'ssid':cfg.get('ssid',''),'mode':cfg.get('mode',''),'up':bool(ifname and entry.get('up',False) and not entry.get('disabled',False)),'band':config.get('band'),'channel':config.get('channel'),'width':config.get('htmode'),'clients':0}
            if ifname:
                try:
                    details=rpc(sid,'iwinfo','info',{'device':ifname})
                    item.update({'channel':details.get('channel',item['channel']),'frequency':details.get('frequency'),'txpower':details.get('txpower'),'width':details.get('htmode') or item['width']})
                except Exception: pass
                try:
                    assocs=rpc(sid,'iwinfo','assoclist',{'device':ifname})
                    raw=assocs.get('results',assocs.get('stations',[]))
                    if isinstance(raw,dict): raw=[dict(v,mac=k) for k,v in raw.items() if isinstance(v,dict)]
                    for station in raw if isinstance(raw,list) else []:
                        if not isinstance(station,dict):continue
                        clients.append({'mac':station.get('mac',''),'ssid':item['ssid'],'ifname':ifname,'radio':radio,'signal':station.get('signal'),'noise':station.get('noise'),'tx_rate':(station.get('tx') or {}).get('rate'),'rx_rate':(station.get('rx') or {}).get('rate'),'tx_retries':(station.get('tx') or {}).get('retries'),'tx_failed':(station.get('tx') or {}).get('failed'),'rx_drops':(station.get('rx') or {}).get('drop_misc'),'tx_bytes':(station.get('tx') or {}).get('bytes'),'rx_bytes':(station.get('rx') or {}).get('bytes'),'signal_avg':station.get('signal_avg'),'inactive_ms':station.get('inactive'),'weak':isinstance(station.get('signal'),(int,float)) and station.get('signal') <= WEAK_RSSI,'connected_time':station.get('connected_time')})
                except Exception:pass
            item['clients']=sum(c['ifname']==ifname for c in clients)
            networks.append(item)
    try: wan=rpc(sid,'network.interface.wan','status')
    except Exception: wan={}
    return {'router':{'model':board.get('model'),'release':(board.get('release') or {}).get('description'),'uptime':info.get('uptime'),'load':info.get('load'),'memory':info.get('memory')},'radios':radios,'networks':networks,'clients':clients,'wifiAvailable':True,'wan':{'up':wan.get('up'),'uptime':wan.get('uptime'),'ipv4':wan.get('ipv4-address',[]),'device':wan.get('device'),'route':wan.get('route',[])}}

def update_history(db, result, now):
    # Only run when an actual RPC snapshot succeeded. Never mark clients offline on RPC errors.
    if not result.get('wifiAvailable'): return
    current={c['mac'].upper():(c.get('ssid') or '',c.get('ifname') or '') for c in result.get('clients',[]) if c.get('mac')}
    previous={r[0]:{'ssid':r[1] or '', 'ifname':r[2] or '', 'online':bool(r[3])} for r in db.execute('SELECT mac,last_ssid,last_ifname,online FROM clients')}
    # Store the first observation only once. A MAC is a device identifier, not proof
    # of a unique physical device (MAC randomization may create multiple identities).
    for mac,(ssid,ifname) in current.items():
        old=previous.get(mac)
        db.execute('INSERT INTO clients(mac,first_seen,last_seen,last_ssid,last_ifname,seen_count,online) VALUES (?,?,?,?,?,1,1) ON CONFLICT(mac) DO UPDATE SET last_seen=excluded.last_seen,last_ssid=excluded.last_ssid,last_ifname=excluded.last_ifname,seen_count=clients.seen_count+1,online=1',(mac,now,now,ssid,ifname))
        if old is None:
            db.execute('INSERT INTO device_events VALUES (?,?,?,?,?)',(now,mac,'NEW',ssid,''))
        elif old['ssid'] != ssid or old['ifname'] != ifname:
            db.execute('INSERT INTO device_events VALUES (?,?,?,?,?)',(now,mac,'SSID_CHANGED',ssid,old['ssid']))
        elif not old['online']:
            db.execute('INSERT INTO device_events VALUES (?,?,?,?,?)',(now,mac,'RECONNECTED',ssid,old['ssid']))
        if old is None or not old['online'] or old['ssid'] != ssid or old['ifname'] != ifname:
            db.execute('UPDATE associations SET last_seen=?,ended=1 WHERE mac=? AND ended=0',(now,mac))
            # Estimate association start using iwinfo connected_time if available.
            # A first observation need not be the actual association start.
            db.execute('INSERT INTO associations(mac,ssid,ifname,first_seen,last_seen,ended) VALUES (?,?,?,?,?,0)',(mac,ssid,ifname,now,now))
        else:
            db.execute('UPDATE associations SET last_seen=? WHERE mac=? AND ended=0',(now,mac))
    for mac,old in previous.items():
        if old['online'] and mac not in current:
            db.execute('UPDATE clients SET online=0 WHERE mac=?',(mac,))
            db.execute('UPDATE associations SET last_seen=?,ended=1 WHERE mac=? AND ended=0',(now,mac))
            db.execute('INSERT INTO device_events VALUES (?,?,?,?,?)',(now,mac,'DISCONNECTED',old['ssid'],old['ssid']))
    db.execute('DELETE FROM device_events WHERE ts < ?', (now-RETENTION_DAYS*86400,))
    wan=result.get('wan') or {}
    if wan.get('up') is not None:
        addresses=wan.get('ipv4') or []
        ip=(addresses[0].get('address') or '') if addresses and isinstance(addresses[0],dict) else ''
        up=int(bool(wan['up']))
        oldwan=db.execute('SELECT up,ip FROM wan_state WHERE id=1').fetchone()
        if oldwan:
            if oldwan[0] != up:
                db.execute('INSERT INTO wan_events VALUES (?,?,?,?)',(now,'UP' if up else 'DOWN',str(oldwan[0]),str(up)))
            if oldwan[1] != ip:
                db.execute('INSERT INTO wan_events VALUES (?,?,?,?)',(now,'IP_CHANGED',oldwan[1],ip))
        db.execute('INSERT INTO wan_state(id,up,ip,initialized) VALUES (1,?,?,1) ON CONFLICT(id) DO UPDATE SET up=excluded.up,ip=excluded.ip',(up,ip))

def wifi_payload(state):
    with connect() as db:
        rows=db.execute('SELECT mac,first_seen,last_seen,last_ssid,last_ifname,seen_count,online FROM clients ORDER BY online DESC,last_seen DESC LIMIT 500').fetchall()
        history=db.execute('SELECT mac,ssid,ifname,first_seen,last_seen,ended FROM associations ORDER BY last_seen DESC LIMIT 100').fetchall()
    details={r[0]:{'mac':r[0],'firstSeen':r[1],'lastSeen':r[2],'lastSsid':r[3],'lastIfname':r[4],'observations':r[5],'online':bool(r[6])} for r in rows}
    clients=[]
    for c in state['clients']:
        v=dict(c); v.update(details.get(str(c.get('mac','')).upper(),{})); clients.append(v)
    return {'updated':state['updated'],'radios':state['radios'],'networks':state['networks'],'clients':clients,'knownDevices':len(details),'deviceHistory':list(details.values()),'associations':[{'mac':r[0],'ssid':r[1],'ifname':r[2],'firstSeen':r[3],'lastSeen':r[4],'ended':bool(r[5]),'durationSeconds':max(0,r[4]-r[3])} for r in history],'weakRssiThreshold':WEAK_RSSI,'errors':state['errors']}

def wifi_history(hours=24, limit=100):
    now=int(time.time())
    since=now-hours*3600
    with connect() as db:
        rows=db.execute('SELECT mac,first_seen,last_seen,last_ssid,last_ifname,seen_count,online FROM clients ORDER BY online DESC,last_seen DESC LIMIT 500').fetchall()
        events=db.execute('SELECT ts,mac,kind,ssid,previous_ssid FROM device_events WHERE ts>=? ORDER BY ts DESC LIMIT ?',(since,limit)).fetchall()
        sessions=db.execute('SELECT mac,ssid,ifname,first_seen,last_seen,ended FROM associations WHERE last_seen>=? ORDER BY last_seen DESC LIMIT ?',(since,limit)).fetchall()
    online=sum(bool(r[6]) for r in rows)
    return {'updated':now,'hours':hours,'retentionDays':RETENTION_DAYS,'totalKnown':len(rows),'online':online,'offline':len(rows)-online,
      'devices':[{'mac':r[0],'firstSeen':r[1],'lastSeen':r[2],'ssid':r[3],'ifname':r[4],'observations':r[5],'online':bool(r[6])} for r in rows],
      'events':[{'timestamp':r[0],'mac':r[1],'kind':r[2],'ssid':r[3], 'previousSsid':r[4]} for r in events],
      'sessions':[{'mac':r[0],'ssid':r[1],'ifname':r[2],'firstSeen':r[3],'lastSeen':r[4],'ended':bool(r[5]),'observedSeconds':max(0,r[4]-r[3])} for r in sessions],
      'note':'First/last seen and session durations are polling observations; disconnections may be detected up to one poll late. MAC randomization affects device identity.'}

def ping(target):
    # Fixed binary, no shell, constrained target syntax.
    if not re.fullmatch(r'[A-Za-z0-9.:-]{1,120}',target):return None
    try:
        p=subprocess.run(['ping','-n','-c','1','-W','2',target],capture_output=True,text=True,timeout=4)
        if p.returncode: return None
        m=re.search(r'time=([\d.]+)',p.stdout)
        return float(m.group(1)) if m else None
    except Exception:return None

def poll():
    while True:
        errors=[]
        if DEMO:
            result={'router':{'model':'Demo Router','release':'OpenWrt demo','uptime':87200,'memory':{'total':268435456,'free':104857600}},'radios':[{'id':'radio0','band':'2g','channel':6,'htmode':'HT20'}], 'networks':[{'id':'wlan0','ssid':'Demo WiFi','band':'2g','channel':6,'clients':1,'up':True}], 'clients':[{'mac':'02:00:00:00:00:01','ssid':'Demo WiFi','signal':-52,'ifname':'wlan0'}],'wan':{'up':True}}
        else:
            try:result=snapshot_openwrt()
            except Exception as exc:
                errors.append('OpenWrt RPC unavailable: '+type(exc).__name__)
                result={}
        now=int(time.time())
        with LOCK:
            STATE.update(result)
            STATE['updated']=now; STATE['errors']=errors
        with connect() as db:
            if not errors: update_history(db,result,now)
            for target in PING_TARGETS:
                latency=ping(target)
                db.execute('INSERT INTO latency VALUES (?,?,?,?)',(now,target,latency,int(latency is not None)))
            db.execute('DELETE FROM latency WHERE ts < ?', (now-RETENTION_DAYS*86400,))
            db.execute('DELETE FROM firewall WHERE ts < ?', (now-RETENTION_DAYS*86400,))
            db.commit()
        time.sleep(INTERVAL)

# Recognize firewall4 kernel log prefixes and explicitly tagged rule logs only.
# Do not count generic syslog messages containing words like 'DROP' or 'REJECT'.
_FW4_PREFIX=re.compile(r'\b(reject|drop)\s+([A-Za-z0-9_-]+)\s+(in|out|forward|invalid ct state)\s*:',re.I)
_RULE_PREFIX=re.compile(r'\b(?:IOT-DROP|GUEST-DROP|FW-DROP|FW-REJECT)\b',re.I)
_TEST_PREFIX=re.compile(r'\bHOMARR-FW-TEST\b',re.I)

def firewall_parse(line):
    is_test=bool(_TEST_PREFIX.search(line))
    match=_FW4_PREFIX.search(line)
    def field(key):
        m=re.search(r'\b'+re.escape(key)+r'=([^\s]+)',line)
        return m.group(1) if m else ''
    if match:
        action=match.group(1).upper()
        zone=match.group(2).lower()
        context=match.group(3).lower()
        direction={'in':'INPUT','out':'OUTPUT','forward':'FORWARD','invalid ct state':'INVALID_STATE'}[context]
        reason='INVALID_CONNTRACK' if context=='invalid ct state' else 'ZONE_POLICY'
    elif is_test and re.search(r'\bDROP\b',line,re.I):
        action='DROP';zone='test';direction='TEST';reason='SYNTHETIC_TEST'
    elif _RULE_PREFIX.search(line) and ('IN=' in line or 'SRC=' in line):
        action='REJECT' if 'REJECT' in line.upper() else 'DROP'
        zone='iot' if 'IOT-' in line.upper() else 'guest' if 'GUEST-' in line.upper() else 'custom'
        direction='RULE';reason='CUSTOM_RULE'
    else:
        return None
    return {'ts':int(time.time()),'action':action,'src':field('SRC'),'dst':field('DST'),
        'proto':field('PROTO'),'dpt':field('DPT'),'line':line[:1000],
        'zone':zone,'direction':direction,'interface_in':field('IN'),'interface_out':field('OUT'),
        'reason':reason,'test_event':int(is_test)}

class SyslogHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data,_=self.request
        line=data.decode('utf-8','replace')[:2000]
        event=firewall_parse(line)
        now=int(time.time())
        with connect() as db:
            db.execute('UPDATE syslog_status SET received=received+1,last_received=? WHERE id=1',(now,))
            if event:
                db.execute('INSERT INTO firewall (ts,action,src,dst,proto,dpt,line,zone,direction,interface_in,interface_out,reason,test_event) VALUES (:ts,:action,:src,:dst,:proto,:dpt,:line,:zone,:direction,:interface_in,:interface_out,:reason,:test_event)',event)
                if not event['test_event']:
                    db.execute('UPDATE syslog_status SET last_firewall=? WHERE id=1',(now,))
            db.commit()

class ThreadedUDP(socketserver.ThreadingMixIn,socketserver.UDPServer): daemon_threads=True; allow_reuse_address=True

def latency_chart(hours):
    # Average successful pings into bounded time buckets; failures remain excluded
    # from latency averages rather than being represented as 0 ms.
    hours = hours if hours in (1, 24, 168) else 1
    bucket = {1: 60, 24: 900, 168: 3600}[hours]
    now = int(time.time())
    start = now - hours * 3600
    with connect() as db:
        rows = db.execute(
            "SELECT (ts / ?) * ? AS slot, target, "
            "ROUND(AVG(CASE WHEN ok=1 THEN ms END), 1), COUNT(*), "
            "SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) "
            "FROM latency WHERE ts >= ? GROUP BY slot, target ORDER BY slot",
            (bucket, bucket, start)
        ).fetchall()
    merged = {}
    for ts, target, avg_ms, total, received in rows:
        entry = merged.setdefault(ts, {'timestamp': ts,
            'time': dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%d/%m %H:%M' if hours == 168 else '%H:%M')})
        if target in PING_TARGETS:
            entry['ping' + str(PING_TARGETS.index(target) + 1)] = avg_ms
    return {'updated': now, 'hours': hours, 'bucketSeconds': bucket,
        'measurementPoint': 'TrueNAS collector', 'series': PING_TARGETS,
        'points': list(merged.values()), 'count': len(merged)}

def payload(path):
    now=int(time.time())
    with LOCK: state=json.loads(json.dumps(STATE))
    if path=='/router':
        with connect() as db: events=db.execute('SELECT ts,kind,old_value,new_value FROM wan_events ORDER BY ts DESC LIMIT 30').fetchall()
        return {'updated':state['updated'],'router':state['router'],'wan':state['wan'],'wanEvents':[{'timestamp':r[0],'kind':r[1],'old':r[2],'new':r[3]} for r in events],'errors':state['errors']}
    if path=='/wifi':return wifi_payload(state)
    with connect() as db:
        if path=='/latency':
            # 7 days at 60s resolution and four targets: up to 40320 samples.
            days=7
            rows=db.execute('SELECT ts,target,ms,ok FROM latency WHERE ts >= ? ORDER BY ts DESC LIMIT 42000',(now-days*86400,)).fetchall()
            samples=[{'time':dt.datetime.fromtimestamp(x[0],dt.timezone.utc).strftime('%H:%M'),'timestamp':x[0],'target':x[1],'ms':x[2],'ok':bool(x[3])} for x in reversed(rows)]
            result=[]
            for target in PING_TARGETS:
                subset=[r for r in rows if r[1]==target][:60]
                valid=[r[2] for r in subset if r[3]]
                result.append({'target':target,'lastMs':valid[0] if valid else None,'lossPct':round(100*(1-len(valid)/len(subset)),1) if subset else None,'avgMs':round(sum(valid)/len(valid),1) if valid else None})
            return {'updated':now,'targets':result,'samples':samples,'measurementPoint':'TrueNAS collector (not OpenWrt)','intervalSeconds':INTERVAL,'retentionDays':RETENTION_DAYS}
        if path=='/firewall':
            # Existing rows remain intact. Classify older events from the stored log text.
            # Test rows are excluded from the real-event totals, including old HOMARR-FW-TEST rows.
            db.execute("UPDATE firewall SET test_event=1,zone='test',direction='TEST',reason='SYNTHETIC_TEST' WHERE line LIKE '%HOMARR-FW-TEST%' AND test_event=0")
            legacy=db.execute("SELECT rowid,line FROM firewall WHERE (zone IS NULL OR zone='') AND line IS NOT NULL LIMIT 1000").fetchall()
            for rowid,line in legacy:
                match=firewall_parse(line)
                if match:
                    db.execute('UPDATE firewall SET zone=?,direction=?,interface_in=?,interface_out=?,reason=?,test_event=? WHERE rowid=?',(match['zone'],match['direction'],match['interface_in'],match['interface_out'],match['reason'],match['test_event'],rowid))
            db.commit()
            since=now-86400
            only_real='ts>=? AND COALESCE(test_event,0)=0'
            count=db.execute('SELECT COUNT(*) FROM firewall WHERE '+only_real,(since,)).fetchone()[0]
            tests=db.execute('SELECT COUNT(*) FROM firewall WHERE ts>=? AND COALESCE(test_event,0)=1',(since,)).fetchone()[0]
            cols='ts,action,src,dst,proto,dpt,zone,direction,interface_in,interface_out,reason'
            rows=db.execute('SELECT '+cols+' FROM firewall WHERE COALESCE(test_event,0)=0 ORDER BY ts DESC LIMIT 50').fetchall()
            def top(column,limit=8):
                return db.execute('SELECT COALESCE(NULLIF('+column+',""),"unknown") AS value,COUNT(*) FROM firewall WHERE '+only_real+' GROUP BY value ORDER BY COUNT(*) DESC LIMIT '+str(limit),(since,)).fetchall()
            status=db.execute('SELECT received,last_received,last_firewall FROM syslog_status WHERE id=1').fetchone() or (0,None,None)
            return {'updated':now,'events24h':count,'testEvents24h':tests,
                'topSources':[{'src':v,'count':n} for v,n in top('src')],
                'topPorts':[{'port':v,'count':n} for v,n in top('dpt')],
                'topActions':[{'action':v,'count':n} for v,n in top('action')],
                'topZones':[{'zone':v,'count':n} for v,n in top('zone')],
                'topDirections':[{'direction':v,'count':n} for v,n in top('direction')],
                'topReasons':[{'reason':v,'count':n} for v,n in top('reason')],
                'syslogEnabled':os.environ.get('SYSLOG_ENABLED','false').lower()=='true',
                'syslogReceived':status[0],'lastSyslogAt':status[1], 'lastFirewallEventAt':status[2],
                'retentionDays':RETENTION_DAYS,
                'events':[{'timestamp':r[0],'action':r[1],'src':r[2],'dst':r[3],
                    'proto':r[4],'port':r[5],'zone':r[6] or 'unknown','direction':r[7] or 'unknown',
                    'interfaceIn':r[8] or '', 'interfaceOut':r[9] or '', 'reason':r[10] or ''} for r in rows],
                'note':'Counts cover logged matching events only; rate-limited logging does not count every blocked packet.'}
    return None


# Firewall Explorer uses the existing firewall table and creates no new schema.
def firewall_explorer(hours=24, filters=None, limit=30, offset=0):
    hours = int(hours)
    if hours not in (1, 24, 168, 720):
        raise ValueError('hours must be 1, 24, 168 or 720')
    limit = int(limit); offset = int(offset)
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise ValueError('limit 1..100 and offset 0..10000 required')
    filters = filters or {}
    # Exact IP or CIDR; blanks mean any address. Reject wildcards and regex.
    ip_filters = {}
    for ip_key in ('src', 'dst'):
        raw = filters.get(ip_key, '')
        if raw is None or str(raw).strip() == '':
            continue
        if not isinstance(raw, str) or len(raw) > 64:
            raise ValueError('invalid '+ip_key+' IP/CIDR')
        try:
            parsed = ipaddress.ip_network(raw, strict=False) if '/' in raw else ipaddress.ip_address(raw)
        except ValueError:
            raise ValueError('invalid '+ip_key+' IP/CIDR; use an IP or subnet such as 192.168.1.0/24')
        ip_filters[ip_key] = str(parsed)
    allowed = {'zone':'zone', 'action':'action', 'direction':'direction',
               'proto':'proto', 'reason':'reason', 'port':'dpt'}
    conditions=['ts >= ?', 'COALESCE(test_event,0)=0']
    args=[int(time.time()) - hours * 3600]
    for param, col in allowed.items():
        val=filters.get(param,'')
        if not val: continue
        if not isinstance(val,str) or len(val)>80 or not re.fullmatch(r'[a-zA-Z0-9.:_/-]+',val):
            raise ValueError('invalid filter '+param)
        conditions.append(col+' = ? COLLATE NOCASE')
        args.append(val)
    for ip_key, ip_filter in ip_filters.items():
        if '/' in ip_filter:
            conditions.append('ip_in_cidr('+ip_key+', ?) = 1')
        else:
            conditions.append(ip_key+' = ? COLLATE NOCASE')
        args.append(ip_filter)
    where=' AND '.join(conditions)
    bucket={1:60, 24:900, 168:3600, 720:14400}[hours]
    with connect() as db:
        def ip_in_cidr(value, cidr):
            try:
                return int(ipaddress.ip_address(value) in ipaddress.ip_network(cidr, strict=False))
            except (ValueError, TypeError):
                return 0
        db.create_function('ip_in_cidr', 2, ip_in_cidr)
        count=db.execute('SELECT COUNT(*) FROM firewall WHERE '+where,args).fetchone()[0]
        columns='ts,action,src,dst,proto,dpt,zone,direction,interface_in,interface_out,reason'
        rows=db.execute('SELECT '+columns+' FROM firewall WHERE '+where+' ORDER BY ts DESC,rowid DESC LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        timeline=db.execute('SELECT (ts / ?) * ? AS slot, action,COUNT(*) FROM firewall WHERE '+where+' GROUP BY slot,action ORDER BY slot', [bucket,bucket]+args).fetchall()
        top_zones=db.execute("SELECT COALESCE(NULLIF(zone,''),'unknown'),COUNT(*) FROM firewall WHERE "+where+' GROUP BY 1 ORDER BY 2 DESC LIMIT 8',args).fetchall()
        top_actions=db.execute('SELECT action,COUNT(*) FROM firewall WHERE '+where+' GROUP BY action ORDER BY 2 DESC LIMIT 8',args).fetchall()
    slots={}
    for ts,action,n in timeline:
        point=slots.setdefault(ts,{'timestamp':ts,'time':dt.datetime.fromtimestamp(ts,dt.timezone.utc).strftime('%d/%m %H:%M' if hours>=168 else '%H:%M'),'DROP':0,'REJECT':0,'BLOCK':0})
        if action in ('DROP','REJECT','BLOCK'):point[action]+=n
    events=[{'timestamp':r[0],'action':r[1] or '', 'src':r[2] or '', 'dst':r[3] or '', 'proto':r[4] or '', 'port':r[5] or '', 'zone':r[6] or 'unknown', 'direction':r[7] or 'unknown','interfaceIn':r[8] or '', 'interfaceOut':r[9] or '', 'reason':r[10] or ''} for r in rows]
    # Fixed object slots permit Homarr JSX to avoid blocked array .map() and .slice().
    event_slots={('event'+str(i+1)):events[i] if i<len(events) else None for i in range(12)}
    return {'updated':int(time.time()), 'hours':hours, 'count':count, 'limit':limit,'offset':offset,
            'hasMore':offset+len(events)<count,'events':events,'eventSlots':event_slots,
            'timeline':list(slots.values()),'bucketSeconds':bucket,
            'topZones':[{'zone':z,'count':n} for z,n in top_zones],
            'topActions':[{'action':a,'count':n} for a,n in top_actions],
            'filters':{**filters, **ip_filters},'note':'Logged events only; rate limits may omit blocked packets. Test events excluded.'}


# Additive monitoring endpoints: no new tables, credentials or router permissions.
def external_class(ip):
    try:
        value=ipaddress.ip_address(str(ip).strip())
    except (ValueError, TypeError):
        return 'unknown'
    if value.is_global:
        return 'public'
    if value.is_private:
        return 'private_or_special'
    return 'reserved_or_special'

def external_traffic(hours=24):
    """Summarize *logged* firewall events; not a packet or connection counter."""
    if hours not in (1, 24, 168, 720):
        raise ValueError('hours must be 1, 24, 168 or 720')
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        local_tz=ZoneInfo(os.environ.get('DISPLAY_TIMEZONE','Europe/Rome'))
    except (ZoneInfoNotFoundError, ValueError):
        local_tz=timezone.utc
    now=int(time.time()); since=now-hours*3600
    bucket={1:60,24:900,168:3600,720:14400}[hours]
    with connect() as db:
        rows=db.execute('SELECT ts,action,src,dst,proto,dpt,zone,direction FROM firewall '
            'WHERE ts >= ? AND COALESCE(test_event,0)=0 ORDER BY ts DESC,rowid DESC',(since,)).fetchall()
    counts={'publicSource':0,'privateOrSpecialSource':0,'reservedOrSpecialSource':0,
            'unknownSource':0,'publicDestination':0,'wanInboundPublicSource':0}
    public_sources={}; all_sources={}; source_networks={}; ports={}; directions={}; actions={}; series={}; examples=[]
    upstream=ipaddress.ip_network('192.168.1.0/24')
    subnet50=ipaddress.ip_network('192.168.50.0/24')
    for ts,action,src,dst,proto,dpt,zone,direction in rows:
        source_kind=external_class(src); destination_kind=external_class(dst)
        key={'public':'publicSource','private_or_special':'privateOrSpecialSource',
             'reserved_or_special':'reservedOrSpecialSource','unknown':'unknownSource'}[source_kind]
        counts[key]+=1
        if destination_kind=='public':counts['publicDestination']+=1
        inbound=source_kind=='public' and (zone or '').lower()=='wan' and (direction or '').upper() in ('INPUT','FORWARD')
        if inbound:counts['wanInboundPublicSource']+=1
        if src:
            all_sources[src]=all_sources.get(src,0)+1
        if source_kind=='public':
            public_sources[src]=public_sources.get(src,0)+1
        try: address=ipaddress.ip_address(str(src).strip())
        except (ValueError,TypeError): address=None
        if address is not None and address in upstream: net='192.168.1.0/24 (rete TIM)'
        elif address is not None and address in subnet50: net='192.168.50.0/24'
        elif source_kind=='public': net='IP globalmente instradabili'
        elif address is not None and address.is_private: net='Altre reti private/speciali'
        else: net='Altri indirizzi / sconosciuti'
        source_networks[net]=source_networks.get(net,0)+1
        if dpt:ports[str(dpt)]=ports.get(str(dpt),0)+1
        d=(direction or 'UNKNOWN').upper();directions[d]=directions.get(d,0)+1
        a=(action or 'UNKNOWN').upper();actions[a]=actions.get(a,0)+1
        if len(examples)<12:
            local=datetime.fromtimestamp(ts,local_tz)
            examples.append({'timestamp':ts,'localTime':local.strftime('%d/%m %H:%M:%S'),
                'action':action,'src':src,'dst':dst,'proto':proto,'port':dpt,'zone':zone,
                'direction':direction,'sourceClass':source_kind,'sourceNetwork':net,
                'wanInboundCandidate':inbound})
        slot=(ts//bucket)*bucket
        item=series.setdefault(slot,{'timestamp':slot,'public':0,'privateOrSpecial':0,'unknown':0})
        if source_kind=='public':item['public']+=1
        elif source_kind=='unknown':item['unknown']+=1
        else:item['privateOrSpecial']+=1
    timeline=[]
    for slot in sorted(series):
        obj=series[slot]
        local=datetime.fromtimestamp(slot,local_tz)
        obj['time']=local.strftime('%H:%M') if hours==1 else local.strftime('%d/%m %H:%M')
        obj['timeZone']=local.strftime('%Z')
        timeline.append(obj)
    def top(d,key):
        return [{key:k,'count':v} for k,v in sorted(d.items(),key=lambda a:(-a[1],a[0]))[:10]]
    return {'updated':now,'hours':hours,'displayTimezone':str(local_tz),'totalEvents':len(rows),**counts,
        'uniquePublicSources':len(public_sources),
        'topPublicSources':top(public_sources,'ip'),'topPublicDestinationPorts':top(ports,'port'),
        'topSources':top(all_sources,'ip'),'topSourceNetworks':top(source_networks,'network'),
        'topDestinationPorts':top(ports,'port'),'topDirections':top(directions,'direction'),
        'topActions':top(actions,'action'),
        'timeline':timeline,'examples':examples,
        'source':'OpenWrt firewall syslog only',
        'note':'These are rate-limited LOG EVENTS, not all traffic. Zone names are firewall log classifications. TIM upstream blocks are not observable.'}

def network_health():
    now=int(time.time())
    with LOCK: state=json.loads(json.dumps(STATE))
    alerts=[]
    def add(code,severity,message):
        alerts.append({'code':code,'severity':severity,'message':message})
    updated=state.get('updated')
    rpc_fresh=bool(updated and now-updated<=max(3*INTERVAL,180) and not state.get('errors'))
    if not updated:add('STARTING','warning','No collector poll completed yet')
    elif now-updated>max(3*INTERVAL,180):add('STALE_POLL','critical','Collector polling data are stale')
    elif state.get('errors'):add('RPC_UNAVAILABLE','critical','OpenWrt RPC polling failed')
    wan_up=(state.get('wan') or {}).get('up') if rpc_fresh else None
    if wan_up is False:add('WAN_DOWN','critical','OpenWrt WAN interface is down')
    weak=[]
    if rpc_fresh:
        weak=[{'mac':c.get('mac'),'ssid':c.get('ssid'),'signal':c.get('signal')} for c in state.get('clients',[]) if isinstance(c.get('signal'),(int,float)) and c['signal']<=WEAK_RSSI]
        if weak:add('WEAK_WIFI','warning',str(len(weak))+' wireless clients at or below '+str(WEAK_RSSI)+' dBm')
    with connect() as db:
        status=db.execute('SELECT last_received,last_firewall FROM syslog_status WHERE id=1').fetchone() or (None,None)
        last=syslog_last=status[0]
        # Use recent samples only; don't label older persisted rows as current packet loss.
        stats=[]
        for target in PING_TARGETS:
            row=db.execute('SELECT COUNT(*),SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END),'
                           'AVG(CASE WHEN ok=1 THEN ms END),MAX(ts) FROM latency WHERE target=? AND ts>=?',
                           (target,now-900)).fetchone()
            total=int(row[0] or 0); good=int(row[1] or 0)
            loss=round((total-good)*100/total,1) if total else None
            stats.append({'target':target,'samples':total,'lossPct':loss,
                          'averageMs':round(row[2],1) if row[2] is not None else None,'lastSample':row[3]})
            if total>=3 and loss is not None and loss>=50:
                add('PING_LOSS_HIGH','critical',target+': '+str(loss)+'% loss over 15m')
            elif total>=3 and loss is not None and loss>=20:
                add('PING_LOSS','warning',target+': '+str(loss)+'% loss over 15m')
            if total>=3 and row[2] is not None and row[2]>=150:
                add('PING_LATENCY','warning',target+': mean RTT '+str(round(row[2],1))+' ms over 15m')
        recent=db.execute('SELECT COUNT(*) FROM device_events WHERE kind=? AND ts>=?',('NEW',now-86400)).fetchone()[0]
    if recent:add('NEW_WIFI_DEVICES','info',str(recent)+' new MAC identities observed in last 24h')
    syslog_enabled=os.environ.get('SYSLOG_ENABLED','false').lower()=='true'
    if syslog_enabled and last is None:
        add('SYSLOG_NOT_SEEN','info','No syslog messages received yet; quiet firewall logging may be normal')
    elif syslog_enabled and now-last>3600:
        add('SYSLOG_QUIET','info','No syslog received for over an hour; this alone does not mean an outage')
    severities={a['severity'] for a in alerts}
    status_value='critical' if 'critical' in severities else 'warning' if 'warning' in severities else 'ok'
    return {'updated':now,'status':status_value,'rpcAvailable':rpc_fresh,'lastPollAt':updated,
            'wanUp':wan_up,'syslogEnabled':syslog_enabled,'lastSyslogAt':last,
            'lastFirewallEventAt':status[1],'weakWifiClients':weak,'newDeviceEvents24h':recent,
            'latency15m':stats,'alerts':alerts,'alertCount':len(alerts),
            'note':'Polling, syslog and ping are independent signals. New devices count observed MAC identities, not necessarily physical devices.'}

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?',1)[0]=='/health':return self.reply(200,{'status':'ok','demo':DEMO})
        if not API_KEY or not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+API_KEY):return self.reply(401,{'error':'unauthorized'})
        from urllib.parse import urlsplit, parse_qs
        parts=urlsplit(self.path)
        if parts.path == '/firewall/events' or parts.path == '/firewall/timeline' or parts.path.startswith('/firewall/explorer/') or parts.path == '/firewall/explorer':
            query=parse_qs(parts.query)
            filters={k:query[k][0] for k in ('zone','action','direction','proto','reason','src','dst','port') if k in query}
            try:
                if parts.path.startswith('/firewall/explorer/'):
                    hours=int(parts.path.rsplit('/',1)[1])
                else:
                    hours=int(query.get('hours',['24'])[0])
                limit=int(query.get('limit',['30'])[0]);offset=int(query.get('offset',['0'])[0])
                result=firewall_explorer(hours, filters,limit,offset)
            except (ValueError, TypeError) as exc:
                return self.reply(400,{'error':str(exc)})
            if parts.path == '/firewall/timeline':
                return self.reply(200,{k:result[k] for k in ('updated','hours','count','bucketSeconds','timeline','filters','note')})
            return self.reply(200,result)
        if parts.path == '/network/health':
            return self.reply(200,network_health())
        if parts.path == '/external/traffic':
            try:
                hours=int(parse_qs(parts.query).get('hours',['24'])[0])
                data=external_traffic(hours)
            except (ValueError,TypeError) as exc:
                return self.reply(400,{'error':str(exc)})
            return self.reply(200,data)
        if parts.path == '/wifi/history':
            query=parse_qs(parts.query)
            try:
                hours=int(query.get('hours',['24'])[0]); limit=int(query.get('limit',['100'])[0])
            except (ValueError, TypeError):return self.reply(400,{'error':'invalid history parameters'})
            if hours not in (1,24,168) or not 1<=limit<=200:return self.reply(400,{'error':'hours must be 1,24,168 and limit 1..200'})
            return self.reply(200,wifi_history(hours,limit))
        if parts.path == '/latency/chart':
            try: hours = int(parse_qs(parts.query).get('hours', ['1'])[0])
            except (ValueError, TypeError): return self.reply(400, {'error':'invalid hours'})
            if hours not in (1,24,168): return self.reply(400, {'error':'hours must be 1, 24, or 168'})
            return self.reply(200,latency_chart(hours))
        if parts.path not in ('/router','/wifi','/latency','/firewall'):return self.reply(404,{'error':'not found'})
        return self.reply(200,payload(parts.path))
    def reply(self,status,obj):
        blob=json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(blob)))
        self.end_headers();self.wfile.write(blob)

if __name__=='__main__':
    if len(API_KEY)<24:raise SystemExit('COLLECTOR_KEY must contain at least 24 characters')
    connect().close()
    threading.Thread(target=poll,daemon=True).start()
    if os.environ.get('SYSLOG_ENABLED','false').lower()=='true':
        threading.Thread(target=lambda: ThreadedUDP(('0.0.0.0',int(os.environ.get('SYSLOG_PORT','5514'))),SyslogHandler).serve_forever(),daemon=True).start()
    port=int(os.environ.get('PORT','8788'))
    http.server.ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
