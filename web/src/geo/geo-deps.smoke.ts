import { geoOrthographic, geoPath } from 'd3-geo'
import type { FeatureCollection, GeometryObject as GeoJsonGeometryObject } from 'geojson'
import { feature } from 'topojson-client'
import type { GeometryCollection, Topology } from 'topojson-specification'
import worldData from 'world-atlas/countries-110m.json'

const worldTopology = worldData as unknown as Topology
const worldCountries = worldTopology.objects.countries as GeometryCollection

export const geoDependenciesSmoke: {
  countries: FeatureCollection<GeoJsonGeometryObject>
  path: ReturnType<typeof geoPath>
  projection: ReturnType<typeof geoOrthographic>
} = {
  countries: feature(worldTopology, worldCountries),
  path: geoPath(geoOrthographic()),
  projection: geoOrthographic(),
}
