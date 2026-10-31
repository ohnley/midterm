"""Recreate the representative-event figures using only the finished HDF5 file."""
from pathlib import Path
import matplotlib.pyplot as plt
from flood import list_events, list_data, coverage, get_raster, get_series, plot_raster, plot_series

OUT = Path(__file__).parent / "outputs"
OUT.mkdir(exist_ok=True)
events = list_events()
event = events.loc[events.begin_date == "2019-07-08", "event_id"].iloc[0]
print(events.loc[events.event_id == event].to_string(index=False))
print(coverage().query("event_id == @event").to_string(index=False))
# July 8 daytime rain belongs to the daily grid ending July 9 at noon UTC.
raster = get_raster(event, "2019-07-09")
plot_raster(event, "2019-07-09", output=OUT / "precipitation_2019-07-08.png")
for station in list_data()["stations"]:
    series = get_series(event, station)
    print(f"{station}: {len(series)} observations")
    plot_series(event, station, output=OUT / f"water_level_2019-07-08_{station}.png")
plt.close("all")
