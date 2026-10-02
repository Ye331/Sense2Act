package com.sense2act.backend.domain.report;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.common.PageParams;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.graph.EventEntityLink;
import com.sense2act.backend.domain.graph.EventEntityLinkMapper;
import com.sense2act.backend.domain.graph.EventRelation;
import com.sense2act.backend.domain.graph.EventRelationMapper;
import com.sense2act.backend.domain.graph.GraphEntity;
import com.sense2act.backend.domain.graph.GraphEntityMapper;
import com.sense2act.backend.domain.graph.GraphEvent;
import com.sense2act.backend.domain.graph.GraphEventMapper;
import com.sense2act.backend.domain.investigation.Evidence;
import com.sense2act.backend.domain.investigation.EvidenceMapper;
import com.sense2act.backend.domain.investigation.Investigation;
import com.sense2act.backend.domain.investigation.InvestigationMapper;
import com.sense2act.backend.domain.investigation.InvestigationService;
import com.sense2act.backend.domain.investigation.InvestigationStep;
import com.sense2act.backend.domain.investigation.InvestigationStepMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 报告链路(E4-2/E4-4/E4-6):ReportDraft 校验落库(claims 引用校验 42201 整份拒收)、
 * 事件图谱沉淀(主体按名幂等复用)、meta 由后端从调查实测计算(D5,不信任报告自报)、
 * 用户侧报告页数据与 md/pdf 导出。
 */
@Service
public class ReportService {

    /** 契约 §6 固定免责声明,逐字。 */
    public static final String DISCLAIMER = "本报告由AI生成，结论不替代人的自主判断";

    private static final Set<String> NATURES = Set.of("fact", "inference", "speculation");
    private static final Set<String> PRIORITIES = Set.of("high", "medium", "low");
    private static final Set<String> ENTITY_TYPES = Set.of("org", "project", "product", "region", "policy");
    private static final Set<String> ENTITY_ROLES = Set.of("participant", "object", "scope", "beneficiary");
    private static final Set<String> RELATIONS = Set.of("前置", "参与", "佐证", "互证", "同属");

    private final ReportMapper reportMapper;
    private final ClaimMapper claimMapper;
    private final EvidenceLinkMapper evidenceLinkMapper;
    private final ActionSuggestionMapper suggestionMapper;
    private final GraphEntityMapper entityMapper;
    private final GraphEventMapper eventMapper;
    private final EventEntityLinkMapper eventEntityLinkMapper;
    private final EventRelationMapper eventRelationMapper;
    private final InvestigationMapper investigationMapper;
    private final InvestigationStepMapper stepMapper;
    private final EvidenceMapper evidenceMapper;
    private final InvestigationService investigationService;

    public ReportService(ReportMapper reportMapper, ClaimMapper claimMapper, EvidenceLinkMapper evidenceLinkMapper,
                         ActionSuggestionMapper suggestionMapper, GraphEntityMapper entityMapper,
                         GraphEventMapper eventMapper, EventEntityLinkMapper eventEntityLinkMapper,
                         EventRelationMapper eventRelationMapper, InvestigationMapper investigationMapper,
                         InvestigationStepMapper stepMapper, EvidenceMapper evidenceMapper,
                         InvestigationService investigationService) {
        this.reportMapper = reportMapper;
        this.claimMapper = claimMapper;
        this.evidenceLinkMapper = evidenceLinkMapper;
        this.suggestionMapper = suggestionMapper;
        this.entityMapper = entityMapper;
        this.eventMapper = eventMapper;
        this.eventEntityLinkMapper = eventEntityLinkMapper;
        this.eventRelationMapper = eventRelationMapper;
        this.investigationMapper = investigationMapper;
        this.stepMapper = stepMapper;
        this.evidenceMapper = evidenceMapper;
        this.investigationService = investigationService;
    }

    // ---------- E4-2 提交 ReportDraft(内部) ----------

