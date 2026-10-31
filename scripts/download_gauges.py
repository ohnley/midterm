#!/usr/bin/env python3
"""Download measured water levels around supplied events; standard library only.
Raw responses are cached, not synthesized. UTC filter windows are explicitly based
on --event-timezone; the instructor CSV itself does not identify a timezone.
"""
import argparse, calendar, collections, concurrent.futures, csv, datetime as dt
import hashlib, json, pathlib, statistics, time, urllib.parse, urllib.request
from zoneinfo import ZoneInfo
ROOT = pathlib.Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
USGS_SITES = ('01648000', '01651800')
FIELDS = ['station_id','timestamp_utc','value','unit','parameter','qualifiers','source_timestamp']


def iso(t):
    return t.astimezone(UTC).isoformat().replace('+00:00','Z')


def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    rows=list(rows)
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f, fieldnames=fields or list(rows[0])); w.writeheader();w.writerows(rows)


def read_events(path, zone):
    events=[]
    with path.open(newline='') as f:
        for n,r in enumerate(csv.DictReader(f),1):
            begin=dt.date.fromisoformat(r['begin_date']);end=dt.date.fromisoformat(r['end_date'])
            start=dt.datetime.combine(begin-dt.timedelta(days=1),dt.time(),zone)
            stop=dt.datetime.combine(end+dt.timedelta(days=2),dt.time(),zone)
            events.append(dict(event_id=f'E{n:03d}',start=start.astimezone(UTC),stop=stop.astimezone(UTC)))
    return events


def merge_windows(events):
    out=[]
    for e in sorted(events,key=lambda e:e['start']):
        a,b=e['start'],e['stop']
        if out and a<=out[-1][1]:out[-1]=(out[-1][0],max(b,out[-1][1]))
        else:out.append((a,b))
    return out


def has_usgs_edge_gap(data, a, b):
    """Check actual source timestamps, not advertised query bounds."""
    for series in data.get('value',{}).get('timeSeries',[]):
        ts=sorted({dt.datetime.fromisoformat(v['dateTime'])
                   for group in series.get('values',[]) for v in group.get('value',[])
                   if a<=dt.datetime.fromisoformat(v['dateTime'])<b})
        if len(ts)<2:return True
        deltas=[(y-x).total_seconds() for x,y in zip(ts,ts[1:])]
        cadence=collections.Counter(deltas).most_common(1)[0][0]
        if ts[0]>a+dt.timedelta(seconds=1) or ts[-1]<b-dt.timedelta(seconds=cadence+1):return True
    return False


