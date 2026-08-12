/*
    `intensity.index` must not reach any model.

    M2 findings B03/B04: the ESO recalibrates its band thresholds as the grid
    decarbonises, so the moderate/high boundary walked from 260 gCO2/kWh in 2018
    to 170 in 2026. The bands overlap heavily and the label encodes the
    publication year as much as the intensity.

    A model using it would partly be learning what year it is, then be asked to
    predict a year it has never seen. This test asserts no column named like an
    index band exists anywhere in staging or marts, so the exclusion survives
    somebody adding a column in good faith.
*/

select
    table_schema,
    table_name,
    column_name
from information_schema.columns
where table_schema in ('staging', 'marts')
  and column_name in ('index', 'intensity_index', 'index_band')
