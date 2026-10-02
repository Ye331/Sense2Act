package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.common.PageParams;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.common.events.Events;
import com.sense2act.backend.common.events.InvestigationStepEvent;
import com.sense2act.backend.config.AppProperties;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.org.Organization;
import com.sense2act.backend.domain.org.OrganizationMapper;
import com.sense2act.backend.domain.policy.InvestigationPolicy;
import com.sense2act.backend.domain.policy.PolicyService;
import com.sense2act.backend.domain.signal.Signal;
import com.sense2act.backend.domain.signal.SignalMapper;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 调查生命周期(E3):领取(start,CAS + D14 并发闸)→ 上下文 → 步骤留痕与预算 → 问题推进 → 停止/TTL 清理。
 * 状态机:created → investigating →(E4:reporting → completed / failed);任意非终态可 stopped;created 超 TTL → failed。
 * 后端不驱动调查,只记账:步骤/问题由外部 Agent 上报,写库即发 SSE(事务提交后)。
 */
@Service
public class InvestigationService {

    private static final Set<String> STATUSES = Set.of(
            Investigation.STATUS_CREATED, Investigation.STATUS_INVESTIGATING, Investigation.STATUS_REPORTING,
            Investigation.STATUS_COMPLETED, Investigation.STATUS_FAILED, Investigation.STATUS_STOPPED);

    /** 契约 §7 事件名 → 步骤类型(data-model §2)。 */
    private static final Map<String, String> TYPE_BY_EVENT = Map.of(
            "questions_generated", InvestigationStep.TYPE_QUESTION,
            "tool_selected", InvestigationStep.TYPE_TOOL_SELECT,
            "tool_completed", InvestigationStep.TYPE_TOOL_CALL,
            "reflection_updated", InvestigationStep.TYPE_REFLECTION,
            "round_started", InvestigationStep.TYPE_STATUS_CHANGE,
            "investigation_started", InvestigationStep.TYPE_STATUS_CHANGE,
            "budget_update", InvestigationStep.TYPE_STATUS_CHANGE,
            "investigation_completed", InvestigationStep.TYPE_STATUS_CHANGE,
            "investigation_failed", InvestigationStep.TYPE_STATUS_CHANGE,
            "investigation_stopped", InvestigationStep.TYPE_STATUS_CHANGE);

    /** Agent 可直接上报的事件;investigation_started/stopped/budget_update 由后端接口派生,completed/failed 属 E4-3。 */
    private static final Set<String> AGENT_EVENTS = Set.of(
            "round_started", "questions_generated", "tool_selected", "tool_completed", "reflection_updated");

    public static final int CLAIM_DEFAULT_LIMIT = 50;
    public static final int CLAIM_MAX_LIMIT = 100;
    public static final int STEPS_DEFAULT_LIMIT = 50;
    public static final int STEPS_MAX_LIMIT = 200;

    private final InvestigationMapper investigationMapper;
    private final InvestigationStepMapper stepMapper;
    private final QuestionMapper questionMapper;
    private final EvidenceMapper evidenceMapper;
    private final SignalMapper signalMapper;
    private final DocumentMapper documentMapper;
    private final OrganizationMapper organizationMapper;
    private final PolicyService policyService;
    private final AppProperties props;
    private final ApplicationEventPublisher publisher;

    public InvestigationService(InvestigationMapper investigationMapper, InvestigationStepMapper stepMapper,
                                QuestionMapper questionMapper, EvidenceMapper evidenceMapper,
                                SignalMapper signalMapper, DocumentMapper documentMapper,
                                OrganizationMapper organizationMapper, PolicyService policyService,
                                AppProperties props, ApplicationEventPublisher publisher) {
        this.investigationMapper = investigationMapper;
        this.stepMapper = stepMapper;
        this.questionMapper = questionMapper;
        this.evidenceMapper = evidenceMapper;
        this.signalMapper = signalMapper;
        this.documentMapper = documentMapper;
        this.organizationMapper = organizationMapper;
        this.policyService = policyService;
        this.props = props;
        this.publisher = publisher;
    }

    // ---------- E3-1 领取 ----------

