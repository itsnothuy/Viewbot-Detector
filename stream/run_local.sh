#!/usr/bin/env bash
set -euo pipefail
docker compose -f infra/docker-compose.yml cp stream/job.py jobmanager:/opt/flink/usrlib/job.py
docker compose -f infra/docker-compose.yml exec -T jobmanager bash -lc '/opt/flink/bin/flink run -py /opt/flink/usrlib/job.py'
