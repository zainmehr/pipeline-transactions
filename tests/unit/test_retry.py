import pytest

from pipeline.retry import RetryError, RetryPolicy, retry


class TemporaryError(Exception):
    pass


class PermanentError(Exception):
    pass


class FlakyFunction:
    """Échoue `failures` fois avec `error`, puis renvoie "ok"."""

    def __init__(self, failures, error=TemporaryError):
        self.failures = failures
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error(f"panne n°{self.calls}")
        return "ok"


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def logs():
    return []


def run(func, sleeps, logs, policy=None):
    return retry(
        func,
        policy or RetryPolicy(max_attempts=3, initial_delay=1, backoff=2),
        (TemporaryError,),
        "opération de test",
        sleep=sleeps.append,
        log=logs.append,
    )


def test_succes_du_premier_coup_sans_attente(sleeps, logs):
    func = FlakyFunction(failures=0)
    assert run(func, sleeps, logs) == "ok"
    assert func.calls == 1
    assert sleeps == []
    assert logs == []


def test_succes_apres_deux_echecs_avec_attente_exponentielle(sleeps, logs):
    func = FlakyFunction(failures=2)
    assert run(func, sleeps, logs) == "ok"
    assert func.calls == 3
    assert sleeps == [1, 2]
    assert len(logs) == 2
    assert "tentative 1/3" in logs[0]
    assert "panne n°1" in logs[0]
    assert "Nouvelle tentative dans 1 s" in logs[0]
    assert "tentative 2/3" in logs[1]


def test_arret_apres_le_nombre_maximal_de_tentatives(sleeps, logs):
    func = FlakyFunction(failures=10)
    with pytest.raises(RetryError) as excinfo:
        run(func, sleeps, logs)

    error = excinfo.value
    assert func.calls == 3
    assert error.attempts == 3
    assert isinstance(error.last_error, TemporaryError)
    assert "après 3 tentative(s)" in str(error)
    assert "panne n°3" in str(error)
    assert sleeps == [1, 2]  # aucune attente inutile après la dernière tentative
    assert "ÉCHEC" in logs[-1]


def test_erreur_definitive_non_reessayee(sleeps, logs):
    func = FlakyFunction(failures=1, error=PermanentError)
    with pytest.raises(PermanentError):
        run(func, sleeps, logs)
    assert func.calls == 1
    assert sleeps == []


def test_une_seule_tentative(sleeps, logs):
    with pytest.raises(RetryError):
        run(FlakyFunction(failures=1), sleeps, logs, RetryPolicy(max_attempts=1))
    assert sleeps == []


def test_delai_plafonne():
    policy = RetryPolicy(max_attempts=5, initial_delay=10, backoff=3, max_delay=20)
    assert [policy.delay_after(n) for n in range(1, 5)] == [10, 20, 20, 20]


@pytest.mark.parametrize(
    "kwargs",
    [{"max_attempts": 0}, {"initial_delay": -1}, {"max_delay": -1}, {"backoff": 0.5}],
)
def test_politique_invalide(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)