    /** created 列表按 created_at asc:FIFO 领取(契约 §9.3)。 */
    @Transactional(readOnly = true)
    public List<InvView> claimList(String status, Integer limit) {
        String st = status == null || status.isBlank() ? Investigation.STATUS_CREATED : status.trim();
        if (!STATUSES.contains(st)) {
            throw BusinessException.badRequest("status 非法: " + status);
        }
        int l = limit == null ? CLAIM_DEFAULT_LIMIT : limit;
        if (l < 1 || l > CLAIM_MAX_LIMIT) {
            throw BusinessException.badRequest("limit 必须在 1.." + CLAIM_MAX_LIMIT);
        }
        return investigationMapper.selectList(new LambdaQueryWrapper<Investigation>()
                        .eq(Investigation::getStatus, st)
                        .orderByAsc(Investigation::getCreatedAt)
                        .last("LIMIT " + l))
                .stream().map(InvView::from).toList();
    }

    /** 领取:created → investigating(CAS)。D14 并发上限在领取时挡:先过事务级独木桥再计数,杜绝并发超卖。 */
    @Transactional
    public InvView start(String id) {
        Investigation inv = mustGet(id);
        if (!Investigation.STATUS_CREATED.equals(inv.getStatus())) {
            throw BusinessException.conflict("只有 created 状态可领取,当前: " + inv.getStatus());
        }
        investigationMapper.lockStartGate();
        InvestigationPolicy policy = policyService.policies();
        long active = investigationMapper.selectCount(new LambdaQueryWrapper<Investigation>()
                .in(Investigation::getStatus, Investigation.STATUS_INVESTIGATING, Investigation.STATUS_REPORTING));
        if (active >= policy.getMaxConcurrentInvestigations()) {
            throw BusinessException.conflict("并发调查数已达上限(D14): " + active + "/" + policy.getMaxConcurrentInvestigations());
        }
        int rows = investigationMapper.update(null, new LambdaUpdateWrapper<Investigation>()
                .set(Investigation::getStatus, Investigation.STATUS_INVESTIGATING)
                .set(Investigation::getStartedAt, OffsetDateTime.now())
                .eq(Investigation::getId, id)
                .eq(Investigation::getStatus, Investigation.STATUS_CREATED));
        if (rows == 0) {
            throw BusinessException.conflict("调查已被并发领取: " + id);
        }
        writeStep(id, 0, InvestigationStep.TYPE_STATUS_CHANGE, "investigation_started", Map.of(
                "investigation_id", id,
                "max_rounds", inv.getMaxRounds(),
                "token_budget", inv.getTokenBudget()), 0);
        return InvView.from(mustGet(id));
    }

    // ---------- E3-2 上下文 ----------

    /** Agent 开工一次性取齐:信号 hits + 文档全文 + 当前 open 问题 + 已登记证据摘要 + 剩余预算。 */
    @Transactional(readOnly = true)
    public ContextView context(String id) {
        Investigation inv = mustGet(id);
        Signal signal = signalMapper.selectById(inv.getSignalId());
        if (signal == null) {
            throw BusinessException.notFound("调查关联的信号不存在: " + inv.getSignalId());
        }
        Document doc = documentMapper.selectById(signal.getDocumentId());
        String orgName = null;
        if (doc != null && doc.getOrgId() != null) {
            Organization org = organizationMapper.selectById(doc.getOrgId());
            orgName = org == null ? null : org.getName();
        }
        Map<String, Object> documentView = new HashMap<>();
        if (doc != null) {
            documentView.put("id", doc.getId());
            documentView.put("doc_type", doc.getDocType());
            documentView.put("title", doc.getTitle());
            documentView.put("org_name", orgName);
            documentView.put("amount", doc.getAmount() == null ? null : doc.getAmount().toPlainString());
            documentView.put("publish_date", doc.getPublishDate());
            documentView.put("deadline", doc.getDeadline());
            documentView.put("region", doc.getRegion());
            documentView.put("category", doc.getCategory());
            documentView.put("url", doc.getUrl());
            documentView.put("content_text", doc.getContentText());
        }
        Map<String, Object> signalView = new HashMap<>();
        signalView.put("id", signal.getId());
        signalView.put("hits", signal.getHits());
        signalView.put("score", signal.getScore() == null ? null : signal.getScore().toPlainString());
        signalView.put("document", documentView.isEmpty() ? null : documentView);

        List<QuestionView> open = questionMapper.selectList(new LambdaQueryWrapper<Question>()
                        .eq(Question::getInvestigationId, id)
                        .eq(Question::getStatus, Question.STATUS_OPEN)
                        .orderByAsc(Question::getCreatedAt))
                .stream().map(QuestionView::from).toList();
        List<Map<String, Object>> evidences = evidenceMapper.selectList(
                        new LambdaQueryWrapper<Evidence>().eq(Evidence::getInvestigationId, id)
                                .orderByDesc(Evidence::getFetchedAt))
                .stream().map(e -> {
                    Map<String, Object> v = new HashMap<>();
                    v.put("id", e.getId());
                    v.put("source_type", e.getSourceType());
                    v.put("title", e.getTitle());
                    v.put("url", e.getUrl());
                    v.put("excerpt", e.getExcerpt());
                    v.put("published_at", e.getPublishedAt());
                    v.put("fetched_at", e.getFetchedAt());
                    v.put("doc_id", e.getDocId());
                    return v;
                }).toList();
        return new ContextView(InvView.from(inv), signalView, open, evidences,
                inv.getTokenBudget() - inv.getTokenUsed());
    }

