-- Base dédiée aux tests d'intégration : ils la vident avant chaque test,
-- ce qui ne doit jamais toucher la base « pipeline » utilisée par run-pipeline.
CREATE DATABASE pipeline_test;