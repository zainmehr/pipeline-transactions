"""Mécanisme générique de nouvelle tentative (retry) avec attente exponentielle.

    tentative 1 ──échec──> attente 1 s ──> tentative 2 ──échec──> attente 2 s ──> tentative 3
                                                                                    │
                                     échec définitif : RetryError (la pipeline s'arrête)

Seules les erreurs déclarées comme temporaires sont réessayées : une erreur définitive
(donnée invalide, fichier déjà traité, ...) est propagée immédiatement, sans attente.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay: float = 1.0
    backoff: float = 2.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts doit être supérieur ou égal à 1")
        if self.initial_delay < 0 or self.max_delay < 0:
            raise ValueError("les délais doivent être positifs")
        if self.backoff < 1:
            raise ValueError("backoff doit être supérieur ou égal à 1")

    def delay_after(self, attempt: int) -> float:
        """Délai d'attente après l'échec de la tentative numéro `attempt` (à partir de 1)."""
        return min(self.initial_delay * self.backoff ** (attempt - 1), self.max_delay)


class RetryError(Exception):
    """Toutes les tentatives ont échoué."""

    def __init__(self, description: str, attempts: int, last_error: BaseException) -> None:
        self.description = description
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"échec de « {description} » après {attempts} tentative(s) : {_describe(last_error)}"
        )


def _describe(error: BaseException) -> str:
    message = str(error).strip().splitlines()
    return f"{type(error).__name__}: {message[0] if message else ''}"


def retry[T](
    func: Callable[[], T],
    policy: RetryPolicy,
    retry_on: tuple[type[BaseException], ...],
    description: str,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> T:
    """Exécute `func` et la réessaie en cas d'erreur temporaire (de type `retry_on`).

    `sleep` et `log` sont injectables pour que les tests s'exécutent instantanément.
    """
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return func()
        except retry_on as error:
            if attempt == policy.max_attempts:
                log(
                    f"  ÉCHEC : tentative {attempt}/{policy.max_attempts} de « {description} » "
                    f"échouée : {_describe(error)}"
                )
                raise RetryError(description, attempt, error) from error
            delay = policy.delay_after(attempt)
            log(
                f"  ATTENTION : tentative {attempt}/{policy.max_attempts} de « {description} » "
                f"échouée : {_describe(error)}. Nouvelle tentative dans {delay:g} s"
            )
            sleep(delay)
    raise AssertionError("inatteignable")  # pragma: no cover
