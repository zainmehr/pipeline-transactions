#!/usr/bin/env bash
# Déploiement et vérification de l'environnement de production sur Kubernetes.
# Utilisé par la pipeline CD (.github/workflows/cd.yml) et par les commandes `make k8s-*`.
#
#   scripts/k8s.sh deployer                       secret + manifestes + attente des services
#   scripts/k8s.sh deposer SEED FICHIERS IMAGES   dépose des données dans le bucket (Job)
#   scripts/k8s.sh attendre-traitement N [DELAI]  attend que le CronJob ait traité N fichiers
#   scripts/k8s.sh lancer                         lance tout de suite le traitement (hors planning)
#   scripts/k8s.sh sql "REQUÊTE"                  exécute une requête SQL sur la base
#   scripts/k8s.sh verifier N                     contrôles de cohérence de la base
#   scripts/k8s.sh etat                           état des ressources et des derniers traitements
#   scripts/k8s.sh diagnostic                     tout ce qui aide à comprendre un échec
set -euo pipefail

NAMESPACE="${NAMESPACE:-pipeline-transactions}"
RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL=(kubectl -n "$NAMESPACE")

log() { printf '\n==> %s\n' "$*"; }
echec() { printf '\nÉCHEC : %s\n' "$*" >&2; exit 1; }

sql() {
  # Les variables POSTGRES_* sont celles du conteneur PostgreSQL (d'où les apostrophes).
  # shellcheck disable=SC2016
  "${KUBECTL[@]}" exec postgres-0 -c postgres -- \
    sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tAc "$1"' _ "$1"
}

deployer() {
  log "Namespace et secret des identifiants"
  kubectl apply -f "$RACINE/k8s/namespace.yaml"
  # Identifiants : variables d'environnement (secrets GitHub en production), sinon valeurs
  # de développement identiques à .env.example.
  "${KUBECTL[@]}" create secret generic pipeline-secrets \
    --from-literal=DB_USER="${DB_USER:-pipeline}" \
    --from-literal=DB_PASSWORD="${DB_PASSWORD:-pipeline}" \
    --from-literal=S3_ACCESS_KEY="${S3_ACCESS_KEY:-pipeline}" \
    --from-literal=S3_SECRET_KEY="${S3_SECRET_KEY:-pipeline-secret}" \
    --dry-run=client -o yaml | kubectl apply -f -

  log "Application des manifestes (kubectl apply -k k8s)"
  kubectl apply -k "$RACINE/k8s"
  if [[ -n "${IMAGE:-}" ]]; then
    log "Image du système de traitement : $IMAGE"
    "${KUBECTL[@]}" patch cronjob pipeline-traitement --type=json -p \
      "[{\"op\":\"replace\",\"path\":\"/spec/jobTemplate/spec/template/spec/containers/0/image\",\"value\":\"$IMAGE\"}]"
  fi

  log "Attente de PostgreSQL et du stockage S3"
  "${KUBECTL[@]}" rollout status statefulset/postgres --timeout=300s
  "${KUBECTL[@]}" rollout status statefulset/rustfs --timeout=300s
  "${KUBECTL[@]}" get all
}

deposer() {
  local seed="$1" fichiers="$2" images="$3" job
  log "Dépôt de $fichiers CSV et $images image(s) dans le bucket (graine $seed)"
  job=$(sed -e "s/\${SEED}/$seed/; s/\${FILES}/$fichiers/; s/\${IMAGES}/$images/" \
      -e "s#image: pipeline-transactions:local#image: ${IMAGE:-pipeline-transactions:local}#" \
      "$RACINE/k8s/jobs/depot-donnees.yaml" | "${KUBECTL[@]}" create -f - -o name)
  if ! "${KUBECTL[@]}" wait --for=condition=complete "$job" --timeout=300s; then
    "${KUBECTL[@]}" logs "$job" || true
    echec "le dépôt de données ($job) n'a pas abouti"
  fi
  "${KUBECTL[@]}" logs "$job"
}

nb_fichiers_traites() {
  sql "SELECT COUNT(*) FROM processed_files" 2>/dev/null || echo "?"
}

