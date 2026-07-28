-- ---------------------------------------------------------------------------
-- AegisLab demonstration data. OPTIONAL - development/demo only.
-- Run AFTER sql/schema.sql. Do not run against a database holding real data
-- you want to keep clean: these rows are marked source='simulated'.
-- ---------------------------------------------------------------------------

USE aegislab;

INSERT INTO sensor_readings (recorded_at, temperature, humidity, light_level, motion, source) VALUES
    (NOW() - INTERVAL 20 MINUTE, 23.10, 48.50, 610, FALSE, 'simulated'),
    (NOW() - INTERVAL 18 MINUTE, 23.40, 48.10, 598, FALSE, 'simulated'),
    (NOW() - INTERVAL 16 MINUTE, 23.80, 47.60, 575, TRUE,  'simulated'),
    (NOW() - INTERVAL 14 MINUTE, 24.20, 47.00, 540, FALSE, 'simulated'),
    (NOW() - INTERVAL 12 MINUTE, 25.10, 46.20, 480, FALSE, 'simulated'),
    (NOW() - INTERVAL 10 MINUTE, 26.70, 44.90, 300, FALSE, 'simulated'),
    (NOW() - INTERVAL 8 MINUTE,  28.90, 42.10, 150, FALSE, 'simulated'),
    (NOW() - INTERVAL 6 MINUTE,  31.20, 39.80,  90, TRUE,  'simulated'),
    (NOW() - INTERVAL 4 MINUTE,  30.60, 40.50,  85, TRUE,  'simulated'),
    (NOW() - INTERVAL 2 MINUTE,  29.40, 41.70, 110, FALSE, 'simulated');

-- Alert linked to the 31.2 C reading inserted above.
INSERT INTO alerts (created_at, alert_type, severity, message, reading_id, acknowledged)
SELECT NOW() - INTERVAL 6 MINUTE,
       'HIGH_TEMPERATURE',
       'high',
       'Temperature reached 31.2°C, above the 30.0°C threshold.',
       r.id,
       FALSE
FROM sensor_readings r
WHERE r.temperature = 31.20 AND r.source = 'simulated'
ORDER BY r.id DESC
LIMIT 1;

INSERT INTO alerts (created_at, alert_type, severity, message, reading_id, acknowledged) VALUES
    (NOW() - INTERVAL 6 MINUTE, 'MOTION_IN_DARK', 'medium',
     'Motion detected while light level was 90, below the dark threshold 100.', NULL, FALSE),
    (NOW() - INTERVAL 15 MINUTE, 'LOW_HUMIDITY', 'low',
     'Humidity fell to 29.5%, below the 30.0% threshold.', NULL, TRUE);

INSERT INTO system_events (created_at, event_type, details) VALUES
    (NOW() - INTERVAL 21 MINUTE, 'collector_started', 'Demo data: collector started in mock mode.'),
    (NOW() - INTERVAL 21 MINUTE, 'serial_connected', 'Demo data: simulated device attached.'),
    (NOW() - INTERVAL 7 MINUTE,  'malformed_message', 'Demo data: rejected line: {"temperature":'),
    (NOW() - INTERVAL 1 MINUTE,  'sensor_error', 'Demo data: sensor=dht code=READ_FAILED');
