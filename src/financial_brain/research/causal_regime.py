"""CPU-only Gaussian hidden-state regime model with causal forward filtering.

Parameters are initialized from training-only K-means labels; this is not a
full maximum-likelihood HMM fit. It is an explicit, reproducible comparator to
deterministic regimes, not a validated tradable regime signal.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import logsumexp
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


@dataclass
class CausalGaussianHMM:
    scaler: StandardScaler
    means: np.ndarray
    variances: np.ndarray
    transition: np.ndarray
    initial: np.ndarray
    feature_names: tuple[str, ...]

    @classmethod
    def fit(cls, training: np.ndarray, *, feature_names: tuple[str, ...],
            states: int = 3, seed: int = 7) -> "CausalGaussianHMM":
        x = np.asarray(training, float)
        if x.ndim != 2 or x.shape[0] < max(60, 20 * states) or x.shape[1] != len(feature_names):
            raise ValueError("insufficient training observations or feature mismatch")
        if states < 2 or not np.isfinite(x).all():
            raise ValueError("invalid HMM configuration or observations")
        scaler = StandardScaler().fit(x)
        scaled = scaler.transform(x)
        labels = KMeans(n_clusters=states, n_init=10, random_state=seed).fit_predict(scaled)
        means = np.stack([scaled[labels == i].mean(axis=0) for i in range(states)])
        variances = np.stack([np.maximum(scaled[labels == i].var(axis=0), .05) for i in range(states)])
        transition = np.ones((states, states), float)  # Laplace smoothing
        for before, after in zip(labels[:-1], labels[1:]):
            transition[before, after] += 1
        transition /= transition.sum(axis=1, keepdims=True)
        initial = np.bincount(labels, minlength=states).astype(float) + 1
        initial /= initial.sum()
        return cls(scaler, means, variances, transition, initial, feature_names)

    def filter(self, observations: np.ndarray, *, prior: np.ndarray | None = None) -> np.ndarray:
        """Posterior at t uses observations through t only, never t+1."""
        x = np.asarray(observations, float)
        if x.ndim != 2 or x.shape[1] != len(self.feature_names) or not np.isfinite(x).all():
            raise ValueError("invalid observations")
        scaled = self.scaler.transform(x)
        belief = self.initial.copy() if prior is None else np.asarray(prior, float).copy()
        if len(belief) != len(self.initial) or (belief < 0).any() or belief.sum() <= 0:
            raise ValueError("invalid prior")
        belief /= belief.sum()
        output = np.zeros((len(x), len(belief)))
        for t, row in enumerate(scaled):
            predicted = belief @ self.transition if t or prior is not None else belief
            log_likelihood = -.5 * np.sum(np.log(2 * np.pi * self.variances)
                                              + (row - self.means) ** 2 / self.variances, axis=1)
            log_posterior = np.log(np.maximum(predicted, 1e-300)) + log_likelihood
            belief = np.exp(log_posterior - logsumexp(log_posterior))
            output[t] = belief
        return output


def compare_regimes(training: np.ndarray, future: np.ndarray, *, feature_names: tuple[str, ...],
                    trend_feature: int = 0, states: int = 3, seed: int = 7) -> dict:
    """Compare train-frozen trend threshold with HMM causal posteriors on future rows."""
    train, test = np.asarray(training, float), np.asarray(future, float)
    if len(test) == 0 or not 0 <= trend_feature < len(feature_names):
        raise ValueError("future observations and a valid trend feature required")
    model = CausalGaussianHMM.fit(train, feature_names=feature_names, states=states, seed=seed)
    lower, upper = np.quantile(train[:, trend_feature], [.33, .67])
    deterministic = np.where(test[:, trend_feature] < lower, 0,
                             np.where(test[:, trend_feature] > upper, 2, 1))
    prior = model.filter(train)[-1]
    probabilities = model.filter(test, prior=prior)
    labels = probabilities.argmax(axis=1)
    return {"deterministic": deterministic, "hmm_probabilities": probabilities,
            "hmm_labels": labels, "agreement": float(np.mean(deterministic == labels)),
            "thresholds": (float(lower), float(upper)),
            "transition": model.transition.copy(), "model": model,
            "warning": "state numbers have no intrinsic bull/bear interpretation"}
