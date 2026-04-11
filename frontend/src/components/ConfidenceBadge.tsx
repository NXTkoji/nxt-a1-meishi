interface Props {
  confidence: number
}

export function ConfidenceBadge({ confidence }: Props) {
  const pct = Math.round(confidence * 100)
  const color =
    confidence >= 0.9
      ? 'bg-green-100 text-green-700'
      : confidence >= 0.7
      ? 'bg-yellow-100 text-yellow-700'
      : 'bg-red-100 text-red-700'

  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${color}`}>
      {pct}%
    </span>
  )
}
