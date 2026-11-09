from pipeline.loading.csv_loader import compute_file_hash
from pipeline.main import main
from pipeline.storage.database import DatabaseConfig, load_schema


def test_configuration_par_defaut():
    assert DatabaseConfig.from_env({}) == DatabaseConfig()


def test_configuration_depuis_l_environnement():
    config = DatabaseConfig.from_env({"DB_HOST": "db", "DB_PORT": "6543", "DB_NAME": "prod"})
    assert (config.host, config.port, config.dbname) == ("db", 6543, "prod")


def test_description_sans_mot_de_passe():
    assert "secret" not in DatabaseConfig(password="secret").describe()


def test_schema_embarque_dans_le_paquet():
    schema = load_schema()
    for table in ["processed_files", "accounts", "banks", "transactions", "total_sent_by_iban"]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema


def test_hash_depend_du_contenu_et_pas_du_nom(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    c = tmp_path / "c.csv"
    a.write_text("contenu", encoding="utf-8")
    b.write_text("contenu", encoding="utf-8")
    c.write_text("autre contenu", encoding="utf-8")
    assert compute_file_hash(a) == compute_file_hash(b)
    assert compute_file_hash(a) != compute_file_hash(c)
    assert len(compute_file_hash(a)) == 64


def test_save_echoue_proprement_si_base_inaccessible(tmp_path, monkeypatch, capsys):
    from pipeline.generation.generator import generate_files

    generate_files(tmp_path, nb_files=1)
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")  # aucun serveur n'écoute sur ce port
    assert main(["--input", str(tmp_path), "--save"]) == 1
    assert "base de données inaccessible" in capsys.readouterr().err
