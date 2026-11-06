"""The two cheap baselines a model must beat: keyword rules, and a character n-gram Naive Bayes.

Both learn only from the training split. Keyword rules: each category's words from its training
families (a word that names several categories is dropped). Naive Bayes over character 2- to 5-grams
handles abbreviations and keying errors well, which makes it a strong baseline for short noisy text;
it cannot know that two words with no letters in common mean the same thing.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from cpgos.codes.data import CATEGORIES, FAMILIES, Example

_TOKEN = re.compile(r"[a-z&']+")


def _words(t: str) -> list[str]:
    return [w for w in _TOKEN.findall(t.lower()) if len(w) > 1]


class Keywords:
    name = "keyword rules"

    def __init__(self, train: list[Example]):
        owners: dict[str, set] = defaultdict(set)
        for cat in CATEGORIES:
            for fam in FAMILIES[cat][0]:
                for w in _words(fam):
                    owners[w].add(cat)
        self.words = {w: next(iter(c)) for w, c in owners.items() if len(c) == 1}

    def predict(self, text: str) -> str | None:
        votes = Counter(self.words[w] for w in _words(text) if w in self.words)
        return votes.most_common(1)[0][0] if votes else None


class CharNaiveBayes:
    name = "character n-gram Naive Bayes"

    def __init__(self, train: list[Example], n=(2, 5), alpha: float = 0.5):
        self.n, self.alpha = n, alpha
        self.counts = {c: Counter() for c in CATEGORIES}
        prior = Counter(e.category for e in train)
        for e in train:
            self.counts[e.category].update(self._grams(e.text))
        self.vocab = set().union(*self.counts.values())
        self.total = {c: sum(v.values()) for c, v in self.counts.items()}
        self.logprior = {c: math.log(prior[c] / len(train)) for c in CATEGORIES}

    def _grams(self, text: str) -> list[str]:
        t = f" {re.sub(r'[^a-z& ]', ' ', text.lower())} "
        return [t[i:i + k] for k in range(self.n[0], self.n[1] + 1) for i in range(len(t) - k + 1)]

    def _scores(self, text: str) -> dict[str, float]:
        g = self._grams(text)
        v = len(self.vocab)
        return {c: self.logprior[c] + sum(math.log((self.counts[c][x] + self.alpha) / (self.total[c] + self.alpha * v))
                                          for x in g) for c in CATEGORIES}

    def predict(self, text: str) -> str:
        s = self._scores(text)
        return max(s, key=s.get)

    def margin(self, text: str) -> float:
        """How much more likely the best category is than the runner-up, per character n-gram (log scale).
        Naive Bayes probabilities are overconfident on long texts, so the margin is divided by the n-gram count."""
        s = sorted(self._scores(text).values(), reverse=True)
        return (s[0] - s[1]) / max(1, len(self._grams(text)))


class Hybrid:
    """Naive Bayes when it is confident (seen phrasings: right and nearly free), the language model otherwise
    (the only reader that handles unseen wording). The threshold is chosen on the development set."""

    def __init__(self, nb: CharNaiveBayes, llm_predict, threshold: float):
        self.nb, self.llm, self.threshold, self.llm_calls = nb, llm_predict, threshold, 0
        self.name = f"hybrid: Naive Bayes, else the language model below margin {threshold}"

    def predict(self, text: str) -> str | None:
        if self.nb.margin(text) >= self.threshold:
            return self.nb.predict(text)
        self.llm_calls += 1
        return self.llm(text)
