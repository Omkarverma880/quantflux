"""
Numpy logistic regression with the metrics a trader needs to decide whether to trust it.

No scikit-learn on the server, and none needed: an L2-regularised logit solved by
Newton/IRLS converges in a handful of iterations on ~40k rows × 12 features.
"""
from __future__ import annotations

import numpy as np


class Logit:
    def __init__(self, l2: float = 2.0):
        self.l2 = l2
        self.mean = self.std = self.w = None

    def _prep(self, X: np.ndarray) -> np.ndarray:
        Z = (X - self.mean) / self.std
        Z = np.nan_to_num(Z, nan=0.0)                  # missing feature = average value
        return np.hstack([np.ones((len(Z), 1)), np.clip(Z, -6, 6)])

    def fit(self, X: np.ndarray, y: np.ndarray, iters: int = 30) -> "Logit":
        X = np.asarray(X, float)
        self.mean = np.nanmean(X, axis=0)
        sd = np.nanstd(X, axis=0)
        self.std = np.where(sd > 1e-9, sd, 1.0)
        A = self._prep(X)
        y = np.asarray(y, float)
        w = np.zeros(A.shape[1])
        R = np.eye(A.shape[1]) * self.l2
        R[0, 0] = 0.0
        for _ in range(iters):
            p = 1 / (1 + np.exp(-np.clip(A @ w, -30, 30)))
            W = np.clip(p * (1 - p), 1e-6, None)
            g = A.T @ (p - y) + R @ w
            H = (A * W[:, None]).T @ A + R
            step = np.linalg.solve(H, g)
            w -= step
            if np.max(np.abs(step)) < 1e-7:
                break
        self.w = w
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        A = self._prep(np.atleast_2d(np.asarray(X, float)))
        return 1 / (1 + np.exp(-np.clip(A @ self.w, -30, 30)))

    def coefficients(self, names: list[str]) -> list[dict]:
        """Standardised coefficients: effect of a one-standard-deviation change."""
        return sorted(({"feature": n, "coef": round(float(c), 4)} for n, c in zip(names, self.w[1:])),
                      key=lambda d: -abs(d["coef"]))


class Reach:
    """Ridge regression on log(1 + distance) with the empirical residual distribution.

    Predicts how far spot will still travel in one direction before the close (in ATM
    straddles), and the odds of travelling at least ``x`` — read from the residuals the
    model actually made in training, not from an assumed normal curve."""

    def __init__(self, l2: float = 2.0):
        self.l2 = l2
        self.mean = self.std = self.w = self.resid = None

    _prep = Logit._prep

    def fit(self, X: np.ndarray, dist: np.ndarray) -> "Reach":
        X = np.asarray(X, float)
        self.mean = np.nanmean(X, axis=0)
        sd = np.nanstd(X, axis=0)
        self.std = np.where(sd > 1e-9, sd, 1.0)
        A = self._prep(X)
        y = np.log1p(np.clip(np.asarray(dist, float), 0, None))
        R = np.eye(A.shape[1]) * self.l2
        R[0, 0] = 0.0
        self.w = np.linalg.solve(A.T @ A + R, A.T @ y)
        r = np.sort(y - A @ self.w)
        self.resid = r[np.linspace(0, len(r) - 1, min(len(r), 2000)).astype(int)]
        return self

    def _log_pred(self, X) -> np.ndarray:
        return self._prep(np.atleast_2d(np.asarray(X, float))) @ self.w

    def quantiles(self, X, qs=(0.2, 0.5, 0.8)) -> np.ndarray:
        """Distance quantiles (straddles), shape (n, len(qs))."""
        lp = self._log_pred(X)[:, None]
        return np.clip(np.expm1(lp + np.quantile(self.resid, qs)[None, :]), 0, None)

    def prob_at_least(self, X, x: float) -> float:
        lp = float(self._log_pred(X)[0])
        return float((self.resid >= np.log1p(max(x, 0.0)) - lp).mean())

    def evaluate(self, X, dist) -> dict:
        dist = np.clip(np.asarray(dist, float), 0, None)
        q = self.quantiles(X, (0.2, 0.5, 0.8))
        lp = self._log_pred(X)
        corr = np.corrcoef(lp, np.log1p(dist))[0, 1] if len(dist) > 2 else np.nan
        return {
            "n": int(len(dist)),
            "corr": round(float(corr), 3) if np.isfinite(corr) else None,
            "band_coverage": round(float(((dist >= q[:, 0]) & (dist <= q[:, 2])).mean()), 3),
            "median_abs_error": round(float(np.median(np.abs(q[:, 1] - dist))), 3),
        }


def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    y = np.asarray(y).astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if not n1 or not n0:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    # average ranks for ties
    ps = np.asarray(p)[order]
    i = 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2
        i = j + 1
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def evaluate(y: np.ndarray, p: np.ndarray, bins: int = 5) -> dict:
    y = np.asarray(y, float)
    p = np.asarray(p, float)
    if not len(y):
        return {"n": 0}
    calib = []
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    for i in range(bins):
        m = (p >= edges[i]) & ((p <= edges[i + 1]) if i == bins - 1 else (p < edges[i + 1]))
        if m.sum():
            calib.append({"predicted": round(float(p[m].mean()), 3),
                          "actual": round(float(y[m].mean()), 3), "n": int(m.sum())})
    a = auc(y, p)
    base = float(y.mean())
    top = p >= np.quantile(p, 0.8)
    bot = p <= np.quantile(p, 0.2)
    return {
        "n": int(len(y)),
        "base_rate": round(base, 3),
        "accuracy": round(float(((p >= 0.5) == (y >= 0.5)).mean()), 3),
        "naive_accuracy": round(max(base, 1 - base), 3),
        "auc": round(a, 3) if a is not None else None,
        "brier": round(float(np.mean((p - y) ** 2)), 4),
        "top_quintile_hit": round(float(y[top].mean()), 3) if top.any() else None,
        "bottom_quintile_hit": round(float(y[bot].mean()), 3) if bot.any() else None,
        "calibration": calib,
    }


def verdict(auc_value: float | None) -> dict:
    """Plain-language grade of an out-of-sample AUC."""
    if auc_value is None:
        return {"grade": "n/a", "text": "Not enough out-of-sample data to judge."}
    if auc_value < 0.53:
        return {"grade": "none", "text": "No reliable edge on unseen data — a coin flip. Shown for transparency; "
                                         "it carries no weight in entry scores."}
    if auc_value < 0.60:
        return {"grade": "weak", "text": "A small edge on unseen data. A tie-breaker at most."}
    if auc_value < 0.75:
        return {"grade": "useful", "text": "A meaningful edge on unseen data. Still probabilistic."}
    return {"grade": "strong", "text": "Strong separation on unseen data, and the probabilities line up with what "
                                       "actually happened (see calibration)."}
