"""Small, read-only interface to the project's integrated HDF5 database."""
from pathlib import Path
import json
import h5py
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from zoneinfo import ZoneInfo

DEFAULT_DB = Path(__file__).parent / "data/processed/dc_flood.h5"


def _frame(group):
    return pd.DataFrame({k: (v.asstr()[:] if v.dtype.kind in "OSU" else v[:])
                         for k, v in group.items() if isinstance(v, h5py.Dataset)})


def list_events(path=DEFAULT_DB):
    """All input events, unchanged source fields, and explicit analysis windows."""
    with h5py.File(path, "r") as h:
        return _frame(h["events"])


def list_data(path=DEFAULT_DB):
    """Available sources, variables, stations, date ranges, and metadata."""
    with h5py.File(path, "r") as h:
        raster = h["rasters/PRISM/ppt"]
        dates = raster["date"].asstr()[:]
        stations = {}
        for sid, g in h["stations"].items():
            times = g["timestamp_utc"].asstr()[:]
            stations[sid] = dict(g.attrs) | {
                "observations": len(times),
                "first_observation": times[0] if len(times) else None,
                "last_observation": times[-1] if len(times) else None,
            }
        investigations = {}
        for name, g in h.get("investigation", {}).items():
            daily_dates = g["date"].asstr()[:]
            investigations[name] = dict(g.attrs) | {"dates_count": len(daily_dates),
                "first_date": daily_dates[0], "last_date": daily_dates[-1]}
        return {"events": len(h["events/event_id"]), "investigation": investigations,
                "PRISM": dict(raster.attrs) | {"dates_count": len(dates),
                    "first_date": dates[0] if len(dates) else None,
                    "last_date": dates[-1] if len(dates) else None},
                "stations": stations, "metadata": dict(h.attrs)}


def _event(h, event_id):
    ids = h["events/event_id"].asstr()[:]
    hits = np.where(ids == event_id)[0]
    if not len(hits):
        raise KeyError(f"Unknown event {event_id}")
    i = int(hits[0])
    return {k: (v.asstr()[i] if v.dtype.kind in "OSU" else v[i])
            for k, v in h["events"].items()}


def get_raster(event_id, date, source="PRISM", path=DEFAULT_DB):
    """Return a stored raster, native cell centers, units, and interval bounds."""
    with h5py.File(path, "r") as h:
        event = _event(h, event_id)
        g = h[f"rasters/{source}/ppt"]
        dates = g["date"].asstr()[:]
        hits = np.where(dates == str(date)[:10])[0]
        if not len(hits):
            raise KeyError(f"No stored {source} raster for {date}")
        i = int(hits[0])
        start, end = g["interval_start_utc"].asstr()[i], g["interval_end_utc"].asstr()[i]
        if not (pd.Timestamp(start) < pd.Timestamp(event["window_end_utc"]) and
                pd.Timestamp(end) > pd.Timestamp(event["window_start_utc"])):
            raise ValueError("Raster interval does not overlap this event's analysis window")
        return {"values": g["values"][i], "x": g["x"][:], "y": g["y"][:],
                "date": dates[i], "interval_start_utc": start, "interval_end_utc": end,
                "event_id": event_id, "source": source, "metadata": dict(g.attrs)}


def get_series(event_id, station_id, start=None, end=None, path=DEFAULT_DB):
    """Return gauge observations within [start,end), bounded by the event window.

    Optional timestamps must include an explicit timezone or UTC offset.
    Source timestamps and quality flags are retained. No interpolation is applied.
    """
    with h5py.File(path, "r") as h:
        event = _event(h, event_id)
        g = h[f"stations/{station_id}"]
        data, metadata = _frame(g), dict(g.attrs)
        cov = _frame(h["coverage/by_event"])
        c = cov.loc[(cov.event_id == event_id) & (cov.station_id == station_id)].iloc[0]
        metadata["modal_interval_minutes"] = c.modal_interval_minutes
    lower, upper = pd.Timestamp(event["window_start_utc"]), pd.Timestamp(event["window_end_utc"])
    for value in (start, end):
        if value is not None and pd.Timestamp(value).tzinfo is None:
            raise ValueError("Use a timezone-aware timestamp, e.g. 2019-07-08T12:00:00Z")
    lower = max(lower, pd.Timestamp(start)) if start else lower
    upper = min(upper, pd.Timestamp(end)) if end else upper
    time = pd.to_datetime(data["timestamp_utc"], utc=True)
    result = data.loc[(time >= lower) & (time < upper)].copy()
    result["timestamp_utc"] = time.loc[result.index]
    result.attrs.update(metadata | {"event_id": event_id, "station_id": station_id,
                                   "window_start_utc": lower.isoformat(),
                                   "window_end_utc": upper.isoformat()})
    return result


