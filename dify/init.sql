-- Memory Can storage schema (seekdb / OceanBase, MySQL mode)
CREATE DATABASE IF NOT EXISTS dify_memory;

CREATE TABLE IF NOT EXISTS dify_memory.memories (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    mood VARCHAR(64) DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
