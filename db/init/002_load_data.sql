-- Chạy sau 001_schema.sql. Thư mục ./data được mount vào /data (xem docker-compose.yml)
\copy param_threshold   FROM '/data/param_threshold.csv' CSV HEADER
\copy eval.event_label  FROM '/data/event_label.csv'     CSV HEADER