    // ---------- E3-3 步骤留痕,E3-5 预算 ----------

    @Transactional
    public StepView appendStep(String investigationId, String event, Integer round,
                               Map<String, Object> payload, Integer tokenUsage) {
        String type = TYPE_BY_EVENT.get(event);
        if (type == null) {
            throw BusinessException.badRequest("未知事件名: " + event + "(契约 §7)");
        }
        if (!AGENT_EVENTS.contains(event)) {
            throw BusinessException.badRequest("事件 " + event + " 由后端接口产生,Agent 不可直接上报");
        }
        Investigation inv = mustGet(investigationId);
        if (!Investigation.STATUS_INVESTIGATING.equals(inv.getStatus())) {
            throw BusinessException.conflict("只有 investigating 状态可写步骤,当前: " + inv.getStatus());
        }
        if (round == null || round < 1) {
            throw BusinessException.badRequest("round 必须为正整数");
        }
        if (round > inv.getMaxRounds()) {
            throw BusinessException.unprocessable("超出 max_rounds: " + round + " > " + inv.getMaxRounds());
        }
        int usage = tokenUsage == null ? 0 : tokenUsage;
        if (usage < 0) {
            throw BusinessException.badRequest("token_usage 不能为负");
        }
        long newUsed = inv.getTokenUsed() + usage;
        if (newUsed > inv.getTokenBudget()) {
            throw BusinessException.unprocessable("超出 token_budget: " + newUsed + " > " + inv.getTokenBudget());
        }
        InvestigationStep step = writeStep(investigationId, round, type, event, payload, usage);
        if (round > inv.getCurrentRound()) {
            investigationMapper.update(null, new LambdaUpdateWrapper<Investigation>()
                    .set(Investigation::getCurrentRound, round)
                    .eq(Investigation::getId, investigationId));
        }
        if (usage > 0) {
            // E3-5:累加 token_used,按单价折 cost_estimate,并派生 budget_update 步骤(可随 SSE 补发)
            BigDecimal cost = BigDecimal.valueOf(newUsed).multiply(unitPrice()).setScale(4, RoundingMode.HALF_UP);
            investigationMapper.update(null, new LambdaUpdateWrapper<Investigation>()
                    .set(Investigation::getTokenUsed, (int) newUsed)
                    .set(Investigation::getCostEstimate, cost)
                    .eq(Investigation::getId, investigationId));
            writeStep(investigationId, round, InvestigationStep.TYPE_STATUS_CHANGE, "budget_update", Map.of(
                    "token_used", (int) newUsed,
                    "token_budget", inv.getTokenBudget()), 0);
        }
        return StepView.from(step);
    }

    // ---------- E3-2 用户侧查询 ----------

    @Transactional(readOnly = true)
    public PageResult<InvView> list(String signalId, String status, String dateFrom, String dateTo,
                                    Integer page, Integer pageSize) {
        if (status != null && !STATUSES.contains(status)) {
            throw BusinessException.badRequest("status 非法: " + status);
        }
        LocalDate from = parseDate(dateFrom, "date_from");
        LocalDate to = parseDate(dateTo, "date_to");
        PageParams pp = PageParams.of(page, pageSize);

        LambdaQueryWrapper<Investigation> w = new LambdaQueryWrapper<>();
        if (signalId != null && !signalId.isBlank()) {
            w.eq(Investigation::getSignalId, signalId);
        }
        if (status != null) {
            w.eq(Investigation::getStatus, status);
        }
        ZoneId zone = ZoneId.systemDefault();
        if (from != null) {
            w.ge(Investigation::getCreatedAt, from.atStartOfDay(zone).toOffsetDateTime());
        }
        if (to != null) {
            w.lt(Investigation::getCreatedAt, to.plusDays(1).atStartOfDay(zone).toOffsetDateTime());
        }
        // count 先于 orderBy:selectCount 不剥 ORDER BY(S4 坑位)
        long total = investigationMapper.selectCount(w);
        w.orderByDesc(Investigation::getCreatedAt);
        List<InvView> items = total == 0 ? List.of()
                : investigationMapper.selectList(w.last("LIMIT " + pp.pageSize() + " OFFSET " + pp.offset()))
                        .stream().map(InvView::from).toList();
        return PageResult.of(items, total, pp);
    }