    /** draft.token_usage 只作对账参考:预算与 meta 一律以 steps 累计为准(D5),报告不改账。 */
    @Transactional
    public ReportView submit(String investigationId, Draft draft) {
        Investigation inv = investigationMapper.selectById(investigationId);
        if (inv == null) {
            throw BusinessException.notFound("调查不存在: " + investigationId);
        }
        if (!Investigation.STATUS_INVESTIGATING.equals(inv.getStatus())) {
            throw BusinessException.conflict("只有 investigating 状态可提交报告,当前: " + inv.getStatus());
        }
        long existing = reportMapper.selectCount(new LambdaQueryWrapper<Report>()
                .eq(Report::getInvestigationId, investigationId));
        if (existing > 0) {
            throw BusinessException.conflict("该调查已有报告(一调查一报告): " + investigationId);
        }
        validateDraft(inv, draft);

        // D5:meta 从留痕计算 —— 轮次取 current_round,工具数数 tool_call 步骤,费用取调查累计口径
        long toolCalls = stepMapper.selectCount(new LambdaQueryWrapper<InvestigationStep>()
                .eq(InvestigationStep::getInvestigationId, investigationId)
                .eq(InvestigationStep::getType, InvestigationStep.TYPE_TOOL_CALL));
        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("rounds", inv.getCurrentRound());
        meta.put("tool_calls", (int) toolCalls);
        meta.put("token_used", inv.getTokenUsed());
        meta.put("cost_estimate", inv.getCostEstimate() == null ? "0"
                : inv.getCostEstimate().toPlainString());

        Report report = new Report();
        report.setId(IdGen.next("rep"));
        report.setInvestigationId(investigationId);
        report.setSignalId(inv.getSignalId());
        report.setTitle(draft.title().strip());
        report.setSummary(draft.summary() == null ? null : draft.summary().strip());
        report.setStatus(Report.STATUS_DRAFT);
        report.setMeta(meta);
        report.setCreatedAt(OffsetDateTime.now());
        report.setUpdatedAt(OffsetDateTime.now());
        reportMapper.insert(report);

        // 事件图谱:主体按名幂等复用;event_id 显式引用优先,否则从 extraction 建新事件
        String eventId = persistGraph(report.getId(), draft);
        if (eventId != null) {
            reportMapper.update(null, new LambdaUpdateWrapper<Report>()
                    .set(Report::getEventId, eventId)
                    .set(Report::getUpdatedAt, OffsetDateTime.now())
                    .eq(Report::getId, report.getId()));
        }

        int seq = 0;
        for (DraftClaim c : draft.claims()) {
            Claim claim = new Claim();
            claim.setId(IdGen.next("cl"));
            claim.setReportId(report.getId());
            claim.setSeq(++seq);
            claim.setText(c.text().strip());
            claim.setNature(c.nature());
            claim.setConfidence(c.confidence());
            claim.setCreatedAt(OffsetDateTime.now());
            claimMapper.insert(claim);
            // 同一 claim 内引用去重,防复合主键冲突
            Set<String> seen = new LinkedHashSet<>(c.evidenceIds() == null ? List.of() : c.evidenceIds());
            for (String evId : seen) {
                evidenceLinkMapper.insert(new EvidenceLink(claim.getId(), evId, "直接依据"));
            }
        }
        if (draft.actionSuggestions() != null) {
            for (DraftSuggestion s : draft.actionSuggestions()) {
                ActionSuggestion as = new ActionSuggestion();
                as.setId(IdGen.next("as"));
                as.setReportId(report.getId());
                as.setText(s.text().strip());
                as.setPriority(s.priority() == null ? "medium" : s.priority());
                as.setCreatedAt(OffsetDateTime.now());
                suggestionMapper.insert(as);
            }
        }

        // investigating → reporting(CAS;失败回滚整份报告)
        int rows = investigationMapper.update(null, new LambdaUpdateWrapper<Investigation>()
                .set(Investigation::getStatus, Investigation.STATUS_REPORTING)
                .eq(Investigation::getId, investigationId)
                .eq(Investigation::getStatus, Investigation.STATUS_INVESTIGATING));
        if (rows == 0) {
            throw BusinessException.conflict("调查状态已被并发修改: " + investigationId);
        }
        investigationService.writeDerivedStep(investigationId, inv.getCurrentRound(),
                "report_ready", Map.of("report_id", report.getId()));
        return detail(report.getId());
    }

