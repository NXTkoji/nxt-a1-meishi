import { useCallback, useState } from 'react'
import { useLang } from '../LangContext'

interface Props {
  onFiles: (files: File[]) => void
  disabled?: boolean
}

export function DropZone({ onFiles, disabled }: Props) {
  const { t } = useLang()
  const [dragging, setDragging] = useState(false)

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      if (disabled) return
      const files = Array.from(e.dataTransfer.files).filter(f =>
        f.type.startsWith('image/'),
      )
      if (files.length) onFiles(files)
    },
    [onFiles, disabled],
  )

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length) onFiles(files)
    e.target.value = ''
  }

  return (
    <label
      className={`flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed transition-colors cursor-pointer select-none p-10 min-h-[360px]
        ${dragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'}
        ${disabled ? 'opacity-40 pointer-events-none' : ''}`}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      <svg className="w-12 h-12 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
      </svg>
      <div className="text-center">
        <p className="text-sm font-medium text-gray-700">{t.dropPrompt}</p>
        <p className="text-xs text-gray-500 mt-1">{t.dropOr}</p>
      </div>
      <input
        type="file"
        accept="image/*"
        multiple
        className="sr-only"
        onChange={handleChange}
        disabled={disabled}
      />
    </label>
  )
}