    @Transactional(readOnly = true)
    public InvDetailView detail(String id) {
        Investigation inv = mustGet(id);
        List<QuestionView> questions = questionMapper.selectList(new LambdaQueryWrapper<Question>()
                        .eq(Question::getInvestigationId, id)
                        .orderByAsc(Question::getCreatedAt))
                .stream().map(QuestionView::from).toList();
        return new InvDetailView(InvView.from(inv), questions);
    }

    /** 步骤游标分页:cursor = 上次读到的 seq,返回 seq 严格更大的批次(契约 §5)。 */
    @Transactional(readOnly = true)
    public Map<String, Object> steps(String id, Integer cursor, Integer limit) {
        mustGet(id);
        int from = cursor == null ? 0 : cursor;
        if (from < 0) {
            throw BusinessException.badRequest("cursor 不能为负: " + cursor);
        }
        int l = limit == null ? STEPS_DEFAULT_LIMIT : limit;
        if (l < 1 || l > STEPS_MAX_LIMIT) {
            throw BusinessException.badRequest("limit 必须在 1.." + STEPS_MAX_LIMIT);
        }
        List<InvestigationStep> rows = stepMapper.selectList(new LambdaQueryWrapper<InvestigationStep>()
                .eq(InvestigationStep::getInvestigationId, id)
                .gt(InvestigationStep::getSeq, from)
                .orderByAsc(InvestigationStep::getSeq)
                .last("LIMIT " + (l + 1)));   // 多取一条判断是否还有下一页
        boolean more = rows.size() > l;
        List<InvestigationStep> page = more ? rows.subList(0, l) : rows;
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("items", page.stream().map(StepView::from).toList());
        result.put("next_cursor", more ? page.get(page.size() - 1).getSeq() : null);
        return result;
    }

    // ---------- E3-4 问题 ----------

    @Transactional
    public QuestionView createQuestion(String investigationId, String text, Integer raisedInRound) {
        if (text == null || text.isBlank()) {
            throw BusinessException.badRequest("text 必填");
        }
        if (text.length() > 1000) {
            throw BusinessException.badRequest("text 过长(≤1000)");
        }
        Investigation inv = mustGet(investigationId);
        if (!Investigation.STATUS_INVESTIGATING.equals(inv.getStatus())) {
            throw BusinessException.conflict("只有 investigating 状态可提问,当前: " + inv.getStatus());
        }
        int round = raisedInRound == null ? 1 : raisedInRound;
        if (round < 1) {
            throw BusinessException.badRequest("raised_in_round 必须为正整数");
        }
        if (round > inv.getMaxRounds()) {
            throw BusinessException.unprocessable("超出 max_rounds: " + round + " > " + inv.getMaxRounds());
        }
        Question q = new Question();
        q.setId(IdGen.next("q"));
        q.setInvestigationId(investigationId);
        q.setText(text.strip());
        q.setRaisedInRound(round);
        q.setStatus(Question.STATUS_OPEN);
        q.setCreatedAt(OffsetDateTime.now());
        q.setUpdatedAt(OffsetDateTime.now());
        questionMapper.insert(q);
        return QuestionView.from(q);
    }

