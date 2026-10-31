# In-DC precipitation investigation

For this part of the project, we used a station (National Arboretum) located inside of DC to get precipitation observations from 1996–2025. We kept these data separate from PRISM because we could not fully confirm the station’s reporting times.

## Station information

The National Arboretum DC station (USC00186350) data is in the NOAA NCEI’s GHCN-Daily dataset. The station is located at 38.91329° N, 76.97009° W, with an elevation of 15.2 meters. You can also find more info here: [NCEI station page](https://www.ncdc.noaa.gov/cdo-web/datasets/GHCND/stations/GHCND%3AUSC00186350/detail) and [USDA places the Arboretum in northeast Washington](https://www.usna.usda.gov/about/who-we-are-about-the-arboretum/). 

## How we found and downloaded the data

We searched the [GHCN station list](https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt) and [precipitation inventory](https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-inventory.txt), using DC labels as well as station names and coordinates near DC.

The bulk station list appears to incorrectly label the National Arboretum station as MD, however, the actual coordinates and the NCEI station page confirm its location in DC. We considered other station data (Georgetown), but their records ended too early to be useful for this project. The DC CoCoRaHS records we found begin in 2006 or later, so they could not cover the earlier events by themselves and we ignored it.

We downloaded the [station archive](https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/USC00186350.dly) on October 18, 2026, without any special access accounts or access keys. However, we were unable to get information on the station observation hours due to the request failing.

## Processing the data

We used the daily precipitation variable, `PRCP` and the format documentation [format documentation](https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt) to read the records. We did a unit change from tenths of millimeters to millimeters and kept the measurement, quality, and source flags.

We selected reporting dates from the day before each event’s start date through the day after its end date. The code is in [investigate_dc_precipitation.py](../scripts/investigate_dc_precipitation.py). To run it from the project folder:

```bash
python scripts/investigate_dc_precipitation.py
```

## Data availability and missing values

The 125 event windows contained 372 distinct reporting dates. Of these, 361 had usable precipitation values. All 125 windows had at least some usable data, and 117 windows had usable data for every requested date.

We populate missing or erroneous data as follows:

- `-9999` represents a missing value. We did not treat it as zero rainfall.
- `P` means “missing presumed zero.” We excluded these values from usable coverage.
- `T` represents trace precipitation. We kept its numeric zero and the trace flag so it can be distinguished from an ordinary zero.
- We counted a value as usable only if its quality flag was blank.

The [coverage table](../data/processed/in_dc_precipitation_coverage.csv) contains event counts and missing or unusable dates. The [metadata file](../data/processed/in_dc_precipitation_metadata.json) contains station information and overall totals.

## Limitations

Obviously, the station only measures rainfall at one location in DC. Additionally, we just get daily rainfall totals, not when rainfall peaked within a day.

The archive gives reporting dates but not observation hours. We could not confirm the exact start and end of each accumulation period, so we did not assume that a station date covers the same hours as a PRISM date.
