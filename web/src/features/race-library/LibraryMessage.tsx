interface LibraryMessageProps {
  readonly variant: 'loading' | 'empty' | 'error'
  readonly title: string
  readonly message: string
  readonly onRetry?: () => void
}

export function LibraryMessage({ variant, title, message, onRetry }: LibraryMessageProps) {
  if (variant === 'loading') {
    return (
      <div className="library-loading" role="status" aria-label={title}>
        <h3 className="library-loading__title">{title}</h3>
        <p className="library-loading__message">{message}</p>
      </div>
    )
  }

  if (variant === 'error') {
    return (
      <div className="library-error" role="alert" aria-label={title}>
        <h3 className="library-error__title">{title}</h3>
        <p className="library-error__message">{message}</p>
        {onRetry && (
          <button type="button" className="library-error__action" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="library-empty" role="status" aria-label={title}>
      <h3 className="library-empty__title">{title}</h3>
      <p className="library-empty__message">{message}</p>
    </div>
  )
}
