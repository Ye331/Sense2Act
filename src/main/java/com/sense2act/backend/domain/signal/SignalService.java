package com.sense2act.backend.domain.signal;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.common.PageParams;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.common.events.Events;
import com.sense2act.backend.common.events.SignalCreatedEvent;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.feedback.FeedbackEvent;
import com.sense2act.backend.domain.feedback.FeedbackEventMapper;
import com.sense2act.backend.domain.investigation.Investigation;
import com.sense2act.backend.domain.investigation.InvestigationMapper;
import com.sense2act.backend.domain.org.OrgStat;
import com.sense2act.backend.domain.org.OrgStatsMapper;
import com.sense2act.backend.domain.org.Organization;
import com.sense2act.backend.domain.org.OrganizationMapper;
import com.sense2act.backend.domain.policy.InvestigationPolicy;
import com.sense2act.backend.domain.policy.PolicyService;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 信号通道领域服务(E2):推送落库(幂等)→ 阈值自动开调查 → 查询/人工决策/手动开调查。
 * hits/score/detector 原样存取;score 只与策略阈值比较,后端不重算。
 */
@Service
public class SignalService {

    public static final int QUEUE_DEFAULT_LIMIT = 50;   // 决策 D9
    public static final int QUEUE_MAX_LIMIT = 100;

    private final SignalMapper signalMapper;
    private final DocumentMapper documentMapper;
    private final OrganizationMapper organizationMapper;
    private final OrgStatsMapper orgStatsMapper;
    private final InvestigationMapper investigationMapper;
    private final FeedbackEventMapper feedbackEventMapper;
    private final PolicyService policyService;
    private final ApplicationEventPublisher eventPublisher;

    public SignalService(SignalMapper signalMapper, DocumentMapper documentMapper,
                         OrganizationMapper organizationMapper, OrgStatsMapper orgStatsMapper,
                         InvestigationMapper investigationMapper, FeedbackEventMapper feedbackEventMapper,
                         PolicyService policyService, ApplicationEventPublisher eventPublisher) {
        this.signalMapper = signalMapper;
        this.documentMapper = documentMapper;
        this.organizationMapper = organizationMapper;
        this.orgStatsMapper = orgStatsMapper;
        this.investigationMapper = investigationMapper;
        this.feedbackEventMapper = feedbackEventMapper;
        this.policyService = policyService;
        this.eventPublisher = eventPublisher;
    }

    // ---------- E2-1 检测队列 ----------

