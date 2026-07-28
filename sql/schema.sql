-- ---------------------------------------------------------------------------
-- AegisLab MySQL schema (baseline + documented extensions).
-- Idempotent: safe to run repeatedly in MySQL Workbench.
-- Indexes are declared inline in CREATE TABLE so re-running this script
-- never raises duplicate-index errors.
--
-- Extension over the baseline specification:
--   * sensor_readings.source distinguishes real device data from simulated
--     data (mock mode). Additive column, backward compatible: existing
--     queries keep working; old rows default to 'device'.
-- ---------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS aegislab
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE aegislab;

CREATE TABLE IF NOT EXISTS sensor_readings (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    temperature DECIMAL(5,2) NULL,
    humidity    DECIMAL(5,2) NULL,
    light_level INT NULL,
    motion      BOOLEAN NOT NULL DEFAULT FALSE,
    source      ENUM('device', 'simulated') NOT NULL DEFAULT 'device',
    PRIMARY KEY (id),
    KEY idx_sensor_readings_recorded_at (recorded_at),
    CONSTRAINT chk_temperature
        CHECK (temperature IS NULL OR temperature BETWEEN -40 AND 100),
    CONSTRAINT chk_humidity
        CHECK (humidity IS NULL OR humidity BETWEEN 0 AND 100),
    CONSTRAINT chk_light_level
        CHECK (light_level IS NULL OR light_level BETWEEN 0 AND 1023)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS alerts (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    alert_type   VARCHAR(100) NOT NULL,
    severity     ENUM('low', 'medium', 'high', 'critical') NOT NULL,
    message      TEXT NOT NULL,
    reading_id   BIGINT UNSIGNED NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (id),
    KEY idx_alerts_created_at (created_at),
    KEY idx_alerts_acknowledged (acknowledged),
    KEY idx_alerts_type_created (alert_type, created_at),
    CONSTRAINT fk_alert_reading
        FOREIGN KEY (reading_id)
        REFERENCES sensor_readings (id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS system_events (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    event_type VARCHAR(100) NOT NULL,
    details    TEXT NULL,
    PRIMARY KEY (id),
    KEY idx_system_events_created_at (created_at)
) ENGINE=InnoDB;
