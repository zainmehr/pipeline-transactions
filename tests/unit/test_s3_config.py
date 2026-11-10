from pipeline.storage.s3 import KEY_PREFIX, S3Config, object_key


def test_configuration_par_defaut():
    assert S3Config.from_env({}) == S3Config()


def test_configuration_depuis_l_environnement():
    config = S3Config.from_env(
        {"S3_ENDPOINT_URL": "http://rustfs:9000", "S3_BUCKET": "prod", "S3_ACCESS_KEY": "cle"}
    )
    assert (config.endpoint_url, config.bucket, config.access_key) == (
        "http://rustfs:9000",
        "prod",
        "cle",
    )


def test_description_sans_secret():
    assert "secret-tres-prive" not in S3Config(secret_key="secret-tres-prive").describe()


def test_cle_de_l_objet_derivee_du_contenu():
    empreinte = "ab" * 32
    assert object_key("f.csv", empreinte) == f"{KEY_PREFIX}{'ab' * 8}/f.csv"
    assert object_key("f.csv", "cd" * 32) != object_key("f.csv", empreinte)


def test_list_s3_stockage_inaccessible(monkeypatch, capsys):
    from pipeline.storage.s3 import main

    monkeypatch.setenv("S3_ENDPOINT_URL", "http://127.0.0.1:1")
    assert main() == 1
    assert "stockage S3 inaccessible" in capsys.readouterr().err