attendre_traitement() {
  local attendu="$1" delai="${2:-420}" debut=$SECONDS n
  log "Attente du traitement planifié (CronJob) : $attendu fichier(s) attendu(s) en base"
  while (( SECONDS - debut < delai )); do
    n=$(nb_fichiers_traites)
    printf '  %3ss : %s fichier(s) traité(s)\n' "$((SECONDS - debut))" "$n"
    if [[ "$n" == "$attendu" ]]; then
      "${KUBECTL[@]}" get jobs -l app.kubernetes.io/name=pipeline-traitement
      local dernier
      dernier=$("${KUBECTL[@]}" get jobs -l app.kubernetes.io/name=pipeline-traitement \
        --sort-by=.metadata.creationTimestamp -o name | tail -1)
      "${KUBECTL[@]}" wait --for=condition=complete "$dernier" --timeout=300s
      log "Journal du dernier traitement ($dernier)"
      "${KUBECTL[@]}" logs "$dernier"
      return 0
    fi
    sleep 10
  done
  echec "$attendu fichier(s) attendu(s) après ${delai}s, $(nb_fichiers_traites) en base"
}

lancer() {
  local job
  job="pipeline-traitement-manuel-$(date +%s)"
  "${KUBECTL[@]}" create job "$job" --from=cronjob/pipeline-traitement
  "${KUBECTL[@]}" wait --for=condition=complete "job/$job" --timeout=300s || {
    "${KUBECTL[@]}" logs "job/$job" || true
    echec "le traitement $job n'a pas abouti"
  }
  "${KUBECTL[@]}" logs "job/$job"
}

verifier() {
  local attendu="$1" n doublons ecarts
  log "Contrôles de la base de production"
  sql "SELECT source_type, COUNT(*), SUM(nb_transactions) FROM processed_files GROUP BY 1"
  n=$(sql "SELECT COUNT(*) FROM processed_files")
  [[ "$n" == "$attendu" ]] || echec "$n fichier(s) en base, $attendu attendu(s)"
  echo "  fichiers traités : $n (attendu : $attendu)"

  n=$(sql "SELECT COUNT(*) FROM processed_files WHERE source_type = 'image'")
  (( n > 0 )) || echec "aucun relevé image traité par OCR"
  echo "  relevés images traités par OCR : $n"

  doublons=$(sql "SELECT COUNT(*) FROM (SELECT file_hash FROM processed_files
                  GROUP BY file_hash HAVING COUNT(*) > 1) d")
  [[ "$doublons" == 0 ]] || echec "$doublons fichier(s) traité(s) plusieurs fois"
  echo "  fichiers traités plusieurs fois : 0"

  n=$(sql "SELECT COUNT(*) FROM transactions t JOIN processed_files f ON f.id = t.file_id
           GROUP BY f.id HAVING COUNT(*) <> MAX(f.nb_transactions)" | wc -l)
  [[ "$n" == 0 ]] || echec "nombre de transactions incohérent pour $n fichier(s)"

  ecarts=$(sql "SELECT
      (SELECT COUNT(*) FROM total_sent_by_iban t FULL JOIN v_total_sent_by_iban v USING (iban)
       WHERE t.total IS DISTINCT FROM v.total)
    + (SELECT COUNT(*) FROM total_received_by_iban t
       FULL JOIN v_total_received_by_iban v USING (iban) WHERE t.total IS DISTINCT FROM v.total)")
  [[ "$ecarts" == 0 ]] || echec "$ecarts écart(s) entre sommes stockées et recalculées"
  echo "  écarts entre sommes stockées et recalculées : 0"

  n=$(sql "SELECT COUNT(*) FROM transactions WHERE flag_montant_eleve <> (montant > 5000)")
  [[ "$n" == 0 ]] || echec "$n flag(s) mal positionné(s)"
  echo "  flags mal positionnés : 0"
}

etat() {
  "${KUBECTL[@]}" get cronjob,jobs,pods,svc,pvc
  log "Fichiers traités"
  sql "SELECT id, source_type, nb_transactions, processed_at, file_name
       FROM processed_files ORDER BY id" || true
}

diagnostic() {
  set +e
  "${KUBECTL[@]}" get all,pvc,events --sort-by=.metadata.creationTimestamp
  "${KUBECTL[@]}" describe pods
  for pod in $("${KUBECTL[@]}" get pods -o name); do
    log "Journal de $pod"
    "${KUBECTL[@]}" logs "$pod" --all-containers --tail=200
  done
}

commande="${1:-}"
shift || true
case "$commande" in
  deployer) deployer ;;
  deposer) deposer "${1:?graine}" "${2:-3}" "${3:-2}" ;;
  attendre-traitement) attendre_traitement "${1:?nombre de fichiers attendu}" "${2:-420}" ;;
  lancer) lancer ;;
  sql) sql "${1:?requête}" ;;
  verifier) verifier "${1:?nombre de fichiers attendu}" ;;
  etat) etat ;;
  diagnostic) diagnostic ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
