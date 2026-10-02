package com.sense2act.backend.domain.report;

import com.baomidou.mybatisplus.annotation.TableName;

/** claim ↔ evidence 多对多(evidence_links,V5)。复合主键,无单列 id;只走 insert。 */
@TableName("evidence_links")
public class EvidenceLink {

    private String claimId;

    private String evidenceId;

    private String relation;

    public EvidenceLink() {
    }

    public EvidenceLink(String claimId, String evidenceId, String relation) {
        this.claimId = claimId;
        this.evidenceId = evidenceId;
        this.relation = relation;
    }

    public String getClaimId() {
        return claimId;
    }

    public void setClaimId(String claimId) {
        this.claimId = claimId;
    }

    public String getEvidenceId() {
        return evidenceId;
    }

    public void setEvidenceId(String evidenceId) {
        this.evidenceId = evidenceId;
    }

    public String getRelation() {
        return relation;
    }

    public void setRelation(String relation) {
        this.relation = relation;
    }
}