    /** 词表与引用校验:参数问题 40001;claim 引用未登记/异调查证据 42201(整份拒收)。 */
    private void validateDraft(Investigation inv, Draft draft) {
        if (draft.title() == null || draft.title().isBlank()) {
            throw BusinessException.badRequest("title 必填");
        }
        if (draft.title().length() > 500) {
            throw BusinessException.badRequest("title 过长(≤500)");
        }
        if (draft.summary() != null && draft.summary().length() > 2000) {
            throw BusinessException.badRequest("summary 过长(≤2000)");
        }
        if (draft.claims() == null || draft.claims().isEmpty()) {
            throw BusinessException.badRequest("claims 至少 1 条");
        }
        List<String> allRefs = new ArrayList<>();
        for (DraftClaim c : draft.claims()) {
            if (c.text() == null || c.text().isBlank()) {
                throw BusinessException.badRequest("claim.text 必填");
            }
            if (c.text().length() > 2000) {
                throw BusinessException.badRequest("claim.text 过长(≤2000)");
            }
            if (c.nature() == null || !NATURES.contains(c.nature())) {
                throw BusinessException.badRequest("claim.nature 只能是 fact/inference/speculation");
            }
            if (c.confidence() == null || c.confidence().compareTo(BigDecimal.ZERO) < 0
                    || c.confidence().compareTo(BigDecimal.ONE) > 0) {
                throw BusinessException.badRequest("claim.confidence 需在 [0,1]");
            }
            if (c.evidenceIds() != null) {
                allRefs.addAll(c.evidenceIds());
            }
        }
        if (!allRefs.isEmpty()) {
            Set<String> registered = new LinkedHashSet<>(evidenceMapper.selectList(
                            new LambdaQueryWrapper<Evidence>()
                                    .eq(Evidence::getInvestigationId, inv.getId())
                                    .in(Evidence::getId, allRefs))
                    .stream().map(Evidence::getId).toList());
            List<String> missing = allRefs.stream().filter(e -> !registered.contains(e)).distinct().toList();
            if (!missing.isEmpty()) {
                throw BusinessException.unprocessable("claims 引用未登记证据: " + missing + "(整份拒收)");
            }
        }
        if (draft.actionSuggestions() != null) {
            for (DraftSuggestion s : draft.actionSuggestions()) {
                if (s.text() == null || s.text().isBlank()) {
                    throw BusinessException.badRequest("action_suggestions[].text 必填");
                }
                if (s.text().length() > 1000) {
                    throw BusinessException.badRequest("action_suggestions[].text 过长(≤1000)");
                }
                if (s.priority() != null && !PRIORITIES.contains(s.priority())) {
                    throw BusinessException.badRequest("priority 只能是 high/medium/low");
                }
            }
        }
        EventExtraction ex = draft.eventExtraction();
        if (ex != null) {
            List<DraftEntity> entities = ex.entities() == null ? List.of() : ex.entities();
            if (ex.event() != null) {
                if (ex.event().title() == null || ex.event().title().isBlank()
                        || ex.event().type() == null || ex.event().type().isBlank()) {
                    throw BusinessException.badRequest("event_extraction.event.title/type 必填");
                }
                if (ex.event().startDate() != null) {
                    try {
                        LocalDate.parse(ex.event().startDate());
                    } catch (Exception e) {
                        throw BusinessException.badRequest("start_date 需为 yyyy-MM-dd: " + ex.event().startDate());
                    }
                }
            } else if (draft.eventId() == null) {
                throw BusinessException.badRequest("event_extraction.event 必填(除非 event_id 引用既有事件)");
            }
            for (DraftEntity e : entities) {
                if (e.name() == null || e.name().isBlank()) {
                    throw BusinessException.badRequest("entities[].name 必填");
                }
                if (e.type() == null || !ENTITY_TYPES.contains(e.type())) {
                    throw BusinessException.badRequest("entities[].type 只能是 org/project/product/region/policy");
                }
                if (e.role() != null && !ENTITY_ROLES.contains(e.role())) {
                    throw BusinessException.badRequest("entities[].role 只能是 participant/object/scope/beneficiary");
                }
            }
            List<DraftRelation> relations = ex.relations() == null ? List.of() : ex.relations();
            for (DraftRelation r : relations) {
                checkNodeRef(r.source(), entities.size());
                checkNodeRef(r.target(), entities.size());
                if (r.relation() == null || !RELATIONS.contains(r.relation())) {
                    throw BusinessException.badRequest("relation 只能是 前置/参与/佐证/互证/同属");
                }
            }
        }
    }

