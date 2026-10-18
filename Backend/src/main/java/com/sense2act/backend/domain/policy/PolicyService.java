package com.sense2act.backend.domain.policy;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;

/** 调查策略与关注画像(E2-3)。策略单行存取;画像独立表(D13),admin 维护、检测队列只读。 */
@Service
public class PolicyService {

    private final InvestigationPolicyMapper policyMapper;
    private final WatchProfileMapper watchProfileMapper;

    public PolicyService(InvestigationPolicyMapper policyMapper, WatchProfileMapper watchProfileMapper) {
        this.policyMapper = policyMapper;
        this.watchProfileMapper = watchProfileMapper;
    }

    /** 幂等兜底:正常情况 V3 迁移已插好 id=1;空表(如测试手工建库)时补默认值。 */
    @Transactional
    public InvestigationPolicy policies() {
        InvestigationPolicy p = policyMapper.selectById(InvestigationPolicy.SINGLETON_ID);
        if (p == null) {
            p = new InvestigationPolicy();
            p.setId(InvestigationPolicy.SINGLETON_ID);
            p.setAutoInvestigateThreshold(new BigDecimal("0.85"));
            p.setMaxConcurrentInvestigations(3);
            p.setDefaultMaxRounds(8);
            p.setDefaultTokenBudget(60000);
            policyMapper.insert(p);
        }
        return p;
    }

    @Transactional
    public InvestigationPolicy updatePolicies(BigDecimal threshold, Integer maxConcurrent,
                                              Integer maxRounds, Integer tokenBudget) {
        if (threshold == null || threshold.signum() < 0 || threshold.compareTo(BigDecimal.ONE) > 0) {
            throw BusinessException.badRequest("auto_investigate_threshold 必须在 [0,1]");
        }
        if (maxConcurrent == null || maxConcurrent < 1) {
            throw BusinessException.badRequest("max_concurrent_investigations 必须为正整数");
        }
        if (maxRounds == null || maxRounds < 1) {
            throw BusinessException.badRequest("default_max_rounds 必须为正整数");
        }
        if (tokenBudget == null || tokenBudget < 1) {
            throw BusinessException.badRequest("default_token_budget 必须为正整数");
        }
        policies();   // 确保行存在
        InvestigationPolicy p = new InvestigationPolicy();
        p.setId(InvestigationPolicy.SINGLETON_ID);
        p.setAutoInvestigateThreshold(threshold);
        p.setMaxConcurrentInvestigations(maxConcurrent);
        p.setDefaultMaxRounds(maxRounds);
        p.setDefaultTokenBudget(tokenBudget);
        p.setUpdatedAt(OffsetDateTime.now());
        policyMapper.updateById(p);
        return policyMapper.selectById(InvestigationPolicy.SINGLETON_ID);
    }

    public List<WatchProfile> listProfiles() {
        return watchProfileMapper.selectList(
                new LambdaQueryWrapper<WatchProfile>().orderByAsc(WatchProfile::getName));
    }

    /** detection-queue 内嵌用:启用中的画像名。 */
    public List<String> enabledProfileNames() {
        return listProfiles().stream()
                .filter(p -> Boolean.TRUE.equals(p.getEnabled()))
                .map(WatchProfile::getName)
                .toList();
    }

    @Transactional
    public WatchProfile createProfile(String name, String note) {
        if (name == null || name.isBlank()) {
            throw BusinessException.badRequest("name 必填");
        }
        WatchProfile p = new WatchProfile();
        p.setId(IdGen.next("wpr"));
        p.setName(name.strip());
        p.setEnabled(true);
        p.setNote(note);
        p.setCreatedAt(OffsetDateTime.now());   // 列有 DB 默认值,但 POST 响应不回读,须自己带上
        try {
            watchProfileMapper.insert(p);
        } catch (DuplicateKeyException e) {
            throw BusinessException.conflict("画像已存在: " + name);
        }
        return p;
    }

    @Transactional
    public void deleteProfile(String id) {
        if (watchProfileMapper.deleteById(id) == 0) {
            throw BusinessException.notFound("画像不存在: " + id);
        }
    }
}
