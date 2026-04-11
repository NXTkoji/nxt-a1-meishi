/**
 * CropModal — drag-to-crop overlay for session temp images.
 *
 * Uses react-image-crop for the interactive crop rectangle.
 * On confirm it calls the backend crop endpoint which overwrites the temp file.
 */
import { useCallback, useRef, useState } from 'react'
import ReactCrop, { type Crop, type PixelCrop, centerCrop, makeAspectCrop } from 'react-image-crop'
import 'react-image-crop/dist/ReactCrop.css'
import { cropImage } from '../api'
import { createPortal } from 'react-dom'

interface Props {
  sessionId: string
  imgId: number
  imageUrl: string
  onDone: () => void   // called after successful crop (parent should refresh)
  onClose: () => void
}

function centerInitialCrop(width: number, height: number): Crop {
  // Start with a crop that covers ~80% of the image centred
  return centerCrop(
    makeAspectCrop({ unit: '%', width: 80 }, width / height, width, height),
    width,
    height,
  )
}

export function CropModal({ sessionId, imgId, imageUrl, onDone, onClose }: Props) {
  const [crop, setCrop] = useState<Crop>()
  const [completedCrop, setCompletedCrop] = useState<PixelCrop>()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string>()
  const imgRef = useRef<HTMLImageElement>(null)

  const onImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const { naturalWidth: w, naturalHeight: h } = e.currentTarget
    setCrop(centerInitialCrop(w, h))
  }, [])

  const handleConfirm = async () => {
    if (!completedCrop || !imgRef.current) return
    const img = imgRef.current
    // completedCrop coords are in display (CSS) pixel space; pass display dimensions
    // so the backend can scale correctly to the file's natural resolution.
    const displayW = img.width
    const displayH = img.height
    if (!displayW || !displayH || completedCrop.width < 1 || completedCrop.height < 1) {
      setError('Select an area first.')
      return
    }
    setSaving(true)
    setError(undefined)
    try {
      await cropImage(sessionId, imgId, {
        x: Math.round(completedCrop.x),
        y: Math.round(completedCrop.y),
        width: Math.round(completedCrop.width),
        height: Math.round(completedCrop.height),
        natural_width: displayW,   // display dimensions (crop coords are relative to these)
        natural_height: displayH,
      })
      onDone()
    } catch (err) {
      setError(`Failed: ${err instanceof Error ? err.message : String(err)}`)
      setSaving(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-3xl w-full max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-800">Crop to card</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        {/* Crop area */}
        <div className="overflow-auto flex-1 flex items-center justify-center p-4 bg-gray-100">
          <ReactCrop
            crop={crop}
            onChange={(c) => setCrop(c)}
            onComplete={(c) => setCompletedCrop(c)}
            className="max-h-[65vh]"
          >
            <img
              ref={imgRef}
              src={imageUrl}
              onLoad={onImageLoad}
              alt="crop"
              style={{ maxHeight: '65vh', maxWidth: '100%', display: 'block' }}
            />
          </ReactCrop>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 gap-3">
          <p className="text-xs text-gray-400">Drag the corners to select the card area</p>
          {error && <p className="text-xs text-red-500">{error}</p>}
          <div className="flex gap-2">
            <button onClick={onClose} className="btn-secondary text-sm px-4 py-1.5">Cancel</button>
            <button
              onClick={handleConfirm}
              disabled={!completedCrop || saving}
              className="btn-primary text-sm px-4 py-1.5 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Apply crop'}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