    /** 关系节点引用:"event" 或 "entity:<i>",i 必须落在 entities 下标内(契约 §9.3)。 */
    private static void checkNodeRef(String ref, int entityCount) {
        if (ref == null || ref.isBlank()) {
            throw BusinessException.badRequest("relations[].source/target 必填");
        }
        if ("event".equals(ref)) {
            return;
        }
        if (ref.startsWith("entity:")) {
            try {
                int i = Integer.parseInt(ref.substring("entity:".length()));
                if (i < 0 || i >= entityCount) {
                    throw BusinessException.badRequest("关系节点越界: " + ref);
                }
                return;
            } catch (NumberFormatException e) {
                // 落到下面的统一报错
            }
        }
        throw BusinessException.badRequest("关系节点只能是 event 或 entity:<下标>: " + ref);
    }

    /**
     * 图谱落库。返回事件 id:event_id 显式引用既有事件(42201 校验),否则按 extraction 建新事件。
     * 主体按 name 幂等复用(跨报告沉淀);事件-主体关联与关系同样幂等(insertIgnore)。
     */
    private String persistGraph(String reportId, Draft draft) {
        EventExtraction ex = draft.eventExtraction();
        if (draft.eventId() == null && ex == null) {
            return null;
        }
        String eventId = draft.eventId();
        if (eventId != null) {
            if (eventMapper.selectById(eventId) == null) {
                throw BusinessException.unprocessable("event_id 引用的事件不存在: " + eventId);
            }
        } else {
            DraftEvent de = ex.event();
            GraphEvent event = new GraphEvent();
            event.setId(IdGen.next("evt"));
            event.setTitle(de.title().strip());
            event.setType(de.type().strip());
            event.setSummary(null);
            event.setStartDate(de.startDate() == null ? null : LocalDate.parse(de.startDate()));
            event.setStatus(GraphEvent.STATUS_ONGOING);
            event.setCreatedByReport(reportId);
            event.setCreatedAt(OffsetDateTime.now());
            eventMapper.insert(event);
            eventId = event.getId();
        }
        if (ex == null) {
            return eventId;
        }
        // 主体:按 name 建或复用,再挂到事件上(role 缺省 participant)
        Map<String, String> entityIdsByName = new LinkedHashMap<>();
        if (ex.entities() != null) {
            for (DraftEntity de : ex.entities()) {
                String name = de.name().strip();
                GraphEntity fresh = new GraphEntity();
                fresh.setId(IdGen.next("ent"));
                fresh.setName(name);
                fresh.setType(de.type());
                entityMapper.insertIgnore(fresh);
                GraphEntity stored = entityMapper.selectOne(new LambdaQueryWrapper<GraphEntity>()
                        .eq(GraphEntity::getName, name));
                entityIdsByName.put(name, stored.getId());
                eventEntityLinkMapper.insertIgnore(new EventEntityLink(eventId, stored.getId(),
                        de.role() == null ? "participant" : de.role()));
            }
        }
        // 关系:"entity:<i>" 解引用 extraction 下标,"event" 指向本次事件
        if (ex.relations() != null && !ex.relations().isEmpty()) {
            List<DraftEntity> entities = ex.entities() == null ? List.of() : ex.entities();
            for (DraftRelation r : ex.relations()) {
                String[] src = resolveNode(r.source(), entities, entityIdsByName, eventId);
                String[] tgt = resolveNode(r.target(), entities, entityIdsByName, eventId);
                EventRelation er = new EventRelation();
                er.setId(IdGen.next("er"));
                er.setSourceType(src[0]);
                er.setSourceId(src[1]);
                er.setTargetType(tgt[0]);
                er.setTargetId(tgt[1]);
                er.setRelation(r.relation());
                eventRelationMapper.insertIgnore(er);
            }
        }
        return eventId;
    }

    /** "event" → (event, eventId);"entity:<i>" → (entity, 第 i 个主体的实际 id)。 */
    private static String[] resolveNode(String ref, List<DraftEntity> entities,
                                        Map<String, String> entityIdsByName, String eventId) {
        if ("event".equals(ref)) {
            return new String[]{"event", eventId};
        }
        int i = Integer.parseInt(ref.substring("entity:".length()));   // 词表校验已在 validateDraft 完成
        String name = entities.get(i).name().strip();
        return new String[]{"entity", entityIdsByName.get(name)};
    }

    // ---------- E4-4 用户侧查询 ----------