    /** PATCH:status 合法词表校验;evidence_ids 必须引用本调查已登记证据,否则 42201(E3-4)。 */
    @Transactional
    public QuestionView updateQuestion(String id, String status, String answerSummary, List<String> evidenceIds) {
        if (status != null && !Set.of(Question.STATUS_OPEN, Question.STATUS_CLARIFIED,
                Question.STATUS_UNRESOLVED, Question.STATUS_ABANDONED).contains(status)) {
            throw BusinessException.badRequest("status 只能是 open/clarified/unresolved/abandoned");
        }
        Question q = questionMapper.selectById(id);
        if (q == null) {
            throw BusinessException.notFound("问题不存在: " + id);
        }
        if (evidenceIds != null) {
            validateEvidenceRefs(q.getInvestigationId(), evidenceIds);
        }
        LambdaUpdateWrapper<Question> w = new LambdaUpdateWrapper<Question>()
                .set(Question::getUpdatedAt, OffsetDateTime.now())
                .eq(Question::getId, id);
        if (status != null) {
            w.set(Question::getStatus, status);
        }
        if (answerSummary != null) {
            w.set(Question::getAnswerSummary, answerSummary);
        }
        if (evidenceIds != null) {
            // wrapper 的 set() 不吃实体上的 @TableField typeHandler,jsonb 列必须显式指定
            w.set(Question::getEvidenceIds, evidenceIds,
                    "typeHandler=com.sense2act.backend.common.mybatis.JsonbTypeHandler");
        }
        questionMapper.update(null, w);
        return QuestionView.from(questionMapper.selectById(id));
    }

    private void validateEvidenceRefs(String investigationId, List<String> evidenceIds) {
        if (evidenceIds.isEmpty()) {
            return;
        }
        Set<String> registered = new LinkedHashSet<>(evidenceMapper.selectList(
                        new LambdaQueryWrapper<Evidence>()
                                .eq(Evidence::getInvestigationId, investigationId)
                                .in(Evidence::getId, evidenceIds))
                .stream().map(Evidence::getId).toList());
        List<String> missing = evidenceIds.stream().filter(e -> !registered.contains(e)).toList();
        if (!missing.isEmpty()) {
            throw BusinessException.unprocessable("evidence_ids 引用未登记证据: " + missing);
        }
    }

    // ---------- E3-6 停止与 TTL 清理 ----------

    /** 停止:investigating/reporting → stopped;信号回退 pending(同一信号不可再开新调查,id 留作追溯)。 */
    @Transactional
    public InvView stop(String id) {
        Investigation inv = mustGet(id);
        if (!Investigation.STATUS_INVESTIGATING.equals(inv.getStatus())
                && !Investigation.STATUS_REPORTING.equals(inv.getStatus())) {
            throw BusinessException.conflict("只有 investigating/reporting 可停止,当前: " + inv.getStatus());
        }
        int rows = investigationMapper.update(null, new LambdaUpdateWrapper<Investigation>()
                .set(Investigation::getStatus, Investigation.STATUS_STOPPED)
                .set(Investigation::getFinishedAt, OffsetDateTime.now())
                .eq(Investigation::getId, id)
                .in(Investigation::getStatus, Investigation.STATUS_INVESTIGATING, Investigation.STATUS_REPORTING));
        if (rows == 0) {
            throw BusinessException.conflict("调查状态已被并发修改: " + id);
        }
        writeStep(id, inv.getCurrentRound(), InvestigationStep.TYPE_STATUS_CHANGE, "investigation_stopped", Map.of(
                "status", Investigation.STATUS_STOPPED,
                "last_round", inv.getCurrentRound()), 0);
        signalMapper.update(null, new LambdaUpdateWrapper<Signal>()
                .set(Signal::getStatus, "pending")
                .eq(Signal::getId, inv.getSignalId())
                .eq(Signal::getStatus, "investigating"));
        return InvView.from(mustGet(id));
    }

    /** D7:created 超过 TTL 未领取 → failed(留 error 与 investigation_failed 步骤,信号回退 pending)。 */
    @Scheduled(fixedDelay = 60_000)
    @Transactional
    public void sweepExpiredCreated() {
        OffsetDateTime cutoff = OffsetDateTime.now().minus(props.investigation().createdTtl());
        List<Investigation> expired = investigationMapper.selectList(new LambdaQueryWrapper<Investigation>()
                .eq(Investigation::getStatus, Investigation.STATUS_CREATED)
                .lt(Investigation::getCreatedAt, cutoff));
        for (Investigation inv : expired) {
            String message = "created 超过 TTL(" + props.investigation().createdTtl() + ")未领取,自动清理(D7)";
            int rows = investigationMapper.update(null, new LambdaUpdateWrapper<Investigation>()
                    .set(Investigation::getStatus, Investigation.STATUS_FAILED)
                    .set(Investigation::getError, message)
                    .set(Investigation::getFinishedAt, OffsetDateTime.now())
                    .eq(Investigation::getId, inv.getId())
                    .eq(Investigation::getStatus, Investigation.STATUS_CREATED));
            if (rows == 0) {
                continue;
            }
            writeStep(inv.getId(), inv.getCurrentRound(), InvestigationStep.TYPE_STATUS_CHANGE,
                    "investigation_failed", Map.of("error", message, "last_round", inv.getCurrentRound()), 0);
            signalMapper.update(null, new LambdaUpdateWrapper<Signal>()
                    .set(Signal::getStatus, "pending")
                    .eq(Signal::getId, inv.getSignalId())
                    .eq(Signal::getStatus, "investigating"));
        }
    }