    @com.fasterxml.jackson.databind.annotation.JsonNaming(
            com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record QueueDoc(Map<String, Object> document, Map<String, Object> orgStats, List<String> profiles) {
    }

    /** 未扫描文档按入库先后吐给检测服务,一次带齐 org_stats 基线与启用画像。 */
    public List<QueueDoc> detectionQueue(Integer limit) {
        int l = limit == null ? QUEUE_DEFAULT_LIMIT : limit;
        if (l < 1 || l > QUEUE_MAX_LIMIT) {
            throw BusinessException.badRequest("limit 必须在 1.." + QUEUE_MAX_LIMIT + "(决策 D9)");
        }
        List<Document> docs = documentMapper.selectList(new LambdaQueryWrapper<Document>()
                .eq(Document::getSignalScanned, false)
                .orderByAsc(Document::getCreatedAt)
                .last("LIMIT " + l));
        if (docs.isEmpty()) {
            return List.of();
        }
        Set<String> orgIds = new HashSet<>();
        for (Document d : docs) {
            if (d.getOrgId() != null) {
                orgIds.add(d.getOrgId());
            }
        }
        Map<String, String> orgNames = orgIds.isEmpty() ? Map.of()
                : organizationMapper.selectBatchIds(orgIds).stream()
                        .collect(java.util.stream.Collectors.toMap(Organization::getId, Organization::getName));
        Map<String, OrgStat> stats = orgIds.isEmpty() ? Map.of()
                : orgStatsMapper.selectList(new LambdaQueryWrapper<OrgStat>().in(OrgStat::getOrgId, orgIds)).stream()
                        .collect(java.util.stream.Collectors.toMap(s -> s.getOrgId() + "\n" + s.getCategory(), s -> s));
        List<String> profiles = policyService.enabledProfileNames();

        List<QueueDoc> items = new ArrayList<>(docs.size());
        for (Document d : docs) {
            Map<String, Object> doc = new HashMap<>();
            doc.put("id", d.getId());
            doc.put("doc_type", d.getDocType());
            doc.put("title", d.getTitle());
            doc.put("org_name", d.getOrgId() == null ? null : orgNames.get(d.getOrgId()));
            doc.put("amount", d.getAmount());
            doc.put("publish_date", d.getPublishDate());
            doc.put("region", d.getRegion());
            doc.put("category", d.getCategory());
            doc.put("content_text", d.getContentText());
            Map<String, Object> statsView = null;
            if (d.getOrgId() != null && d.getCategory() != null) {
                OrgStat s = stats.get(d.getOrgId() + "\n" + d.getCategory());
                if (s != null) {
                    statsView = new HashMap<>();
                    statsView.put("category", s.getCategory());
                    statsView.put("sample_count", s.getSampleCount());
                    statsView.put("amount_mean", s.getAmountMean());
                    statsView.put("amount_std", s.getAmountStd());
                    statsView.put("amount_p95", s.getAmountP95());
                    statsView.put("freq_mean_30d", s.getFreqMean30d());
                }
            }
            items.add(new QueueDoc(doc, statsView, profiles));
        }
        return items;
    }

    /** 批量置已扫描;signal_scanned 是全局幂等位(不按 detector 区分),重复调用无害。 */
    @Transactional
    public int scanComplete(List<String> documentIds) {
        if (documentIds == null || documentIds.isEmpty()) {
            throw BusinessException.badRequest("document_ids 不能为空");
        }
        return documentMapper.update(null, new LambdaUpdateWrapper<Document>()
                .set(Document::getSignalScanned, true)
                .in(Document::getId, documentIds)
                .eq(Document::getSignalScanned, false));
    }

    // ---------- E2-2/E2-4 推送落库与自动触发 ----------

    @com.fasterxml.jackson.databind.annotation.JsonNaming(
            com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record PushResult(String signalId, String status, boolean autoInvestigated) {
    }

    @Transactional
    public PushResult push(String documentId, List<Map<String, Object>> hits, BigDecimal score, String detector) {
        if (documentId == null || documentId.isBlank()) {
            throw BusinessException.badRequest("document.id 必填");
        }
        if (detector == null || detector.isBlank()) {
            throw BusinessException.badRequest("detector 必填");
        }
        if (hits == null || hits.isEmpty()) {
            throw BusinessException.badRequest("hits 不能为空");
        }
        if (score == null || score.signum() < 0 || score.compareTo(BigDecimal.ONE) > 0) {
            throw BusinessException.badRequest("score 必须在 [0,1]");
        }
        Document doc = documentMapper.selectById(documentId);
        if (doc == null) {
            throw BusinessException.notFound("文档不存在: " + documentId);
        }

        Signal existing = findNaturalKey(documentId, detector, hits);
        if (existing != null) {
            return new PushResult(existing.getId(), existing.getStatus(), existing.getInvestigationId() != null);
        }

        Signal s = new Signal();
        s.setId(IdGen.next("sig"));
        s.setDocumentId(documentId);
        s.setOrgId(doc.getOrgId());
        s.setDetector(detector);
        s.setHits(hits);
        s.setScore(score);
        s.setStatus("pending");
        int inserted = signalMapper.insertIgnoreConflict(s);
        if (inserted == 0) {
            // 并发同推:唯一约束被 ON CONFLICT 静默吞掉,回读已有信号按幂等语义返回
            Signal race = findNaturalKey(documentId, detector, hits);
            if (race != null) {
                return new PushResult(race.getId(), race.getStatus(), race.getInvestigationId() != null);
            }
            throw BusinessException.conflict("信号写入冲突: " + documentId);
        }

        InvestigationPolicy policy = policyService.policies();
        if (score.compareTo(policy.getAutoInvestigateThreshold()) >= 0) {
            createInvestigation(s, policy);
        }

        Events.publishAfterCommit(eventPublisher, new SignalCreatedEvent(
                s.getId(), documentId, doc.getTitle(), doc.getOrgId(), score, java.time.Instant.now()));
        return new PushResult(s.getId(), s.getStatus(), s.getInvestigationId() != null);
    }

    /** 找同 document + detector + hits 的已有信号(jsonb 相等忽略键序,与唯一约束同语义)。
     *  序列化必须用与 JsonbTypeHandler 相同语义的裸 mapper(保留 null 值键)——
     *  Spring 全局 non_null 会丢掉值为 null 的键,导致预查永远匹配不上带 null 的 hits。 */
    private static final ObjectMapper NATURAL_KEY_JSON = new ObjectMapper();

    private Signal findNaturalKey(String documentId, String detector, List<Map<String, Object>> hits) {
        String hitsJson;
        try {
            hitsJson = NATURAL_KEY_JSON.writeValueAsString(hits);
        } catch (Exception e) {
            throw BusinessException.badRequest("hits 序列化失败");
        }
        return signalMapper.selectOne(new LambdaQueryWrapper<Signal>()
                .eq(Signal::getDocumentId, documentId)
                .eq(Signal::getDetector, detector)
                .apply("hits = {0}::jsonb", hitsJson));
    }

    /** 建调查(created,E2-4)并把信号 pending → investigating(CAS);investigation_id 唯一约束兜底并发。 */
    private void createInvestigation(Signal s, InvestigationPolicy policy) {
        Investigation inv = new Investigation();
        inv.setId(IdGen.next("inv"));
        inv.setSignalId(s.getId());
        inv.setStatus(Investigation.STATUS_CREATED);
        inv.setCurrentRound(0);
        inv.setMaxRounds(policy.getDefaultMaxRounds());
        inv.setTokenBudget(policy.getDefaultTokenBudget());
        inv.setTokenUsed(0);
        inv.setCostEstimate(BigDecimal.ZERO);
        try {
            investigationMapper.insert(inv);
        } catch (DuplicateKeyException e) {
            throw BusinessException.conflict("信号已有调查: " + s.getId());
        }
        int rows = signalMapper.update(null, new LambdaUpdateWrapper<Signal>()
                .set(Signal::getStatus, "investigating")
                .set(Signal::getInvestigationId, inv.getId())
                .eq(Signal::getId, s.getId())
                .eq(Signal::getStatus, "pending"));
        if (rows == 0) {
            throw BusinessException.conflict("信号状态已变化,自动触发失败: " + s.getId());
        }
        s.setStatus("investigating");
        s.setInvestigationId(inv.getId());
    }

    // ---------- E2-2 查询 ----------

    public PageResult<SignalView> list(String status, String ruleType, String scoreGte, String orgId,
                                       String dateFrom, String dateTo, Integer page, Integer pageSize) {
        if (status != null && !Set.of("pending", "investigating", "confirmed", "dismissed").contains(status)) {
            throw BusinessException.badRequest("status 非法: " + status);
        }
        BigDecimal score = null;
        if (scoreGte != null) {
            try {
                score = new BigDecimal(scoreGte);
            } catch (NumberFormatException e) {
                throw BusinessException.badRequest("score_gte 非数值: " + scoreGte);
            }
        }
        LocalDate from = parseDate(dateFrom, "date_from");
        LocalDate to = parseDate(dateTo, "date_to");
        PageParams pp = PageParams.of(page, pageSize);

        LambdaQueryWrapper<Signal> w = new LambdaQueryWrapper<>();
        if (status != null) {
            w.eq(Signal::getStatus, status);
        }
        if (orgId != null) {
            w.eq(Signal::getOrgId, orgId);
        }
        if (score != null) {
            w.ge(Signal::getScore, score);
        }
        if (ruleType != null) {
            w.apply("EXISTS (SELECT 1 FROM jsonb_array_elements(hits) h WHERE h->>'rule_type' = {0})", ruleType);
        }
        ZoneId zone = ZoneId.systemDefault();
        if (from != null) {
            w.ge(Signal::getCreatedAt, from.atStartOfDay(zone).toOffsetDateTime());
        }
        if (to != null) {
            w.lt(Signal::getCreatedAt, to.plusDays(1).atStartOfDay(zone).toOffsetDateTime());
        }

        // count 先于 orderBy:selectCount 不剥 ORDER BY,聚合+排序非分组列会让 PG 直接报错
        long total = signalMapper.selectCount(w);
        w.orderByDesc(Signal::getCreatedAt);
        List<Signal> rows = total == 0 ? List.of()
                : signalMapper.selectList(w.last("LIMIT " + pp.pageSize() + " OFFSET " + pp.offset()));
        return PageResult.of(views(rows), total, pp);
    }

    private static LocalDate parseDate(String raw, String field) {
        if (raw == null) {
            return null;
        }
        try {
            return LocalDate.parse(raw);
        } catch (Exception e) {
            throw BusinessException.badRequest(field + " 非法: " + raw);
        }
    }

    public SignalView detail(String id) {
        Signal s = signalMapper.selectById(id);
        if (s == null) {
            throw BusinessException.notFound("信号不存在: " + id);
        }
        return views(List.of(s)).get(0);
    }

    // ---------- E2-5 人工决策与手动开调查 ----------

    @Transactional
    public SignalView decide(String id, String status, String reason, String userId) {
        if (!Set.of("confirmed", "dismissed").contains(status)) {
            throw BusinessException.badRequest("status 只能是 confirmed / dismissed");
        }
        boolean hasReason = reason != null && !reason.isBlank();
        if ("dismissed".equals(status) && !hasReason) {
            throw BusinessException.badRequest("dismiss 必填 reason");
        }
        Signal s = signalMapper.selectById(id);
        if (s == null) {
            throw BusinessException.notFound("信号不存在: " + id);
        }
        if (!"pending".equals(s.getStatus())) {
            throw BusinessException.conflict("仅 pending 状态可决策,当前: " + s.getStatus());
        }
        int rows = signalMapper.update(null, new LambdaUpdateWrapper<Signal>()
                .set(Signal::getStatus, status)
                .set(Signal::getDecidedBy, userId)
                .set(Signal::getDecidedAt, OffsetDateTime.now())
                .set(hasReason, Signal::getDecisionReason, reason)
                .eq(Signal::getId, id)
                .eq(Signal::getStatus, "pending"));
        if (rows == 0) {
            throw BusinessException.conflict("信号状态已被并发修改: " + id);
        }
        FeedbackEvent fb = new FeedbackEvent();
        fb.setId(IdGen.next("fbe"));
        fb.setUserId(userId);
        fb.setTargetType("signal");
        fb.setTargetId(id);
        fb.setAction("confirmed".equals(status) ? "confirm" : "dismiss");   // 信号状态 → 反馈动作词表
        fb.setReason(hasReason ? reason : null);
        feedbackEventMapper.insert(fb);
        return detail(id);
    }

    /** 手动开调查:pending/confirmed 可;已在调查中 40901;已忽略 42201(契约 §4)。 */
    @Transactional
    public Map<String, Object> investigate(String id) {
        Signal s = signalMapper.selectById(id);
        if (s == null) {
            throw BusinessException.notFound("信号不存在: " + id);
        }
        if ("dismissed".equals(s.getStatus())) {
            throw BusinessException.unprocessable("已忽略的信号不能开调查");
        }
        if (s.getInvestigationId() != null || "investigating".equals(s.getStatus())) {
            throw BusinessException.conflict("信号已在调查中: " + id);
        }
        createInvestigation(s, policyService.policies());
        return Map.of("signal_id", s.getId(), "status", s.getStatus(), "investigation_id", s.getInvestigationId());
    }

    // ---------- 视图组装(契约 §4) ----------

    private List<SignalView> views(List<Signal> signals) {
        Set<String> docIds = new HashSet<>();
        Set<String> orgIds = new HashSet<>();
        for (Signal s : signals) {
            docIds.add(s.getDocumentId());
            if (s.getOrgId() != null) {
                orgIds.add(s.getOrgId());
            }
        }
        Map<String, Document> docs = docIds.isEmpty() ? Map.of()
                : documentMapper.selectBatchIds(docIds).stream()
                        .collect(java.util.stream.Collectors.toMap(Document::getId, d -> d));
        Map<String, Organization> orgs = orgIds.isEmpty() ? Map.of()
                : organizationMapper.selectBatchIds(orgIds).stream()
                        .collect(java.util.stream.Collectors.toMap(Organization::getId, o -> o));
        List<SignalView> views = new ArrayList<>(signals.size());
        for (Signal s : signals) {
            Document d = docs.get(s.getDocumentId());
            Organization o = s.getOrgId() == null ? null : orgs.get(s.getOrgId());
            views.add(new SignalView(
                    s.getId(),
                    d == null ? null : new SignalView.DocRef(d.getId(), d.getTitle(), d.getAmount(), d.getPublishDate()),
                    o == null ? null : new SignalView.OrgRef(o.getId(), o.getName()),
                    s.getHits(), s.getScore(), s.getStatus(), s.getInvestigationId(), s.getCreatedAt()));
        }
        return views;
    }
}
