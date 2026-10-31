

In this project, we look at the observed DC water levels across 1996-2025 during flood events and combine that with PRISM daily precipitation data. We analyze this information and organize it and provide some commentary on the data.

## Running this code

This project used Python 3.12.14 on MacOS Golden Gate 27.0.1.

To run the code, open this folder in your terminal and run the following commands:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python demo.py
```

You can find the processed data in the data/processed folder in the dc_flood.h5 file, along with functions, figures and tests. The build_database.py file will recreate everything as needed. 

To download fresh public data:

```bash
python scripts/download_prism.py
python scripts/download_gauges.py
python scripts/investigate_dc_precipitation.py
python build_database.py
python demo.py
```

For your awareness, please note that PRISM is rate-limited.


## Sources and timing

- **Flood-event CSV:** `inputs/DC_flood_times.csv` contains the dates and times of the 125 flood events. This file was provided by the instructor and we use it to choose the when to pull precipitation and water level data points
- **PRISM:** This is daily precipitation estimates on a grid with 800m granularity, with precip data in millimeters, reported to 0.01mm accuracy. A data grid labelled with date D represents precipitation accumulated from 12:00 UTC on D−1 to 12:00 UTC on D. 
- **USGS:** Instantaneous gauge-height observations at Rock Creek / Sherrill Drive (01648000) and Watts Branch (01651800). 
- **NOAA:** Hourly observed water levels at the Washington station (8594900). 
- **Independent precipitation-station investigation:** The National Arboretum station (USC00186350) provides water level metrics. 

In the original flood data CSV from Blackboard, there was no time zone information, but it matched up with the NOAA Storm Events data on EST (UTC-5:00) time zone, so we ran with that assumption for this project. 

We use a window from midnight on the day before the event through midnight two days after the event date -- i.e. the whole event and one calendar day on each side.

## HDF5 layout

```text
/events                  this has the original CSV columns, rows IDs, analysis windows
/rasters/PRISM/ppt       this is data for date × y × x precipitation, cell centers, interval bounds
/stations/{station_id}  observed values, UTC and source timestamps, quality flags
/coverage/by_event      requested/usable observations and missingness per event/source
/investigation          in-DC daily precipitation search evidence, separate day semantics
/metadata               source metadata, unchanged input CSV, DC map boundary
```

The PRISM data uses 32-bit float point numbers but gauge values use 64-bit floating precision. Given the granularity of the data, this doesn't have a practical effect, but we note it here for completeness. 

## How to use the HDF5 File

We supply access to several functions which extract the data from the HDF5 file. You can find these in the flood.py file.

```python
from flood import list_events, list_data, coverage, get_raster, get_series, plot_raster, plot_series

print(list_events()) # prints out the event list
print(list_data()) # prints out the list data
print(coverage()) # prints the coverage statistics

# pulls a specific event 
event = list_events().query("begin_date == '2019-07-08'").event_id.iloc[0]

# gets the raster and series
raster = get_raster(event, '2019-07-09')
series = get_series(event, '01648000')

# plots the data, and outputs to outputs/
plot_raster(event, '2019-07-09', output='rain.png')
plot_series(event, '01648000', output='stage.png')
```


## Data Availability and Processing

The data for this assigned was pulled on October 18, 2026. We have kept all 125 events (1996–2025). Please look at `outputs/coverage_by_event.csv` to see coverage by event/source and in `coverage_by_station_year.csv` you will find the data coverage for the windows around the event. 

Additional information can be found in the markdown files references below as well.

### Final coverage report

| Source | Events with data | event windows | Stored observations |
|---|---:|---:|---:|
| PRISM  | 125/125 | 125/125 | 479 daily grids |
| USGS 01648000 | 87/125 | 87/125 | 24,486 |
| USGS 01651800 | 87/125 | 74/125 | 133,437 |
| NOAA CO-OPS 8594900 | 120/125 | 119/125 | 8,531 |


### Precipitation

We got data from PRISM for all of the 479 labels, spanning January 18, 1996 to July 21, 2025. This data covers the DC area on a 30-arcsecond grid, in millimeters and we did not apply resampling.

The PRISM daily value covers noon UTC on the preceding day through noon UTC on the label date, which means that daily values at edge of an event window may include rain outside that window. Also, the data is daily, and thus cannot show intra-day peaks.


### Water levels and gaps

For our USGS records, these start in October 2007, for the earlier flood windows (38 of them), we did not obtain data. NOAA also did not have data for five windows in November 2003 and June–August 2004. You can find details in the gaps in [event coverage report](outputs/coverage_by_event.csv) and [gauge gap intervals](data/processed/gauge_gap_intervals.csv).

Expected observation counts are estimates based on each window’s normal sampling interval. Missing values and absent observations are reported separately and we do not zero any missing/null values. 

When looking at the USGS gauge heights and NOAA tidal water levels please note that they use different vertical reference points, which means they should not be trusted as absolute elevations. Watts Branch’s gauge datum changed in October 2008. See [gauge sources and limitations](docs/gauge_sources.md) for station details, flags and access information.

### In-DC precipitation investigation

We looked at the National Arboretum precipitation station, which is inside DC. There were 361/372 daily data points that were usable, and all 125 event windows had at least some usable data, and 117 had all requested dates. Six dates were missing and five values flagged as “missing presumed zero” were excluded. Additional information can be found in [in-DC station investigation](docs/in_dc_precipitation_investigation.md).


## AI acknowledgment

We used OpenAI tools to assist with the scripting for this project, in particular the data pull logic. We also used OpenAI tools to help with environment management and package identification.

## Data Sources

- [PRISM datasets and time conventions](https://prism.oregonstate.edu/documents/PRISM_datasets.pdf)
- [PRISM Data Explorer bulk retrieval](https://prism.oregonstate.edu/explorer/bulk.php)
- [PRISM terms and attribution](https://prism.oregonstate.edu/terms/)
- [USGS instantaneous-value services](https://waterservices.usgs.gov/docs/instantaneous-values/)
- [USGS station inventory](https://waterservices.usgs.gov/docs/site-service/)
- [NOAA CO-OPS API](https://api.tidesandcurrents.noaa.gov/api/prod/)
- [NOAA National Arboretum station](https://www.ncdc.noaa.gov/cdo-web/datasets/GHCND/stations/GHCND%3AUSC00186350/detail)
- Instructor-supplied project description, temporal/spatial lecture, and `DC_flood_times.csv`.
- PRISM Climate Group, Oregon State University, https://prism.oregonstate.edu/
