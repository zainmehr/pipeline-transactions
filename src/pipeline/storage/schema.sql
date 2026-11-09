-- Schéma relationnel de la pipeline de transactions (PostgreSQL).
-- Idempotent : il peut être rejoué sans risque sur une base existante.
--
-- Les colonnes issues du CSV gardent leur nom d'origine (iban_origine, montant, ...).
-- Les sommes sont stockées dans des tables dédiées, alimentées par les traitements Python,
-- et des vues v_* les recalculent depuis les transactions pour contrôler leur cohérence.

-- Fichiers déjà traités : garantit qu'un même fichier n'est jamais inséré deux fois.
CREATE TABLE IF NOT EXISTS processed_files (
    id              SERIAL       PRIMARY KEY,
    file_name       TEXT         NOT NULL,
    file_hash       CHAR(64)     NOT NULL,
    nb_transactions INTEGER      NOT NULL CHECK (nb_transactions >= 0),
    processed_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT processed_files_file_hash_key UNIQUE (file_hash)
);

-- Comptes bancaires (émetteurs et destinataires).
CREATE TABLE IF NOT EXISTS accounts (
    iban  VARCHAR(34)  PRIMARY KEY,
    pays  CHAR(2)      NOT NULL,
    CONSTRAINT accounts_pays_coherent CHECK (pays = substring(iban FROM 1 FOR 2))
);

-- Banques émettrices.
CREATE TABLE IF NOT EXISTS banks (
    id    SERIAL  PRIMARY KEY,
    name  TEXT    NOT NULL UNIQUE
);

-- Transactions, rattachées au fichier dont elles proviennent.
CREATE TABLE IF NOT EXISTS transactions (
    id                    BIGSERIAL      PRIMARY KEY,
    file_id               INTEGER        NOT NULL REFERENCES processed_files (id) ON DELETE CASCADE,
    datetime_transaction  TIMESTAMP      NOT NULL,
    iban_origine          VARCHAR(34)    NOT NULL REFERENCES accounts (iban),
    bank_source_id        INTEGER        NOT NULL REFERENCES banks (id),
    iban_destinataire     VARCHAR(34)    NOT NULL REFERENCES accounts (iban),
    montant               NUMERIC(14, 2) NOT NULL CHECK (montant > 0),
    flag_montant_eleve    BOOLEAN        NOT NULL,
    CONSTRAINT transactions_comptes_distincts CHECK (iban_origine <> iban_destinataire)
);

CREATE INDEX IF NOT EXISTS idx_transactions_file_id ON transactions (file_id);
CREATE INDEX IF NOT EXISTS idx_transactions_iban_origine ON transactions (iban_origine);
CREATE INDEX IF NOT EXISTS idx_transactions_iban_destinataire ON transactions (iban_destinataire);

-- Résultats des traitements : sommes cumulées sur l'ensemble des fichiers traités.
CREATE TABLE IF NOT EXISTS total_sent_by_iban (
    iban        VARCHAR(34)     PRIMARY KEY REFERENCES accounts (iban),
    total       NUMERIC(16, 2)  NOT NULL CHECK (total >= 0),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS total_sent_by_bank (
    bank_id     INTEGER         PRIMARY KEY REFERENCES banks (id),
    total       NUMERIC(16, 2)  NOT NULL CHECK (total >= 0),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS total_received_by_iban (
    iban        VARCHAR(34)     PRIMARY KEY REFERENCES accounts (iban),
    total       NUMERIC(16, 2)  NOT NULL CHECK (total >= 0),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- Vues de contrôle : mêmes sommes, recalculées directement depuis les transactions.
CREATE OR REPLACE VIEW v_total_sent_by_iban AS
    SELECT iban_origine AS iban, SUM(montant) AS total
    FROM transactions
    GROUP BY iban_origine;

CREATE OR REPLACE VIEW v_total_sent_by_bank AS
    SELECT b.name AS bank, SUM(t.montant) AS total
    FROM transactions t
    JOIN banks b ON b.id = t.bank_source_id
    GROUP BY b.name;

CREATE OR REPLACE VIEW v_total_received_by_iban AS
    SELECT iban_destinataire AS iban, SUM(montant) AS total
    FROM transactions
    GROUP BY iban_destinataire;