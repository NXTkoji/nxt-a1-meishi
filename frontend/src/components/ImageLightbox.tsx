import { useState } from 'react'
import { createPortal } from 'react-dom'

interface Props {
  src: string
  alt: string
  className?: string
}

export function LightboxImage({ src, alt, className }: Props) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <img
        src={src}
        alt={alt}
        className={`cursor-zoom-in ${className ?? ''}`}
        onClick={() => setOpen(true)}
        draggable={false}
      />
      {open && createPortal(
        <div
          className="fixed inset-0 z-50 bg-black/85 flex items-center justify-center p-6"
          onClick={() => setOpen(false)}
        >
          <img
            src={src}
            alt={alt}
            className="max-h-[90vh] max-w-[90vw] object-contain rounded-lg shadow-2xl"
            onClick={e => e.stopPropagation()}
          />
          <button
            className="absolute top-4 right-4 text-white/70 hover:text-white text-3xl leading-none"
            onClick={() => setOpen(false)}
            aria-label="Close"
          >
            ✕
          </button>
        </div>,
        document.body,
      )}
    </>
  )
}
