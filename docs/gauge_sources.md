# Water-level gauge source

For this project, we used three different water level stations located inside of DC. The USGS data measure gauge height and the NOAA station measures the observed tidal water level. 


| Station | Latitude, longitude | Measurement | Record used |
|---|---|---|---|
| USGS 01648000, Rock Creek at Sherrill Drive | 38.9725, −77.0400 | Gauge height, feet | October 2007–March 2026 |
| USGS 01651800, Watts Branch | 38.90128, −76.94328 | Gauge height, feet | October 2007–retrieval date |
| NOAA 8594900, Washington | 38.87333, −77.02167 | Hourly observed water level, meters relative to NAVD88 | Requested flood windows, 1996–2025 |

The USGS dates refer to the gauge-height parameter, not discharge data.

## Access and processing

We retrieved the data on October 18, 2026 from [USGS Water Services](https://waterservices.usgs.gov/) and [NOAA CO-OPS](https://tidesandcurrents.noaa.gov/) and the script used to download the data can be found in `scripts/download_gauges.py`. It will pull event windows,source responses and flags, and converts timestamps to UTC, and removes duplicate station/timestamp observations. 

The NOAA inventory endpoint returned HTTP 404, so we just look at the actual data request response to assess NOAA availability. We were able to confirm a gap in USGS data at the end of the 2012 Sandy window.

## Coverage and limitations

| Station | Windows with some data | Complete windows |
|---|---:|---:|
| Rock Creek | 87/125 | 87/125 |
| Watts Branch | 87/125 | 74/125 |
| NOAA Washington | 120/125 | 119/125 |

For the 38 flood events before October 2007, there was no USGS data available for the gauge-height record. NOAA returned no data for five windows in November 2003 and June–August 2004. We estimate if a gauge’s record has gaps by looking at how often it typically reports data. Please see the coverage reports for more info: [event coverage report](../outputs/coverage_by_event.csv) and [gauge gap intervals](../data/processed/gauge_gap_intervals.csv).

- Rock Creek normally reports every 15 minutes and Watts Branch every 2 minutes. NOAA observations are hourly.
- USGS gauge height is measured relatively to a gauge specific zero and the NOAA values use NAVD88. Watts Branch’s zero level changed in October 2008 and we did not apply an correction.
- We kept the USGS quality flags: `A` means approved and `P` means provisional. We also kept NOAA’s flags: `I` identifies an inferred value, and `L` means the value exceeded an expected-height limit.
- Current USGS sources list the coordinates in WGS84, while an older inventory lists them in NAD83. The NOAA metadata we retrieved did not identify a coordinate system.
