#!/usr/bin/env python3
"""Reproduce the required in-DC station investigation and daily PRCP extraction.
Daily dates remain reporting-date labels: no unsupported UTC interval assigned.
"""
import calendar, collections, csv, datetime as dt, json, pathlib, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
URL='https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/USC00186350.dly'

def write(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main():
    raw=ROOT/'data/raw/precipitation';raw.mkdir(parents=True,exist_ok=True)
    path=raw/'USC00186350.dly'
    if not path.exists():path.write_bytes(urllib.request.urlopen(URL,timeout=90).read())
    dates=set();events=[]
    for n,e in enumerate(csv.DictReader((ROOT/'inputs/DC_flood_times.csv').open()),1):
        a=dt.date.fromisoformat(e['begin_date'])-dt.timedelta(days=1);b=dt.date.fromisoformat(e['end_date'])+dt.timedelta(days=1)
        days=[(a+dt.timedelta(days=i)).isoformat() for i in range((b-a).days+1)]
        dates.update(days);events.append((f'E{n:03d}',days))
    records={}
    for line in path.read_text().splitlines():
        if line[17:21]!='PRCP':continue
        year,month=int(line[11:15]),int(line[15:17])
        for day in range(1,calendar.monthrange(year,month)[1]+1):
            date=f'{year:04d}-{month:02d}-{day:02d}'
            if date not in dates:continue
            k=21+(day-1)*8;value=int(line[k:k+5]);mf,qf,sf=[s.strip() for s in line[k+5:k+8]]
            usable=value!=-9999 and not qf and mf!='P'
            records[date]=dict(station_id='USC00186350',date=date,precipitation_mm=value/10 if value!=-9999 else '',measurement_flag=mf,quality_flag=qf,source_flag=sf,usable=usable)
    # Explicit requested missing days, including an absent monthly record.
    for date in dates:
        records.setdefault(date,dict(station_id='USC00186350',date=date,precipitation_mm='',measurement_flag='',quality_flag='',source_flag='',usable=False))
    rows=[records[d] for d in sorted(dates)];coverage=[]
    for eid,days in events:
        coverage.append(dict(event_id=eid,requested_days=len(days),usable_days=sum(records[d]['usable'] for d in days),missing_dates=';'.join(d for d in days if not records[d]['usable'])))
    out=ROOT/'data/processed';out.mkdir(parents=True,exist_ok=True)
    write(out/'in_dc_precipitation.csv',rows);write(out/'in_dc_precipitation_coverage.csv',coverage)
    summary=dict(station_id='USC00186350',station_name='National Arboretum DC',latitude=38.91329,longitude=-76.97009,horizontal_crs='Geographic decimal degrees; datum not specified in retrieved GHCN station listing',elevation_m=15.2,station_detail_url='https://www.ncdc.noaa.gov/cdo-web/datasets/GHCND/stations/GHCND%3AUSC00186350/detail',source_url=URL,inventory_url='https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-inventory.txt',format_documentation='https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt',variable='PRCP',units='mm',native_units='tenths of mm',sampling='Daily accumulation; date is source reporting-date label',timestamp_notes='Observation hour and accumulation boundaries not established. Do not equate printed date to midnight local, UTC day, or PRISM interval.',quality_notes='Raw missing sentinel -9999 becomes empty numeric cell. Blank QFLAG passes source checks. T is trace (numeric zero retained with trace flag); P is missing presumed zero, excluded from usable records. All measurement, quality and source flags retained.',requested_distinct_dates=len(dates),retrieved_numeric_records=sum(r['precipitation_mm']!='' for r in rows),usable_records=sum(r['usable'] for r in rows),unusable_records=sum(not r['usable'] for r in rows),requested_events=len(events),events_with_usable_data=sum(r['usable_days']>0 for r in coverage),fully_covered_events=sum(r['usable_days']==r['requested_days'] for r in coverage),measurement_flags=dict(collections.Counter(r['measurement_flag'] for r in rows)),quality_flags=dict(collections.Counter(r['quality_flag'] for r in rows)),retrieved_utc=dt.datetime.fromtimestamp(path.stat().st_mtime,dt.timezone.utc).isoformat())
    (out/'in_dc_precipitation_metadata.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