    @Transactional(readOnly = true)
    public PageResult<ReportListItem> list(String investigationId, String dateFrom, String dateTo,
                                           Integer page, Integer pageSize) {
        LocalDate from = parseDate(dateFrom, "date_from");
        LocalDate to = parseDate(dateTo, "date_to");
        PageParams pp = PageParams.of(page, pageSize);
        LambdaQueryWrapper<Report> w = new LambdaQueryWrapper<>();
        if (investigationId != null && !investigationId.isBlank()) {
            w.eq(Report::getInvestigationId, investigationId);
        }
        ZoneId zone = ZoneId.systemDefault();
        if (from != null) {
            w.ge(Report::getCreatedAt, from.atStartOfDay(zone).toOffsetDateTime());
        }
        if (to != null) {
            w.lt(Report::getCreatedAt, to.plusDays(1).atStartOfDay(zone).toOffsetDateTime());
        }
        long total = reportMapper.selectCount(w);   // count 先于 orderBy(坑位)
        w.orderByDesc(Report::getCreatedAt);
        List<ReportListItem> items = total == 0 ? List.of()
                : reportMapper.selectList(w.last("LIMIT " + pp.pageSize() + " OFFSET " + pp.offset()))
                        .stream().map(ReportListItem::from).toList();
        return PageResult.of(items, total, pp);
    }

    /** 报告详情 = 契约 §6 形状:claims 内嵌证据摘要、meta、固定 disclaimer。 */
    @Transactional(readOnly = true)
    public ReportView detail(String id) {
        Report report = reportMapper.selectById(id);
        if (report == null) {
            throw BusinessException.notFound("报告不存在: " + id);
        }
        List<Claim> claims = claimMapper.selectList(new LambdaQueryWrapper<Claim>()
                .eq(Claim::getReportId, id).orderByAsc(Claim::getSeq));
        List<ActionSuggestion> suggestions = suggestionMapper.selectList(new LambdaQueryWrapper<ActionSuggestion>()
                .eq(ActionSuggestion::getReportId, id).orderByAsc(ActionSuggestion::getCreatedAt));

        List<String> evidenceIds = claims.stream()
                .flatMap(c -> evidenceLinkMapper.selectList(new LambdaQueryWrapper<EvidenceLink>()
                                .eq(EvidenceLink::getClaimId, c.getId()))
                        .stream().map(EvidenceLink::getEvidenceId)).distinct().toList();
        Map<String, Evidence> evidences = evidenceIds.isEmpty() ? Map.of()
                : evidenceMapper.selectBatchIds(evidenceIds).stream()
                        .collect(LinkedHashMap::new, (m, e) -> m.put(e.getId(), e), Map::putAll);

        List<ClaimView> claimViews = claims.stream().map(c -> {
            List<EvidenceRef> refs = evidenceLinkMapper.selectList(new LambdaQueryWrapper<EvidenceLink>()
                            .eq(EvidenceLink::getClaimId, c.getId()))
                    .stream().map(l -> evidences.get(l.getEvidenceId()))
                    .filter(java.util.Objects::nonNull)
                    .map(e -> new EvidenceRef(e.getId(), e.getTitle(), e.getSourceType())).toList();
            return new ClaimView(c.getId(), c.getSeq(), c.getText(), c.getNature(), c.getConfidence(), refs);
        }).toList();
        List<SuggestionView> sv = suggestions.stream()
                .map(s -> new SuggestionView(s.getId(), s.getText(), s.getPriority(), s.getAdoptedAt())).toList();
        return new ReportView(report.getId(), report.getInvestigationId(), report.getSignalId(),
                report.getEventId(), report.getTitle(), report.getSummary(), report.getStatus(),
                claimViews, sv, report.getMeta(), DISCLAIMER, report.getCreatedAt());
    }

    // ---------- E4-6 导出 ----------

