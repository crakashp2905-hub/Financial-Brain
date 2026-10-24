"""CPU-only Gaussian hidden-state regime model with causal forward filtering.

Parameters are initialized from training-only K-means labels then fitted with
Baum-Welch EM on the training period. Forward filtering is used on evaluation
rows. This is not a validated tradable regime signal.
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
            states: int = 3, seed: int = 7, em_iterations: int = 12) -> "CausalGaussianHMM":
        x = np.asarray(training, float)
        if x.ndim != 2 or x.shape[0] < max(60, 20 * states) or x.shape[1] != len(feature_names):
            raise ValueError("insufficient training observations or feature mismatch")
        if states < 2 or em_iterations < 1 or not np.isfinite(x).all():
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
        model = cls(scaler, means, variances, transition, initial, feature_names)
        model._fit_em(scaled, em_iterations)
        return model

    def _log_emission(self, scaled: np.ndarray) -> np.ndarray:
        return -.5 * np.sum(np.log(2 * np.pi * self.variances)[None, :, :]
                              + (scaled[:, None, :] - self.means[None, :, :]) ** 2
                              / self.variances[None, :, :], axis=2)

    def _fit_em(self, scaled: np.ndarray, iterations: int) -> None:
        """Baum-Welch on training data only; test periods never enter parameter fitting."""
        n, k = len(scaled), len(self.initial)
        for _ in range(iterations):
            emission = self._log_emission(scaled)
            log_transition = np.log(np.maximum(self.transition, 1e-300))
            forward = np.empty((n, k))
            backward = np.zeros((n, k))
            forward[0] = np.log(np.maximum(self.initial, 1e-300)) + emission[0]
            for t in range(1, n):
                forward[t] = emission[t] + logsumexp(forward[t - 1, :, None] + log_transition, axis=0)
            for t in range(n - 2, -1, -1):
                backward[t] = logsumexp(log_transition + emission[t + 1, None, :]
                                         + backward[t + 1, None, :], axis=1)
            normalizer = logsumexp(forward[-1])
            gamma = np.exp(forward + backward - normalizer)
            gamma /= gamma.sum(axis=1, keepdims=True)
            transitions = np.ones((k, k))  # smoothing prevents zero transition probabilities
            for t in range(n - 1):
                joint = forward[t, :, None] + log_transition + emission[t + 1, None, :]
                joint += backward[t + 1, None, :] - normalizer
                transitions += np.exp(joint)
            self.transition = transitions / transitions.sum(axis=1, keepdims=True)
            self.initial = (gamma[0] + 1e-3) / (gamma[0].sum() + k * 1e-3)
            mass = np.maximum(gamma.sum(axis=0), 1e-6)
            self.means = (gamma.T @ scaled) / mass[:, None]
            residual = scaled[:, None, :] - self.means[None, :, :]
            self.variances = np.maximum((gamma[:, :, None] * residual ** 2).sum(axis=0)
                                        / mass[:, None], .05)

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
            log_likelihood = self._log_emission(row[None, :])[0]
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
