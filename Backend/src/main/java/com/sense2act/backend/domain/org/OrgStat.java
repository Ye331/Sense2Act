package com.sense2act.backend.domain.org;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;

/** 机构×类别统计画像(org_stats,复合主键 (org_id, category),入库后集合式重算)。 */
@TableName("org_stats")
public class OrgStat {

    private String orgId;

    private String category;

    private Integer windowDays;

    private Integer sampleCount;

    private BigDecimal amountMean;

    private BigDecimal amountStd;

    private BigDecimal amountP95;

    @TableField("freq_mean_30d")   // 驼峰转下划线对数字段名有歧义,显式指定
    private BigDecimal freqMean30d;

    private LocalDate lastDocAt;

    private OffsetDateTime updatedAt;

    public String getOrgId() {
        return orgId;
    }

    public void setOrgId(String orgId) {
        this.orgId = orgId;
    }

    public String getCategory() {
        return category;
    }

    public void setCategory(String category) {
        this.category = category;
    }

    public Integer getWindowDays() {
        return windowDays;
    }

    public void setWindowDays(Integer windowDays) {
        this.windowDays = windowDays;
    }

    public Integer getSampleCount() {
        return sampleCount;
    }

    public void setSampleCount(Integer sampleCount) {
        this.sampleCount = sampleCount;
    }

    public BigDecimal getAmountMean() {
        return amountMean;
    }

    public void setAmountMean(BigDecimal amountMean) {
        this.amountMean = amountMean;
    }

    public BigDecimal getAmountStd() {
        return amountStd;
    }

    public void setAmountStd(BigDecimal amountStd) {
        this.amountStd = amountStd;
    }

    public BigDecimal getAmountP95() {
        return amountP95;
    }

    public void setAmountP95(BigDecimal amountP95) {
        this.amountP95 = amountP95;
    }

    public BigDecimal getFreqMean30d() {
        return freqMean30d;
    }

    public void setFreqMean30d(BigDecimal freqMean30d) {
        this.freqMean30d = freqMean30d;
    }

    public LocalDate getLastDocAt() {
        return lastDocAt;
    }

    public void setLastDocAt(LocalDate lastDocAt) {
        this.lastDocAt = lastDocAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
