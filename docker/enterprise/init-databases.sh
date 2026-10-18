#!/bin/sh
set -eu
: "${OCTOP_APP_PASSWORD:?required}" "${IM_APP_PASSWORD:?required}"
[ "$OCTOP_APP_PASSWORD" != "$IM_APP_PASSWORD" ] || { echo 'Use distinct application passwords' >&2; exit 1; }
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
 --set octop_pw="$OCTOP_APP_PASSWORD" --set im_pw="$IM_APP_PASSWORD" <<'SQL'
CREATE ROLE octop_app LOGIN PASSWORD :'octop_pw';
CREATE ROLE im_app LOGIN PASSWORD :'im_pw';
CREATE DATABASE octop OWNER octop_app;
CREATE DATABASE octop_im OWNER im_app;
REVOKE ALL ON DATABASE octop FROM PUBLIC;
REVOKE ALL ON DATABASE octop_im FROM PUBLIC;
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE octop TO octop_app;
GRANT CONNECT ON DATABASE octop_im TO im_app;
\connect octop
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO octop_app;
\connect octop_im
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO im_app;
SQL