def request(job, raw):
    kind,a,b,url=job
    nominal_url=url; boundary_padding=False
    fn=raw/(kind+'_'+hashlib.sha256(url.encode()).hexdigest()[:16]+'.json')
    error=''
    for attempt in range(3):
        try:
            if fn.exists():data=json.loads(fn.read_text());cached=True
            else:
                with urllib.request.urlopen(url, timeout=90) as res:body=res.read()
                data=json.loads(body);fn.write_bytes(body);cached=False
            # Legacy IV UTC-bounded queries were observed to omit an hour at
            # winter edges. Verify any edge gap with padded date-only bounds.
            # This also checks suspected source gaps without inventing values.
            if kind=='USGS' and not boundary_padding and has_usgs_edge_gap(data,a,b):
                params=urllib.parse.parse_qs(urllib.parse.urlsplit(nominal_url).query)
                params['startDT']=[(a.date()-dt.timedelta(days=1)).isoformat()]
                params['endDT']=[(b.date()+dt.timedelta(days=1)).isoformat()]
                url='https://waterservices.usgs.gov/nwis/iv/?'+urllib.parse.urlencode(params,doseq=True)
                fn=raw/('USGS_'+hashlib.sha256(url.encode()).hexdigest()[:16]+'.json')
                boundary_padding=True
                if fn.exists():data=json.loads(fn.read_text());cached=True
                else:
                    with urllib.request.urlopen(url,timeout=90) as res:body=res.read()
                    data=json.loads(body);fn.write_bytes(body);cached=False
            error=''
            if 'error' in data:error=str(data['error'])
            return job,data,dict(source=kind,window_start_utc=iso(a),window_end_utc_exclusive=iso(b),url=url,nominal_request_url=nominal_url,boundary_padding=boundary_padding,raw_file=str(fn.relative_to(ROOT)),cached=cached,status='api_error' if error else 'ok',error=error,retrieved_utc=iso(dt.datetime.fromtimestamp(fn.stat().st_mtime,UTC)) if fn.exists() else iso(dt.datetime.now(UTC)))
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'
            time.sleep(attempt+1)
    return job,{},dict(source=kind,window_start_utc=iso(a),window_end_utc_exclusive=iso(b),url=url,nominal_request_url=nominal_url,boundary_padding=boundary_padding,raw_file=str(fn.relative_to(ROOT)),cached=False,status='failed',error=error,retrieved_utc=iso(dt.datetime.fromtimestamp(fn.stat().st_mtime,UTC)) if fn.exists() else iso(dt.datetime.now(UTC)))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--event-timezone',default='EST');ap.add_argument('--workers',type=int,default=4);args=ap.parse_args()
    zone=dt.timezone(dt.timedelta(hours=-5),'EST') if args.event_timezone=='EST' else ZoneInfo(args.event_timezone)
    raw=ROOT/'data/raw/gauges';raw.mkdir(parents=True,exist_ok=True);out=ROOT/'data/processed'
    events=read_events(ROOT/'inputs/DC_flood_times.csv',zone);merged=merge_windows(events);jobs=[]
    for start,stop in merged:
        # Chunk on at most 28 elapsed days: safely below NOAA hourly one-year limit.
        a=start
        while a<stop:
            b=min(stop,a+dt.timedelta(days=28));last=b-dt.timedelta(seconds=1)
            p=dict(product='hourly_height',application='STAT6289',begin_date=a.strftime('%Y%m%d %H:%M'),end_date=last.strftime('%Y%m%d %H:%M'),datum='NAVD',station='8594900',time_zone='gmt',units='metric',format='json')
            jobs.append(('NOAA',a,b,'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?'+urllib.parse.urlencode(p)))
            if b>dt.datetime(2007,10,1,tzinfo=UTC):
                p=dict(format='json',sites=','.join(USGS_SITES),startDT=iso(a),endDT=iso(last),parameterCd='00065',siteStatus='all')
                jobs.append(('USGS',a,b,'https://waterservices.usgs.gov/nwis/iv/?'+urllib.parse.urlencode(p)))
            a=b
    obs={};logs=[];qualifiers={};source_meta={};invalid=collections.Counter()
    print(f'{len(events)} events; {len(merged)} merged windows; {len(jobs)} source requests',flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(request,job,raw) for job in jobs]
        for i,future in enumerate(concurrent.futures.as_completed(futures),1):
            (kind,a,b,url),data,log=future.result();logs.append(log)
            if kind=='USGS':
                for series in data.get('value',{}).get('timeSeries',[]):
                    sid=series['sourceInfo']['siteCode'][0]['value'];source_meta[sid]=series['sourceInfo']
                    if sid not in USGS_SITES:continue
                    for group in series.get('values',[]):
                        for q in group.get('qualifier',[]):qualifiers[q['qualifierCode']]=q['qualifierDescription']
                        for v in group.get('value',[]):
                            timestamp=dt.datetime.fromisoformat(v['dateTime']);value=v['value']
                            if not a<=timestamp<b:continue
                            try:
                                if float(value)==float(series['variable'].get('noDataValue',-999999)):invalid[sid]+=1;continue
                            except ValueError:invalid[sid]+=1;continue
                            obs[(sid,iso(timestamp))]=dict(station_id=sid,timestamp_utc=iso(timestamp),value=value,unit='ft',parameter='00065',qualifiers=';'.join(v.get('qualifiers',[])),source_timestamp=v['dateTime'])
            else:
                for v in data.get('data',[]):
                    timestamp=dt.datetime.strptime(v['t'],'%Y-%m-%d %H:%M').replace(tzinfo=UTC)
                    if not a<=timestamp<b:continue
                    try:float(v['v'])
                    except (ValueError,TypeError):invalid['8594900']+=1;continue
                    obs[('8594900',iso(timestamp))]=dict(station_id='8594900',timestamp_utc=iso(timestamp),value=v['v'],unit='m',parameter='hourly_height',qualifiers='I,L='+v.get('f',''),source_timestamp=v['t']+' GMT')
            if i%10==0 or i==len(jobs):print(f'{i}/{len(jobs)} requests; {len(obs)} observations',flush=True)
    rows=sorted(obs.values(),key=lambda r:(r['station_id'],r['timestamp_utc']))
    write_csv(out/'gauges.csv',rows,FIELDS);write_csv(out/'gauge_retrieval_log.csv',sorted(logs,key=lambda r:(r['source'],r['window_start_utc'])))
    bysite=collections.defaultdict(list)
    for row in rows:bysite[row['station_id']].append(dt.datetime.fromisoformat(row['timestamp_utc'].replace('Z','+00:00')))
    cover=[];gaps=[]
    for e in events:
        for sid in (*USGS_SITES,'8594900'):
            ts=[t for t in bysite[sid] if e['start']<=t<e['stop']]
            deltas=[round((b-a).total_seconds()/60,6) for a,b in zip(ts,ts[1:])]
            interval=60.0 if sid=='8594900' else (collections.Counter(deltas).most_common(1)[0][0] if deltas else None)
            expected=round((e['stop']-e['start']).total_seconds()/60/interval) if interval else ''
            large=[(a,b) for a,b in zip(ts,ts[1:]) if interval and (b-a).total_seconds()/60>interval*1.5]
            for a,b in large:gaps.append(dict(event_id=e['event_id'],station_id=sid,last_before_gap_utc=iso(a),first_after_gap_utc=iso(b),gap_minutes=(b-a).total_seconds()/60,window_modal_interval_minutes=interval))
            status='usable' if ts else ('outside_parameter_record' if sid in USGS_SITES and e['stop']<=dt.datetime(2007,10,1,tzinfo=UTC) else 'no_data_or_access_failure')
            cover.append(dict(event_id=e['event_id'],station_id=sid,window_start_utc=iso(e['start']),window_end_utc_exclusive=iso(e['stop']),n_observations=len(ts),first_observation_utc=iso(ts[0]) if ts else '',last_observation_utc=iso(ts[-1]) if ts else '',modal_interval_minutes=interval or '',expected_at_modal_interval=expected,coverage_fraction_at_modal_interval=min(1,len(ts)/expected) if expected else '',internal_gap_count=len(large),status=status))
    write_csv(out/'event_gauge_coverage.csv',cover)
    write_csv(out/'gauge_gap_intervals.csv',gaps,['event_id','station_id','last_before_gap_utc','first_after_gap_utc','gap_minutes','window_modal_interval_minutes'])
    stations={
        '01648000':dict(source='USGS',name='Rock Creek at Sherrill Drive Washington, DC',latitude=38.9725,longitude=-77.04,parameter='00065',units='ft',nominal_interval_minutes=15,record_start='2007-10-01',record_end='2026-03-19',datum='Local gage datum; current gage zero 148.15 ft NAVD88',source_url='https://waterdata.usgs.gov/monitoring-location/USGS-01648000/',horizontal_crs='WGS84 (EPSG:4326) as current monitoring page and IV JSON label; legacy NWIS site inventory labels these coordinates NAD83 (EPSG:4269)',measurement_type='instantaneous stream stage'),
        '01651800':dict(source='USGS',name='Watts Branch at Washington, DC',latitude=38.90127778,longitude=-76.94327778,parameter='00065',units='ft',nominal_interval_minutes=2,record_start='2007-10-01',record_end='2026-10-18',datum='Local gage datum: 15.74 ft NAVD88 through 2008-09-30; 10.74 ft NAVD88 from 2008-10-01; source heights retained without elevation conversion',source_url='https://waterdata.usgs.gov/monitoring-location/USGS-01651800/',horizontal_crs='WGS84 (EPSG:4326) as current monitoring page and IV JSON label; legacy NWIS site inventory labels these coordinates NAD83 (EPSG:4269)',measurement_type='instantaneous stream stage'),
        '8594900':dict(source='NOAA CO-OPS',name='Washington, DC',latitude=38.873333,longitude=-77.02167,parameter='hourly_height',units='m',nominal_interval_minutes=60,record_start='1924-11-10 (station established, not a completeness claim)',record_end='active at retrieval; actual obtained dates below',datum='NAVD88; requested datum=NAVD',source_url='https://tidesandcurrents.noaa.gov/stationhome.html?id=8594900',horizontal_crs='Geographic decimal degrees; horizontal datum not specified in retrieved station metadata',measurement_type='observed verified tidal water-level height; hourly samples, not hourly mean')}
    for sid,m in stations.items():
        ts=bysite[sid];m.update(obtained_start_utc=iso(ts[0]) if ts else None,obtained_end_utc=iso(ts[-1]) if ts else None,n_observations=len(ts),requested_event_windows=len(events),usable_event_windows=sum(x['n_observations']>0 for x in cover if x['station_id']==sid),rejected_missing_values=invalid[sid],event_window_timezone=args.event_timezone,timestamp_notes='CSV timezone absent. Fixed EST (UTC-05:00, no DST) inferred from exact NOAA Storm Events matches in 1996, 2005, 2019 and merged 2025 windows; not all 125 rows individually verified. USGS source ISO offsets preserved; NOAA gmt responses are UTC. Never relabel naive source times as UTC.',processing='Only actual numeric observations retained; no interpolation. Deduplicated on station_id and UTC instant. Event windows [begin date minus 1 day 00:00, end date plus 2 days 00:00), exclusive right bound.',retrieval_notes='USGS edge gaps are checked by a padded date-only query to avoid observed winter UTC-bound endpoint omission; all timestamps are then filtered to the original UTC window. Genuine absent records are not interpolated.',sampling_notes='Interval may change historically. Metadata nominal interval is representative; actual per-window modal intervals, gaps and counts stored separately. Edge gaps are visible from first/last observation. Data absence is not zero water level.')
        if sid in USGS_SITES:m['qualifier_definitions']=qualifiers;m['source_time_zone_metadata']=source_meta.get(sid,{}).get('timeZoneInfo',{})
        else:m['qualifier_definitions']={'I':'1 if inferred','L':'1 if maximum/minimum expected height limit exceeded'}
    (out/'stations_metadata.json').write_text(json.dumps(stations,indent=2)+'\n')
    summary=dict(event_window_timezone=args.event_timezone,event_timezone_status='fixed EST source-inferred; sample verified against NOAA Storm Events',n_events=len(events),requests=len(logs),failed_requests=sum(r['status']!='ok' for r in logs),n_observations=len(rows),stations={s:{k:v for k,v in m.items() if k in ['n_observations','usable_event_windows','obtained_start_utc','obtained_end_utc','rejected_missing_values']} for s,m in stations.items()})
    (out/'gauge_acquisition_summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__': main()
