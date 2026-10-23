import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  ChevronLeft,
  ChevronRight,
  Download,
  RotateCcw,
  RotateCw,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';

interface ImageLightboxProps {
  images: string[];
  startIndex?: number;
  alt?: string;
  onClose: () => void;
}

const MIN_SCALE = 0.25;
const MAX_SCALE = 5;
const SCALE_STEP = 0.25;

/**
 * 图片预览（聊天图片用）。
 *
 * 参考常见电商工作台的查看器：黑底全屏、底部悬浮工具条（缩放比例、放大/缩小、
 * 旋转、复位、下载），支持滚轮缩放、放大后拖拽平移、Esc 关闭、多图左右切换。
 */
const ImageLightbox: React.FC<ImageLightboxProps> = ({
  images,
  startIndex = 0,
  alt = '图片预览',
  onClose,
}) => {
  const [index, setIndex] = useState(() => Math.min(Math.max(startIndex, 0), Math.max(images.length - 1, 0)));
  const [scale, setScale] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number } | null>(null);

  const src = images[index] || '';
  const hasMultiple = images.length > 1;

  const resetTransform = useCallback(() => {
    setScale(1);
    setRotation(0);
    setOffset({ x: 0, y: 0 });
  }, []);

  const go = useCallback((delta: number) => {
    setIndex((current) => {
      if (!images.length) return current;
      const next = (current + delta + images.length) % images.length;
      return next;
    });
    resetTransform();
  }, [images.length, resetTransform]);

  const zoom = useCallback((delta: number) => {
    setScale((current) => {
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number((current + delta).toFixed(2))));
      if (next <= 1) setOffset({ x: 0, y: 0 });
      return next;
    });
  }, []);

  // 打开期间锁住页面滚动
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      else if (event.key === '+' || event.key === '=') zoom(SCALE_STEP);
      else if (event.key === '-') zoom(-SCALE_STEP);
      else if (event.key === '0') resetTransform();
      else if (event.key === 'ArrowLeft' && hasMultiple) go(-1);
      else if (event.key === 'ArrowRight' && hasMultiple) go(1);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [go, hasMultiple, onClose, resetTransform, zoom]);

  const handleWheel = (event: React.WheelEvent) => {
    event.preventDefault();
    zoom(event.deltaY < 0 ? SCALE_STEP : -SCALE_STEP);
  };

  const handlePointerDown = (event: React.PointerEvent) => {
    if (scale <= 1) return;
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: offset.x,
      originY: offset.y,
    };
  };

  const handlePointerMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    setOffset({
      x: drag.originX + (event.clientX - drag.startX),
      y: drag.originY + (event.clientY - drag.startY),
    });
  };

  const handlePointerUp = () => {
    dragRef.current = null;
  };

  if (!src) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[10000] flex select-none flex-col bg-black/95 backdrop-blur-md"
      role="dialog"
      aria-modal="true"
      aria-label={alt}
      onClick={(event) => {
        // 点遮罩关闭，点图片/工具条不关闭
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="flex items-center justify-between px-4 py-3 text-white/80">
        <span className="text-xs">
          {hasMultiple ? `${index + 1} / ${images.length}` : ''}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-full p-2 transition-colors hover:bg-white/10 hover:text-white"
          aria-label="关闭预览"
          title="关闭（Esc）"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <div
        className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden"
        onWheel={handleWheel}
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        {hasMultiple && (
          <button
            type="button"
            onClick={() => go(-1)}
            className="absolute left-4 z-10 rounded-full bg-black/40 p-2 text-white/80 transition-colors hover:bg-black/60 hover:text-white"
            aria-label="上一张"
          >
            <ChevronLeft className="h-6 w-6" />
          </button>
        )}

        <img
          src={src}
          alt={alt}
          draggable={false}
          onDoubleClick={() => (scale > 1 ? resetTransform() : setScale(2))}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          className="max-h-full max-w-full object-contain transition-transform duration-75"
          style={{
            transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale}) rotate(${rotation}deg)`,
            cursor: scale > 1 ? (dragRef.current ? 'grabbing' : 'grab') : 'zoom-in',
            touchAction: 'none',
          }}
        />

        {hasMultiple && (
          <button
            type="button"
            onClick={() => go(1)}
            className="absolute right-4 z-10 rounded-full bg-black/40 p-2 text-white/80 transition-colors hover:bg-black/60 hover:text-white"
            aria-label="下一张"
          >
            <ChevronRight className="h-6 w-6" />
          </button>
        )}
      </div>

      <div className="flex items-center justify-center pb-5 pt-2">
        <div className="flex items-center gap-1 rounded-full bg-black/60 px-3 py-2 text-white/90 shadow-lg ring-1 ring-white/10">
          <span className="w-12 text-center text-xs tabular-nums">{Math.round(scale * 100)}%</span>
          <button type="button" onClick={() => zoom(-SCALE_STEP)} className="rounded-full p-2 hover:bg-white/10" title="缩小（-）">
            <ZoomOut className="h-4 w-4" />
          </button>
          <button type="button" onClick={() => zoom(SCALE_STEP)} className="rounded-full p-2 hover:bg-white/10" title="放大（+）">
            <ZoomIn className="h-4 w-4" />
          </button>
          <span className="mx-1 h-4 w-px bg-white/20" />
          <button type="button" onClick={() => setRotation((value) => value + 90)} className="rounded-full p-2 hover:bg-white/10" title="旋转">
            <RotateCw className="h-4 w-4" />
          </button>
          <button type="button" onClick={resetTransform} className="rounded-full p-2 hover:bg-white/10" title="复位（0）">
            <RotateCcw className="h-4 w-4" />
          </button>
          <span className="mx-1 h-4 w-px bg-white/20" />
          <a
            href={src}
            download
            target="_blank"
            rel="noreferrer"
            className="rounded-full p-2 hover:bg-white/10"
            title="下载"
            onClick={(event) => event.stopPropagation()}
          >
            <Download className="h-4 w-4" />
          </a>
        </div>
      </div>
    </div>,
    document.body,
  );
};

export default ImageLightbox;
