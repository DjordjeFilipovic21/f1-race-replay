import { Component, type ReactNode } from 'react'

interface ReplayErrorBoundaryProps {
  readonly children: ReactNode
  readonly label: string
}

interface ReplayErrorBoundaryState {
  readonly error: unknown | null
}

export class ReplayErrorBoundary extends Component<ReplayErrorBoundaryProps, ReplayErrorBoundaryState> {
  public state: ReplayErrorBoundaryState = { error: null }

  public static getDerivedStateFromError(error: unknown): ReplayErrorBoundaryState {
    return { error }
  }

  public render(): ReactNode {
    if (this.state.error === null) return this.props.children

    return (
      <section className="replay-error-boundary" role="alert" aria-label={`${this.props.label} error`}>
        <h2>{this.props.label} unavailable</h2>
        <p>{errorMessage(this.state.error)}</p>
        <button className="retry-button" type="button" onClick={() => this.setState({ error: null })}>Retry {this.props.label.toLowerCase()}</button>
      </section>
    )
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected rendering error occurred.'
}
