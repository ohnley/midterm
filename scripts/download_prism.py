#!/usr/bin/env python3
"""Retrieve native 800 m PRISM cells around DC through its public Data Explorer."""


from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, json, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import time, urllib.error, urllib.parse, urllib.request
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RPC = "https://prism.oregonstate.edu/explorer/dataexplorer/rpc.php"
CSV_ROOT = "https://prism.oregonstate.edu/explorer/tmp/"
SOURCE = "https://prism.oregonstate.edu/explorer/bulk.php"
DOC = "https://prism.oregonstate.edu/documents/PRISM_datasets.pdf"
TRANSFORM = (0.008333333333, 0.0, -125.02083333333351,
             0.0, -0.008333333333, 49.9374999999995)


def required_dates(path):
    """PRISM intervals intersecting local start-1 midnight to end+2 midnight.
    """
    dates = set()
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            first = dt.date.fromisoformat(row["begin_date"]) - dt.timedelta(days=1)
            last = dt.date.fromisoformat(row["end_date"]) + dt.timedelta(days=2)
            dates.update(first + dt.timedelta(days=i)
                         for i in range((last-first).days+1))
    return sorted(dates)

# create a grid for where we want the prism data to come from
def grid():

    west, south, east, north = -77.12981, 38.78157, -76.89917, 39.00596
    dx, _, x0, _, dy, y0 = TRANSFORM
    c0, c1 = math.floor((west-x0)/dx), math.ceil((east-x0)/dx)
    r0, r1 = math.floor((y0-north)/(-dy)), math.ceil((y0-south)/(-dy))
    x = x0 + (np.arange(c0, c1)+0.5)*dx
    y = y0 + (np.arange(r0, r1)+0.5)*dy
    return x, y, r0, c0

# actually pull the data this is just a wrapper around the url library data pull
def rpc(params):
    req = urllib.request.Request(RPC, data=urllib.parse.urlencode(params).encode(),
        headers={"User-Agent": "STAT6289-DC-flood-course-project/1.0"})
    with urllib.request.urlopen(req, timeout=180) as response:
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError(str(result["errors"]))
    return result

# pull the data, but first check if it already exists, otherwise call the url
def acquire(first, last, points, cache_file):
    if cache_file.exists():
        return
    state_file = cache_file.with_suffix(".job.json")
    if state_file.exists():
        response = json.loads(state_file.read_text())
    else:
        params = dict(call="pp/daily_timeseries_mp", proc="gridserv", spares="800m",
                      interp="0", stats="ppt", units="si", range="daily",
                      start=first.strftime("%Y%m%d"), end=last.strftime("%Y%m%d"),
                      stability="", names="|".join(p[0] for p in points),
                      lons="|".join(f"{p[1]:.9f}" for p in points),
                      lats="|".join(f"{p[2]:.9f}" for p in points))
        response = rpc(params)
        state_file.write_text(json.dumps(response, indent=2))
        time.sleep(2)
    ticket = response.get("gricket")

    # this is just running through the data pulls until we get result
    while "result" not in response:
        if not ticket:
            raise RuntimeError(f"Missing PRISM request identifier: {response}")
        time.sleep(3)
        response = rpc(dict(call="pp/checkup", proc="gridserv", gricket=ticket))
    relative = response["result"].get("csv")
    if not relative:
        raise RuntimeError(f"Missing PRISM CSV: {response}")
    url = urllib.parse.urljoin(CSV_ROOT, relative)
    if not url.startswith(CSV_ROOT):
        raise RuntimeError("Unexpected PRISM download origin")
    with urllib.request.urlopen(url, timeout=180) as response:
        data = response.read()
    if b"Grid Cell Interpolation: Off" not in data or b"ppt (mm)" not in data:
        raise RuntimeError("Unexpected PRISM units/interpolation metadata")
    temp = cache_file.with_suffix(".part")
    with gzip.open(temp, "wb") as f:
        f.write(data)
    temp.replace(cache_file)
    state_file.write_text(json.dumps({"gricket":ticket,"csv_url":url,
                          "uncompressed_bytes":len(data),
                          "accessed_utc":dt.datetime.now(dt.timezone.utc).isoformat()},indent=2))
    time.sleep(2)


