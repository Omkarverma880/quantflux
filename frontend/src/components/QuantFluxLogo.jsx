import React from 'react';

/**
 * QuantFlux brand SVG icon — waveform pulse mark.
 * Used in collapsed sidebar, mobile bar, and favicon contexts.
 */
export function LogoIcon({ size = 32, className = '' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* Background circle */}
      <rect width="40" height="40" rx="10" fill="url(#bg_grad)" />
      {/* Waveform path */}
      <path
        d="M6 22 C8 22, 9 14, 11 14 S13 26, 15 26 S17 10, 20 10 S23 28, 25 28 S27 16, 29 16 S31 22, 34 22"
        stroke="url(#wave_grad)"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
      {/* Glow dots at peaks */}
      <circle cx="15" cy="26" r="2" fill="#a855f7" opacity="0.8" />
      <circle cx="20" cy="10" r="2" fill="#22d3ee" opacity="0.9" />
      <circle cx="25" cy="28" r="2" fill="#10b981" opacity="0.7" />
      <defs>
        <linearGradient id="bg_grad" x1="0" y1="0" x2="40" y2="40">
          <stop offset="0%" stopColor="#0c1528" />
          <stop offset="100%" stopColor="#111d35" />
        </linearGradient>
        <linearGradient id="wave_grad" x1="6" y1="20" x2="34" y2="20">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="40%" stopColor="#22d3ee" />
          <stop offset="70%" stopColor="#8b5cf6" />
          <stop offset="100%" stopColor="#6366f1" />
        </linearGradient>
      </defs>
    </svg>
  );
}

/**
 * Full QuantFlux brand logo — icon + wordmark side by side.
 * Used in expanded sidebar and login page.
 */
export function LogoFull({ iconSize = 32, className = '' }) {
  return (
    <div className={`flex items-center gap-2.5 select-none ${className}`} draggable={false}>
      <LogoIcon size={iconSize} />
      <span className="text-lg font-bold tracking-tight">
        <span className="text-white">Quant</span>
        <span className="text-brand-400">Flux</span>
      </span>
    </div>
  );
}
