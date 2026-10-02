package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.time.OffsetDateTime;
import java.util.List;

/** 待确认问题(questions,E3-4)。evidence_ids 只能引用本调查已登记的 evidences。 */
@TableName(value = "questions", autoResultMap = true)
public class Question {

    public static final String STATUS_OPEN = "open";
    public static final String STATUS_CLARIFIED = "clarified";
    public static final String STATUS_UNRESOLVED = "unresolved";
    public static final String STATUS_ABANDONED = "abandoned";

    @TableId
    private String id;

    private String investigationId;

    private String text;

    private Integer raisedInRound;

    private String status;

    private String answerSummary;

    /** ["ev_..."],PATCH 时校验存在且归属同一调查。 */
    @TableField(typeHandler = JsonbTypeHandler.class)
    private List<String> evidenceIds;

    private OffsetDateTime createdAt;

    private OffsetDateTime updatedAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getInvestigationId() {
        return investigationId;
    }

    public void setInvestigationId(String investigationId) {
        this.investigationId = investigationId;
    }

    public String getText() {
        return text;
    }

    public void setText(String text) {
        this.text = text;
    }

    public Integer getRaisedInRound() {
        return raisedInRound;
    }

    public void setRaisedInRound(Integer raisedInRound) {
        this.raisedInRound = raisedInRound;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getAnswerSummary() {
        return answerSummary;
    }

    public void setAnswerSummary(String answerSummary) {
        this.answerSummary = answerSummary;
    }

    public List<String> getEvidenceIds() {
        return evidenceIds;
    }

    public void setEvidenceIds(List<String> evidenceIds) {
        this.evidenceIds = evidenceIds;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