    /** md 直接返回(契约 §6):标题/元信息/摘要/按 nature 分节的结论+依据/行动建议/证据清单,免责声明首尾各一次。 */
    @Transactional(readOnly = true)
    public String exportMarkdown(String id) {
        ReportView r = detail(id);
        StringBuilder md = new StringBuilder();
        md.append("# ").append(r.title()).append("\n\n");
        md.append("> ").append(DISCLAIMER).append("\n\n");
        Map<String, Object> meta = r.meta() == null ? Map.of() : r.meta();
        md.append("- 调查: ").append(r.investigationId()).append('\n');
        md.append("- 状态: ").append(r.status()).append('\n');
        md.append("- 轮次: ").append(meta.getOrDefault("rounds", "-"))
                .append(" ｜ 工具调用: ").append(meta.getOrDefault("tool_calls", "-"))
                .append(" ｜ Token: ").append(meta.getOrDefault("token_used", "-"))
                .append(" ｜ 费用估算(元): ").append(meta.getOrDefault("cost_estimate", "-")).append("\n\n");
        if (r.summary() != null && !r.summary().isBlank()) {
            md.append("## 摘要\n\n").append(r.summary()).append("\n\n");
        }
        sectionByNature(md, "事实结论（fact）", "fact", r);
        sectionByNature(md, "推断（inference）", "inference", r);
        sectionByNature(md, "推测（speculation）", "speculation", r);
        if (!r.actionSuggestions().isEmpty()) {
            md.append("## 行动建议\n\n");
            for (SuggestionView s : r.actionSuggestions()) {
                md.append("- 【").append(s.priority()).append("】").append(s.text()).append('\n');
            }
            md.append('\n');
        }
        List<EvidenceRef> all = r.claims().stream()
                .flatMap(c -> c.evidences().stream()).distinct().toList();
        if (!all.isEmpty()) {
            md.append("## 证据清单\n\n");
            for (EvidenceRef e : all) {
                md.append("- ").append(e.id()).append("｜").append(e.title()).append("｜")
                        .append(e.sourceType()).append('\n');
            }
            md.append('\n');
        }
        md.append("---\n\n").append(DISCLAIMER).append('\n');
        return md.toString();
    }

    private static void sectionByNature(StringBuilder md, String heading, String nature, ReportView r) {
        List<ClaimView> claims = r.claims().stream().filter(c -> nature.equals(c.nature())).toList();
        if (claims.isEmpty()) {
            return;
        }
        md.append("## ").append(heading).append("\n\n");
        int i = 0;
        for (ClaimView c : claims) {
            md.append(++i).append(". ").append(c.text())
                    .append("（置信度 ").append(c.confidence().toPlainString()).append("）\n");
            for (EvidenceRef e : c.evidences()) {
                md.append("   - 依据: ").append(e.title()).append("（").append(e.sourceType())
                        .append("，").append(e.id()).append("）\n");
            }
        }
        md.append('\n');
    }

    /** pdf 异步占位(C 级,OQ5):真实排版留 S8,这里回 202 + task_id。 */
    @Transactional(readOnly = true)
    public Map<String, Object> exportPdfTask(String id) {
        detail(id);   // 40401 早失败
        return Map.of("task_id", IdGen.next("task"), "status", "pending", "format", "pdf");
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

    // ---------- 请求/视图(契约 §6/§9.3) ----------

    /** ReportDraft(§9.3)。token_usage 仅对账参考,不入账(D5:steps 为准)。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record Draft(String title, String summary, String eventId, List<DraftClaim> claims,
                        List<DraftSuggestion> actionSuggestions, EventExtraction eventExtraction,
                        Integer tokenUsage) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record DraftClaim(String text, String nature, BigDecimal confidence, List<String> evidenceIds) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record DraftSuggestion(String text, String priority) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record DraftEvent(String title, String type, String startDate) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record DraftEntity(String name, String type, String role) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record DraftRelation(String source, String target, String relation) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record EventExtraction(DraftEvent event, List<DraftEntity> entities, List<DraftRelation> relations) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record EvidenceRef(String id, String title, String sourceType) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ClaimView(String id, Integer seq, String text, String nature, BigDecimal confidence,
                            List<EvidenceRef> evidences) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record SuggestionView(String id, String text, String priority, OffsetDateTime adoptedAt) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ReportListItem(String id, String investigationId, String title, String status,
                                 OffsetDateTime createdAt) {
        static ReportListItem from(Report r) {
            return new ReportListItem(r.getId(), r.getInvestigationId(), r.getTitle(), r.getStatus(),
                    r.getCreatedAt());
        }
    }

    /** 契约 §6 报告形状;meta.cost_estimate 序列化为字符串小数。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ReportView(String id, String investigationId, String signalId, String eventId, String title,
                             String summary, String status, List<ClaimView> claims,
                             List<SuggestionView> actionSuggestions, Map<String, Object> meta,
                             String disclaimer, OffsetDateTime createdAt) {
    }
}
