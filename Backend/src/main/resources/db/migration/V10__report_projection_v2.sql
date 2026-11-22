-- ReportDraft v2:结构化结论/待补证/过程说明/呈现附件均为向后兼容增量。
ALTER TABLE reports
    ADD COLUMN conclusions JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN pending_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN process_notes JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN attachments JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE claims
    ADD COLUMN source_claim_id VARCHAR(100),
    ADD COLUMN claim_status VARCHAR(20) NOT NULL DEFAULT 'verified'
        CHECK (claim_status IN ('verified', 'contested'));

CREATE UNIQUE INDEX uq_claims_report_source
    ON claims (report_id, source_claim_id) WHERE source_claim_id IS NOT NULL;
