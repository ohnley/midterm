"""Combine processed PRISM grids and observed gauges into one HDF5 database."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import h5py
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data/processed"
UTF8 = h5py.string_dtype("utf-8")


def event_table():
    events = pd.read_csv(ROOT / "inputs/DC_flood_times.csv", dtype=str)
    assert not events.isna().any().any() and not events.duplicated().any()
    events.insert(0, "event_id", [f"E{i + 1:03}" for i in range(len(events))])
    begin = pd.to_datetime(events.begin_date + " " + events.begin_time)
    end = pd.to_datetime(events.end_date + " " + events.end_time)
    assert (end >= begin).all()
    # The CSV omits timezone. Exact NOAA source matches support fixed EST, UTC−05:00.
    local_begin = begin.dt.tz_localize("Etc/GMT+5", ambiguous="raise", nonexistent="raise")
    local_end = end.dt.tz_localize("Etc/GMT+5", ambiguous="raise", nonexistent="raise")
    window_start = (begin.dt.normalize() - pd.Timedelta(days=1)).dt.tz_localize("Etc/GMT+5")
    window_end = (end.dt.normalize() + pd.Timedelta(days=2)).dt.tz_localize("Etc/GMT+5")
    for name, value in [("begin_utc", local_begin), ("end_utc", local_end),
                        ("window_start_utc", window_start), ("window_end_utc", window_end)]:
        events[name] = value.dt.tz_convert("UTC").map(lambda x: x.isoformat())
    return events


def put_table(group, frame):
    for col in frame:
        values = frame[col]
        if pd.api.types.is_numeric_dtype(values):
            group.create_dataset(col, data=values.to_numpy(), compression="gzip")
        else:
            encoded = [v.encode("utf-8") for v in values.astype("string").fillna("")]
            width = max([1] + [len(v) for v in encoded])
            group.create_dataset(col, data=np.asarray(encoded, dtype=h5py.string_dtype("utf-8", width)),
                                 dtype=h5py.string_dtype("utf-8", width), compression="gzip")


def attributes(group, metadata):
    for key, value in metadata.items():
        if value is not None:
            group.attrs[key] = json.dumps(value) if isinstance(value, (dict, list)) else value


def read_metadata(path):
    return json.loads(path.read_text()) if path.exists() else {}


def make_database():
    (ROOT / "outputs").mkdir(exist_ok=True)
    events = event_table()
    prism = np.load(DATA / "prism.npz")
    pmeta = read_metadata(DATA / "prism_metadata.json")
    gauges = pd.read_csv(DATA / "gauges.csv", dtype=str, keep_default_na=False)
    gauges["value"] = pd.to_numeric(gauges["value"], errors="coerce")
    metadata = read_metadata(DATA / "stations_metadata.json")
    gauge_coverage = pd.read_csv(DATA / "event_gauge_coverage.csv", dtype={"station_id": str})
    records = []
    with h5py.File(DATA / "dc_flood.h5", "w") as h:
        attributes(h, {"title": "Washington, DC flood-event environmental observations",
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                        "event_timezone": "Fixed EST (UTC−05:00), source-inferred from exact NOAA Storm Events sample matches",
                        "window_rule": "Local midnight day before event through midnight two days after event end, end exclusive",
                        "input_csv_sha256": hashlib.sha256((ROOT / "inputs/DC_flood_times.csv").read_bytes()).hexdigest(),
                        "missing_values": "NaN for numeric observations; absent time records are not zero",
                        "storage": "float32 precipitation; float64 gauge values; exact-width UTF-8 table strings; gzip with automatic chunks"})
        put_table(h.create_group("events"), events)
        g = h.create_group("rasters/PRISM/ppt")
        dates = np.asarray(prism["dates"], dtype=str)
        precip = prism["precipitation_mm"].astype("float32")
        g.create_dataset("values", data=precip, compression="gzip", shuffle=True).attrs["units"] = "mm"
        g.create_dataset("date", data=dates.astype(object), dtype=UTF8)
        for coordinate in ("x", "y"):
            g.create_dataset(coordinate, data=prism[coordinate]).attrs["units"] = "degrees_east" if coordinate == "x" else "degrees_north"
        interval_end = pd.to_datetime(dates, utc=True) + pd.Timedelta(hours=12)
        interval_start = interval_end - pd.Timedelta(days=1)
        for name, values in [("interval_start_utc", interval_start), ("interval_end_utc", interval_end)]:
            g.create_dataset(name, data=np.array([t.isoformat() for t in values], dtype=object), dtype=UTF8)
        attributes(g, pmeta | {"units": "mm", "variable": "daily precipitation amount",
                               "axis_order": "date,y,x", "stored_nodata": "NaN",
                               "interval": "Label D represents D−1 12:00 UTC to D 12:00 UTC",
                               "processing": "Native-grid spatial crop, no reprojection/resampling; missing pixels become NaN"})
        for _, e in events.iterrows():
            start, end = pd.Timestamp(e.window_start_utc), pd.Timestamp(e.window_end_utc)
            wanted = (interval_start < end) & (interval_end > start)
            values = precip[wanted]
            # Derive the exact requested labels independently of downloaded dates.
            labels = pd.date_range(start.floor("D"), end.ceil("D"), freq="D") + pd.Timedelta(hours=12)
            n_requested = int(((labels - pd.Timedelta(days=1) < end) & (labels > start)).sum())
            n_available = int(np.isfinite(values).any(axis=(1, 2)).sum()) if len(values) else 0
            records.append({"event_id": e.event_id, "source": "PRISM", "station_id": "",
                            "requested_observations": n_requested, "usable_observations": n_available,
                            "missing_values": int(np.isnan(values).sum()),
                            "missing_observations": n_requested - n_available, "gap_count": 0,
                            "modal_interval_minutes": 1440.0,
                            "coverage_fraction": n_available / n_requested,
                            "status": "complete" if n_available == n_requested and not np.isnan(values).any() else "partial" if n_available else "missing",
                            "first_observation_utc": interval_start[wanted][0].isoformat() if wanted.any() else "",
                            "last_observation_utc": interval_end[wanted][-1].isoformat() if wanted.any() else ""})
        stations = h.create_group("stations")
        for sid, meta in metadata.items():
            frame = gauges.loc[gauges.station_id == str(sid)].copy()
            frame = frame.sort_values("timestamp_utc").drop_duplicates("timestamp_utc")
            times = pd.to_datetime(frame.timestamp_utc, utc=True)
            frame["timestamp_utc"] = times.map(lambda x: x.isoformat())
            station = stations.create_group(str(sid))
            put_table(station, frame.drop(columns=["station_id"]))
            attributes(station, meta)
            station["value"].attrs["units"] = meta.get("units", "")
            station["timestamp_utc"].attrs["timezone"] = "UTC"
            for _, e in events.iterrows():
                start, end = pd.Timestamp(e.window_start_utc), pd.Timestamp(e.window_end_utc)
                selected = frame.loc[(times >= start) & (times < end)]
                t = pd.to_datetime(selected.timestamp_utc, utc=True)
                count = int(selected.value.notna().sum())
                source_coverage = gauge_coverage.loc[(gauge_coverage.event_id == e.event_id) &
                                                       (gauge_coverage.station_id == str(sid))].iloc[0]
                cadence = source_coverage.modal_interval_minutes
                expected = source_coverage.expected_at_modal_interval
                gaps = int(source_coverage.internal_gap_count)
                records.append({"event_id": e.event_id, "source": meta.get("source", ""), "station_id": str(sid),
                                "requested_observations": expected, "usable_observations": count,
                                "missing_values": int(selected.value.isna().sum()),
                                "missing_observations": max(expected - count, 0) if pd.notna(expected) else np.nan, "gap_count": gaps,
                                "modal_interval_minutes": cadence,
                                "coverage_fraction": min(count / expected, 1) if pd.notna(expected) and expected > 0 else np.nan,
                                "status": "complete" if count >= expected and gaps == 0 else "partial" if count else "missing",
                                "first_observation_utc": t.iloc[0].isoformat() if len(t) else "",
                                "last_observation_utc": t.iloc[-1].isoformat() if len(t) else ""})
        coverage = pd.DataFrame(records).sort_values(["event_id", "source", "station_id"])
        put_table(h.create_group("coverage/by_event"), coverage)
        mg = h.create_group("metadata")
        mg.create_dataset("input_csv", data=(ROOT / "inputs/DC_flood_times.csv").read_bytes().decode("utf-8"), dtype=UTF8)
        boundary = DATA / "dc_boundary.geojson"
        if boundary.exists():
            mg.create_dataset("dc_boundary_geojson", data=boundary.read_text(), dtype=UTF8)
        for file in sorted(DATA.glob("*.json")):
            mg.create_dataset(file.stem, data=file.read_text(), dtype=UTF8)
        # Preserve the required independent in-DC precipitation investigation.
        rain = DATA / "in_dc_precipitation.csv"
        if rain.exists():
            investigation = h.create_group("investigation/arboretum_precipitation")
            daily_rain = pd.read_csv(rain, keep_default_na=False)
            daily_rain["precipitation_mm"] = pd.to_numeric(daily_rain["precipitation_mm"], errors="coerce")
            daily_rain["reported_precipitation_mm"] = daily_rain["precipitation_mm"]
            daily_rain.loc[daily_rain["usable"].astype(str).str.lower() != "true", "precipitation_mm"] = np.nan
            put_table(investigation, daily_rain)
            attributes(investigation, read_metadata(DATA / "in_dc_precipitation_metadata.json") | {"station_id": "USC00186350", "units": "mm",
                "source": "NOAA NCEI GHCN-Daily", "latitude": 38.91329, "longitude": -76.97009,
                "time_meaning": "Reported daily date; observation-hour boundary not verified; not aligned to PRISM",
                "purpose": "Required independent investigation; not merged with gauge instants",
                "quality_handling": "precipitation_mm masks unusable records to NaN; reported_precipitation_mm and original flags preserve source values"})
        for file in sorted(DATA.glob("*log*.csv")):
            mg.create_dataset(file.stem, data=file.read_text(), dtype=UTF8)
    coverage.to_csv(ROOT / "outputs/coverage_by_event.csv", index=False)
    events.to_csv(ROOT / "outputs/events.csv", index=False)
    merged = coverage.merge(events[["event_id", "begin_date"]], on="event_id")
    merged["year"] = merged.begin_date.str[:4]
    summary = merged.groupby(["source", "station_id", "year"], dropna=False).agg(
        requested_events=("event_id", "size"), events_with_data=("usable_observations", lambda x: int((x > 0).sum())),
        complete_events=("status", lambda x: int((x == "complete").sum())),
        observations_in_event_windows=("usable_observations", "sum"))
    summary.to_csv(ROOT / "outputs/coverage_by_station_year.csv")
    print(f"Built {DATA / 'dc_flood.h5'}: {len(events)} events, {len(dates)} requested raster dates "
          f"({int(np.isfinite(precip).all(axis=(1, 2)).sum())} complete), {len(gauges):,} gauge rows")


if __name__ == "__main__":
    make_database()
