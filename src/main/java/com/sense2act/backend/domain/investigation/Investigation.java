package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

/** 调查(investigations)。S4 只写 created(E2-4 自动/手动触发);状态机全集与 CAS 转移在 E3 落地。 */
@TableName("investigations")
public class Investigation {

    public static final String STATUS_CREATED = "created";
    public static final String STATUS_INVESTIGATING = "investigating";
    public static final String STATUS_REPORTING = "reporting";
    public static final String STATUS_COMPLETED = "completed";
    public static final String STATUS_FAILED = "failed";
    public static final String STATUS_STOPPED = "stopped";

    @TableId
    private String id;

    private String signalId;

    private String status;

    private Integer currentRound;

    private Integer maxRounds;

    private Integer tokenBudget;

    private Integer tokenUsed;

    private BigDecimal costEstimate;

    private String judgment;

    private String error;

    private OffsetDateTime startedAt;

    private OffsetDateTime finishedAt;

    private OffsetDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getSignalId() {
        return signalId;
    }

    public void setSignalId(String signalId) {
        this.signalId = signalId;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public Integer getCurrentRound() {
        return currentRound;
    }

    public void setCurrentRound(Integer currentRound) {
        this.currentRound = currentRound;
    }

    public Integer getMaxRounds() {
        return maxRounds;
    }

    public void setMaxRounds(Integer maxRounds) {
        this.maxRounds = maxRounds;
    }

    public Integer getTokenBudget() {
        return tokenBudget;
    }

    public void setTokenBudget(Integer tokenBudget) {
        this.tokenBudget = tokenBudget;
    }

    public Integer getTokenUsed() {
        return tokenUsed;
    }

    public void setTokenUsed(Integer tokenUsed) {
        this.tokenUsed = tokenUsed;
    }

    public BigDecimal getCostEstimate() {
        return costEstimate;
    }

    public void setCostEstimate(BigDecimal costEstimate) {
        this.costEstimate = costEstimate;
    }

    public String getJudgment() {
        return judgment;
    }

    public void setJudgment(String judgment) {
        this.judgment = judgment;
    }

    public String getError() {
        return error;
    }

    public void setError(String error) {
        this.error = error;
    }

    public OffsetDateTime getStartedAt() {
        return startedAt;
    }

    public void setStartedAt(OffsetDateTime startedAt) {
        this.startedAt = startedAt;
    }

    public OffsetDateTime getFinishedAt() {
        return finishedAt;
    }

    public void setFinishedAt(OffsetDateTime finishedAt) {
        this.finishedAt = finishedAt;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
