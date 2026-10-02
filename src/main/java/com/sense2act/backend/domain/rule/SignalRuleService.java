package com.sense2act.backend.domain.rule;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/**
 * 规则版本管理(E2-6,契约 §8)。
 * PATCH 语义:以当前最高版本为基底合并请求字段,生成 version+1 的新行并停用全部旧行 ——
 * 规则不可变,改规则 = 出新版本;"同 id 只有一个 enabled" 由部分唯一索引兜底。
 */
@Service
public class SignalRuleService {

    private final SignalRuleMapper ruleMapper;

    public SignalRuleService(SignalRuleMapper ruleMapper) {
        this.ruleMapper = ruleMapper;
    }

    public List<RuleView> list() {
        return ruleMapper.selectList(new LambdaQueryWrapper<SignalRule>()
                        .orderByAsc(SignalRule::getName).orderByDesc(SignalRule::getVersion))
                .stream().map(SignalRuleService::view).toList();
    }

    @Transactional
    public RuleView create(String name, String type, Map<String, Object> params, BigDecimal weight) {
        if (name == null || name.isBlank()) {
            throw BusinessException.badRequest("name 必填");
        }
        if (type == null || !SignalRule.TYPES.contains(type)) {
            throw BusinessException.badRequest("type 只能是 " + SignalRule.TYPES);
        }
        if (weight != null && (weight.compareTo(BigDecimal.ZERO) < 0 || weight.compareTo(BigDecimal.ONE) > 0)) {
            throw BusinessException.badRequest("weight 取值 0~1");
        }
        Boolean nameTaken = ruleMapper.selectCount(new LambdaQueryWrapper<SignalRule>()
                .eq(SignalRule::getName, name).eq(SignalRule::getEnabled, true)) > 0;
        if (nameTaken) {
            throw BusinessException.conflict("同名规则已存在: " + name);
        }
        SignalRule rule = new SignalRule();
        rule.setId(IdGen.next("rule"));
        rule.setVersion(1);
        rule.setName(name.trim());
        rule.setType(type);
        rule.setParams(params == null ? Map.of() : params);
        rule.setWeight(weight == null ? new BigDecimal("0.500") : weight);
        rule.setEnabled(true);
        ruleMapper.insert(rule);
        return view(rule);
    }

    /** PATCH = 版本演进:合并最新版本的字段,插新行、停旧行;请求至少改一个字段。 */
    @Transactional
    public RuleView patch(String id, String name, String type, Map<String, Object> params,
                          BigDecimal weight, Boolean enabled) {
        if ((name == null || name.isBlank()) && type == null && params == null && weight == null
                && enabled == null) {
            throw BusinessException.badRequest("PATCH 至少要改一个字段");
        }
        List<SignalRule> versions = ruleMapper.selectList(
                new LambdaQueryWrapper<SignalRule>().eq(SignalRule::getId, id));
        if (versions.isEmpty()) {
            throw BusinessException.notFound("规则不存在: " + id);
        }
        SignalRule latest = versions.stream()
                .max(Comparator.comparingInt(SignalRule::getVersion)).orElseThrow();
        if (type != null && !SignalRule.TYPES.contains(type)) {
            throw BusinessException.badRequest("type 只能是 " + SignalRule.TYPES);
        }
        if (weight != null && (weight.compareTo(BigDecimal.ZERO) < 0 || weight.compareTo(BigDecimal.ONE) > 0)) {
            throw BusinessException.badRequest("weight 取值 0~1");
        }
        // 先停全部旧行,再插新行:部分唯一索引只在"同 id 多个 enabled"时才会拦
        ruleMapper.update(null, new LambdaUpdateWrapper<SignalRule>()
                .eq(SignalRule::getId, id).set(SignalRule::getEnabled, false));
        SignalRule next = new SignalRule();
        next.setId(id);
        next.setVersion(latest.getVersion() + 1);
        next.setName(name != null && !name.isBlank() ? name.trim() : latest.getName());
        next.setType(type != null ? type : latest.getType());
        next.setParams(params != null ? params : latest.getParams());
        next.setWeight(weight != null ? weight : latest.getWeight());
        next.setEnabled(enabled == null || enabled);   // 未指定则新版本默认生效
        next.setBacktestResult(latest.getBacktestResult());
        ruleMapper.insert(next);
        return view(next);
    }

    /** 契约 §8 形状:backtest_result 键名为 backtest;weight 按列精度(3 位小数)出串。 */
    public static RuleView view(SignalRule r) {
        return new RuleView(r.getId(), r.getVersion(), r.getName(), r.getType(), r.getParams(),
                r.getWeight() == null ? null : r.getWeight().setScale(3).toPlainString(), r.getEnabled(),
                r.getBacktestResult(), r.getCreatedAt());
    }

    @com.fasterxml.jackson.databind.annotation.JsonNaming(
            com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record RuleView(String id, int version, String name, String type,
                           Map<String, Object> params, String weight, boolean enabled,
                           Map<String, Object> backtest, java.time.OffsetDateTime createdAt) {
    }
}