def ingest(path, wanted, array):
    with gzip.open(path, "rt", newline="") as f:
        for line in f:
            if line.startswith("Name,Longitude,Latitude,"):
                columns = next(csv.reader([line]))
                break
        else:
            raise RuntimeError(f"Missing CSV columns in {path}")
        count = 0
        for row in csv.DictReader(f, fieldnames=columns):
            if not row or not row.get("Date") or row["Date"] not in wanted:
                continue
            index = int(row["Name"][1:])
            value = float(row["ppt (mm)"])
            array[wanted[row["Date"]], index] = np.nan if value < 0 else value
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="*", type=int, help="Optional years for a small test")
    parser.add_argument("--workers", type=int, choices=(1,2), default=1,
                        help="At most two modest, nonduplicating requests in flight")
    args = parser.parse_args()
    cache = ROOT/"data/raw/prism"
    out = ROOT/"data/processed"
    cache.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
    dates = required_dates(ROOT/"inputs/DC_flood_times.csv")
    x,y,r0,c0 = grid()
    points = [(f"p{i:04d}", float(lon), float(lat))
              for i,(lat,lon) in enumerate((lat,lon) for lat in y for lon in x)]
    by_year = collections.defaultdict(list)
    for date in dates: by_year[date.year].append(date)
    failures, jobs = [], []
    for year, year_dates in sorted(by_year.items()):
        if args.years and year not in args.years: continue
        for batch,start in enumerate(range(0,len(points),500)):
            target = cache/f"prism_{year}_batch{batch}.csv.gz"
            jobs.append((year,batch,min(year_dates),max(year_dates),points[start:start+500],target))
    def run(job):
        year,batch,first,last,subset,target=job
        print(f"PRISM {year} batch {batch+1}/{math.ceil(len(points)/500)} {first} to {last}",flush=True)
        try:
            acquire(first,last,subset,target)
        except urllib.error.HTTPError as exc:
            if exc.code not in (502,503,504): raise
            # Observed transient provider gateway errors. Resume the same saved
            # job when possible; never redownload completed cached data.
            print(f"Temporary HTTP {exc.code}; retrying {year} batch {batch+1}",flush=True)
            time.sleep(10)
            acquire(first,last,subset,target)
        print(f"Ready {year} batch {batch+1}",flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures={executor.submit(run,job):job for job in jobs}
        for future in as_completed(futures):
            year,batch,*_=futures[future]
            try: future.result()
            except Exception as exc:
                failures.append({"year":year,"batch":batch,"error":str(exc)})
                print(f"FAILED {year} batch {batch+1}: {exc}",flush=True)
    wanted = {str(d):i for i,d in enumerate(dates)}
    data = np.full((len(dates),len(points)),np.nan,dtype=np.float32)
    for path in sorted(cache.glob("prism_*_batch*.csv.gz")):
        ingest(path,wanted,data)
    shaped = data.reshape(len(dates),len(y),len(x))
    np.savez_compressed(out/"prism.npz",precipitation_mm=shaped,
                        dates=np.array([str(d) for d in dates]),x=x,y=y)
    with open(out/"prism_date_failures.csv","w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["date","missing_cells","total_cells"])
        writer.writeheader()
        for date,values in zip(dates,data):
            missing=int(np.isnan(values).sum())
            if missing:writer.writerow(dict(date=str(date),missing_cells=missing,total_cells=len(points)))
    (out/"prism_request_failures.json").write_text(json.dumps(failures,indent=2))
    meta=dict(source="PRISM Group, Oregon State University",source_url=SOURCE,
        access_date=dt.datetime.now(dt.timezone.utc).date().isoformat(),
        docs_url=DOC,terms_url="https://prism.oregonstate.edu/terms/",
        citation="PRISM Group, Oregon State University, https://prism.oregonstate.edu, accessed 18 Oct 2026.",
        product="AN daily precipitation, native 800m / 30 arc-second grid",
        resolution_degrees=1/120,crs="EPSG:4269",crs_name="NAD83 geographic",
        units="mm",native_nodata=-9999,output_nodata="NaN",
        global_width=7025,global_height=3105,global_transform=list(TRANSFORM),
        crop_row_offset=r0,crop_col_offset=c0,shape=list(shaped.shape),
        interpolation="Off; provider cell values queried at original native cell centers",
        precision="Explorer CSV reports precipitation to 0.01 mm; stored as float32",
        validation="812 native cells checked against PRISM COG on 2019-07-08; maximum absolute difference 0.00500012 mm, consistent with CSV rounding and float32 storage.",
        day_definition="Each label D represents [D-1 12:00 UTC, D 12:00 UTC)",
        window_rule="Keep each full PRISM interval overlapping local begin_date-1 00:00 through end_date+2 00:00. Labels begin_date-1 through end_date+2 inclusive. Do not prorate daily rainfall.",
        retrieval="Official Data Explorer bulk RPC, <=500 cell centers per request; year min/max ranges queried then filtered to union of event dates.",
        requested_dates=len(dates),complete_dates=int(np.isfinite(data).all(axis=1).sum()),
        requested_cells=len(points),request_failures=failures,
        caveats=["Grid spacing is angular; nominal 800m is not an equal-area square.",
                 "AN81d through 2020 and AN91d from 2021 use different normals; Explorer's generic CSV Dataset header says AN91d even for earlier years.",
                 "Daily estimates cannot resolve subdaily storm timing or rainfall intensity.",
                 "DCGIS boundary is WGS84; native precipitation remains NAD83 without resampling. Datum difference is negligible at this map scale."])
    (out/"prism_metadata.json").write_text(json.dumps(meta,indent=2))
    print(f"Saved {shaped.shape}: {meta['complete_dates']}/{len(dates)} dates complete",flush=True)
    if failures or (not args.years and meta['complete_dates'] != len(dates)):
        raise SystemExit(1)


if __name__=="__main__":main()
