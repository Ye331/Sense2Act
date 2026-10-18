-- Agent 重启/超时重试：可选 request_id 保证同一步只记账一次。
ALTER TABLE investigation_steps ADD COLUMN request_id VARCHAR(100);
CREATE UNIQUE INDEX uniq_step_request ON investigation_steps (investigation_id, request_id)
    WHERE request_id IS NOT NULL;
