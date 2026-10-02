package com.sense2act.backend.domain.policy;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

/** 调查策略(investigation_policies,单行表 id=1)。成本闸门:自动触发阈值 + 并发/轮次/预算默认值。 */
@TableName("investigation_policies")
public class InvestigationPolicy {

    public static final int SINGLETON_ID = 1;

    @TableId
    private Integer id;

    private BigDecimal autoInvestigateThreshold;

    private Integer maxConcurrentInvestigations;

    private Integer defaultMaxRounds;

    private Integer defaultTokenBudget;

    private OffsetDateTime updatedAt;

    public Integer getId() {
        return id;
    }

    public void setId(Integer id) {
        this.id = id;
    }

    public BigDecimal getAutoInvestigateThreshold() {
        return autoInvestigateThreshold;
    }

    public void setAutoInvestigateThreshold(BigDecimal autoInvestigateThreshold) {
        this.autoInvestigateThreshold = autoInvestigateThreshold;
    }

    public Integer getMaxConcurrentInvestigations() {
        return maxConcurrentInvestigations;
    }

    public void setMaxConcurrentInvestigations(Integer maxConcurrentInvestigations) {
        this.maxConcurrentInvestigations = maxConcurrentInvestigations;
    }

    public Integer getDefaultMaxRounds() {
        return defaultMaxRounds;
    }

    public void setDefaultMaxRounds(Integer defaultMaxRounds) {
        this.defaultMaxRounds = defaultMaxRounds;
    }

    public Integer getDefaultTokenBudget() {
        return defaultTokenBudget;
    }

    public void setDefaultTokenBudget(Integer defaultTokenBudget) {
        this.defaultTokenBudget = defaultTokenBudget;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
