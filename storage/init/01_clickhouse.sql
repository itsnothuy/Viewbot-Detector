CREATE DATABASE IF NOT EXISTS viewbot;

CREATE TABLE IF NOT EXISTS viewbot.raw_cdn_log
(
  ts DateTime,
  request_id String,
  channel_id String,
  session_id String,
  client_ip IPv4,
  user_agent String,
  referrer String,
  host String,
  path String,
  query String,
  status UInt16,
  ttfb_ms UInt32,
  resp_bytes UInt64,
  country FixedString(2),
  asn UInt32,
  ja3 String,
  ja4 String,
  cmcd Map(String, String)
)
ENGINE = MergeTree
ORDER BY (channel_id, session_id, ts);

CREATE TABLE IF NOT EXISTS viewbot.player_events
(
  ts DateTime,
  session_id String,
  viewer_id String,
  channel_id String,
  event_type String,
  bitrate UInt32,
  stall_ms UInt32,
  buffer_len_ms UInt32,
  error_code String
)
ENGINE = MergeTree
ORDER BY (channel_id, session_id, ts);

CREATE TABLE IF NOT EXISTS viewbot.chat_events
(
  ts DateTime,
  channel_id String,
  user_id String,
  event_type String,
  message_len UInt16,
  is_subscriber UInt8
)
ENGINE = MergeTree
ORDER BY (channel_id, ts);

CREATE TABLE IF NOT EXISTS viewbot.edge_challenges
(
  ts DateTime,
  session_id String,
  request_id String,
  outcome Enum8('pass' = 1, 'fail' = 2, 'unsolved' = 3),
  provider String,
  risk_score Float32
)
ENGINE = MergeTree
ORDER BY (session_id, ts);

CREATE MATERIALIZED VIEW IF NOT EXISTS viewbot.mv_viewer_5m
ENGINE = AggregatingMergeTree
ORDER BY (channel_id, session_id, window_start)
AS
SELECT
  channel_id,
  session_id,
  toStartOfFiveMinute(ts) AS window_start,
  count() AS reqs,
  avg(ttfb_ms) AS avg_ttfb_ms,
  uniqExact(referrer) AS uniq_referrers,
  any(ja4) AS ja4_sample,
  sumIf(1, status != 200) AS non200s
FROM viewbot.raw_cdn_log
GROUP BY channel_id, session_id, window_start;

CREATE TABLE IF NOT EXISTS viewbot.decisions
(
  ts DateTime DEFAULT now(),
  session_id String,
  channel_id String,
  score Float32,
  reasons Array(String),
  action Enum8('count' = 1, 'suppress' = 2, 'challenge' = 3, 'block' = 4)
)
ENGINE = MergeTree
ORDER BY (channel_id, session_id, ts);
