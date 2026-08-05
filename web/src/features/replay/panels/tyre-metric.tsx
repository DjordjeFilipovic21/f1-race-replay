import type { ReactNode } from 'react'
import hardTyreImage from '../../../assets/tyres/hard.png'
import intermediateTyreImage from '../../../assets/tyres/intermediate.png'
import mediumTyreImage from '../../../assets/tyres/medium.png'
import softTyreImage from '../../../assets/tyres/soft.png'
import wetTyreImage from '../../../assets/tyres/wet.png'

export type TyreCompound = 'SOFT' | 'MEDIUM' | 'HARD' | 'INTERMEDIATE' | 'WET'

const TYRE_IMAGES: Readonly<Record<TyreCompound, string>> = {
  SOFT: softTyreImage,
  MEDIUM: mediumTyreImage,
  HARD: hardTyreImage,
  INTERMEDIATE: intermediateTyreImage,
  WET: wetTyreImage,
}
const TYRE_UNAVAILABLE = 'Unavailable'

/** Renders the shared race/qualifying tyre icon, compound, and sampled age. */
export function formatTyreMetric(tyreCompound: string | null, tyreAge: number | null): ReactNode {
  const compound = tyreCompound?.trim().toUpperCase() as TyreCompound | undefined
  const image = compound === undefined ? undefined : TYRE_IMAGES[compound]
  if (compound === undefined || image === undefined || typeof tyreAge !== 'number' || !Number.isSafeInteger(tyreAge) || tyreAge < 0) {
    return <span className="live-leaderboard__tyre-unavailable" aria-label="Tyres unavailable">{TYRE_UNAVAILABLE}</span>
  }
  const label = `${compound.charAt(0)}${compound.slice(1).toLowerCase()} tyre`
  return (
    <span className="live-leaderboard__tyre" aria-label={`${label}, ${formatTyreAge(tyreAge)}`}>
      <img className="live-leaderboard__tyre-image" src={image} alt={label} />
      <span className="live-leaderboard__tyre-age">{formatTyreAge(tyreAge)}</span>
    </span>
  )
}

function formatTyreAge(age: number): string {
  return `${age} lap${age === 1 ? '' : 's'}`
}