    // ---------- 内部 ----------

    private BigDecimal unitPrice() {
        BigDecimal price = props.investigation().tokenUnitPrice();
        return price == null ? BigDecimal.ZERO : price;
    }

    private Investigation mustGet(String id) {
        Investigation inv = investigationMapper.selectById(id);
        if (inv == null) {
            throw BusinessException.notFound("调查不存在: " + id);
        }
        return inv;
    }

    /** 落一步:分配 seq → 插库 → 提交后发 SSE(事件名与载荷来自 content)。 */
    private InvestigationStep writeStep(String investigationId, int round, String type, String event,
                                        Map<String, Object> payload, int tokenUsage) {
        Map<String, Object> content = new LinkedHashMap<>();
        content.put("event", event);
        if (payload != null) {
            content.putAll(payload);
        }
        InvestigationStep s = new InvestigationStep();
        s.setId(IdGen.next("ist"));
        s.setInvestigationId(investigationId);
        s.setSeq(stepMapper.nextSeq(investigationId));
        s.setRound(round);
        s.setType(type);
        s.setContent(content);
        s.setTokenUsage(tokenUsage);
        s.setCreatedAt(OffsetDateTime.now());
        stepMapper.insert(s);
        Map<String, Object> data = new LinkedHashMap<>(content);
        data.remove("event");
        Events.publishAfterCommit(publisher, new InvestigationStepEvent(investigationId, s.getSeq(), event, data));
        return s;
    }

    private static LocalDate parseDate(String raw, String field) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            return LocalDate.parse(raw.trim());
        } catch (Exception e) {
            throw BusinessException.badRequest(field + " 需为 yyyy-MM-dd: " + raw);
        }
    }

    // ---------- 视图(契约 §5/§9.3) ----------

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record InvView(String id, String signalId, String status, Integer currentRound, Integer maxRounds,
                          Integer tokenUsed, Integer tokenBudget,
                          @JsonFormat(shape = JsonFormat.Shape.STRING) BigDecimal costEstimate,
                          String judgment, String error, OffsetDateTime startedAt, OffsetDateTime finishedAt,
                          OffsetDateTime createdAt) {
        static InvView from(Investigation inv) {
            return new InvView(inv.getId(), inv.getSignalId(), inv.getStatus(), inv.getCurrentRound(),
                    inv.getMaxRounds(), inv.getTokenUsed(), inv.getTokenBudget(), inv.getCostEstimate(),
                    inv.getJudgment(), inv.getError(), inv.getStartedAt(), inv.getFinishedAt(), inv.getCreatedAt());
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record StepView(Integer seq, Integer round, String type, Map<String, Object> content,
                           Integer tokenUsage, OffsetDateTime createdAt) {
        static StepView from(InvestigationStep s) {
            return new StepView(s.getSeq(), s.getRound(), s.getType(), s.getContent(), s.getTokenUsage(),
                    s.getCreatedAt());
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record QuestionView(String id, String investigationId, String text, Integer raisedInRound,
                               String status, String answerSummary, List<String> evidenceIds,
                               OffsetDateTime createdAt, OffsetDateTime updatedAt) {
        static QuestionView from(Question q) {
            return new QuestionView(q.getId(), q.getInvestigationId(), q.getText(), q.getRaisedInRound(),
                    q.getStatus(), q.getAnswerSummary(), q.getEvidenceIds(), q.getCreatedAt(), q.getUpdatedAt());
        }
    }

    /** 详情 = 调查字段 + 当前问题清单(全状态,契约 §5)。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record InvDetailView(InvView investigation, List<QuestionView> questions) {
    }

    /** 领取后开工上下文(契约 §9.3):一次给齐信号/文档/问题/证据/预算。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ContextView(InvView investigation, Map<String, Object> signal, List<QuestionView> openQuestions,
                              List<Map<String, Object>> evidences, Integer budgetRemaining) {
    }
}