def coverage(path=DEFAULT_DB):
    """Compact per-event/source/station coverage table read directly from HDF5."""
    with h5py.File(path, "r") as h:
        return _frame(h["coverage/by_event"])


def plot_raster(event_id, date, source="PRISM", path=DEFAULT_DB, output=None):
    raster = get_raster(event_id, date, source, path)
    fig, ax = plt.subplots(figsize=(6.8, 6))
    mesh = ax.pcolormesh(raster["x"], raster["y"], raster["values"],
                         shading="nearest", cmap="Blues", vmin=0)
    fig.colorbar(mesh, ax=ax, label=f"Daily precipitation ({raster['metadata']['units']})", shrink=.8)
    with h5py.File(path, "r") as h:
        if "metadata/dc_boundary_geojson" in h:
            boundary = json.loads(h["metadata/dc_boundary_geojson"].asstr()[()])
            features = boundary.get("features", [{"geometry": boundary}])
            for feature in features:
                geom = feature["geometry"]
                polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
                for polygon in polys:
                    xy = np.asarray(polygon[0])
                    ax.plot(xy[:, 0], xy[:, 1], color="black", linewidth=1.1, label="DC boundary")
        for sid, g in h["stations"].items():
            if "longitude" in g.attrs and "latitude" in g.attrs:
                ax.plot(g.attrs["longitude"], g.attrs["latitude"], "^", color="#b63737", ms=5)
                ax.annotate(sid, (g.attrs["longitude"], g.attrs["latitude"]),
                            xytext=(4, 4), textcoords="offset points", fontsize=7)
    ax.set(xlabel="Longitude (°)", ylabel="Latitude (°)",
           title=f"{source} precipitation · {event_id} · label {date}\n"
                 f"24 hours ending {raster['interval_end_utc']}")
    ax.set_aspect(1 / np.cos(np.deg2rad(float(np.mean(raster["y"])))) )
    ax.text(.02, .02, "Black: DC boundary   ▲: water-level stations", transform=ax.transAxes,
            fontsize=8, bbox={"facecolor": "white", "alpha": .85, "edgecolor": "none"})
    fig.tight_layout()
    if output:
        fig.savefig(output, dpi=150)
    return fig, ax


def plot_series(event_id, station_id, path=DEFAULT_DB, output=None, timezone="America/New_York"):
    data = get_series(event_id, station_id, path=path)
    fig, ax = plt.subplots(figsize=(9, 3.6))
    lower, upper = pd.Timestamp(data.attrs["window_start_utc"]), pd.Timestamp(data.attrs["window_end_utc"])
    cadence = data.attrs.get("modal_interval_minutes", np.nan)
    if not np.isfinite(cadence):
        cadence = float(data.attrs.get("nominal_interval_minutes", 60))
    time = pd.DatetimeIndex(data["timestamp_utc"])
    # Insert NaN between separated observations so a line never bridges an outage.
    x, y = [], []
    previous = None
    for t, value in zip(time, data["value"]):
        if previous is not None and t - previous > pd.Timedelta(minutes=cadence * 1.5):
            x.append(previous + pd.Timedelta(minutes=cadence)); y.append(np.nan)
        x.append(t); y.append(value); previous = t
    if len(data):
        ax.plot(x, y, color="#21618c", lw=1)
        boundaries = [lower] + list(time) + [upper]
        for a, b in zip(boundaries[:-1], boundaries[1:]):
            if b - a > pd.Timedelta(minutes=cadence * 1.5):
                ax.axvspan(a, b, color="#ddd", alpha=.6)
    else:
        ax.axvspan(lower, upper, color="#ddd", alpha=.7)
        ax.text(.5, .5, "No observations in this event window", transform=ax.transAxes, ha="center")
    with h5py.File(path, "r") as h:
        e = _event(h, event_id)
    ax.axvspan(pd.Timestamp(e["begin_utc"]), pd.Timestamp(e["end_utc"]),
               alpha=.15, color="#b63737", label="Event (source-inferred EST)")
    ax.set_xlim(lower, upper)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M %Z", tz=ZoneInfo(timezone)))
    ax.set(xlabel=f"Time · {timezone} (DST observed); grey = missing interval",
           ylabel=(f"Gauge height ({data.attrs.get('units', '')}; local datum)" if data.attrs.get('parameter') == '00065'
                   else f"Tidal water level ({data.attrs.get('units', '')} NAVD88)"),
           title=f"{station_id} · {data.attrs.get('name', '')} · {event_id}")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=.2)
    fig.autofmt_xdate(rotation=20); fig.tight_layout()
    if output:
        fig.savefig(output, dpi=150)
    return fig, ax
