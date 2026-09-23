import React from 'react';
import { ArrowUpRight, History } from 'lucide-react';
import { N, PCT, RS_, STAGE_STYLE, tone } from './ui';

/**
 * The stage board: how many stocks sit at each point of the trade, and what moved between
 * stages since the previous scan. Tap a stage to see the names.
 */
export default function Board({ board = [], changes = [], stage, setStage, headline }) {
  return (
    <div className="card !p-4 lg:!p-5">
      <h2 className="text-[19px] sm:text-[22px] font-bold text-white">{headline}</h2>
      <p className="text-[12.5px] text-gray-500 mt-0.5 mb-3">
        Every setup this scan finds, at the stage it has reached — tap a stage to see the names.
      </p>
      <div className="grid lg:grid-cols-[minmax(0,420px)_1fr] gap-3">
        <div className="grid grid-cols-2 gap-2 content-start">
          {board.map((b) => {
            const st = STAGE_STYLE[b.stage] || STAGE_STYLE.PLAYED_OUT;
            const on = stage === b.stage;
            return (
              <button key={b.stage} onClick={() => setStage(on ? '' : b.stage)}
                className={`text-left rounded-xl border px-3 py-2.5 transition ${on ? st.ring : 'border-surface-3 bg-surface-2/40 hover:bg-surface-2'}`}>
                <div className="flex items-center justify-between">
                  <span className={`text-[22px] font-bold ${on ? st.text : 'text-gray-100'}`}>{b.count}</span>
                  <span className="text-[10px] text-gray-500">{on ? 'showing' : 'tap to view ›'}</span>
                </div>
                <div className="text-[12.5px] font-semibold text-gray-200">{b.label}</div>
                <div className="text-[10.5px] text-gray-500 italic">{b.blurb}</div>
              </button>
            );
          })}
        </div>

        <div className="rounded-xl border border-surface-3 bg-surface-2/30 p-3">
          <div className="flex items-center gap-1.5 text-[12px] font-semibold text-gray-300 mb-2">
            <History className="w-3.5 h-3.5 text-gray-500" />What changed since last close?
          </div>
          {!changes.length ? (
            <div className="text-[12px] text-gray-500 py-4 text-center">
              Nothing moved between stages — or this is the first scan, so there is nothing to compare with yet.
            </div>
          ) : (
            <div className="space-y-1.5 max-h-[260px] overflow-y-auto pr-1">
              {changes.map((c, i) => (
                <div key={i} className="flex items-start justify-between gap-3 rounded-lg bg-surface-1/60 px-2.5 py-1.5">
                  <div className="text-[12px] text-gray-300 min-w-0">
                    <span className="font-semibold text-white">{c.name || c.symbol}</span>{' '}
                    <span className="text-gray-400">{c.text}</span>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-[12px] mono text-gray-200">{RS_(c.close)}</div>
                    {c.change_pct != null && (
                      <div className={`text-[11px] mono ${tone(c.change_pct)}`}>{PCT(c.change_pct)}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
