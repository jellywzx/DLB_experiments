from pathlib import Path
import pandas as pd,numpy as np,json
from shapely.geometry import box
from shapely.ops import split,unary_union
from cartopy.io.shapereader import Reader
from pyproj import Geod
p=Path(__file__).resolve().parent;path=Path('/share/home/dq134/.local/share/cartopy/shapefiles/natural_earth/cultural/ne_10m_admin_0_boundary_lines_land.shp');recs=[r for r in Reader(path).records() if r.attributes.get('ADM0_LEFT')=='Canada' and r.attributes.get('ADM0_RIGHT')=='United States of America' and r.attributes.get('TYPE')=='Land'];bd=unary_union([r.geometry for r in recs]).intersection(box(-116,48.8,-104,49.2));pieces=split(box(-116,40,-104,52),bd);ca=[g for g in pieces.geoms if g.centroid.y>49][0];print('PIECES',[(v.area,v.centroid.y) for v in pieces.geoms]);grid=pd.read_csv(p/'input_grid_area.csv');geod=Geod(ellps='WGS84');rows=[]
for z in grid.itertuples():
 cell=box(z.lon-.05,z.lat-.05,z.lon+.05,z.lat+.05);geom=cell.intersection(ca)
 if not geom.is_empty and geom.area>1e-12:
  ar=abs(geod.geometry_area_perimeter(geom)[0])/1e6;rows.append({'grid_x':z.grid_x_1based,'grid_y':z.grid_y_1based,'lon':z.lon,'lat':z.lat,'mapped_area_km2':z.assigned_area_km2,'boundary_canada_overlap_km2':ar,'upper_bound_target_canada_km2':min(ar,z.assigned_area_km2)})
pd.DataFrame(rows).to_csv(p/'border_overlap_cells.csv',index=False);s={'method':'Natural Earth 10m Canada-USA Land boundary split polygon; intersect each 0.1deg input cell; WGS84 geodesic area. inpmat does not locate fractions inside a cell, so only upper bounds possible.','overlap_cells':len(rows),'canada_overlap_whole_cell_km2':sum(r['boundary_canada_overlap_km2'] for r in rows),'target_canada_upper_bound_km2':sum(r['upper_bound_target_canada_km2'] for r in rows),'center_canada_count':int(grid.canada_center.sum()),'warning':'Boundary data offset from nominal 49N by about 0.0075 degrees; subgrid fringe is resolution-sensitive, not evidence of Milk River connection.'};(p/'border_intersection.json').write_text(json.dumps(s,indent=2));print(s)
