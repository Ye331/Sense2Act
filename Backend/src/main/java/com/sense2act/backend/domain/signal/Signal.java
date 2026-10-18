package com.sense2act.backend.domain.signal;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/** 信号(signals)。hits/score/detector 均为外部检测服务产出,后端原样存取、只消费不重算。 */
@TableName(value = "signals", autoResultMap = true)
public class Signal {

    @TableId
    private String id;

    private String documentId;

    private String orgId;

    private String detector;

    /** [{rule_id, rule_version, rule_type, weight, detail}],契约 §4/§9.2。 */
    @TableField(typeHandler = JsonbTypeHandler.class)
    private List<Map<String, Object>> hits;

    private BigDecimal score;

    private String status;          // pending / investigating / confirmed / dismissed

    private String investigationId;

    private String decidedBy;

    private OffsetDateTime decidedAt;

    private String decisionReason;

    private OffsetDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getDocumentId() {
        return documentId;
    }

    public void setDocumentId(String documentId) {
        this.documentId = documentId;
    }

    public String getOrgId() {
        return orgId;
    }

    public void setOrgId(String orgId) {
        this.orgId = orgId;
    }

    public String getDetector() {
        return detector;
    }

    public void setDetector(String detector) {
        this.detector = detector;
    }

    public List<Map<String, Object>> getHits() {
        return hits;
    }

    public void setHits(List<Map<String, Object>> hits) {
        this.hits = hits;
    }

    public BigDecimal getScore() {
        return score;
    }

    public void setScore(BigDecimal score) {
        this.score = score;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getInvestigationId() {
        return investigationId;
    }

    public void setInvestigationId(String investigationId) {
        this.investigationId = investigationId;
    }

    public String getDecidedBy() {
        return decidedBy;
    }

    public void setDecidedBy(String decidedBy) {
        this.decidedBy = decidedBy;
    }

    public OffsetDateTime getDecidedAt() {
        return decidedAt;
    }

    public void setDecidedAt(OffsetDateTime decidedAt) {
        this.decidedAt = decidedAt;
    }

    public String getDecisionReason() {
        return decisionReason;
    }

    public void setDecisionReason(String decisionReason) {
        this.decisionReason = decisionReason;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
